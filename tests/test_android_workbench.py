# ruff: noqa: C408
import pytest

from trace2task.android_workbench import validate_config
from trace2task.web_console import WebConsoleController


def configuration(tmp_path):
    adb = tmp_path / 'adb.exe'
    adb.touch()
    return dict(adb=str(adb), serial='127.0.0.1:62273', package='com.android.deskclock', server_port=5038)


@pytest.mark.parametrize('change', [dict(serial='a;bad'), dict(package='bad'), dict(server_port=True), dict(adb='adb.exe'), dict(extra=True)])
def test_invalid_config(tmp_path, change):
    with pytest.raises(ValueError):
        validate_config({**configuration(tmp_path), **change})


def test_android_job_routes_and_freezes_target(tmp_path, monkeypatch):
    monkeypatch.setattr('trace2task.android_workbench.prepare', lambda value: {})
    controller = WebConsoleController(tmp_path)
    monkeypatch.setattr(controller, '_run_job', lambda *a: None)
    config = configuration(tmp_path)
    job = controller.start_job(task_path='', instruction='Pause stopwatch', execute=True,
        executor_backend='android', execution_scope='android', android_config=config)
    config['serial'] = 'different'
    assert job['android_config']['serial'] == '127.0.0.1:62273'
    assert job['input_mode'] == job['run_facts']['input_mode'] == 'remote_android'
    assert job['run_facts']['operation_scope'] == 'android'


@pytest.mark.parametrize('change', [dict(execution_scope='desktop'), dict(executor_backend='win32'),
    dict(provider='trained_d'), dict(use_experience=True), dict(execute=False),
    dict(cua_target={'kind': 'desktop'}), dict(background=True), dict(resume_from='runs/old')])
def test_android_rejects_mixed_routes(tmp_path, change):
    controller = WebConsoleController(tmp_path)
    options = dict(task_path='', instruction='test', execute=True, executor_backend='android',
                   execution_scope='android', android_config=configuration(tmp_path))
    with pytest.raises(ValueError):
        controller.start_job(**{**options, **change})


def test_mobileforge_session_closed_on_failure(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from trace2task import (
        android_workbench,
        chat_agent_runner,
        mobileforge_adapter,
        mobileforge_codex,
    )
    closed = []
    session = SimpleNamespace(close=lambda: closed.append(True))
    monkeypatch.setattr(mobileforge_adapter, 'MobileForgeSource', lambda path: object())
    monkeypatch.setattr(mobileforge_adapter, 'MobileForgeAdapter', lambda *a: object())
    monkeypatch.setattr(mobileforge_codex, 'MobileForgeCodexSession', lambda *a: session)
    def run(**kwargs):
        assert kwargs['session'] is session
        assert kwargs['executor_backend'] == 'android'
        raise RuntimeError('stopped')
    monkeypatch.setattr(chat_agent_runner, 'run_chat_agent', run)
    with pytest.raises(RuntimeError, match='stopped'):
        android_workbench.run_mobileforge(upstream='source', model='test', output_root=tmp_path,
                                         executor_backend='android')
    assert closed == [True]


@pytest.mark.parametrize('mobileforge', ['', 'pinned-source'])
def test_worker_routes_android_and_keeps_stop_control(
    tmp_path, monkeypatch, mobileforge, synthetic_console_hotkey
):
    from trace2task import android_workbench, chat_agent_runner, mobileforge_adapter
    calls = []
    monkeypatch.setattr(android_workbench, 'prepare', lambda value: {})
    def runner(**kwargs):
        calls.append(kwargs)
        return {'stop_reason': 'emergency_stop', 'task_complete': False, 'verified': False}
    monkeypatch.setattr(chat_agent_runner, 'run_chat_agent', runner)
    monkeypatch.setattr(android_workbench, 'run_mobileforge', runner)
    monkeypatch.setattr(mobileforge_adapter, 'MobileForgeSource', lambda path: object())
    controller = WebConsoleController(tmp_path)
    config = configuration(tmp_path)
    job = controller.start_job(task_path='', instruction='test', execute=True,
        execution_scope='android', executor_backend='android', android_config=config, mobileforge=mobileforge)
    result = controller.wait(job['job_id'])
    assert result['status'] == 'stopped'
    assert calls[0]['android_config'] == config
    assert calls[0]['max_actions'] == 80
    assert calls[0]['max_seconds'] == 1200
    assert calls[0].get('upstream', '') == mobileforge
    assert calls[0]['emergency_stop'].stop_event is controller._jobs[job['job_id']].stop_event
    if mobileforge:
        assert 'interventions' not in calls[0]
        assert result['interventions'] == {}
    else:
        assert calls[0]['interventions'] is controller._jobs[job['job_id']].intervention_control
        assert result['interventions']['phase'] == 'closed'
