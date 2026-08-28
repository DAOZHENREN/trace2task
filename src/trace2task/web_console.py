from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
import webbrowser
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import yaml

from trace2task import __version__
from trace2task.codex_app_server import (
    CODEX_MODELS,
    CODEX_REASONING_EFFORTS,
    DEFAULT_CODEX_MODEL,
    DEFAULT_CODEX_REASONING_EFFORT,
)
from trace2task.compiler import compile_trace, confirm_taskpack
from trace2task.windows_capture import GdiWindowCapture
from trace2task.windows_control import Win32Backend, WindowSelector, list_window_records
from trace2task.windows_recording import InputSnapshot, Win32InputMonitor, record_window_trace
from trace2task.windows_runner import (
    EmergencyStopRequested,
    Win32EmergencyStop,
    run_windows_agent,
)
from trace2task.windows_task import WINDOWS_ADAPTER, load_windows_task

MAX_REQUEST_BYTES = 16_384
MAX_LOG_ENTRIES = 300


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


@dataclass
class ConsoleJob:
    job_id: str
    task_path: str
    task_id: str
    instruction: str
    mode: str
    model: str = DEFAULT_CODEX_MODEL
    reasoning_effort: str = DEFAULT_CODEX_REASONING_EFFORT
    kind: str = "agent"
    status: str = "queued"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    logs: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    stop_requested: bool = False
    stop_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def snapshot(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "task_path": self.task_path,
            "task_id": self.task_id,
            "instruction": self.instruction,
            "mode": self.mode,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "kind": self.kind,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "logs": list(self.logs),
            "result": self.result,
            "error": self.error,
            "stop_requested": self.stop_requested,
        }


class ConsoleEmergencyStop:
    """Combine the existing F9 stop with a stop request from the web console."""

    def __init__(self, stop_event: threading.Event) -> None:
        self.stop_event = stop_event
        self.hotkey = Win32EmergencyStop()

    def start(self) -> None:
        self.hotkey.start()

    def raise_if_requested(self) -> None:
        if self.stop_event.is_set():
            raise EmergencyStopRequested("Web console stop requested")
        self.hotkey.raise_if_requested()

    def sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, seconds)
        while True:
            self.raise_if_requested()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self.stop_event.wait(min(remaining, 0.05))

    def close(self) -> None:
        self.hotkey.close()


class ConsoleRecordingMonitor:
    """Expose the web stop button as the recorder's normal cancel request."""

    def __init__(self, stop_event: threading.Event) -> None:
        self.inner = Win32InputMonitor(success_key="f8", cancel_key="f9")
        self.stop_event = stop_event
        self.success_key = self.inner.success_key
        self.cancel_key = self.inner.cancel_key

    def start(self) -> None:
        self.inner.start()

    def poll(self) -> InputSnapshot:
        snapshot = self.inner.poll()
        if not self.stop_event.is_set():
            return snapshot
        return InputSnapshot(
            keys_down=snapshot.keys_down,
            buttons_down=snapshot.buttons_down,
            cursor_position=snapshot.cursor_position,
            success_requested=snapshot.success_requested,
            cancel_requested=True,
        )

    def close(self) -> None:
        self.inner.close()


Runner = Callable[..., object]


class WebConsoleController:
    """Thread-safe local job controller behind the browser UI."""

    def __init__(self, project_root: Path, *, runner: Runner = run_windows_agent) -> None:
        self.project_root = project_root.expanduser().resolve()
        self.task_root = (self.project_root / "taskpacks").resolve()
        self.runner = runner
        self._lock = threading.RLock()
        self._jobs: dict[str, ConsoleJob] = {}
        self._active_job_id: str | None = None

    def list_taskpacks(self) -> list[dict[str, Any]]:
        if not self.task_root.is_dir():
            return []
        records: list[tuple[float, dict[str, Any]]] = []
        for path in self.task_root.rglob("task.yaml"):
            try:
                contract = load_windows_task(path)
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if contract.task.environment_adapter != WINDOWS_ADAPTER:
                continue
            relative = path.resolve().relative_to(self.project_root).as_posix()
            is_messaging_target = bool(
                contract.selector.process_name
                and contract.selector.process_name.casefold()
                in {"weixin.exe", "wechat.exe"}
            )
            missing_capabilities = (
                [
                    skill
                    for skill in ("type_text", "press_key")
                    if skill not in contract.task.actions
                ]
                if is_messaging_target
                else []
            )
            records.append(
                (
                    path.stat().st_mtime,
                    {
                        "path": relative,
                        "local_path": path.parent.resolve().relative_to(
                            self.project_root
                        ).as_posix(),
                        "task_id": contract.task.task_id,
                        "instruction": contract.task.instruction,
                        "review_status": contract.task.review_status,
                        "confirmed": not contract.task.requires_confirmation,
                        "process_name": contract.selector.process_name,
                        "title_contains": contract.selector.title_contains,
                        "actions": list(contract.task.actions),
                        "max_actions": contract.task.max_actions,
                        "missing_message_capabilities": missing_capabilities,
                    },
                )
            )
        records.sort(key=lambda item: item[0], reverse=True)
        return [record for _, record in records]

    def list_windows(self) -> list[dict[str, Any]]:
        return list_window_records(backend=Win32Backend())["windows"]

    def list_recordings(self) -> list[dict[str, Any]]:
        runs_root = self.project_root / "runs"
        if not runs_root.is_dir():
            return []
        records: list[tuple[float, dict[str, Any]]] = []
        for metadata_path in runs_root.glob("*/metadata.json"):
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if metadata.get("source") != "windows_human":
                continue
            trace_path = metadata_path.with_name("trace.jsonl")
            if not trace_path.is_file():
                continue
            records.append(
                (
                    metadata_path.stat().st_mtime,
                    {
                        "task_id": metadata.get("task_id"),
                        "success": bool(metadata.get("success")),
                        "stop_reason": metadata.get("stop_reason"),
                        "input_events": metadata.get("input_event_count", 0),
                        "process_name": (metadata.get("initial_window") or {}).get(
                            "process_name"
                        ),
                        "title": (metadata.get("initial_window") or {}).get("title"),
                        "trace_path": trace_path.relative_to(self.project_root).as_posix(),
                        "local_path": metadata_path.parent.relative_to(
                            self.project_root
                        ).as_posix(),
                    },
                )
            )
        records.sort(key=lambda item: item[0], reverse=True)
        return [record for _, record in records]

    def start_job(
        self,
        *,
        task_path: str,
        instruction: str,
        execute: bool,
        model: str = DEFAULT_CODEX_MODEL,
        reasoning_effort: str = DEFAULT_CODEX_REASONING_EFFORT,
    ) -> dict[str, Any]:
        normalized_instruction = " ".join(instruction.split())
        if not normalized_instruction:
            raise ValueError("请输入一条任务指令")
        if len(normalized_instruction) > 2_000:
            raise ValueError("任务指令不能超过 2000 个字符")
        if model not in CODEX_MODELS:
            raise ValueError(f"不支持的模型：{model}")
        if reasoning_effort not in CODEX_REASONING_EFFORTS:
            raise ValueError(f"不支持的思考强度：{reasoning_effort}")
        resolved_task = self._resolve_task_path(task_path)
        contract = load_windows_task(resolved_task)
        if execute and contract.task.requires_confirmation:
            raise ValueError("这个示范任务仍是草稿，确认任务包后才能执行")
        if (
            execute
            and contract.selector.process_name
            and contract.selector.process_name.casefold() in {"weixin.exe", "wechat.exe"}
            and "type_text" not in contract.task.actions
        ):
            raise ValueError("这份微信示范缺少文本输入能力，目前只能生成计划")
        with self._lock:
            self._require_idle()
            job = ConsoleJob(
                job_id=uuid.uuid4().hex,
                task_path=resolved_task.relative_to(self.project_root).as_posix(),
                task_id=contract.task.task_id,
                instruction=normalized_instruction,
                mode="execute" if execute else "plan",
                model=model,
                reasoning_effort=reasoning_effort,
            )
            self._jobs[job.job_id] = job
            self._active_job_id = job.job_id
        thread = threading.Thread(
            target=self._run_job,
            args=(job, resolved_task, execute),
            name=f"trace2task-web-{job.job_id[:8]}",
            daemon=True,
        )
        thread.start()
        return job.snapshot()

    def start_recording(self, *, handle: int, task_id: str) -> dict[str, Any]:
        normalized_task_id = " ".join(task_id.split())
        if not normalized_task_id:
            raise ValueError("请输入经验名称")
        if len(normalized_task_id) > 80:
            raise ValueError("经验名称不能超过 80 个字符")
        if re.search(r"[\\/:*?\"<>|\x00-\x1f]", normalized_task_id):
            raise ValueError("经验名称包含 Windows 路径不支持的字符")
        windows = self.list_windows()
        selected = next((window for window in windows if window["handle"] == handle), None)
        if selected is None:
            raise ValueError("所选窗口已经不存在，请刷新窗口列表")
        with self._lock:
            self._require_idle()
            job = ConsoleJob(
                job_id=uuid.uuid4().hex,
                task_path="",
                task_id=normalized_task_id,
                instruction=f"录制 {selected['process_name']} - {selected['title']}",
                mode="record",
                kind="recording",
            )
            self._jobs[job.job_id] = job
            self._active_job_id = job.job_id
        thread = threading.Thread(
            target=self._run_recording,
            args=(job, handle, selected),
            name=f"trace2task-record-{job.job_id[:8]}",
            daemon=True,
        )
        thread.start()
        return job.snapshot()

    def upgrade_taskpack(self, raw_path: str) -> dict[str, Any]:
        task_path = self._resolve_task_path(raw_path)
        contract = load_windows_task(task_path)
        if not contract.selector.process_name or contract.selector.process_name.casefold() not in {
            "weixin.exe",
            "wechat.exe",
        }:
            raise ValueError("当前只支持给微信经验补齐消息能力")
        root = yaml.safe_load(task_path.read_text(encoding="utf-8"))
        actions = root.get("actions")
        if not isinstance(actions, list):
            raise TypeError("任务包 actions 必须是列表")
        for skill in ("type_text", "press_key", "hotkey"):
            if skill not in actions:
                actions.append(skill)
        review = root.setdefault("review", {})
        review["status"] = "draft"
        review["requires_confirmation"] = True
        review.pop("confirmed_at", None)
        checklist = review.setdefault("checklist", [])
        note = "Confirm that runtime text input and final send behavior are intended."
        if note not in checklist:
            checklist.append(note)
        task_path.write_text(
            yaml.safe_dump(root, sort_keys=False, allow_unicode=True, width=100),
            encoding="utf-8",
        )
        load_windows_task(task_path)
        return {
            "task_path": task_path.relative_to(self.project_root).as_posix(),
            "status": "draft",
            "added_capabilities": ["type_text", "press_key", "hotkey"],
        }

    def confirm_local_taskpack(self, raw_path: str) -> dict[str, Any]:
        task_path = self._resolve_task_path(raw_path)
        result = confirm_taskpack(task_path)
        return _jsonable(result)

    def compile_recording(self, raw_path: str) -> dict[str, Any]:
        trace_path = self._resolve_recording_trace(raw_path)
        result = compile_trace(
            trace_path,
            self.project_root / "taskpacks" / "generated" / "web",
        )
        return _jsonable(result)

    def open_local(self, raw_path: str) -> dict[str, Any]:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("缺少本地路径")
        candidate = (self.project_root / raw_path).resolve()
        allowed_roots = ((self.project_root / "runs").resolve(), self.task_root)
        if not any(candidate.is_relative_to(root) for root in allowed_roots):
            raise ValueError("只能查看项目中的 runs 或 taskpacks 路径")
        if not candidate.exists():
            raise FileNotFoundError(f"本地路径不存在: {raw_path}")
        target = candidate if candidate.is_dir() else candidate.parent
        if os.name != "nt":
            raise RuntimeError("在本地查看目前仅支持 Windows")
        os.startfile(target)  # type: ignore[attr-defined]
        return {"opened": target.relative_to(self.project_root).as_posix()}

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"Unknown job: {job_id}")
            return job.snapshot()

    def active_job(self) -> dict[str, Any] | None:
        with self._lock:
            if self._active_job_id is None:
                return None
            return self._jobs[self._active_job_id].snapshot()

    def stop_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"Unknown job: {job_id}")
            if job.status not in {"queued", "running", "stopping"}:
                return job.snapshot()
            job.stop_requested = True
            job.status = "stopping"
            job.updated_at = _now()
            job.logs.append("网页控制台已请求停止；正在等待当前安全边界。")
            job.stop_event.set()
            return job.snapshot()

    def wait(self, job_id: str, timeout: float = 10) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snapshot = self.get_job(job_id)
            if snapshot["status"] not in {"queued", "running", "stopping"}:
                return snapshot
            time.sleep(0.01)
        raise TimeoutError(f"Job {job_id} did not finish within {timeout:g}s")

    def _resolve_task_path(self, raw_path: str) -> Path:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("请选择一个示范任务")
        candidate = (self.project_root / raw_path).resolve()
        if not candidate.is_relative_to(self.task_root) or candidate.name != "task.yaml":
            raise ValueError("任务路径必须指向项目 taskpacks 目录中的 task.yaml")
        if not candidate.is_file():
            raise FileNotFoundError(f"任务包不存在: {raw_path}")
        return candidate

    def _resolve_recording_trace(self, raw_path: str) -> Path:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("请选择一个原始录制")
        runs_root = (self.project_root / "runs").resolve()
        candidate = (self.project_root / raw_path).resolve()
        if not candidate.is_relative_to(runs_root) or candidate.name != "trace.jsonl":
            raise ValueError("录制路径必须指向项目 runs 目录中的 trace.jsonl")
        if not candidate.is_file():
            raise FileNotFoundError(f"原始录制不存在: {raw_path}")
        return candidate

    def _require_idle(self) -> None:
        if self._active_job_id is None:
            return
        active = self._jobs[self._active_job_id]
        if active.status in {"queued", "running", "stopping"}:
            raise RuntimeError("已有任务正在运行，请先等待或停止它")

    def _run_recording(
        self,
        job: ConsoleJob,
        handle: int,
        selected_window: dict[str, Any],
    ) -> None:
        self._update(
            job,
            status="running",
            log="录制已启动。请在目标窗口完成示范，按 F8 标记成功，F9 取消。",
        )
        process_name = str(selected_window.get("process_name") or "")
        capability_profile = (
            "messaging"
            if process_name.casefold() in {"weixin.exe", "wechat.exe"}
            else None
        )
        try:
            result = record_window_trace(
                WindowSelector(handle=handle),
                task_id=job.task_id,
                output_root=self.project_root / "runs",
                backend=Win32Backend(),
                capture=GdiWindowCapture(),
                monitor=ConsoleRecordingMonitor(job.stop_event),
                status_callback=lambda message: self._update(job, log=message),
                capability_profile=capability_profile,
            )
            payload = asdict(result)
            if result.success:
                self._update(job, log="示范录制成功，正在自动编译为草稿经验。")
                try:
                    compilation = compile_trace(
                        Path(result.trace_path),
                        self.project_root / "taskpacks" / "generated" / "web",
                    )
                except Exception as error:  # noqa: BLE001 - preserve successful recording
                    payload["compilation"] = {
                        "status": "failed",
                        "error": f"{type(error).__name__}: {error}",
                    }
                    self._update(
                        job,
                        status="partial",
                        result=payload,
                        log=(
                            "录制已保存，但自动编译失败："
                            f"{type(error).__name__}: {error}"
                        ),
                    )
                else:
                    payload["compilation"] = {
                        "status": "completed",
                        "result": _jsonable(compilation),
                    }
                    self._update(
                        job,
                        status="completed",
                        result=payload,
                        log="草稿经验已生成。",
                    )
            else:
                self._update(job, status="stopped", result=payload, log="录制未标记成功。")
        except Exception as error:  # noqa: BLE001 - recording failures must become UI state
            self._update(
                job,
                status="failed",
                error=f"{type(error).__name__}: {error}",
                log=f"录制失败：{type(error).__name__}: {error}",
            )

    def _run_job(self, job: ConsoleJob, task_path: Path, execute: bool) -> None:
        self._update(job, status="running", log="任务已启动。")
        try:
            kwargs: dict[str, Any] = {
                "instruction": job.instruction,
                "execute": execute,
                "model": job.model,
                "reasoning_effort": job.reasoning_effort,
                "output_root": self.project_root / "runs",
                "focus": not execute,
                "status_callback": lambda message: self._update(job, log=message),
            }
            if execute and self.runner is run_windows_agent:
                kwargs["emergency_stop"] = ConsoleEmergencyStop(job.stop_event)
            result = self.runner(task_path, **kwargs)
            payload = _jsonable(result)
            if not isinstance(payload, dict):
                payload = {"value": payload}
            final_status = "stopped" if payload.get("stop_reason") == "emergency_stop" else "completed"
            self._update(job, status=final_status, result=payload, log="任务运行结束。")
        except Exception as error:  # noqa: BLE001 - job failures must become UI state
            self._update(
                job,
                status="failed",
                error=f"{type(error).__name__}: {error}",
                log=f"运行失败：{type(error).__name__}: {error}",
            )

    def _update(
        self,
        job: ConsoleJob,
        *,
        status: str | None = None,
        log: str | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            if status is not None:
                job.status = status
            if log:
                job.logs.append(str(log))
                job.logs[:] = job.logs[-MAX_LOG_ENTRIES:]
            if result is not None:
                job.result = result
            if error is not None:
                job.error = error
            job.updated_at = _now()


class WebConsoleHandler(BaseHTTPRequestHandler):
    controller: WebConsoleController
    asset_root: Path

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            self._json(
                {
                    "version": __version__,
                    "taskpacks": self.controller.list_taskpacks(),
                    "recordings": self.controller.list_recordings(),
                    "active_job": self.controller.active_job(),
                    "agent_options": {
                        "models": list(CODEX_MODELS),
                        "reasoning_efforts": list(CODEX_REASONING_EFFORTS),
                        "defaults": {
                            "model": DEFAULT_CODEX_MODEL,
                            "reasoning_effort": DEFAULT_CODEX_REASONING_EFFORT,
                        },
                    },
                }
            )
            return
        if parsed.path == "/api/windows":
            self._json({"windows": self.controller.list_windows()})
            return
        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.removeprefix("/api/jobs/")
            try:
                self._json(self.controller.get_job(job_id))
            except KeyError as error:
                self._error(HTTPStatus.NOT_FOUND, str(error))
            return
        asset = "index.html" if parsed.path == "/" else unquote(parsed.path).removeprefix("/")
        if asset not in {"index.html", "app.js", "styles.css"}:
            self._error(HTTPStatus.NOT_FOUND, "Not found")
            return
        path = self.asset_root / asset
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
        }[path.suffix]
        self._bytes(path.read_bytes(), content_type=content_type)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self._read_json()
            if parsed.path == "/api/jobs":
                result = self.controller.start_job(
                    task_path=payload.get("task_path", ""),
                    instruction=payload.get("instruction", ""),
                    execute=payload.get("mode") == "execute",
                    model=payload.get("model", DEFAULT_CODEX_MODEL),
                    reasoning_effort=payload.get(
                        "reasoning_effort", DEFAULT_CODEX_REASONING_EFFORT
                    ),
                )
                self._json(result, status=HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/api/recordings":
                result = self.controller.start_recording(
                    handle=payload.get("handle"),
                    task_id=payload.get("task_id", ""),
                )
                self._json(result, status=HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/api/taskpacks/upgrade":
                self._json(self.controller.upgrade_taskpack(payload.get("task_path", "")))
                return
            if parsed.path == "/api/taskpacks/confirm":
                self._json(
                    self.controller.confirm_local_taskpack(payload.get("task_path", ""))
                )
                return
            if parsed.path == "/api/recordings/compile":
                self._json(
                    self.controller.compile_recording(payload.get("trace_path", ""))
                )
                return
            if parsed.path == "/api/open-local":
                self._json(self.controller.open_local(payload.get("path", "")))
                return
            if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/stop"):
                job_id = parsed.path.removeprefix("/api/jobs/").removesuffix("/stop")
                self._json(self.controller.stop_job(job_id))
                return
            self._error(HTTPStatus.NOT_FOUND, "Not found")
        except KeyError as error:
            self._error(HTTPStatus.NOT_FOUND, str(error))
        except (FileNotFoundError, TypeError, ValueError) as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
        except RuntimeError as error:
            self._error(HTTPStatus.CONFLICT, str(error))

    def _read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as error:
            raise ValueError("Invalid Content-Length") from error
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("Request body must be between 1 and 16384 bytes")
        try:
            payload = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as error:
            raise ValueError("Request body must be valid JSON") from error
        if not isinstance(payload, dict):
            raise TypeError("Request JSON must be an object")
        return payload

    def _json(self, payload: object, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._bytes(data, content_type="application/json; charset=utf-8", status=status)

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json({"error": message}, status=status)

    def _bytes(
        self,
        data: bytes,
        *,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        return


def create_web_server(
    project_root: Path,
    *,
    port: int = 8765,
    controller: WebConsoleController | None = None,
) -> ThreadingHTTPServer:
    if not 1 <= port <= 65_535 and port != 0:
        raise ValueError("Web console port must be between 1 and 65535")
    active_controller = controller or WebConsoleController(project_root)
    asset_root = Path(__file__).with_name("web")
    class BoundWebConsoleHandler(WebConsoleHandler):
        pass

    BoundWebConsoleHandler.controller = active_controller
    BoundWebConsoleHandler.asset_root = asset_root

    return ThreadingHTTPServer(("127.0.0.1", port), BoundWebConsoleHandler)


def serve_web_console(
    *,
    project_root: Path | None = None,
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    server = create_web_server(project_root or Path.cwd(), port=port)
    actual_port = server.server_address[1]
    url = f"http://127.0.0.1:{actual_port}/"
    print(f"Trace2Task web console: {url}")
    print("This console is local-only. Press Ctrl+C here to stop the server.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
