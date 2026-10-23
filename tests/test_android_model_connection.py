import io
import json
from types import SimpleNamespace

import pytest
from test_android_execution import phone  # noqa: F401 - synthetic ADB fixture

from trace2task import android_model_connection as connection


@pytest.mark.parametrize('health,connected', [
    ({'status': 'ok', 'model': connection.MODEL}, True),
    ({'status': 'loading', 'model': connection.MODEL}, False),
    ({'status': 'ok', 'model': 'other'}, False), ([], False),
])
def test_status_checks_identity_without_images_or_inputs(monkeypatch, health, connected):
    calls = []
    def open_(url, **kwargs):
        calls.append(url)
        return io.BytesIO(json.dumps(health).encode())
    monkeypatch.setattr(connection, 'build_opener', lambda *a: SimpleNamespace(open=open_))
    result = connection.status()
    assert result['connected'] is connected
    assert result['input_actions'] == result['model_calls'] == 0
    assert calls == ['http://127.0.0.1:18998/health']


def test_reuse_never_starts_or_stops_any_process(monkeypatch):
    monkeypatch.setattr(connection, 'status', lambda: {'connected': True})
    monkeypatch.setattr(connection.subprocess, 'Popen', lambda *a, **k: pytest.fail('must reuse'))
    assert connection.connect()['reused'] is True


def test_occupied_unhealthy_port_is_not_overwritten(monkeypatch):
    monkeypatch.setattr(connection, 'status', lambda: {'connected': False})
    class Socket:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def settimeout(self, value): pass
        def connect_ex(self, target): return 0
    monkeypatch.setattr(connection.socket, 'socket', Socket)
    monkeypatch.setattr(connection.subprocess, 'Popen', lambda *a, **k: pytest.fail('port occupied'))
    with pytest.raises(RuntimeError, match='不会覆盖'):
        connection.connect()


def test_connect_is_fixed_loopback_only_and_no_remote_command(monkeypatch):
    monkeypatch.setattr(connection, 'ssh_host', lambda: 'fixture-model-host')
    replies = iter([{'connected': False}, {'connected': True}])
    monkeypatch.setattr(connection, 'status', lambda: next(replies))
    monkeypatch.setattr(connection, '_tunnel', None)
    class Socket:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def settimeout(self, value): pass
        def connect_ex(self, target): return 1
    monkeypatch.setattr(connection.socket, 'socket', Socket)
    monkeypatch.setattr(connection.shutil, 'which', lambda name: '/usr/bin/ssh')
    calls = []
    process = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(connection.subprocess, 'Popen', lambda args, **kwargs: calls.append((args, kwargs)) or process)
    assert connection.connect()['reused'] is False
    command = calls[0][0]
    assert '-N' in command and 'StrictHostKeyChecking=yes' in command
    assert command[-3:] == ['-L', '127.0.0.1:18998:127.0.0.1:8098', 'fixture-model-host']
    assert calls[0][1]['stdin'] == connection.subprocess.DEVNULL


def test_ssh_target_is_local_configuration_and_environment_takes_precedence(tmp_path, monkeypatch):
    path = tmp_path / 'profile.json'
    path.write_text(json.dumps({'ssh_host': 'fixture@model-host'}), encoding='utf-8')
    monkeypatch.setenv('TRACE2TASK_ANDROID_MODEL_PROFILE', str(path))
    monkeypatch.delenv('TRACE2TASK_ANDROID_SSH_HOST', raising=False)
    assert connection.ssh_host() == 'fixture@model-host'
    monkeypatch.setenv('TRACE2TASK_ANDROID_SSH_HOST', 'another-host')
    assert connection.ssh_host() == 'another-host'


@pytest.mark.parametrize('host', ['-oProxyCommand=bad', 'host command', 'host;command', None, 12])
def test_ssh_target_rejects_options_commands_and_invalid_types(tmp_path, monkeypatch, host):
    path = tmp_path / 'profile.json'
    path.write_text(json.dumps({'ssh_host': host}), encoding='utf-8')
    monkeypatch.setenv('TRACE2TASK_ANDROID_MODEL_PROFILE', str(path))
    monkeypatch.delenv('TRACE2TASK_ANDROID_SSH_HOST', raising=False)
    with pytest.raises(ValueError, match='SSH'):
        connection.ssh_host()


@pytest.mark.parametrize('changes', [
    {'value': 'other'}, {'provider': 'codex'}, {'executor': 'win32'},
    {'base_url': 'https://example.com/v1'}, {'base_url': None},
    {'model': 'other'}, {'mobileforge': ''},
])
def test_profile_cannot_silently_change_destination(changes):
    args = {'value': connection.PROFILE_ID, 'provider': 'api', 'executor': 'android',
                'model': connection.MODEL, 'base_url': connection.BASE_URL, 'mobileforge': 'verified-source'}
    with pytest.raises(ValueError, match='配置不匹配'):
        connection.validate_selection(**{**args, **changes})


def test_job_profile_requires_ready_model_and_never_uses_cloud_secret(tmp_path, monkeypatch):
    from trace2task import android_workbench, mobileforge_adapter
    from trace2task.web_console import WebConsoleController
    adb = tmp_path / 'adb.exe'
    adb.touch()
    controller = WebConsoleController(tmp_path)
    calls = []
    monkeypatch.setattr(controller, '_run_job', lambda *args: calls.append(args))
    monkeypatch.setattr(mobileforge_adapter, 'MobileForgeSource', lambda path: None)
    monkeypatch.setattr(android_workbench, 'prepare', lambda value: {})
    args = {'task_path': '', 'instruction': 'fixture', 'execute': True, 'provider': 'api',
                'model': connection.MODEL, 'reasoning_effort': 'default', 'executor_backend': 'android',
                'execution_scope': 'android', 'android_config': {'adb': str(adb), 'serial': 'fixture',
                    'package': 'com.example.fixture', 'server_port': 5038},
                'mobileforge': 'fixture', 'android_model_profile': connection.PROFILE_ID,
                'api_options': {'base_url': connection.BASE_URL, 'api_key': 'do-not-forward-cloud-key'}}
    monkeypatch.setattr(connection, 'status', lambda: {'connected': False, 'error': 'not ready'})
    with pytest.raises(ValueError, match='not ready'):
        controller.start_job(**args)
    assert not calls and controller.active_job() is None
    monkeypatch.setattr(connection, 'status', lambda: {'connected': True})
    result = controller.start_job(**args)
    import time
    deadline = time.monotonic() + 2
    while not calls and time.monotonic() < deadline:
        time.sleep(.01)
    assert calls[0][-1].api_key == 'local-only'
    assert calls[0][-1].context_window_tokens == 4096
    assert result['run_facts']['data_flow'] == 'remote'
    assert '3090' in result['run_facts']['destination']
    assert result['run_facts']['execution_backend'] == 'android'
    assert result['android_model_profile'] == connection.PROFILE_ID
    assert 'do-not-forward-cloud-key' not in json.dumps(result)


def test_console_job_runs_model_directive_through_synthetic_adb_and_records_it(phone, tmp_path, monkeypatch):  # noqa: F811
    from test_chat_agent_bridge import Stop

    from trace2task import android_execution, web_console, windows_runner
    from trace2task import mobileforge_adapter as mf
    driver, data = phone
    driver.start = lambda: None
    monkeypatch.setattr(android_execution, 'AndroidDriver', lambda *a, **kw: driver)
    monkeypatch.setattr(web_console, 'ConsoleEmergencyStop', lambda *a: Stop())
    monkeypatch.setattr(windows_runner, 'Win32EmergencyStop', Stop)
    monkeypatch.setattr(connection, 'status', lambda: {'connected': True})
    source = SimpleNamespace(prompt='fixture', provenance={'commit': 'synthetic'})
    monkeypatch.setattr(mf, 'MobileForgeSource', lambda path: source)
    replies = iter([{'action': 'click', 'coordinate': [500, 500]}, {'action': 'terminate', 'status': 'failure'}])
    requests = []
    def requester(config, payload, **kwargs):
        requests.append(payload)
        raw = '<tool_call>' + json.dumps({'name': 'mobile_use', 'arguments': next(replies)}) + '</tool_call>'
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': raw}}], 'usage': {}}
    session_class = mf.MobileForgeSession
    monkeypatch.setattr(mf, 'MobileForgeSession', lambda config, model: session_class(config, model, requester=requester))
    adb = tmp_path / 'adb.exe'
    adb.touch()
    controller = web_console.WebConsoleController(tmp_path)
    job = controller.start_job(task_path='', instruction='synthetic only', execute=True,
        provider='api', model=connection.MODEL, reasoning_effort='default', executor_backend='android',
        execution_scope='android', mobileforge='fixture', android_model_profile=connection.PROFILE_ID,
        api_options={'base_url': connection.BASE_URL}, android_config={'adb': str(adb),
            'serial': 'phone', 'package': 'com.example.game', 'server_port': 5038})
    result = controller.wait(job['job_id'])
    assert len(requests) == 2
    assert result['result']['actions'] == 1
    assert result['result']['final_screenshot']
    assert result['run_facts']['data_flow'] == 'remote'
    commands = [c[5:] for c in data['commands'] if c[5:7] == ['shell', 'input']]
    assert commands == [['shell', 'input', 'tap', '640', '360']]
    assert controller.run_store.read_round(job['job_id'], 0)['execution']['executed']


def test_connection_http_routes_require_csrf_and_do_not_submit_tasks(tmp_path, monkeypatch):
    import threading
    from urllib.error import HTTPError
    from urllib.request import ProxyHandler, Request, build_opener

    from test_web_console import _csrf_headers

    from trace2task.web_console import create_web_server
    calls = []
    monkeypatch.setattr(connection, 'connect', lambda: calls.append('connect') or {'connected': True})
    server = create_web_server(tmp_path, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    opener = build_opener(ProxyHandler({}))
    body = json.dumps({'profile': connection.PROFILE_ID, 'action': 'connect'}).encode()
    try:
        with opener.open(base + '/api/android/model-profile') as response:
            assert json.load(response)['model'] == connection.MODEL
        with pytest.raises(HTTPError):
            opener.open(Request(base + '/api/android/model-connection', data=body))
        assert not calls
        headers = {'Content-Type': 'application/json', **_csrf_headers(opener, base)}
        with opener.open(Request(base + '/api/android/model-connection', data=body, headers=headers)) as response:
            assert json.load(response)['connected']
        assert calls == ['connect']
        assert server.RequestHandlerClass.controller.active_job() is None
    finally:
        server.shutdown()
        server.server_close()
