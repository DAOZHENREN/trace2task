import json
from pathlib import Path

from trace2task.local_run_audit import LocalRunAudit


class _NeverStopped:
    def raise_if_requested(self):
        return None


def test_local_audit_publishes_live_request_reply_and_execution(tmp_path, monkeypatch):
    from trace2task import local_gui_client

    monkeypatch.setattr(local_gui_client, 'cancel_gui', lambda _request_id: None)
    result = {}
    updates = []
    (tmp_path/'screen.png').write_bytes(b'fake-png')
    audit = LocalRunAudit(tmp_path, result, lambda *_args, **_kwargs: None,
                          lambda _text: None, on_round=lambda entry: updates.append(dict(entry)))

    def predict(_task, **kwargs):
        folder = Path(kwargs['audit_dir'])
        (folder/'input.json').write_text(json.dumps({'messages': [
            {'role': 'system', 'content': 'custom rules'},
            {'role': 'user', 'content': [{'type': 'text', 'text': 'do the task'}]},
        ]}), encoding='utf-8')
        return {'status': 'predicted', 'raw_output': 'Action: wait',
                'prediction': {'actions': [{'skill': 'wait', 'args': {'duration_ms': 100}}]}}

    audit.predict(predict, _NeverStopped(), 'gui-owl-2b', 'do the task',
                  tmp_path/'screen.png', [], 0,
                  prompt_profile={'system_prompt': 'custom rules', 'turn_template': '{{task}}'})
    assert updates[0]['status'] == 'pending'
    assert updates[-1]['input']['messages'][0]['content'] == 'custom rules'
    assert updates[-1]['raw_output'] == 'Action: wait'
    audit.execution({'status': 'delivered', 'effect': 'unverifiable'})
    assert updates[-1]['execution']['status'] == 'delivered'


def test_structured_model_native_actions_are_not_mislabeled_as_missing_reply(tmp_path):
    result = {}
    (tmp_path/'screen.png').write_bytes(b'fake-png')
    audit = LocalRunAudit(tmp_path, result, lambda *_args, **_kwargs: None,
                          lambda _text: None)
    native = [{'skill': 'click', 'args': {'x': .25, 'y': .5, 'button': 'left'}}]
    output = audit.predict(lambda *_args, **_kwargs: {
        'status': 'predicted', 'emitted_actions': native,
        'prediction': {'actions': native}}, _NeverStopped(), 'D-5970',
        'click once', tmp_path/'screen.png', [], 0)
    assert output['emitted_actions'] == native
    assert result['model_io'][0]['raw_output_kind'] == 'native_structured_actions'
    assert json.loads(result['model_io'][0]['raw_output']) == native
