"""One per-run index for local model requests, replies and measured phase timings."""
import base64
import json
import time
import uuid
from pathlib import Path

from trace2task.windows_runner import EmergencyStopRequested


class LocalRunAudit:
    def __init__(self, root, result, record, status, on_round=None):
        self.root, self.result, self.record, self.status = Path(root), result, record, status
        self.on_round = on_round
        result['model_io'] = []
        result['performance'] = {'model_roundtrip_ms': 0, 'planning_ms': 0,
            'model_completion_wait_ms': 0, 'capture_ms': 0, 'action_ms': 0, 'explicit_wait_ms': 0}
        result['audit_path'] = str(self.root/'model-io.json')

    def elapsed(self, field, started):
        elapsed = round((time.perf_counter()-started)*1000, 2)
        perf = self.result['performance']
        perf[field] = round(perf.get(field, 0)+elapsed, 2)
        return elapsed

    def predict(self, predictor, stop, model, task, screenshot, history, step, context=None,
                previous_screenshot=None, execution_feedback=None, experience_context=None,
                prompt_profile=None, cursor_position=None):
        from trace2task.local_gui_client import cancel_gui
        from trace2task.trained_model_runner import interruptible_prediction
        request_id = uuid.uuid4().hex
        folder = self.root/'model-io'/f'{step:04d}'
        folder.mkdir(parents=True)
        kwargs = {'image': base64.b64encode(Path(screenshot).read_bytes()).decode(),
                      'history': history, 'step_index': step}
        if previous_screenshot is not None:
            kwargs['previous_image'] = base64.b64encode(Path(previous_screenshot).read_bytes()).decode()
        entry = {'step_index': step, 'request_id': request_id, 'status': 'pending',
            'purpose': (context or {}).get('purpose', 'plan'),
            'previous_screenshot': str(previous_screenshot) if previous_screenshot else None,
            'experience_context': experience_context,
            'output_directory': str(folder), 'screenshot': str(screenshot),
            'request_path': str(folder/'request.json'), 'response_path': str(folder/'response.json')}
        self.result['model_io'].append(entry)
        self.publish(entry)
        (folder/'request.json').write_text(json.dumps(dict(model=model, task=task,
            execution_context=context, execution_feedback=execution_feedback,
            experience_context=experience_context, prompt_profile=prompt_profile,
            cursor_position=cursor_position, **kwargs),
            ensure_ascii=False, indent=2), encoding='utf-8')
        self.record('model_input', task=task, screenshot=str(screenshot), history=history,
                    previous_screenshot=entry['previous_screenshot'],
                    step_index=step, request_id=request_id, execution_context=context,
                    execution_feedback=execution_feedback, experience_context=experience_context,
                    request_path=entry['request_path'])
        self.status(f'[plan {step+1}] {model} 预测中；日志：{folder}')
        cancel = None
        if model != 'D-5970':
            kwargs.update(request_id=request_id, audit_dir=str(folder), execution_context=context,
                          execution_feedback=execution_feedback,
                          experience_context=experience_context, prompt_profile=prompt_profile,
                          cursor_position=cursor_position)
            def cancel():
                self.status('已停止发送动作，正在取消本次模型生成；等待当前 GPU 计算段结束…')
                reply = cancel_gui(request_id)
                self.record('model_cancel_requested', request_id=request_id, receipt=reply)
                return reply
        started = time.perf_counter()
        output = None
        try:
            output = interruptible_prediction(predictor, stop, task, on_cancel=cancel,
                on_progress=lambda seconds: self.status(f'[plan {step+1}] 模型已等待 {seconds:.0f} 秒，尚未返回；可停止。'),
                **kwargs)
            entry['status'] = output.get('status', 'error')
            return output
        except EmergencyStopRequested as error:
            output = getattr(error, 'model_output', None)
            pending = getattr(error, 'cancel_pending', False) or (model == 'D-5970' and output is None)
            status = 'cancel_pending' if pending else 'cancelled'
            if output and output.get('status') == 'predicted':
                status = 'discarded'
            entry.update(status=status,
                         cancel_receipt=getattr(error, 'cancel_receipt', None))
            self.result['performance']['cancel_wait_ms'] = self.result['performance'].get('cancel_wait_ms',0) + getattr(error,'cancel_wait_ms',0)
            if getattr(error, 'cancel_error', None):
                entry['error'] = error.cancel_error
            if getattr(error, 'model_error', None):
                entry['error'] = error.model_error
            if entry['status'] == 'cancel_pending':
                self.status('动作已停止；服务仍在退出当前计算，尚未确认生成取消。迟到结果不会执行。')
            raise
        except Exception as error:
            entry.update(status='error', error=f'{type(error).__name__}: {error}')
            raise
        finally:
            entry['model_roundtrip_ms'] = self.elapsed('model_roundtrip_ms', started)
            self.result['performance']['planning_ms'] = self.result['performance']['model_roundtrip_ms']
            if output is not None:
                safe = {k:v for k,v in output.items() if k != 'annotated_image'}
                # Full wire reply (including annotation) was archived by the local GUI client.
                if not (folder/'response.json').exists():
                    (folder/'response.json').write_text(json.dumps(safe,ensure_ascii=False,indent=2),encoding='utf-8')
                metrics = output.get('metrics', {})
                raw_output = output.get('raw_output')
                if raw_output is None and output.get('emitted_actions') is not None:
                    # D-5970 emits native structured actions, not generated prose.
                    raw_output = json.dumps(output['emitted_actions'], ensure_ascii=False)
                    entry['raw_output_kind'] = 'native_structured_actions'
                entry.update(raw_output=raw_output, prediction=output.get('prediction'),
                    verification=output.get('verification'),
                    control=output.get('control'), control_text=output.get('text'),
                    protocol_normalizations=output.get('protocol_normalizations', []),
                    timings=metrics.get('phase_ms', {}), memory=metrics.get('memory', {}),
                    tokens=metrics.get('tokens', {}), service_output_directory=output.get('output_directory'))
                if output.get('error'):
                    entry['error'] = output['error']
                self.result['performance']['model_completion_wait_ms'] += metrics.get('phase_ms', {}).get(
                    'generate_ms', output.get('elapsed_seconds', 0)*1000)
                self.record('model_output', step_index=step, request_id=request_id, output=safe,
                            model_roundtrip_ms=entry['model_roundtrip_ms'])
            else:
                (folder/'response.json').write_text(json.dumps({'status': entry['status'],
                    'error': entry.get('error'), 'response_received': False},ensure_ascii=False,indent=2),encoding='utf-8')
            if (folder/'input.json').exists():
                entry['input_path'] = str(folder/'input.json')
                entry['input'] = json.loads((folder/'input.json').read_text(encoding='utf-8'))
            (folder/'round.json').write_text(json.dumps(entry,ensure_ascii=False,indent=2),encoding='utf-8')
            self.save()
            self.publish(entry)
            self.status(f"[plan {step+1}] {entry['status']} · 往返 {entry['model_roundtrip_ms']/1000:.2f} 秒"
                        + (f" · 生成 {entry['timings'].get('generate_ms',0)/1000:.2f} 秒" if entry.get('timings') else ''))
            if entry.get('raw_output'):
                self.status('模型原始回答：'+entry['raw_output'])

    def save(self):
        Path(self.result['audit_path']).write_text(json.dumps(self.result['model_io'],
            ensure_ascii=False,indent=2),encoding='utf-8')

    def publish(self, entry):
        if self.on_round is not None:
            self.on_round(entry)

    def execution(self, value):
        """Keep executor receipts separate from the model-service prediction reply."""
        if not self.result['model_io']:
            return
        entry = self.result['model_io'][-1]
        entry['execution'] = {'phase': 'execution', **value}
        path = Path(entry['output_directory'])/'execution.json'
        entry['execution_path'] = str(path)
        path.write_text(json.dumps(entry['execution'], ensure_ascii=False, indent=2), encoding='utf-8')
        (path.parent/'round.json').write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding='utf-8')
        self.save()
        self.publish(entry)

    def finish(self, seconds):
        self.result['performance']['total_elapsed_ms'] = round(seconds*1000,2)
        self.save()
