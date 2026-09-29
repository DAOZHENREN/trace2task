"""Bounded D-model desktop loop. Frozen inference stays in its dedicated process."""
from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from trace2task.desktop_runner import DesktopSession
from trace2task.execution_core import ExecutionCore
from trace2task.local_agent_loop import run_local_agent_loop
from trace2task.local_observation import Win32DesktopObservation
from trace2task.trained_model_client import predict_local
from trace2task.win32_execution import Win32ExecutionBackend
from trace2task.windows_capture import GdiWindowCapture
from trace2task.windows_control import Win32Backend, WindowsMotorExecutor, physical_dpi_context
from trace2task.windows_runner import EmergencyStopRequested


def interruptible_prediction(predictor, stop, instruction, *, on_cancel=None, on_progress=None,
                             cancel_wait_seconds=30, **kwargs):
    """Stopping discards the eventual response, never executes a late plan."""
    mailbox = queue.Queue(maxsize=1)

    def work():
        try:
            mailbox.put((predictor(instruction, **kwargs), None))
        except Exception as error:  # noqa: BLE001 - relay inference failures to the run log
            mailbox.put((None, error))

    threading.Thread(target=work, daemon=True).start()
    began = time.perf_counter()
    reported = began
    while True:
        try:
            stop.raise_if_requested()
        except EmergencyStopRequested as stopped:
            if on_cancel is not None:
                cancellation_started = time.perf_counter()
                try:
                    stopped.cancel_receipt = on_cancel()
                except Exception as error:  # noqa: BLE001 - cancellation receipt is best-effort
                    stopped.cancel_error = str(error)
                try:
                    value, error = mailbox.get(timeout=cancel_wait_seconds)
                    stopped.model_output = value
                    if error:
                        stopped.model_error = str(error)
                except queue.Empty:
                    stopped.cancel_pending = True
                stopped.cancel_wait_ms = round((time.perf_counter()-cancellation_started)*1000,2)
            raise
        try:
            value, error = mailbox.get(timeout=.1)
        except queue.Empty:
            now = time.perf_counter()
            if on_progress and now - reported >= 5:
                on_progress(now - began)
                reported = now
            continue
        try:
            stop.raise_if_requested()
        except EmergencyStopRequested as stopped:
            # Response arrived concurrently with stop: preserve it, but never dispatch it.
            stopped.model_output = value
            if error:
                stopped.model_error = str(error)
            raise
        if error:
            raise error
        return value


def run_trained_desktop(*, instruction, output_root, emergency_stop, status_callback,
                        approve, continuous=False, max_actions=40, backend=None,
                        capture=None, size=None, predictor=predict_local, model="D-5970", executor_backend="win32", cua_target=None,
                        experience_context=None, prompt_profile=None, on_model_round=None):
    if model == "D-5970" and experience_context is not None:
        raise ValueError("D-5970 冻结输入结构不支持经验指导")
    if executor_backend == "cua":
        from trace2task.cua_runner import run_cua_local
        return run_cua_local(instruction=instruction, output_root=output_root,
                             emergency_stop=emergency_stop, status_callback=status_callback, model=model,
                             cua_target=cua_target, experience_context=experience_context,
                             prompt_profile=prompt_profile, on_model_round=on_model_round)
    if executor_backend != "win32":
        raise ValueError("Unknown executor backend")
    if model != "D-5970":
        from functools import partial

        from trace2task.local_gui_client import predict_gui
        from trace2task.local_gui_protocol import MODELS
        if model not in MODELS:
            raise ValueError("Unknown local GUI model")
        predictor = partial(predict_gui, model=model)
    suffix = "trained-d" if model == "D-5970" else model
    root = Path(output_root) / (datetime.now(UTC).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8] + "-" + suffix)
    root.mkdir(parents=True)
    trace = root / "trace.jsonl"
    result = {"mode": "trained_d", "model": model, "executor_backend": "win32",
              "actions": 0, "task_complete": False, "verified": False,
              "stop_reason": "action_limit", "trace_path": str(trace), "history": [],
              "experience_task_id": (experience_context or {}).get("task_id")}

    def record(kind, **data):
        with trace.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": kind, "time": time.time(), **data}, ensure_ascii=False) + "\n")
        if kind == "execution_result" and data.get("status") in ("interrupted_or_unknown", "foreground_unavailable"):
            audit.execution(data)

    from trace2task.local_run_audit import LocalRunAudit
    audit = LocalRunAudit(root, result, record, status_callback, on_round=on_model_round)

    emergency_stop.start()
    started = time.perf_counter()
    try:
        backend = backend or Win32Backend()
        capture = capture or GdiWindowCapture(desktop=True)
        if size is None:
            def size():
                with physical_dpi_context(capture.user32):
                    return capture.user32.GetSystemMetrics(0), capture.user32.GetSystemMetrics(1)
        session = DesktopSession(backend, size)
        motor = WindowsMotorExecutor(session, sleeper=emergency_stop.sleep)
        execution_backend = Win32ExecutionBackend(motor, foreground_handle=backend.foreground_handle,
                                                   screen_size=size)
        core = ExecutionCore(execution_backend, emergency_stop, record, max_actions=max_actions)
        status_callback(f"{model} 常驻服务 · 3 秒后截图，请切换到目标窗口。F9 或程序停止。")
        emergency_stop.sleep(3)
        run_local_agent_loop(
            observer=Win32DesktopObservation(session, capture, root), initial_target=None,
            core=core, audit=audit, predictor=predictor, instruction=instruction,
            model=model, root=root, result=result, record=record,
            stop=emergency_stop, status_callback=status_callback,
            max_actions=max_actions, approve=approve, continuous=continuous,
            experience_context=experience_context,
            prompt_profile=prompt_profile,
        )
    except EmergencyStopRequested:
        result["stop_reason"] = "emergency_stop"
    except Exception as error:  # noqa: BLE001 - preserve partial execution and never retry it
        result.update(stop_reason="error", error=f"{type(error).__name__}: {error}")
        record("error", error=result["error"], note="No automatic retry after uncertain input")
    finally:
        emergency_stop.close()
        result["elapsed_seconds"] = time.perf_counter() - started
        audit.finish(result['elapsed_seconds'])
        record("result", **result)
        (root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
