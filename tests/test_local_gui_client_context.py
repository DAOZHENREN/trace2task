"""The resident model wire format must not leak a backend-specific contract."""

import io
import json

import pytest

from trace2task import local_gui_client


@pytest.mark.parametrize('generic', [False, True])
def test_execution_context_negotiates_with_old_and_new_service(
        monkeypatch, tmp_path, generic):
    service_dir = tmp_path / 'runs' / 'local-gui'
    service_dir.mkdir(parents=True)
    (service_dir / 'service.token').write_text('test-token', encoding='ascii')
    monkeypatch.setenv('TRACE2TASK_DATA_ROOT', str(tmp_path))
    requests = []

    class Opener:
        def open(self, request, timeout):
            if isinstance(request, str) and request.endswith('/health'):
                health = {'service': 'trace2task-local-gui', 'protocol': 1,
                          'capabilities': {'generic_execution_context': generic}}
                return io.BytesIO(json.dumps(health).encode())
            requests.append(json.loads(request.data))
            return io.BytesIO(b'{"status":"predicted"}')

    monkeypatch.setattr(local_gui_client, 'build_opener', lambda *args: Opener())
    context = {'execution_scope': 'win32_desktop', 'capabilities': {}}
    result = local_gui_client.predict_gui('test', model='mai-ui-2b', image='image-bytes',
                                          execution_context=context)
    assert result['status'] == 'predicted'
    assert len(requests) == 1
    expected_key = 'execution_context' if generic else 'cua_context'
    other_key = 'cua_context' if generic else 'execution_context'
    assert requests[0][expected_key] == context
    assert other_key not in requests[0]


def test_predict_gui_rejects_conflicting_context_aliases_before_network():
    with pytest.raises(ValueError, match='Use either execution_context'):
        local_gui_client.predict_gui('test', model='mai-ui-2b', image='image-bytes',
                                     execution_context={}, cua_context={})


@pytest.mark.parametrize('supported', [False, True])
def test_a_images_require_explicit_service_support_and_send_frozen_bytes(monkeypatch, tmp_path, supported):
    from test_task_conversations import image_experience

    directory = tmp_path / 'runs' / 'local-gui'
    directory.mkdir(parents=True)
    (directory / 'service.token').write_text('test-token', encoding='ascii')
    monkeypatch.setenv('TRACE2TASK_DATA_ROOT', str(tmp_path))
    requests = []

    class Opener:
        def open(self, request, timeout):
            if isinstance(request, str):
                return io.BytesIO(json.dumps({'service': 'trace2task-local-gui', 'protocol': 1,
                    'capabilities': {'trace_evidence_images': supported}}).encode())
            requests.append(json.loads(request.data))
            return io.BytesIO(b'{"status":"predicted"}')

    monkeypatch.setattr(local_gui_client, 'build_opener', lambda *args: Opener())
    context = image_experience()
    if not supported:
        with pytest.raises(RuntimeError, match='未发送截图或经验'):
            local_gui_client.predict_gui('task', model='mai-ui-2b', image='private-current', experience_context=context)
        assert requests == []
    else:
        result = local_gui_client.predict_gui('task', model='mai-ui-2b', image='current', experience_context=context)
        assert result['status'] == 'predicted'
        assert requests[0]['experience_context'] == context


def test_frozen_engine_mismatch_rejects_before_sending_screenshot(monkeypatch, tmp_path):
    monkeypatch.setenv('TRACE2TASK_DATA_ROOT', str(tmp_path))
    class Opener:
        def open(self, request, timeout):
            assert isinstance(request, str) and request.endswith('/health')
            return io.BytesIO(b'{"service":"trace2task-local-gui","protocol":1,"backend":"transformers","capabilities":{"expected_backend":true}}')
    monkeypatch.setattr(local_gui_client, 'build_opener', lambda *args: Opener())
    with pytest.raises(RuntimeError, match='未发送截图'):
        local_gui_client.predict_gui('test', model='qwen3-vl-2b', image='private-image', expected_backend='llama-server')
