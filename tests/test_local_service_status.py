"""Service rediscovery is read-only and independent of the workbench's lifetime."""
import io
import json
import threading
from urllib.request import ProxyHandler, build_opener

import pytest

from trace2task import local_model_service as service


def probe(monkeypatch, responses):
    calls = []

    class Opener:
        def open(self, url, timeout):
            calls.append(url)
            assert timeout == 2
            response = responses[url]
            if isinstance(response, Exception):
                raise response
            return io.BytesIO(json.dumps(response).encode())

    monkeypatch.setattr(service, 'build_opener', lambda *args: Opener())
    monkeypatch.setattr(service, 'managed_processes', lambda: pytest.fail('Must not depend on process ownership'))
    return calls


GUI = 'http://127.0.0.1:8768/health'
D = 'http://127.0.0.1:8767/health'


def test_rediscovers_multiple_services_without_session_state(monkeypatch):
    calls = probe(monkeypatch, {GUI: {'service': 'trace2task-local-gui', 'protocol': 1,
        'model': 'gui-owl-2b', 'backend': 'llama-server', 'status': 'running', 'pid': 456},
        D: {'protocol': 2, 'model': 'D-5970', 'status': 'ready'}})
    for _ in range(2):  # A new workbench session receives the same live state.
        result = service.service_status()
        assert result['checked_at']
        gui, trained = result['services']
        assert gui['model'] == 'gui-owl-2b' and gui['pid'] == 456 and gui['status'] == 'running'
        assert trained['model'] == 'trained_d' and trained['status'] == 'running'
    assert sorted(calls) == sorted([GUI, D] * 2)


def test_disconnection_does_not_reuse_last_model(monkeypatch):
    responses = {GUI: {'service': 'trace2task-local-gui', 'protocol': 1, 'model': 'gui-owl-2b'}, D: OSError('offline')}
    probe(monkeypatch, responses)
    assert service.service_status()['services'][0]['status'] == 'reported'
    responses[GUI] = OSError('timeout')
    result = service.service_status()['services'][0]
    assert not result['reachable'] and result['status'] == 'unavailable'
    assert 'model' not in result


@pytest.mark.parametrize('payload', [[], {'service': 'other', 'protocol': 1},
    {'service': 'trace2task-local-gui', 'protocol': 1, 'model': {}},
    {'service': 'trace2task-local-gui', 'protocol': 1, 'status': []}])
def test_invalid_health_is_not_a_running_model(monkeypatch, payload):
    probe(monkeypatch, {GUI: payload, D: {'protocol': 2, 'model': 'other', 'status': 'ready'}})
    assert all(not s['reachable'] for s in service.service_status()['services'])


def test_idle_and_loading_are_distinct(monkeypatch):
    probe(monkeypatch, {GUI: {'service': 'trace2task-local-gui', 'protocol': 1, 'status': 'idle', 'model': 'stale'},
                       D: {'protocol': 2, 'model': 'D-5970', 'status': 'loading'}})
    gui, trained = service.service_status()['services']
    assert gui['reachable'] and gui['model'] is None and gui['status'] == 'idle'
    assert trained['reachable'] and trained['status'] == 'loading'


def test_workbench_status_endpoint_is_live_on_each_get(monkeypatch, tmp_path):
    from trace2task.web_console import create_web_server

    snapshot = {'services': [], 'checked_at': 'first'}
    monkeypatch.setattr(service, 'service_status', lambda: snapshot)
    server = create_web_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = build_opener(ProxyHandler({}))
    try:
        for stamp in ('first', 'reopened'):
            snapshot['checked_at'] = stamp
            with opener.open(f'http://127.0.0.1:{server.server_port}/api/local-model/status', timeout=5) as response:
                assert json.load(response) == snapshot
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
