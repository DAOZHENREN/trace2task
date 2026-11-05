"""Bounded local-model loop. Frozen inference stays in its dedicated process."""
from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from trace2task.desktop_runner import DesktopSession
from trace2task.execution_budget import BudgetStop, TimeBudgetExceeded
from trace2task.execution_core import ExecutionCore
from trace2task.local_agent_loop import observe_with_timing, run_local_agent_loop
from trace2task.local_observation import Win32DesktopObservation
from trace2task.trained_model_client import predict_local
from trace2task.win32_execution import Win32ExecutionBackend
from trace2task.windows_capture import GdiWindowCapture
from trace2task.windows_control import Win32Backend, WindowsMotorExecutor, physical_dpi_context
from trace2task.windows_runner import EmergencyStopRequested


def interruptible_prediction(predictor, stop, instruction, *, on_cancel=None, on_progress=None, on_poll=None,
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
            if on_poll is not None:
                on_poll()
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
                        experience_context=None, prompt_profile=None, on_model_round=None, inference_backend=None,
                        android_config=None, android_driver=None, max_seconds=None, application_knowledge=None):
    if model == "D-5970" and experience_context is not None:
        raise ValueError("D-5970 冻结输入结构不支持经验指导")
    if executor_backend == 'android':
        if not android_config or cua_target is not None:
            raise ValueError('Android requires explicit device configuration and no desktop target')
        if max_seconds is not None:
            if type(max_seconds) is not int or max_seconds <= 0:
                raise ValueError('max_seconds must be a positive integer')
            emergency_stop = BudgetStop(emergency_stop, max_seconds)
    elif android_config is not None or android_driver is not None or max_seconds is not None:
        raise ValueError('Android options require the Android execution backend')
    if executor_backend == "cua":
        from trace2task.cua_runner import run_cua_local
        return run_cua_local(instruction=instruction, output_root=output_root,
                             emergency_stop=emergency_stop, status_callback=status_callback, model=model,
                             cua_target=cua_target, experience_context=experience_context,
                             prompt_profile=prompt_profile, on_model_round=on_model_round, inference_backend=inference_backend,
                             application_knowledge=application_knowledge)
    if executor_backend not in {"win32", "android"}:
        raise ValueError("Unknown executor backend")
    if model != "D-5970":
        from functools import partial

        from trace2task.local_gui_client import predict_gui
        from trace2task.local_gui_protocol import MODELS
        if model not in MODELS:
            raise ValueError("Unknown local GUI model")
        predictor = partial(predict_gui, model=model, expected_backend=inference_backend)
    suffix = "trained-d" if model == "D-5970" else model
    root = Path(output_root) / (datetime.now(UTC).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8] + "-" + suffix)
    root.mkdir(parents=True)
    trace = root / "trace.jsonl"
    result = {"mode": "trained_d", "model": model, "executor_backend": executor_backend,
              "actions": 0, "task_complete": False, "verified": False,
              "stop_reason": "action_limit", "trace_path": str(trace), "history": [],
              "experience_task_id": (experience_context or {}).get("task_id")}
    if executor_backend == 'android':
        result.update(instruction=instruction, budget={'max_actions': max_actions, 'max_seconds': max_seconds})

    def record(kind, **data):
        with trace.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": kind, "time": time.time(), **data}, ensure_ascii=False) + "\n")
        if kind == "execution_result" and data.get("status") in ("interrupted_or_unknown", "foreground_unavailable"):
            audit.execution(data)
        if kind.startswith('android_capture_'):
            from trace2task.android_execution import capture_status
            message = capture_status(kind, data)
            if message:
                status_callback(message)

    from trace2task.local_run_audit import LocalRunAudit
    audit = LocalRunAudit(root, result, record, status_callback, on_round=on_model_round)

    emergency_stop.start()
    started = time.perf_counter()
    driver = None
    try:
        if executor_backend == 'android':
            from trace2task.android_execution import (
                AndroidDriver,
                AndroidExecutionBackend,
                AndroidObservation,
            )

            driver = android_driver or AndroidDriver(root, record, emergency_stop, **android_config)
            driver.start()
            target = dict(driver.target)
            result['android_scope'] = target
            observer = AndroidObservation(driver)
            execution_backend = AndroidExecutionBackend(driver)
            record('authorized_scope', selection=target)
            status_callback(f'{model} · Android 已绑定设备和授权应用；使用手机截图和单指输入，F9 可停止。')
        else:
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
            observer, target = Win32DesktopObservation(session, capture, root), None
            status_callback(f"{model} 常驻服务 · 3 秒后截图，请切换到目标窗口。F9 或程序停止。")
            emergency_stop.sleep(3)
        core = ExecutionCore(execution_backend, emergency_stop, record, max_actions=max_actions)
        from trace2task.application_knowledge import knowledge_prompt

        run_local_agent_loop(
            observer=observer, initial_target=target,
            core=core, audit=audit, predictor=predictor, instruction=instruction + knowledge_prompt(application_knowledge),
            model=model, root=root, result=result, record=record,
            stop=emergency_stop, status_callback=status_callback,
            max_actions=max_actions, approve=approve, continuous=continuous,
            experience_context=experience_context,
            prompt_profile=prompt_profile,
        )
        if driver is not None:
            _, _, final_path, capture_ms = observe_with_timing(
                observer, target, len(result['model_io']), audit=audit, record=record,
                status_callback=status_callback, phase='final')
            result['final_screenshot'] = str(final_path)
            record('final_capture', screenshot=str(final_path), elapsed_ms=capture_ms)
    except TimeBudgetExceeded:
        result.update(stop_reason='time_limit', task_complete=False, verified=False)
        status_callback('时间预算耗尽，任务未完成；已停止后续输入。')
    except EmergencyStopRequested:
        result["stop_reason"] = "emergency_stop"
    except Exception as error:  # noqa: BLE001 - preserve partial execution and never retry it
        result.update(stop_reason="error", error=f"{type(error).__name__}: {error}")
        record("error", error=result["error"], note="No automatic retry after uncertain input")
    finally:
        if driver is not None:
            try:
                driver.close()
            except Exception as error:  # noqa: BLE001 - retain the original run outcome
                result['cleanup_error'] = str(error)
                record('cleanup_error', error=str(error))
        emergency_stop.close()
        result["elapsed_seconds"] = time.perf_counter() - started
        audit.finish(result['elapsed_seconds'])
        record("result", **result)
        (root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
