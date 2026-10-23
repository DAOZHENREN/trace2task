"""Codex/Model API execution through the same loop and core as local models."""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from trace2task.chat_model_adapter import ChatModelAdapter
from trace2task.chat_run_audit import ChatRunAudit
from trace2task.execution_budget import BudgetStop, TimeBudgetExceeded
from trace2task.execution_core import ExecutionCore
from trace2task.io_audit import IOAudit
from trace2task.local_agent_loop import run_local_agent_loop
from trace2task.windows_runner import EmergencyStopRequested


def run_chat_agent(*, instruction, model, reasoning_effort, output_root,
                   emergency_stop, status_callback, executor_backend="win32",
                   cua_target=None, api_config=None, task_path=None, use_experience=False,
                   max_actions=80, max_seconds=None, on_model_round=None, session=None, prompt_guidance="",
                   backend=None, capture=None, size=None, cua_driver=None, browser_driver=None,
                   android_config=None, android_driver=None, model_adapter=None,
                   memory_mode=None, memory_root=None, on_memory_selection=None, interventions=None):
    """Execute a primary-desktop or explicitly selected-window chat task.

    This is the shared-loop route. Legacy single-window taskpack semantics and
    LangGraph checkpoint recovery are not silently replaced by this function.
    """
    if executor_backend not in {"win32", "cua", "browser", "android"}:
        raise ValueError("Unknown execution backend")
    if executor_backend == "cua" and cua_target is None:
        raise ValueError("Cua execution requires an explicit selected-window scope")
    if executor_backend == 'browser' and cua_target is not None:
        raise ValueError('Browser execution cannot authorize desktop/window targets')
    if executor_backend == 'android' and (cua_target is not None or not android_config):
        raise ValueError('Android requires explicit device configuration and no desktop target')
    if interventions is not None and (executor_backend != 'android' or model_adapter is not None):
        raise ValueError('运行中干预目前仅支持 Android 通用视觉代理')
    memory_mode = memory_mode if memory_mode is not None else ('manual' if use_experience else 'off')
    if memory_mode not in {'off', 'manual', 'auto'} or (memory_mode == 'manual') != use_experience:
        raise ValueError('Inconsistent memory mode and explicit experience selection')
    if memory_mode == 'auto' and (memory_root is None or task_path is not None or model_adapter is not None):
        raise ValueError('Automatic memory requires a library root and the generic chat adapter, without a manual path')
    if model_adapter is not None and (executor_backend != 'android' or session is None
                                      or model_adapter.session is not session or use_experience):
        raise ValueError('Custom Android adapter requires its explicit session and no demonstration bundle')
    if max_seconds is not None:
        if type(max_seconds) is not int or max_seconds <= 0:
            raise ValueError('max_seconds must be a positive integer')
        emergency_stop = BudgetStop(emergency_stop, max_seconds)
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
              "memory_mode": memory_mode,
              "experience_task_path": str(task_path) if use_experience else None,
              "actions": 0, "history": [], "task_complete": False, "verified": False,
              "stop_reason": "action_limit", "trace_path": str(trace),
              "instruction": instruction,
              "budget": {"max_actions": max_actions, "max_seconds": max_seconds}}
    if executor_backend == 'browser':
        result['mode'] = 'browser_agent' if use_experience else 'browser_baseline'
    if executor_backend == 'android':
        result['mode'] = 'android_agent' if use_experience else 'android_baseline'

    record_lock = threading.RLock()

    def record(kind, **data):
        with record_lock, trace.open("a", encoding="utf-8") as stream:
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
            thread_config = {"model_auto_compact_token_limit": 2_000_000_000}
            if executor_backend in {'browser', 'android'} or memory_mode == 'auto':
                thread_config.update(web_search='disabled', project_doc_max_bytes=0,
                                     include_environment_context=False,
                                     skills={'include_instructions': False},
                                     features={'shell_tool': False, 'unified_exec': False,
                                               'view_image': False, 'apps': False,
                                               'multi_agent': False, 'code_mode_host': False})
            session = CodexAppServerSession(resolve_codex_binary(), model=model,
                                             reasoning_effort=reasoning_effort,
                                             cwd=root, timeout_seconds=300,
                                             thread_config=thread_config)
    session.audit = raw_audit
    if provider == "api":
        session.system_guidance = prompt_guidance
    from trace2task.cua_desktop import DESKTOP_TARGET
    cua_desktop = executor_backend == "cua" and cua_target == DESKTOP_TARGET
    adapter = model_adapter or ChatModelAdapter(session, selected_windows=executor_backend == "cua" and not cua_desktop,
                               provider=provider, prompt_guidance=prompt_guidance,
                               evidence_directory=root / 'experience-images',
                               browser=executor_backend == 'browser',
                               android=executor_backend == 'android')
    if model_adapter is not None:
        result['model_adapter'] = model_adapter.provenance
        result['instruction'] = instruction
    started = time.perf_counter()
    driver = None
    emergency_stop.start()
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
            observation = AndroidObservation(driver)
            execution_backend = AndroidExecutionBackend(driver, interventions=interventions)
            status_callback('Android 已绑定设备和前台应用；只用截图和单指输入，不占用 Windows 键鼠。')
        elif executor_backend == 'browser':
            from trace2task.browser_execution import (
                BROWSER_TARGET,
                BrowserDriver,
                BrowserExecutionBackend,
                BrowserObservation,
            )
            driver = browser_driver or BrowserDriver(root, record, emergency_stop)
            driver.start()
            target = dict(BROWSER_TARGET)
            result['browser_scope'] = target
            observation = BrowserObservation(driver)
            execution_backend = BrowserExecutionBackend(driver)
            status_callback('Ancient Beast 独立无头浏览器已启动；不占用系统键鼠，不回退前台。')
        elif cua_desktop:
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
                             max_actions=max_actions, interventions=interventions)
        record("start", instruction=instruction, provider=provider, model=model,
               executor_backend=executor_backend, use_experience=use_experience,
               selected_scope=result.get('android_scope', result.get("browser_scope", result.get("cua_scope"))))
        if memory_mode == 'auto':
            from trace2task.agent_memory import MemoryCatalog, select_memory

            scope = ('selected_windows' if executor_backend == 'cua' and not cua_desktop else
                     executor_backend if executor_backend in {'android', 'browser'} else 'desktop')
            emergency_stop.raise_if_requested()
            catalog = MemoryCatalog(memory_root, scope, (android_config or {}).get('package', ''))
            screenshot, context = None, None
            if catalog.entries:
                captured = time.perf_counter()
                target, state, screenshot = observation.observe(target, len(result['model_io']))
                context = observation.context(target, state, execution_backend)
                record('memory_capture', screenshot=str(screenshot), elapsed_ms=audit.elapsed('capture_ms', captured))
            experience = select_memory(catalog=catalog, session=session, audit=audit, stop=emergency_stop,
                                       model=model, task=instruction, screenshot=screenshot, target_context=context,
                                       provider=provider, status_callback=status_callback,
                                       on_selection=on_memory_selection)
            # Selection is archived separately. The first motor prompt receives only
            # the chosen frozen method and a fresh observation, not stale search images.
            if result['model_io']:
                session.reset_thread()
            result.update(use_experience=experience is not None,
                          experience_task_path=result['memory_selection'].get('experience_path'))
            result['mode'] = ('android' if executor_backend == 'android' else
                              'browser' if executor_backend == 'browser' else 'desktop') + (
                                  '_agent' if experience is not None else '_baseline')
        if interventions is not None:
            interventions.activate(record)
        run_local_agent_loop(
            observer=observation, initial_target=target, core=core, audit=audit,
            predictor=adapter.predict, instruction=instruction, model=model,
            root=root, result=result, record=record, stop=emergency_stop,
            status_callback=status_callback, max_actions=max_actions,
            experience_context=experience,
            interventions=interventions,
        )
        if executor_backend == 'android':
            _, _, final_path = observation.observe(target, len(result['model_io']))
            result['final_screenshot'] = str(final_path)
            record('final_capture', screenshot=str(final_path))
    except TimeBudgetExceeded:
        result.update(stop_reason='time_limit', task_complete=False, verified=False)
        status_callback('时间预算耗尽，任务未完成；已停止后续输入，迟到的模型回答不会执行。')
    except EmergencyStopRequested:
        result["stop_reason"] = "emergency_stop"
    except Exception as error:  # noqa: BLE001 - persist uncertain delivery; never retry it
        result.update(stop_reason="error", error=f"{type(error).__name__}: {error}")
        record("error", error=result["error"])
    finally:
        if interventions is not None:
            interventions.close(result['stop_reason'])
            result['interventions'] = interventions.snapshot()
            result['assisted_by_user'] = result['interventions']['assisted_by_user']
        if result['stop_reason'] == 'action_limit':
            status_callback(f'动作预算耗尽（{max_actions}），任务未完成；未自动续跑。')
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
