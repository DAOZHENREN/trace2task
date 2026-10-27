from __future__ import annotations

import json
import math
import os
import queue
import re
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TextIO

from trace2task import __version__

_CODEX_CONNECTIVITY_ERROR_MARKERS = (
    "network reconnect",
    "tls handshake",
    "stream disconnected",
    "connection failed",
    "error sending request",
)


_CODEX_RATE_LIMIT_PATTERN = re.compile(
    r"\b(?:HTTP\s+429|status:\s*429|429\s+Too Many Requests|rate limit exceeded)\b",
    re.IGNORECASE,
)
_SYSTEM_ERROR_GRACE_SECONDS = 1.0


class CodexTurnError(RuntimeError):
    """A classified model failure with bounded, run-local diagnostic evidence."""

    def __init__(self, category: str, message: str, *, diagnostics: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.category = category
        self.diagnostics = diagnostics or {}


class CodexTurnTimeoutError(CodexTurnError):
    """A model turn missed a specific response-progress deadline."""


class _CodexReceiveTimeout(RuntimeError):
    pass


def classify_codex_failure(error: BaseException) -> str | None:
    """Prefer explicit model errors over generic transport wording in wrappers."""

    current: BaseException | None = error
    seen: set[int] = set()
    inferred = None
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, CodexTurnError):
            return current.category
        text = str(current).casefold()
        if _CODEX_RATE_LIMIT_PATTERN.search(text):
            inferred = "codex_rate_limit"
        elif inferred is None and any(marker in text for marker in _CODEX_CONNECTIVITY_ERROR_MARKERS):
            inferred = "codex_connectivity"
        current = current.__cause__ or current.__context__
    return inferred


def _codex_error_details(error: Any) -> tuple[str, dict[str, Any]]:
    error = error if isinstance(error, dict) else {}
    info = error.get("codexErrorInfo")
    code = info if isinstance(info, str) else None
    http_status = None
    if isinstance(info, dict) and len(info) == 1:
        code, detail = next(iter(info.items()))
        if isinstance(detail, dict):
            http_status = detail.get("httpStatusCode")
    category = {
        "contextWindowExceeded": "codex_context_limit",
        "sessionBudgetExceeded": "codex_usage_limit",
        "usageLimitExceeded": "codex_usage_limit",
        "rateLimitExceeded": "codex_rate_limit",
        "serverOverloaded": "codex_server_error",
        "internalServerError": "codex_server_error",
        "httpConnectionFailed": "codex_connectivity",
        "responseStreamConnectionFailed": "codex_connectivity",
        "responseStreamDisconnected": "codex_connectivity",
        "responseTooManyFailedAttempts": "codex_connectivity",
    }.get(code, "codex_error")
    if http_status == 403:
        category = "codex_access_denied"
    elif http_status == 429 or _CODEX_RATE_LIMIT_PATTERN.search(str(error.get("message", ""))):
        category = "codex_rate_limit"
    elif isinstance(http_status, int) and http_status >= 500:
        category = "codex_server_error"
    return category, {"error_code": code, "http_status_code": http_status}


def _stderr_retry_notice(line: str) -> dict[str, Any] | None:
    # This CLI version reports long 429 backoffs only through structured stderr,
    # before emitting any protocol error. Copy metadata, never the raw log text.
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(entry, dict) or entry.get("target") != "codex_core::responses_retry":
        return None
    fields = entry.get("fields")
    if not isinstance(fields, dict):
        return None
    turn_id = fields.get("turn_id")
    if not isinstance(turn_id, str) or not turn_id:
        return None
    if not _CODEX_RATE_LIMIT_PATTERN.search(str(fields.get("sampling_error", ""))):
        return None
    match = re.search(r"\bin ([0-9]+(?:\.[0-9]+)?)s\b", str(fields.get("message", "")))
    delay = float(match.group(1)) if match else None
    if delay is not None and not math.isfinite(delay):
        delay = None
    params = {"turnId": turn_id, "httpStatusCode": 429, "retryAfterSeconds": delay}
    for source, key in (("retries", "retryAttempt"), ("max_retries", "maxRetries")):
        value = fields.get(source)
        if type(value) is int and value >= 0:
            params[key] = value
    return {"method": "trace2task/codexRetry", "params": params}


def is_codex_connectivity_error(error: BaseException) -> bool:
    """Return whether an exception chain indicates Codex transport unavailability."""

    return classify_codex_failure(error) == "codex_connectivity"


# Protocol values only. Picker availability comes from model/list per model.
CODEX_REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
DEFAULT_CODEX_MODEL = "gpt-6.1-sol"
DEFAULT_CODEX_REASONING_EFFORT = "low"


@dataclass(frozen=True)
class CodexTurnMetrics:
    total_ms: float
    thread_start_ms: float
    request_ack_ms: float
    completion_wait_ms: float
    prompt_chars: int
    image_count: int
    thread_reused: bool
    thread_generation: int


class AppServerTransport(Protocol):
    """Minimal transport boundary used by the persistent Codex session."""

    def send(self, message: dict[str, Any]) -> None: ...

    def receive(self, timeout_seconds: float) -> dict[str, Any]: ...

    def close(self) -> None: ...


class StdioJsonTransport:
    """Exchange newline-delimited JSON with one long-running App Server process."""

    def __init__(self, codex_executable: str, *, command: list[str] | None = None) -> None:
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        # GUI launches may not inherit the Codex shell's JSON logging settings.
        # Retry warnings are part of our stderr protocol, including silent 429s.
        child_env = os.environ.copy()
        child_env["LOG_FORMAT"] = "json"
        child_env["RUST_LOG"] = (
            f"{child_env.get('RUST_LOG') or 'warn'},codex_core::responses_retry=warn"
        )
        try:
            self._process = subprocess.Popen(
                command if command is not None else [codex_executable, "app-server"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creation_flags,
                env=child_env,
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
                    notice = _stderr_retry_notice(line)
                    if notice is not None:
                        # Wake a receive blocked on stdout while Codex sleeps.
                        self._messages.put(notice)
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

    def diagnostics(self) -> dict[str, Any]:
        # Keep timeout evidence without copying credentials, URLs or message
        # contents that a dependency might have written to stderr.
        stderr = "\n".join(tuple(self._stderr_lines)).casefold()
        return {
            "process_pid": self._process.pid,
            "process_exit_code": self._process.poll(),
            "stderr_connectivity_markers": [
                marker for marker in _CODEX_CONNECTIVITY_ERROR_MARKERS if marker in stderr
            ],
        }

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
        reasoning_effort: str = DEFAULT_CODEX_REASONING_EFFORT,
        cwd: Path,
        timeout_seconds: float = 120,
        progress_timeout_seconds: float | None = None,
        hard_timeout_seconds: float | None = None,
        transport_factory: TransportFactory = StdioJsonTransport,
        base_instructions: str | None = None,
        thread_config: dict[str, Any] | None = None,
        wait_on_rate_limit: bool = False,
        rate_limit_callback: Callable[[float | None], None] | None = None,
        wait_while_active: bool = False,
        active_wait_callback: Callable[[float], None] | None = None,
    ) -> None:
        if reasoning_effort not in CODEX_REASONING_EFFORTS:
            raise ValueError(
                "Codex reasoning effort must be one of "
                f"{', '.join(CODEX_REASONING_EFFORTS)}"
            )
        if timeout_seconds <= 0:
            raise ValueError("Codex timeout must be positive")
        if progress_timeout_seconds is not None and progress_timeout_seconds <= 0:
            raise ValueError("Codex progress timeout must be positive")
        if hard_timeout_seconds is not None and hard_timeout_seconds < timeout_seconds:
            raise ValueError("Codex hard timeout cannot be shorter than the initial timeout")
        self.model = model
        self.base_instructions = base_instructions
        self.thread_config = thread_config
        self.reasoning_effort = reasoning_effort
        self.cwd = cwd.resolve()
        self.timeout_seconds = timeout_seconds
        self.progress_timeout_seconds = progress_timeout_seconds
        self.hard_timeout_seconds = hard_timeout_seconds
        self.wait_on_rate_limit = wait_on_rate_limit
        self.rate_limit_callback = rate_limit_callback
        # Callers opting in must retain their stop control / overall run budget.
        # A live thread is evidence of an outstanding turn, not model progress.
        self.wait_while_active = wait_while_active
        self.active_wait_callback = active_wait_callback
        self.last_turn_wait_extended = False
        self._rate_limit_started: float | None = None
        self._rate_limit_until = 0.0
        self.last_rate_limit_wait_seconds = 0.0
        self._transport = transport_factory(codex_executable)
        self._request_id = 0
        self._thread_id: str | None = None
        self._thread_generation = 0
        self._initialized = False
        self._pending_messages: deque[dict[str, Any]] = deque()
        self.last_turn_metrics: CodexTurnMetrics | None = None
        self.context_near_limit = False
        self.last_token_usage = {}
        self._closed = False

    @property
    def thread_id(self) -> str | None:
        return self._thread_id

    def start(self) -> str:
        if self._thread_id is not None:
            return self._thread_id
        self.initialize()
        return self._start_thread()

    def initialize(self) -> None:
        """Connect without starting a conversation or requesting inference."""
        if not self._initialized:
            self._request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "trace2task",
                        "title": "Trace2Task",
                        "version": __version__,
                    }
                }
            )
            self._send({"method": "initialized", "params": {}})
            self._initialized = True

    def list_models(self) -> list[dict[str, Any]]:
        self.initialize()
        models, seen = [], set()
        cursor = None
        while True:
            params = {"limit": 100, "includeHidden": False}
            if cursor is not None:
                params["cursor"] = cursor
            result = self._request("model/list", params)
            if not isinstance(result.get("data"), list):
                raise TypeError("Codex model/list returned no model catalog")
            models.extend(result["data"])
            cursor = result.get("nextCursor")
            if cursor is None:
                return models
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise ValueError("Codex model/list returned an invalid pagination cursor")
            seen.add(cursor)

    def _start_thread(self) -> str:
        params: dict[str, Any] = {
            "cwd": str(self.cwd),
            "approvalPolicy": "never",
            "sandbox": "read-only",
            "ephemeral": True,
            "serviceName": "trace2task",
        }
        if self.model:
            params["model"] = self.model
        if self.base_instructions is not None:
            params["baseInstructions"] = self.base_instructions
        if self.thread_config is not None:
            params["config"] = self.thread_config
        result = self._request("thread/start", params)
        thread = result.get("thread")
        if not isinstance(thread, dict) or not isinstance(thread.get("id"), str):
            raise TypeError("Codex App Server returned no thread id")
        self._thread_id = thread["id"]
        self._thread_generation += 1
        return self._thread_id

    def reset_thread(self) -> None:
        """Start the next turn in a fresh ephemeral thread without restarting Codex."""

        if self._closed:
            raise RuntimeError("Cannot reset a closed Codex App Server session")
        self._thread_id = None
        self.context_near_limit = False

    def _rate_limit_elapsed(self) -> float:
        pending = (max(0.0, min(time.monotonic(), self._rate_limit_until) - self._rate_limit_started)
                   if self._rate_limit_started is not None else 0.0)
        return self.last_rate_limit_wait_seconds + pending

    def _finish_rate_limit_wait(self) -> None:
        if self._rate_limit_started is None:
            return
        self.last_rate_limit_wait_seconds = self._rate_limit_elapsed()
        self._rate_limit_started = None
        self._rate_limit_until = 0.0
        if self.rate_limit_callback is not None:
            self.rate_limit_callback(None)

    def run_turn(self, **kwargs: Any) -> str:
        self.last_rate_limit_wait_seconds = 0.0
        self.last_turn_wait_extended = False
        try:
            return self._run_turn(**kwargs)
        finally:
            self._finish_rate_limit_wait()

    def _run_turn(
        self,
        *,
        prompt: str,
        image_path: Path | None,
        output_schema: dict[str, Any] | None,
        additional_image_paths: tuple[Path, ...] = (),
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        total_started = time.perf_counter()
        self.last_token_usage = {}
        active_effort = reasoning_effort or self.reasoning_effort
        if active_effort not in CODEX_REASONING_EFFORTS:
            raise ValueError(
                "Codex reasoning effort must be one of "
                f"{', '.join(CODEX_REASONING_EFFORTS)}"
            )
        thread_reused = self._thread_id is not None
        thread_started = time.perf_counter()
        thread_id = self.start()
        thread_start_ms = (time.perf_counter() - thread_started) * 1000
        turn_started_at = time.monotonic()
        deadline = turn_started_at + self.timeout_seconds
        hard_deadline = (
            turn_started_at + self.hard_timeout_seconds
            if self.hard_timeout_seconds is not None
            else None
        )
        if image_path is None and additional_image_paths:
            raise ValueError("Additional images require a primary image")
        image_paths = () if image_path is None else (image_path, *additional_image_paths)
        params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [
                {"type": "text", "text": prompt},
                *(
                    {
                        "type": "localImage",
                        "path": str(current_path.resolve()),
                        "detail": "original",
                    }
                    for current_path in image_paths
                ),
            ],
            "effort": active_effort,
            "summary": "none",
            "approvalPolicy": "never",
            "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
        }
        if output_schema is not None:
            params["outputSchema"] = output_schema
        active_model = model or self.model
        if active_model:
            params["model"] = active_model
        request_started = time.perf_counter()
        result = self._request("turn/start", params, deadline=deadline)
        request_ack_ms = (time.perf_counter() - request_started) * 1000
        turn = result.get("turn")
        if not isinstance(turn, dict) or not isinstance(turn.get("id"), str):
            raise TypeError("Codex App Server returned no turn id")
        turn_id = turn["id"]
        completion_started = time.perf_counter()
        response_progress_seen = False
        last_response_progress_at: float | None = None
        last_event: dict[str, Any] | None = None
        pending_rate_limit: dict[str, Any] | None = None
        last_retry_error: tuple[str, dict[str, Any]] | None = None
        system_error_deadline: float | None = None
        wait_failure: tuple[str, str] | None = None
        wait_failure_deadline: float | None = None

        def turn_error(category: str, message: str, **details: Any) -> CodexTurnError:
            diagnostics = {
                "thread_id": thread_id, "turn_id": turn_id,
                "turn_elapsed_seconds": round(time.monotonic() - turn_started_at, 3),
                "model_progress_seen": response_progress_seen,
                "last_event": last_event,
                **details,
            }
            audit = getattr(self, "audit", None)
            if audit is not None:
                audit.record("codex_turn_error", category=category, **diagnostics)
            return CodexTurnError(category, message, diagnostics=diagnostics)

        def system_error() -> CodexTurnError:
            category, details = last_retry_error or ("codex_thread_error", {})
            message = "Codex thread entered systemError"
            http_status = details.get("http_status_code")
            if http_status is not None:
                message += f"; last retry failed with HTTP {http_status}"
                if http_status == 403:
                    message += " Forbidden"
            return turn_error(category, message, thread_status="systemError",
                              error_source="last_retry" if last_retry_error else "thread_status",
                              **details)

        def timeout_error(category: str, message: str) -> CodexTurnTimeoutError:
            now = time.monotonic()
            diagnostics = {
                "thread_id": thread_id, "turn_id": turn_id,
                "turn_elapsed_seconds": round(now - turn_started_at, 3),
                "model_progress_seen": response_progress_seen,
                "model_progress_idle_seconds": (
                    round(now - last_response_progress_at, 3)
                    if last_response_progress_at is not None else None
                ),
                "last_event": last_event,
                "timeout_seconds": self.timeout_seconds,
                "progress_timeout_seconds": self.progress_timeout_seconds,
                "hard_timeout_seconds": self.hard_timeout_seconds,
            }
            transport_diagnostics = getattr(self._transport, "diagnostics", None)
            if callable(transport_diagnostics):
                diagnostics["transport"] = transport_diagnostics()
            audit = getattr(self, "audit", None)
            if audit is not None:
                audit.record("codex_timeout", category=category, **diagnostics)
            return CodexTurnTimeoutError(category, message, diagnostics=diagnostics)

        while True:
            if self._rate_limit_started is not None and time.monotonic() >= self._rate_limit_until:
                self._finish_rate_limit_wait()
            # Only a known upstream cooldown pauses the clocks. Health checks
            # below extend the receive wait, never the caller's execution budget.
            pause = self._rate_limit_elapsed() + max(0.0, self._rate_limit_until - time.monotonic())
            active_deadline = (
                min(deadline, hard_deadline) if hard_deadline is not None else deadline
            ) + pause
            if wait_failure_deadline is not None:
                active_deadline = min(active_deadline, wait_failure_deadline)
            if system_error_deadline is not None:
                active_deadline = min(active_deadline, system_error_deadline)
            try:
                message = self._receive(active_deadline)
            except _CodexReceiveTimeout as error:
                if system_error_deadline is not None:
                    raise system_error() from error
                if wait_failure is not None:
                    raise turn_error(*wait_failure) from error
                if hard_deadline is not None and deadline >= hard_deadline:
                    raise timeout_error(
                        "hard_timeout",
                        "Codex model response exceeded the hard limit of "
                        f"{self.hard_timeout_seconds:g} seconds",
                    ) from error
                if self.wait_while_active:
                    # Read metadata only: ephemeral threads reject includeTurns.
                    # This is not a retry and must never issue another turn/start.
                    self.last_turn_wait_extended = True
                    status, check_error = None, None
                    check_deadline = time.monotonic() + 5
                    if hard_deadline is not None:
                        check_deadline = min(check_deadline, hard_deadline + pause)
                    try:
                        thread = self._request(
                            "thread/read", {"threadId": thread_id, "includeTurns": False},
                            deadline=check_deadline,
                        ).get("thread")
                        if isinstance(thread, dict) and thread.get("id") == thread_id:
                            status = thread.get("status")
                        if not isinstance(status, dict):
                            status = {}
                    except (RuntimeError, TypeError) as check_exception:
                        # Keep only the error type, never raw remote log contents.
                        check_error = type(check_exception).__name__
                        status = {}
                    status_type = status.get("type")
                    elapsed = time.monotonic() - turn_started_at
                    audit = getattr(self, "audit", None)
                    if audit is not None:
                        audit.record("codex_wait_status", thread_id=thread_id, turn_id=turn_id,
                                     turn_elapsed_seconds=round(elapsed, 3),
                                     status=status_type, check_error=check_error)
                    if status_type == "active":
                        deadline = time.monotonic() - self._rate_limit_elapsed() + 60
                        if self.active_wait_callback is not None:
                            self.active_wait_callback(elapsed)
                    else:
                        # A completion/error may have arrived during thread/read.
                        # _request defers notifications; drain them before failing.
                        # idle can precede turn/completed, so allow a short grace.
                        grace = (5 if status_type == "idle" else
                                 _SYSTEM_ERROR_GRACE_SECONDS if status_type == "systemError" else 0)
                        wait_failure_deadline = time.monotonic() + grace
                        if status_type == "systemError":
                            system_error_deadline = wait_failure_deadline
                        deadline = wait_failure_deadline - self._rate_limit_elapsed()
                        wait_failure = (
                            ("codex_thread_error", f"Codex thread is {status_type} without a completed turn")
                            if status_type in {"idle", "systemError", "notLoaded"} else
                            ("codex_status_unavailable", ("Codex did not return a valid thread status "
                             "within the bounded health check; no duplicate request was sent"))
                        )
                    continue
                if pending_rate_limit is not None:
                    raise turn_error(
                        "codex_rate_limit",
                        "Codex rate limited the request (HTTP 429); its retry did not finish "
                        "within this turn's response deadline. Wait before retrying.",
                        **pending_rate_limit,
                    ) from error
                if response_progress_seen:
                    if self.progress_timeout_seconds is not None:
                        detail = (
                            "Codex model response stopped before completion; no progress arrived for "
                            f"{self.progress_timeout_seconds:g} seconds"
                        )
                    else:
                        detail = f"Codex model response did not complete within {self.timeout_seconds:g} seconds"
                    raise timeout_error("response_in_progress_timeout", detail) from error
                raise timeout_error(
                    "first_token_timeout",
                    "Codex did not begin its model response within "
                    f"{self.timeout_seconds:g} seconds",
                ) from error
            except RuntimeError as error:
                if system_error_deadline is not None:
                    raise system_error() from error
                raise
            if self._handle_server_request(message):
                continue
            method = message.get("method")
            params = message.get("params")
            params = params if isinstance(params, dict) else {}
            item = params.get("item")
            item_type = item.get("type") if isinstance(item, dict) else None
            last_event = {"method": method, "item_type": item_type,
                          "at_seconds": round(time.monotonic() - turn_started_at, 3)}
            if method == "trace2task/codexRetry" and params.get("turnId") == turn_id:
                delay = params.get("retryAfterSeconds")
                pending_rate_limit = {
                    "http_status_code": params.get("httpStatusCode"),
                    "retry_after_seconds": delay,
                    "retry_attempt": params.get("retryAttempt"),
                    "max_retries": params.get("maxRetries"),
                }
                last_retry_error = ("codex_rate_limit", dict(pending_rate_limit))
                audit = getattr(self, "audit", None)
                if audit is not None:
                    audit.record("codex_retry", thread_id=thread_id, turn_id=turn_id,
                                 **pending_rate_limit)
                if system_error_deadline is not None:
                    continue
                if self.wait_on_rate_limit and isinstance(delay, (int, float)) and math.isfinite(delay) and delay >= 0:
                    self._finish_rate_limit_wait()
                    self._rate_limit_started = time.monotonic()
                    self._rate_limit_until = self._rate_limit_started + delay
                    if self.rate_limit_callback is not None:
                        self.rate_limit_callback(delay)
                    continue
                remaining = max(0.0, active_deadline - time.monotonic())
                if delay is not None and delay >= remaining:
                    raise turn_error(
                        "codex_rate_limit",
                        f"Codex rate limited the request (HTTP 429); retry is scheduled in "
                        f"{math.ceil(delay)} seconds, beyond this turn's remaining "
                        f"{remaining:.0f} seconds. Wait before retrying.",
                        remaining_seconds=round(remaining, 3), **pending_rate_limit,
                    )
                # The CLI owns this retry. Do not resend the turn or extend its budget.
                continue
            if (method == "error" and params.get("threadId") == thread_id
                    and params.get("turnId") == turn_id):
                error = params.get("error")
                category, details = _codex_error_details(error)
                if params.get("willRetry") is False:
                    detail = error.get("message") if isinstance(error, dict) else None
                    if category == "codex_access_denied":
                        detail = "Codex request was rejected by the service (HTTP 403 Forbidden)"
                    raise turn_error(category, f"Codex decision failed: {detail or category}",
                                     **details)
                last_retry_error = (category, details)
                if category == "codex_rate_limit":
                    pending_rate_limit = pending_rate_limit or {**details, "retry_after_seconds": None}
                continue
            status = params.get("status")
            if (method == "thread/status/changed" and params.get("threadId") == thread_id
                    and isinstance(status, dict) and status.get("type") == "systemError"):
                # The status can precede the terminal error/completion. Drain
                # that detail briefly, without resubmitting or accepting actions.
                if system_error_deadline is None:
                    system_error_deadline = time.monotonic() + _SYSTEM_ERROR_GRACE_SECONDS
                continue
            if method == "thread/tokenUsage/updated":
                usage = params.get("tokenUsage", {})
                limit = usage.get("modelContextWindow")
                used = usage.get("last", {}).get("totalTokens")
                if params.get("threadId") == thread_id and limit and isinstance(used, int):
                    self.context_near_limit = used >= limit * .8
                    self.last_token_usage = usage.get('last', {})
            # Input receipts are local acknowledgements, not model output.
            # Late items from a previous turn must not keep this turn alive.
            if (system_error_deadline is None
                    and isinstance(method, str) and method.startswith("item/")
                    and params.get("threadId") == thread_id and params.get("turnId") == turn_id
                    and (method not in {"item/started", "item/completed"}
                         or item_type not in {None, "userMessage", "functionCallOutput"})):
                response_progress_seen = True
                self._finish_rate_limit_wait()
                pending_rate_limit = None
                last_retry_error = None
                last_response_progress_at = time.monotonic()
                if self.progress_timeout_seconds is not None:
                    deadline = max(
                        deadline,
                        time.monotonic() - self._rate_limit_elapsed() + self.progress_timeout_seconds,
                    )
            if method != "turn/completed":
                continue
            completion = message.get("params")
            if not isinstance(completion, dict) or completion.get("threadId") != thread_id:
                continue
            completed_turn = completion.get("turn")
            if not isinstance(completed_turn, dict) or completed_turn.get("id") != turn_id:
                continue
            if completed_turn.get("status") != "completed":
                error = completed_turn.get("error")
                category, details = _codex_error_details(error)
                if completed_turn.get("status") == "interrupted":
                    category = "codex_interrupted"
                if isinstance(error, dict):
                    message_text = error.get("message")
                else:
                    message_text = None
                if category == "codex_access_denied":
                    message_text = "Codex request was rejected by the service (HTTP 403 Forbidden)"
                raise turn_error(
                    category, f"Codex decision failed: {message_text or completed_turn.get('status')}",
                    **details,
                )
            if system_error_deadline is not None:
                raise system_error()
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
            self.last_turn_metrics = CodexTurnMetrics(
                total_ms=(time.perf_counter() - total_started) * 1000,
                thread_start_ms=thread_start_ms,
                request_ack_ms=request_ack_ms,
                completion_wait_ms=(time.perf_counter() - completion_started) * 1000,
                prompt_chars=len(prompt),
                image_count=len(image_paths),
                thread_reused=thread_reused,
                thread_generation=self._thread_generation,
            )
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
        wire_request = {"method": method, "id": request_id, "params": params}
        self._send(wire_request)
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

    def _send(self, message: dict[str, Any]) -> None:
        audit = getattr(self, "audit", None)
        if audit is not None:
            audit.record("codex_request", payload=message)
        self._transport.send(message)

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
            raise _CodexReceiveTimeout(
                "Codex App Server did not respond within "
                f"{self.timeout_seconds:g} seconds"
            )
        try:
            message = self._transport.receive(remaining)
            audit = getattr(self, "audit", None)
            if audit is not None:
                audit.record("codex_response", payload=message)
            return message
        except TimeoutError as error:
            raise _CodexReceiveTimeout(
                "Codex App Server did not respond within "
                f"{self.timeout_seconds:g} seconds"
            ) from error

    def _handle_server_request(self, message: dict[str, Any]) -> bool:
        if "method" not in message or "id" not in message:
            return False
        self._send(
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
