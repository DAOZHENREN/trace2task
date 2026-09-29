"""Opt-in local-model loop bound to the window selected at start."""
import json
import time
import uuid
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

from trace2task.cua_backend import CuaBackend
from trace2task.cua_execution import CuaExecutionBackend
from trace2task.cua_scope import CuaScope
from trace2task.execution_core import ExecutionCore
from trace2task.execution_protocol import ForegroundUnavailable
from trace2task.local_agent_loop import run_local_agent_loop
from trace2task.local_observation import CuaWindowObservation
from trace2task.local_run_audit import LocalRunAudit
from trace2task.trained_model_client import predict_local
from trace2task.windows_runner import EmergencyStopRequested


def run_cua_local(*, instruction, output_root, emergency_stop, status_callback, model, cua_target=None,
                  experience_context=None, prompt_profile=None, on_model_round=None):
    if model == 'D-5970' and experience_context is not None:
        raise ValueError('D-5970 冻结输入结构不支持经验指导')
    root = Path(output_root)/(datetime.now(UTC).strftime('%Y%m%d-%H%M%S-')+uuid.uuid4().hex[:8]+'-cua')
    root.mkdir(parents=True)
    result = {'mode': 'trained_d', 'model': model, 'executor_backend': 'cua', 'actions': 0,
                  'foreground_fallbacks': 0,
                  'task_complete': False, 'verified': False, 'history': [], 'stop_reason': 'action_limit',
                  'trace_path': str(root/'trace.jsonl'),
                  'experience_task_id': (experience_context or {}).get('task_id')}
    def record(kind, **value):
        with (root/'trace.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(type=kind,time=time.time(),**value),ensure_ascii=False)+'\n')
        if kind == 'foreground_retry':
            # Count attempts, including a foreground call that later times out or fails.
            result['foreground_fallbacks'] += 1
        if kind == 'execution_result' and value.get('status') in ('interrupted_or_unknown', 'foreground_unavailable'):
            audit.execution(value)
    driver = CuaBackend(root, record)
    audit = LocalRunAudit(root, result, record, status_callback, on_round=on_model_round)
    predictor = predict_local
    if model != 'D-5970':
        from trace2task.local_gui_client import predict_gui
        predictor = partial(predict_gui, model=model)
    started = time.perf_counter()
    emergency_stop.start()
    try:
        from trace2task.cua_desktop import (
            DESKTOP_TARGET,
            CuaDesktopExecutionBackend,
            CuaDesktopObservation,
        )
        desktop = cua_target == DESKTOP_TARGET
        scope = None if desktop else CuaScope(driver, cua_target)
        result['cua_scope'] = dict(DESKTOP_TARGET) if desktop else scope.selection
        record('requested_scope', selection=result['cua_scope'])
        driver.start()
        target = dict(DESKTOP_TARGET) if desktop else scope.start()
        result['window_bindings'] = [] if desktop else scope.bindings
        record('authorized_scope', selection=result['cua_scope'], bindings=result['window_bindings'])
        record('target', window=target)
        audit.elapsed('startup_ms', started)
        if desktop:
            status_callback("Cua 全桌面 3 秒后截图；使用系统键鼠，F9 停止。")
            emergency_stop.sleep(3)
        else:
            status_callback(f"已绑定初始窗口；仅允许本次选中的 {len(scope.selection['targets'])} 个目标，不提供全机应用目录。")
        backend = CuaDesktopExecutionBackend(driver) if desktop else CuaExecutionBackend(driver, scope)
        core = ExecutionCore(backend, emergency_stop, record)
        run_local_agent_loop(
            observer=CuaDesktopObservation(driver) if desktop else CuaWindowObservation(scope, driver), initial_target=target,
            core=core, audit=audit, predictor=predictor, instruction=instruction,
            model=model, root=root, result=result, record=record,
            stop=emergency_stop, status_callback=status_callback,
            experience_context=experience_context,
            prompt_profile=prompt_profile,
        )
    except EmergencyStopRequested:
        result['stop_reason']='emergency_stop'
    except ForegroundUnavailable as error:
        result.update(stop_reason='foreground_unavailable',error=f'{type(error).__name__}: {error}')
        record('error',error=result['error'])
        status_callback(f'Cua 未能安全切换到目标前台，已停止且未发送前台输入：{error}')
    except Exception as error:  # noqa: BLE001 - run must record every external-driver failure
        result.update(stop_reason='error',error=f'{type(error).__name__}: {error}')
        record('error',error=result['error'])
        status_callback(f"Cua 任务已停止：{result['error']}")
    finally:
        try:
            driver.close()
        except Exception as error:  # noqa: BLE001 - preserve the primary run result on cleanup failure
            result['cleanup_error'] = str(error)
            record('cleanup_error', error=str(error))
        finally:
            emergency_stop.close()
        result['elapsed_seconds']=time.perf_counter()-started
        audit.finish(result['elapsed_seconds'])
        record('result',**result)
        (root/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result
