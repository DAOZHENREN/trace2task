"""Opt-in local-model loop bound to the window selected at start."""
import json
import time
import uuid
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

from trace2task.cua_backend import CuaBackend, action_request, valid_window_bounds
from trace2task.cua_scope import CuaScope
from trace2task.local_gui_protocol import cua_action
from trace2task.local_run_audit import LocalRunAudit
from trace2task.trained_model_client import predict_local
from trace2task.trained_model_runner import parse_actions
from trace2task.windows_runner import EmergencyStopRequested


def run_cua_local(*, instruction, output_root, emergency_stop, status_callback, model, cua_target=None):
    root = Path(output_root)/(datetime.now(UTC).strftime('%Y%m%d-%H%M%S-')+uuid.uuid4().hex[:8]+'-cua')
    root.mkdir(parents=True)
    result = {'mode': 'trained_d', 'model': model, 'executor_backend': 'cua', 'actions': 0,
                  'task_complete': False, 'verified': False, 'history': [], 'stop_reason': 'action_limit',
                  'trace_path': str(root/'trace.jsonl')}
    def record(kind, **value):
        with (root/'trace.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(type=kind,time=time.time(),**value),ensure_ascii=False)+'\n')
    driver = CuaBackend(root, record)
    audit = LocalRunAudit(root, result, record, status_callback)
    predictor = predict_local
    if model != 'D-5970':
        from trace2task.local_gui_client import predict_gui
        predictor = partial(predict_gui, model=model)
    started = time.perf_counter()
    emergency_stop.start()
    try:
        scope = CuaScope(driver, cua_target)
        result['cua_scope'] = scope.selection
        record('requested_scope', selection=scope.selection)
        driver.start()
        target = scope.start()
        result['window_bindings'] = scope.bindings
        record('authorized_scope', selection=scope.selection, bindings=scope.bindings)
        record('target', window=target)
        audit.elapsed('startup_ms', started)
        status_callback(f"已绑定初始窗口；仅允许本次选中的 {len(scope.selection['targets'])} 个目标，不提供全机应用目录。")
        previous = None
        repeats = 0
        for step in range(40):
            emergency_stop.raise_if_requested()
            # Revalidate only the initially bound identity; never expand its root at runtime.
            target = scope.switch(target)
            capture_started = time.perf_counter()
            state, path = driver.observe(target, step)
            capture_ms = audit.elapsed('capture_ms', capture_started)
            record('capture', step_index=step, screenshot=str(path), elapsed_ms=capture_ms)
            context = None
            if model == 'qwen3-vl-2b':
                context = scope.context(target)
            output = audit.predict(predictor, emergency_stop, model, instruction,
                                   path, result['history'][-4:], step, context)
            if output.get('status') != 'predicted':
                raise RuntimeError(output.get('error','模型未返回有效计划'))
            if context is not None:
                value = output['prediction']
                if not isinstance(value, dict) or set(value) != {'actions'} or not isinstance(value['actions'], list) or len(value['actions']) != 1:
                    raise ValueError('Cua Qwen requires exactly one action')
                done = value['actions'] == [{'done': True}]
                raw = [] if done else value['actions']
                calls = [cua_action(a) for a in raw]
            else:
                raw, calls, done = parse_actions(output['prediction'])
            if not calls and done:
                result['stop_reason']='model_done_unverified'
                break
            # Validate the entire group; execute only its first action, then reobserve.
            for call in calls:
                if call.skill == 'launch_app':
                    scope.require_app(call.args['app_id'])
                elif call.skill == 'switch_window':
                    scope.require_window(call.args)
                elif call.skill != 'wait':
                    action_request(call, state)
            signature = json.dumps(raw[0],sort_keys=True)
            repeats = repeats+1 if previous == signature else 1
            previous = signature
            if repeats > 3:
                result['stop_reason']='no_progress'
                status_callback('Cua 重复动作保护：连续相同动作超过上限，已停止。')
                break
            # Validate the target still exists and has not moved/resized during inference.
            now = driver.window(target)
            if (not valid_window_bounds(now.get('bounds'))
                    or not valid_window_bounds(state.get('window_bounds'))
                    or any(now.get(k) != v for k, v in target.items())
                    or now.get('bounds') != state.get('window_bounds')):
                raise RuntimeError('目标窗口位置/尺寸或状态变化，请重新开始；未执行旧计划')
            emergency_stop.raise_if_requested()
            scope.require_window(target)
            call = calls[0]
            record('dispatch', action=raw[0])
            action_started = time.perf_counter()
            if call.skill == 'launch_app':
                target = scope.launch(call.args['app_id'])
                receipt = {'effect':'confirmed', 'route':'launch_and_bind', 'target':target}
            elif call.skill == 'switch_window':
                target = scope.switch(call.args)
                receipt = {'effect':'confirmed', 'route':'observation_target_switch', 'target':target}
            elif call.skill == 'wait':
                emergency_stop.sleep(call.args['duration_ms']/1000)
                receipt={'effect':'confirmed','route':'local_wait'}
            else:
                tool, payload = action_request(call,state)
                receipt = driver.call(tool,payload)
                if receipt.get('effect') not in {'confirmed','unverifiable'}:
                    raise RuntimeError('Cua 动作效果未知；不重试：'+json.dumps(receipt,ensure_ascii=False))
            result['actions']+=1
            effect = receipt['effect']
            result['history'].append({'step_index':step,'action':{'actions':[raw[0]]},
                                      'executed':True, 'effect':effect})
            elapsed_ms = audit.elapsed('explicit_wait_ms' if call.skill == 'wait' else 'action_ms', action_started)
            record('delivered', action=raw[0],receipt=receipt,elapsed_ms=elapsed_ms)
            if effect == 'unverifiable':
                result['stop_reason'] = 'effect_unverifiable'
                record('uncertain_effect_stop', action=raw[0], receipt=receipt)
                status_callback('Cua 已送达动作，但无法确认效果；为避免重复操作，已停止且不会自动重试。')
                break
            status_callback(f"[Cua action {result['actions']}] {call.skill} · {elapsed_ms:.0f} ms · {effect}；下一轮重新观察。")
            if len(calls)>1:
                record('discard_remaining',reason='experimental_single_action_reobserve',count=len(calls)-1)
            emergency_stop.sleep(.3)
    except EmergencyStopRequested:
        result['stop_reason']='emergency_stop'
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
