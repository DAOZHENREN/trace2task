"""Preparation never dispatches model actions; launch stays bound to the selected package."""
import subprocess

import pytest
from test_android_execution import phone  # noqa: F401
from test_chat_agent_bridge import Stop

from trace2task import android_workbench, windows_runner
from trace2task.android_execution import AndroidDriver


def config(tmp_path, **extra):
    adb = tmp_path / 'adb.exe'
    adb.touch()
    return dict(adb=str(adb), serial='phone', package='com.example.game', server_port=5038, **extra)


def test_preflight_is_read_only_even_with_launch_enabled(phone, tmp_path, monkeypatch):  # noqa: F811
    _, data = phone
    monkeypatch.setattr(windows_runner, 'Win32EmergencyStop', Stop)
    data['package'] = 'com.example.launcher'
    result = android_workbench.prepare(config(tmp_path, launch_app=True))
    assert result['launcher'] == 'com.example.game/.Main'
    assert result['model_calls'] == 0
    assert not any('am' in c or 'connect' in c for c in data['commands'])


def test_launch_is_explicit_and_bound(phone, tmp_path):  # noqa: F811
    _, data = phone
    data['package'] = 'com.example.launcher'
    events = []
    driver = AndroidDriver(tmp_path, lambda kind, **kw: events.append(kind), Stop(), **config(tmp_path, launch_app=True))
    driver.start()
    launches = [c for c in data['commands'] if 'am' in c]
    assert len(launches) == 1
    assert launches[0][3:] == ['-t', '7', 'shell', 'am', 'start', '-W', '-n', 'com.example.game/.Main']
    assert 'android_app_launch' in events
    assert not any('force-stop' in c or 'clear' in c or 'connect' in c for c in data['commands'])


def test_probe_cannot_launch(phone, tmp_path, monkeypatch):  # noqa: F811
    _, data = phone
    monkeypatch.setattr(windows_runner, 'Win32EmergencyStop', Stop)
    data['package'] = 'com.example.launcher'
    with pytest.raises(RuntimeError, match='not focused'):
        android_workbench.probe(config(tmp_path, launch_app=True))
    assert not any('am' in c for c in data['commands'])


def test_explicit_connect_uses_only_selected_endpoint(phone, tmp_path, monkeypatch):  # noqa: F811
    _, data = phone
    monkeypatch.setattr(windows_runner, 'Win32EmergencyStop', Stop)
    original = subprocess.run
    from types import SimpleNamespace
    def run(command, **kwargs):
        if command[3:] == ['connect', '127.0.0.1:52130']:
            data['commands'].append(command)
            return SimpleNamespace(stdout=b'connected')
        if command[3:] == ['devices', '-l']:
            return SimpleNamespace(stdout=b'127.0.0.1:52130 device transport_id:7')
        return original(command, **kwargs)
    monkeypatch.setattr(subprocess, 'run', run)
    value = config(tmp_path, launch_app=True)
    value['serial'] = '127.0.0.1:52130'
    result = android_workbench.prepare(value, connect=True)
    assert result['target']['serial'] == value['serial']
    assert sum('connect' in c for c in data['commands']) == 1
    assert not any('am' in c for c in data['commands'])


def test_missing_phone_prevents_job_creation(tmp_path, monkeypatch):
    from trace2task.web_console import WebConsoleController
    def fail(value):
        raise RuntimeError('手机未连接')
    monkeypatch.setattr(android_workbench, 'prepare', fail)
    controller = WebConsoleController(tmp_path)
    with pytest.raises(RuntimeError, match='手机未连接'):
        controller.start_job(task_path='', instruction='test', execute=True, provider='codex',
            model='gpt-6.1-sol', reasoning_effort='low', executor_backend='android',
            execution_scope='android', android_config=config(tmp_path))
    assert controller.active_job() is None


def test_connection_timeout_becomes_displayable_error(tmp_path, monkeypatch):
    monkeypatch.setattr(windows_runner, 'Win32EmergencyStop', Stop)
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('adb connect', 20)
    monkeypatch.setattr(subprocess, 'run', timeout)
    value = config(tmp_path)
    value['serial'] = '127.0.0.1:52130'
    with pytest.raises(RuntimeError, match='超时'):
        android_workbench.prepare(value, connect=True)
