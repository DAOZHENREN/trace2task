"""Bounded D-model desktop loop. Frozen inference stays in its dedicated process."""
from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pygame

from trace2task.actions import ActionCall
from trace2task.desktop_runner import DesktopSession, NoProgressGuard
from trace2task.trained_model_client import predict_local
from trace2task.windows_capture import GdiWindowCapture
from trace2task.windows_control import Win32Backend, WindowsMotorExecutor, physical_dpi_context
from trace2task.windows_runner import EmergencyStopRequested

SUPPORTED = {"click", "double_click", "hold_mouse", "drag", "type_text",
             "press_key", "hold_key", "hotkey", "wait"}


def parse_actions(value):
    """Validate the whole group before sending any input; never reinterpret unknown skills."""
    if not isinstance(value, dict) or set(value) != {"actions"}:
        raise ValueError("Invalid D action group")
    raw = value["actions"]
    if not isinstance(raw, list) or not 1 <= len(raw) <= 8:
        raise ValueError("D action group must contain 1-8 actions")
    done = raw[-1] == {"done": True}
    calls = []
    for action in raw[:-1] if done else raw:
        if not isinstance(action, dict) or action.get("skill") not in SUPPORTED:
            raise ValueError(f"Unsupported D executor action: {action!r}")
        calls.append(ActionCall.from_payload(action))
    return raw[:-1] if done else raw, calls, done


def frame_signature(frame):
    return pygame.image.tobytes(pygame.transform.smoothscale(frame, (160, 100)), "RGB")


def same_frame(a, b):
    return len(a) == len(b) and sum(abs(x-y) > 24 for x, y in zip(a, b)) / len(a) < .01


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
                        capture=None, size=None, predictor=predict_local, model="D-5970", executor_backend="win32", cua_target=None):
    if executor_backend == "cua":
        from trace2task.cua_runner import run_cua_local
        return run_cua_local(instruction=instruction, output_root=output_root,
                             emergency_stop=emergency_stop, status_callback=status_callback, model=model, cua_target=cua_target)
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
    result = {"mode": "trained_d", "model": model, "actions": 0, "task_complete": False, "verified": False,
              "stop_reason": "action_limit", "trace_path": str(trace), "history": []}

    def record(kind, **data):
        with trace.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": kind, "time": time.time(), **data}, ensure_ascii=False) + "\n")

    from trace2task.local_run_audit import LocalRunAudit
    audit = LocalRunAudit(root, result, record, status_callback)

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
        guard = NoProgressGuard()
        status_callback(f"{model} 常驻服务 · 3 秒后截图，请切换到目标窗口。F9 或程序停止。")
        emergency_stop.sleep(3)
        for step in range(40):
            emergency_stop.raise_if_requested()
            if result["actions"] >= max_actions:
                break
            window = session.observe()
            capture_started = time.perf_counter()
            frame = capture.capture(window)
            audit.elapsed('capture_ms', capture_started)
            guard.observe(frame, window.handle)
            before = frame_signature(frame)
            image_path = root / f"{step:04d}.png"
            pygame.image.save(frame, image_path)
            output = audit.predict(predictor, emergency_stop, model, instruction,
                                   image_path, result['history'][-4:], step)
            emergency_stop.raise_if_requested()
            if output.get("status") != "predicted":
                raise ValueError(output.get("error", "D prediction failed"))
            raw, calls, done = parse_actions(output["prediction"])
            status_callback(f"[{model} plan {step+1}] {output.get('elapsed_seconds', 0):.2f} 秒 · {len(calls)} 个动作。")
            if not calls and done:
                result["stop_reason"] = "model_done_unverified"
                break
            if result["actions"] + len(calls) > max_actions:
                break
            if not continuous:
                approve({"actions": raw, "annotated_image": output.get("annotated_image"),
                         "inference_seconds": output.get("elapsed_seconds"),
                         "prediction_log": output.get("output_directory"), "step": step})
                status_callback("已确认；3 秒内切回原目标窗口。若画面变化，将丢弃本批重新预测。")
                emergency_stop.sleep(3)
            # Confirmation in the browser changes focus; never apply that stale plan there.
            if backend.foreground_handle() != window.handle or size() != frame.get_size():
                record("discard", reason="focus_or_resolution_changed")
                status_callback("焦点或分辨率已变化，丢弃本批并重新截图。")
                continue
            if not same_frame(before, frame_signature(capture.capture(window))):
                record("discard", reason="screen_changed")
                status_callback("画面已变化，丢弃本批并重新预测。")
                continue
            executed = []
            try:
                for source, call in zip(raw, calls):
                    emergency_stop.raise_if_requested()
                    if backend.foreground_handle() != window.handle or size() != frame.get_size():
                        record("discard_remaining", reason="focus_or_resolution_changed")
                        break
                    if guard.repeated(call):
                        result["stop_reason"] = "no_progress"
                        status_callback("无进展保护：重复点击已阻止，任务停止。")
                        return result
                    record("dispatch", action=source)
                    action_started = time.perf_counter()
                    motor_result = motor.execute(call)
                    audit.elapsed('explicit_wait_ms' if call.skill == 'wait' else 'action_ms', action_started)
                    executed.append(source)
                    result["actions"] += 1
                    guard.delivered(call)
                    record("delivered", action=source, elapsed_ms=motor_result.elapsed_ms)
                    status_callback(f"[{model} action {result['actions']}/{max_actions}] {call.skill} 已送达（不等于效果成功）。")
                    if not same_frame(before, frame_signature(capture.capture(window))):
                        record("discard_remaining", reason="screen_changed_after_action")
                        break
            finally:
                if executed:
                    result["history"].append({"step_index": step,
                                               "action": {"actions": executed}, "executed": True})
                    record("executed_history", history=result["history"][-4:])
            # Always reobserve after a group, including a group ending in done.
            emergency_stop.sleep(.3)
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
