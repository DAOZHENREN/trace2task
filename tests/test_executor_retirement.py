import pytest

from trace2task.web_console import WebConsoleController


def test_workbench_rejects_retired_win32_before_execution(tmp_path, monkeypatch):
    controller = WebConsoleController(tmp_path)
    calls = []
    monkeypatch.setattr(controller, '_run_job', lambda *args: calls.append(args))
    with pytest.raises(ValueError, match='Win32 执行后端已停用'):
        controller.start_job(task_path='', instruction='test', execute=True,
                             execution_scope='desktop', executor_backend='win32')
    assert calls == []


def test_workbench_defaults_to_cua_desktop(tmp_path, monkeypatch):
    controller = WebConsoleController(tmp_path)
    monkeypatch.setattr(controller, '_run_job', lambda *args: None)
    job = controller.start_job(task_path='', instruction='test', execute=True,
                               execution_scope='desktop')
    assert job['executor_backend'] == 'cua'
    assert job['cua_target'] == {'kind': 'desktop', 'display_id': 'primary'}
