"""Codex/Model API execution through the same loop and core as local models."""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from trace2task.chat_model_adapter import ChatModelAdapter
from trace2task.chat_run_audit import ChatRunAudit
from trace2task.execution_core import ExecutionCore
from trace2task.io_audit import IOAudit
from trace2task.local_agent_loop import run_local_agent_loop
from trace2task.windows_runner import EmergencyStopRequested


def run_chat_agent(*, instruction, model, reasoning_effort, output_root,
                   emergency_stop, status_callback, executor_backend="win32",
                   cua_target=None, api_config=None, task_path=None, use_experience=False,
                   max_actions=80, on_model_round=None, session=None, prompt_guidance="",
                   backend=None, capture=None, size=None, cua_driver=None):
    """Execute a primary-desktop or explicitly selected-window chat task.

    This is the shared-loop route. Legacy single-window taskpack semantics and
    LangGraph checkpoint recovery are not silently replaced by this function.
    """
    if executor_backend not in {"win32", "cua"}:
        raise ValueError("Unknown execution backend")
    if executor_backend == "cua" and cua_target is None:
        raise ValueError("Cua execution requires an explicit selected-window scope")
    from trace2task.desktop_runner import build_desktop_experience_context

    experience = (build_desktop_experience_context(task_path, execute=True)
                  if use_experience else None)
    provider = "api" if api_config is not None else "codex"
    root = Path(output_root) / (datetime.now(UTC).strftime("%Y%m%d-%H%M%S-")
                                + uuid.uuid4().hex[:8] + "-chat-agent")
    root.mkdir(parents=True)
    trace = root / "trace.jsonl"
    result = {"mode": "desktop_agent" if use_experience else "desktop_baseline",
              "provider": provider, "model": model, "reasoning_effort": reasoning_effort,
              "executor_backend": executor_backend, "use_experience": use_experience,
              "experience_task_path": str(task_path) if use_experience else None,
              "actions": 0, "history": [], "task_complete": False, "verified": False,
              "stop_reason": "action_limit", "trace_path": str(trace)}

    def record(kind, **data):
        with trace.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": kind, "time": time.time(), **data},
                                    ensure_ascii=False) + "\n")
        if kind == "execution_result" and data.get("status") in {
                "interrupted_or_unknown", "foreground_unavailable"}:
            audit.execution(data)

    audit = ChatRunAudit(root, result, record, status_callback, on_round=on_model_round)
    raw_audit = IOAudit(root)
    owned_session = session is None
    if session is None:
        if api_config is not None:
            from trace2task.model_api import ModelAPISession
            session = ModelAPISession(api_config, model=model,
                                      reasoning_effort=reasoning_effort,
                                      stop_check=emergency_stop.raise_if_requested)
        else:
            from trace2task.codex_agent import resolve_codex_binary
            from trace2task.codex_app_server import CodexAppServerSession
            session = CodexAppServerSession(resolve_codex_binary(), model=model,
                                             reasoning_effort=reasoning_effort,
                                             cwd=root, timeout_seconds=300)
    session.audit = raw_audit
    if provider == "api":
        session.system_guidance = prompt_guidance
    from trace2task.cua_desktop import DESKTOP_TARGET
    cua_desktop = executor_backend == "cua" and cua_target == DESKTOP_TARGET
    adapter = ChatModelAdapter(session, selected_windows=executor_backend == "cua" and not cua_desktop,
                               provider=provider, prompt_guidance=prompt_guidance)
    started = time.perf_counter()
    driver = None
    emergency_stop.start()
    try:
        if cua_desktop:
            from trace2task.cua_backend import CuaBackend
            from trace2task.cua_desktop import CuaDesktopExecutionBackend, CuaDesktopObservation
            driver = cua_driver or CuaBackend(root, record)
            driver.start()
            target = dict(DESKTOP_TARGET)
            result["cua_scope"] = target
            observation = CuaDesktopObservation(driver)
            execution_backend = CuaDesktopExecutionBackend(driver)
            status_callback("Cua 全桌面 3 秒后截图；使用系统键鼠，F9 停止。")
            emergency_stop.sleep(3)
        elif executor_backend == "cua":
            from trace2task.cua_backend import CuaBackend
            from trace2task.cua_execution import CuaExecutionBackend
            from trace2task.cua_scope import CuaScope
            from trace2task.local_observation import CuaWindowObservation

            driver = cua_driver or CuaBackend(root, record)
            scope = CuaScope(driver, cua_target)
            result["cua_scope"] = scope.selection
            driver.start()
            target = scope.start()
            result["window_bindings"] = scope.bindings
            observation = CuaWindowObservation(scope, driver)
            execution_backend = CuaExecutionBackend(driver, scope)
            status_callback("Cua 已绑定本次授权窗口；模型按当前窗口截图规划。")
        else:
            from trace2task.desktop_runner import DesktopSession
            from trace2task.local_observation import Win32DesktopObservation
            from trace2task.win32_execution import Win32ExecutionBackend
            from trace2task.windows_capture import GdiWindowCapture
            from trace2task.windows_control import (
                Win32Backend,
                WindowsMotorExecutor,
                physical_dpi_context,
            )

            backend = backend or Win32Backend()
            capture = capture or GdiWindowCapture(desktop=True)
            if size is None:
                def size():
                    with physical_dpi_context(capture.user32):
                        return (capture.user32.GetSystemMetrics(0),
                                capture.user32.GetSystemMetrics(1))
            desktop = DesktopSession(backend, size)
            motor = WindowsMotorExecutor(desktop, sleeper=emergency_stop.sleep)
            execution_backend = Win32ExecutionBackend(
                motor, foreground_handle=backend.foreground_handle, screen_size=size)
            observation = Win32DesktopObservation(desktop, capture, root)
            target = None
            status_callback("主屏桌面 3 秒后截图；运行期间请勿切换前台窗口。F9 可停止。")
            emergency_stop.sleep(3)
        core = ExecutionCore(execution_backend, emergency_stop, record,
                             max_actions=max_actions)
        record("start", instruction=instruction, provider=provider, model=model,
               executor_backend=executor_backend, use_experience=use_experience,
               selected_scope=result.get("cua_scope"))
        run_local_agent_loop(
            observer=observation, initial_target=target, core=core, audit=audit,
            predictor=adapter.predict, instruction=instruction, model=model,
            root=root, result=result, record=record, stop=emergency_stop,
            status_callback=status_callback, max_actions=max_actions,
            experience_context=experience,
        )
    except EmergencyStopRequested:
        result["stop_reason"] = "emergency_stop"
    except Exception as error:  # noqa: BLE001 - persist uncertain delivery; never retry it
        result.update(stop_reason="error", error=f"{type(error).__name__}: {error}")
        record("error", error=result["error"])
    finally:
        if driver is not None:
            try:
                driver.close()
            except Exception as error:  # noqa: BLE001 - retain original task outcome
                result["cleanup_error"] = f"{type(error).__name__}: {error}"
        if owned_session:
            session.close()
        emergency_stop.close()
        result["elapsed_seconds"] = time.perf_counter() - started
        audit.finish(result["elapsed_seconds"])
        record("result", **result)
        (root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    return result
