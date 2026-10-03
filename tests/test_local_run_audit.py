import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from trace2task.local_run_audit import LocalRunAudit
from trace2task.trained_model_runner import interruptible_prediction
from trace2task.windows_runner import EmergencyStopRequested


def test_cancel_waits_for_scoped_ack_and_preserves_partial_output():
    release = threading.Event()
    stop = SimpleNamespace(raise_if_requested=lambda: (_ for _ in ()).throw(EmergencyStopRequested('stop')))
    def predict(*args, **kwargs):
        release.wait(2)
        return {'status':'cancelled', 'raw_output':'partial'}
    def cancel():
        release.set()
        return {'status':'cancel_requested','request_id':'test-id'}
    with pytest.raises(EmergencyStopRequested) as caught:
        interruptible_prediction(predict,stop,'task',on_cancel=cancel,cancel_wait_seconds=2)
    assert caught.value.model_output['raw_output'] == 'partial'
    assert caught.value.cancel_receipt['request_id'] == 'test-id'


def test_cancel_timeout_never_returns_a_plan():
    release = threading.Event()
    stop = SimpleNamespace(raise_if_requested=lambda: (_ for _ in ()).throw(EmergencyStopRequested('stop')))
    try:
        with pytest.raises(EmergencyStopRequested) as caught:
            interruptible_prediction(lambda *a,**k:release.wait(2),stop,'task',
                on_cancel=lambda:{'status':'cancel_requested'},cancel_wait_seconds=.01)
        assert caught.value.cancel_pending
    finally:
        release.set()


def test_round_audit_preserves_actual_context_and_timings(tmp_path):
    image = tmp_path/'frame.png'
    image.write_bytes(b'png')
    result, events = {}, []
    audit = LocalRunAudit(tmp_path,result,lambda kind,**value:events.append((kind,value)),lambda _:None)
    output = {'status':'predicted','raw_output':'{"actions":[{"done":true}]}',
        'prediction':{'actions':[{'done':True}]},'metrics':{'phase_ms':{'generate_ms':12},'tokens':{'input_tokens':5}}}
    captured = {}
    def predict(task,**kwargs):
        captured.update(kwargs)
        return output
    audit.predict(predict,SimpleNamespace(raise_if_requested=lambda:None),'qwen3-vl-2b',
        'task',image,[],0,{'windows':[]})
    audit.finish(.5)
    entry = result['model_io'][0]
    assert captured['execution_context'] == {'windows':[]}
    assert json.loads(Path(entry['request_path']).read_text())['execution_context'] == {'windows':[]}
    assert entry['raw_output'] == output['raw_output']
    assert entry['timings']['generate_ms'] == 12
    assert result['performance']['total_elapsed_ms'] == 500
    assert Path(result['audit_path']).exists()


def test_archive_rejects_service_paths_outside_log_root(tmp_path):
    from trace2task.local_gui_client import archive_prediction
    with pytest.raises(ValueError):
        archive_prediction(tmp_path/'archive', {}, {'output_directory':str(tmp_path/'outside')},tmp_path/'logs')


def test_later_client_turn_sends_no_system_profile_experience_or_before_image(tmp_path):
    image = tmp_path/'frame.png'
    image.write_bytes(b'png')
    audit = LocalRunAudit(tmp_path, {}, lambda *a, **kw: None, lambda _: None)
    calls = []
    def predict(_task, **kwargs):
        calls.append(kwargs)
        return {'status': 'predicted', 'conversation_id': kwargs['conversation_id']}
    for step in range(2):
        audit.predict(predict, SimpleNamespace(raise_if_requested=lambda: None), 'gui-owl-2b',
                      'task', image, [], step, previous_screenshot=image,
                      experience_context={'kind': 'trace_sequence'},
                      prompt_profile={'system_prompt': 'fixed system', 'turn_template': 'fixed template'})
    assert calls[0]['prompt_profile']['system_prompt'] == 'fixed system'
    assert calls[0]['experience_context']
    assert calls[1]['conversation_id'] == calls[0]['conversation_id']
    assert calls[1]['conversation_start'] is False
    assert calls[1]['prompt_profile'] is None
    assert calls[1]['experience_context'] is None
    assert all('previous_image' not in call for call in calls)


def test_context_audit_and_input_images_are_copied(tmp_path):
    from trace2task.local_gui_client import archive_prediction
    source = tmp_path/'logs'/'round'
    source.mkdir(parents=True)
    for name in ('context-management.json', 'attempt-00-input.json', 'context-image-00-000.png'):
        (source/name).write_bytes(b'archive evidence')
    destination = tmp_path/'archive'
    archive_prediction(destination, {}, {'output_directory': str(source)}, tmp_path/'logs')
    assert (destination/'context-image-00-000.png').read_bytes() == b'archive evidence'
    assert (destination/'attempt-00-input.json').is_file()
    assert (destination/'context-management.json').is_file()


def test_native_action_survives_unknown_dispatch_and_empty_delivered_history(tmp_path):
    image = tmp_path/'frame.png'
    image.write_bytes(b'png')
    result, calls = {}, []
    audit = LocalRunAudit(tmp_path, result, lambda *a, **kw: None, lambda _: None)
    raw = '<tool_call>{"name":"computer_use","arguments":{"action":"right_click","coordinate":[144,220]}}</tool_call>'
    def predict(_task, **kwargs):
        calls.append(kwargs)
        return {'status': 'predicted', 'raw_output': raw,
                'conversation_id': kwargs['conversation_id']}
    stop = SimpleNamespace(raise_if_requested=lambda: None)
    audit.predict(predict, stop, 'gui-owl-2b', 'task', image, [], 0)
    audit.execution({'executed': True, 'steps': [{'skill': 'click'}]})
    normalized = [{'action': {'skill': 'click', 'args': {'x': .144, 'y': .22}}}]
    audit.predict(predict, stop, 'gui-owl-2b', 'task', image, normalized, 1)
    audit.execution({'status': 'reobserve', 'executed': None, 'steps': [],
                     'executor_requests': [{'status': 'unknown'}]})
    audit.predict(predict, stop, 'gui-owl-2b', 'task', image, [], 2)
    assert calls[0]['history'] == []
    assert calls[1]['history'] == [{'step_index': 0, 'model_output': raw, 'input_call_status': 'returned'}]
    assert calls[2]['history'] == [{'step_index': 1, 'model_output': raw, 'input_call_status': 'unknown'}]
    assert calls[2]['conversation_id'] == calls[0]['conversation_id']
    assert calls[2]['conversation_start'] is False
    assert normalized[0]['action']['args']['x'] == .144


@pytest.mark.parametrize(('execution', 'status', 'count'), [
    ({'executed': False, 'steps': []}, 'not_sent', None),
    ({'executed': False, 'steps': [{}]}, 'partial', 1),
    ({'executed': None, 'steps': [{}], 'executor_requests': [{'status': 'unknown'}]}, 'unknown', 1),
    ({}, 'not_requested', None),
])
def test_native_history_distinguishes_partial_and_unsent_calls(tmp_path, execution, status, count):
    audit = LocalRunAudit(tmp_path, {}, lambda *a, **kw: None, lambda _: None)
    raw = 'native batch output stays intact'
    audit.result['model_io'] = [
        {'step_index': 0, 'raw_output': raw, 'execution': execution},
        {'step_index': 1, 'purpose': 'verify_completion', 'raw_output': 'review verdict'},
    ]
    item = audit.previous_model_action()[0]
    assert item['model_output'] == raw
    assert item['input_call_status'] == status
    assert item.get('returned_action_count') == count


def test_d5970_keeps_native_structured_history(tmp_path):
    image = tmp_path/'frame.png'
    image.write_bytes(b'png')
    audit = LocalRunAudit(tmp_path, {}, lambda *a, **kw: None, lambda _: None)
    calls = []
    def predict(_task, **kwargs):
        calls.append(kwargs)
        return {'status': 'predicted'}
    history = [{'actions': [{'type': 'click', 'x': 144, 'y': 220}]}]
    audit.predict(predict, SimpleNamespace(raise_if_requested=lambda: None),
                  'D-5970', 'task', image, history, 0)
    assert calls[0]['history'] == history
