from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, TextIO

from trace2task import __version__


class AppServerTransport(Protocol):
    """Minimal transport boundary used by the persistent Codex session."""

    def send(self, message: dict[str, Any]) -> None: ...

    def receive(self, timeout_seconds: float) -> dict[str, Any]: ...

    def close(self) -> None: ...


class StdioJsonTransport:
    """Exchange newline-delimited JSON with one long-running App Server process."""

    def __init__(self, codex_executable: str) -> None:
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            self._process = subprocess.Popen(
                [codex_executable, "app-server"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creation_flags,
            )
        except FileNotFoundError as error:
            raise RuntimeError(
                f"The resolved Codex CLI no longer exists: {codex_executable}"
            ) from error

        if self._process.stdin is None or self._process.stdout is None:
            self._process.terminate()
            raise RuntimeError("Codex App Server did not expose its stdio transport")

        self._stdin: TextIO = self._process.stdin
        self._messages: queue.Queue[dict[str, Any] | RuntimeError | None] = queue.Queue()
        self._stderr_lines: deque[str] = deque(maxlen=40)
        self._write_lock = threading.Lock()
        self._closed = False
        threading.Thread(
            target=self._read_stdout,
            args=(self._process.stdout,),
            name="trace2task-codex-stdout",
            daemon=True,
        ).start()
        if self._process.stderr is not None:
            threading.Thread(
                target=self._read_stderr,
                args=(self._process.stderr,),
                name="trace2task-codex-stderr",
                daemon=True,
            ).start()

    def _read_stdout(self, stream: TextIO) -> None:
        try:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    self._messages.put(
                        RuntimeError(f"Codex App Server emitted invalid JSON: {line.strip()}")
                    )
                    continue
                if isinstance(message, dict):
                    self._messages.put(message)
        except (OSError, ValueError):
            if not self._closed:
                self._messages.put(RuntimeError("Failed to read Codex App Server output"))
        finally:
            self._messages.put(None)

    def _read_stderr(self, stream: TextIO) -> None:
        try:
            for line in stream:
                if line.strip():
                    self._stderr_lines.append(line.rstrip())
        except (OSError, ValueError):
            return

    def send(self, message: dict[str, Any]) -> None:
        if self._closed or self._process.poll() is not None:
            raise RuntimeError(self._closed_message())
        wire_message = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        try:
            with self._write_lock:
                self._stdin.write(wire_message + "\n")
                self._stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as error:
            raise RuntimeError(self._closed_message()) from error

    def receive(self, timeout_seconds: float) -> dict[str, Any]:
        try:
            message = self._messages.get(timeout=max(timeout_seconds, 0.001))
        except queue.Empty as error:
            raise TimeoutError("Timed out waiting for Codex App Server") from error
        if message is None:
            raise RuntimeError(self._closed_message())
        if isinstance(message, RuntimeError):
            raise message
        return message

    def _closed_message(self) -> str:
        exit_code = self._process.poll()
        stderr = "\n".join(self._stderr_lines).strip()
        status = f" (exit code {exit_code})" if exit_code is not None else ""
        details = f": {stderr}" if stderr else ""
        return f"Codex App Server stopped unexpectedly{status}{details}"

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._stdin.close()
        except (OSError, ValueError):
            pass
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=3)


TransportFactory = Callable[[str], AppServerTransport]


class CodexAppServerSession:
    """Own one Codex process and one reusable conversation thread for a task run."""

    def __init__(
        self,
        codex_executable: str,
        *,
        model: str | None,
        cwd: Path,
        timeout_seconds: float = 120,
        transport_factory: TransportFactory = StdioJsonTransport,
    ) -> None:
        self.model = model
        self.cwd = cwd.resolve()
        self.timeout_seconds = timeout_seconds
        self._transport = transport_factory(codex_executable)
        self._request_id = 0
        self._thread_id: str | None = None
        self._pending_messages: deque[dict[str, Any]] = deque()
        self._closed = False

    @property
    def thread_id(self) -> str | None:
        return self._thread_id

    def start(self) -> str:
        if self._thread_id is not None:
            return self._thread_id
        self._request(
            "initialize",
            {
                "clientInfo": {
                    "name": "trace2task",
                    "title": "Trace2Task",
                    "version": __version__,
                }
            },
        )
        self._transport.send({"method": "initialized", "params": {}})
        params: dict[str, Any] = {
            "cwd": str(self.cwd),
            "approvalPolicy": "never",
            "sandbox": "read-only",
            "ephemeral": True,
            "serviceName": "trace2task",
        }
        if self.model:
            params["model"] = self.model
        result = self._request("thread/start", params)
        thread = result.get("thread")
        if not isinstance(thread, dict) or not isinstance(thread.get("id"), str):
            raise TypeError("Codex App Server returned no thread id")
        self._thread_id = thread["id"]
        return self._thread_id

    def run_turn(
        self,
        *,
        prompt: str,
        image_path: Path,
        output_schema: dict[str, Any],
    ) -> str:
        thread_id = self.start()
        deadline = time.monotonic() + self.timeout_seconds
        params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [
                {"type": "text", "text": prompt},
                {
                    "type": "localImage",
                    "path": str(image_path.resolve()),
                    "detail": "original",
                },
            ],
            "effort": "low",
            "summary": "none",
            "approvalPolicy": "never",
            "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
            "outputSchema": output_schema,
        }
        if self.model:
            params["model"] = self.model
        result = self._request("turn/start", params, deadline=deadline)
        turn = result.get("turn")
        if not isinstance(turn, dict) or not isinstance(turn.get("id"), str):
            raise TypeError("Codex App Server returned no turn id")
        turn_id = turn["id"]

        while True:
            message = self._receive(deadline)
            if self._handle_server_request(message):
                continue
            if message.get("method") != "turn/completed":
                continue
            completion = message.get("params")
            if not isinstance(completion, dict) or completion.get("threadId") != thread_id:
                continue
            completed_turn = completion.get("turn")
            if not isinstance(completed_turn, dict) or completed_turn.get("id") != turn_id:
                continue
            if completed_turn.get("status") != "completed":
                error = completed_turn.get("error")
                if isinstance(error, dict):
                    message_text = error.get("message")
                else:
                    message_text = None
                raise RuntimeError(
                    f"Codex decision failed: {message_text or completed_turn.get('status')}"
                )
            items = completed_turn.get("items")
            if not isinstance(items, list):
                raise TypeError("Codex completed the turn without any response items")
            responses = [
                item.get("text")
                for item in items
                if isinstance(item, dict)
                and item.get("type") == "agentMessage"
                and item.get("phase") != "commentary"
                and isinstance(item.get("text"), str)
            ]
            if not responses:
                raise RuntimeError("Codex completed the turn without a final agent message")
            return responses[-1]

    def _request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        self._request_id += 1
        request_id = self._request_id
        self._transport.send({"method": method, "id": request_id, "params": params})
        if deadline is None:
            deadline = time.monotonic() + self.timeout_seconds
        deferred: list[dict[str, Any]] = []
        try:
            while True:
                message = self._receive(deadline, include_pending=False)
                if self._handle_server_request(message):
                    continue
                if message.get("id") != request_id:
                    deferred.append(message)
                    continue
                if "error" in message:
                    error = message["error"]
                    detail = error.get("message") if isinstance(error, dict) else str(error)
                    raise RuntimeError(f"Codex App Server rejected {method}: {detail}")
                result = message.get("result")
                if not isinstance(result, dict):
                    raise TypeError(f"Codex App Server returned an invalid {method} response")
                return result
        finally:
            self._pending_messages.extend(deferred)

    def _receive(
        self,
        deadline: float,
        *,
        include_pending: bool = True,
    ) -> dict[str, Any]:
        if include_pending and self._pending_messages:
            return self._pending_messages.popleft()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(
                f"Codex did not return a decision within {self.timeout_seconds:g} seconds"
            )
        try:
            return self._transport.receive(remaining)
        except TimeoutError as error:
            raise RuntimeError(
                f"Codex did not return a decision within {self.timeout_seconds:g} seconds"
            ) from error

    def _handle_server_request(self, message: dict[str, Any]) -> bool:
        if "method" not in message or "id" not in message:
            return False
        self._transport.send(
            {
                "id": message["id"],
                "error": {
                    "code": -32601,
                    "message": "Trace2Task does not permit model-initiated tools or approvals",
                },
            }
        )
        return True

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._transport.close()
