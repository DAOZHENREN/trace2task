"""Job API authorization, persistent history and restart behavior; no device/model."""
import json
import threading
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest
from test_web_console import _csrf_headers

from trace2task.run_interventions import RunInterventions
from trace2task.run_session import RunStore
from trace2task.web_console import ConsoleJob, WebConsoleController, create_web_server


def controller_job(tmp_path):
    controller = WebConsoleController(tmp_path, rsi_client=object())
    job = ConsoleJob(job_id='android-test', task_path='', task_id='Android', instruction='test',
                     mode='execute', status='running', executor_backend='android', execution_scope='android')
    job.intervention_control = RunInterventions(on_change=lambda state: controller._intervention_update(job, state))
    controller._register_job(job)
    job.intervention_control.activate(lambda *a, **k: None)
    return controller, job


def test_requests_persist_with_ids_and_reopened_history_never_resumes(tmp_path):
    controller, job = controller_job(tmp_path)
    payload = {'request_id': 'correct-1', 'kind': 'instruction', 'text': '检查奖励'}
    response = controller.intervene_job(job.job_id, payload)
    assert controller.intervene_job(job.job_id, payload) == response
    saved = controller.run_store.get(job.job_id)['session']
    assert saved['interventions']['commands'][0]['text'] == '检查奖励'
    assert 'intervention_control' not in saved
    reopened = RunStore(tmp_path)
    archived = reopened.get(job.job_id)['session']
    assert archived['status'] == 'interrupted'
    assert archived['interventions']['phase'] == 'closed'
    assert archived['interventions']['commands'] == saved['interventions']['commands']
    assert WebConsoleController(tmp_path, rsi_client=object())._jobs == {}


def test_restart_during_handoff_keeps_only_existing_evidence(tmp_path):
    controller, job = controller_job(tmp_path)
    controller.intervene_job(job.job_id, {'request_id': 'pause-001', 'kind': 'pause'})
    archived = RunStore(tmp_path).get(job.job_id)['session']['interventions']
    assert archived['closed_reason'] == 'session_interrupted'
    assert archived['takeovers'][0]['status'] == 'not_returned'
    assert archived['takeovers'][0]['before'] is None and archived['takeovers'][0]['after'] is None


def test_inactive_stopping_missing_or_unsupported_job_is_rejected(tmp_path):
    controller, job = controller_job(tmp_path)
    payload = {'request_id': 'correct-1', 'kind': 'instruction', 'text': 'test'}
    with pytest.raises(KeyError):
        controller.intervene_job('missing', payload)
    with pytest.raises(ValueError):
        controller.intervene_job(job.job_id, {**payload, 'package': 'com.other.app'})
    with pytest.raises(ValueError):
        controller.intervene_job(job.job_id, {'kind': 'pause'})
    job.stop_requested = True
    with pytest.raises(RuntimeError):
        controller.intervene_job(job.job_id, payload)
    job.stop_requested, job.status = False, 'completed'
    with pytest.raises(RuntimeError):
        controller.intervene_job(job.job_id, payload)
    job.status, job.intervention_control = 'running', None
    with pytest.raises(ValueError, match='Android'):
        controller.intervene_job(job.job_id, payload)


def test_intervention_http_requires_same_origin_and_csrf(tmp_path):
    controller, job = controller_job(tmp_path)
    server = create_web_server(tmp_path, port=0, controller=controller)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f'http://127.0.0.1:{server.server_address[1]}'
    opener = build_opener(ProxyHandler({}))
    payload = json.dumps({'request_id': 'correct-1', 'kind': 'instruction', 'text': 'test'}).encode()
    endpoint = base + f'/api/jobs/{job.job_id}/interventions'
    try:
        with pytest.raises(HTTPError) as denied:
            opener.open(Request(endpoint, data=payload, headers={'Content-Type': 'application/json'}))
        assert denied.value.code == 400 and not job.interventions['commands']
        headers = {'Content-Type': 'application/json', **_csrf_headers(opener, base)}
        with opener.open(Request(endpoint, data=payload, headers=headers)) as response:
            assert json.loads(response.read())['commands'][0]['id'] == 'correct-1'
        with pytest.raises(HTTPError) as denied:
            opener.open(Request(endpoint, data=payload, headers={**headers, 'Origin': 'https://untrusted.example'}))
        assert denied.value.code == 400 and len(job.interventions['commands']) == 1
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
