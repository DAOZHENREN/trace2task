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
    assert captured['cua_context'] == {'windows':[]}
    assert json.loads(Path(entry['request_path']).read_text())['cua_context'] == {'windows':[]}
    assert entry['raw_output'] == output['raw_output']
    assert entry['timings']['generate_ms'] == 12
    assert result['performance']['total_elapsed_ms'] == 500
    assert Path(result['audit_path']).exists()


def test_archive_rejects_service_paths_outside_log_root(tmp_path):
    from trace2task.local_gui_client import archive_prediction
    with pytest.raises(ValueError):
        archive_prediction(tmp_path/'archive', {}, {'output_directory':str(tmp_path/'outside')},tmp_path/'logs')
