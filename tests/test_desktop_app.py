from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from trace2task import desktop_app


def test_close_refuses_running_task_even_when_dialog_confirmed():
    controller = SimpleNamespace(active_job=lambda: {"status": "running"})
    window = SimpleNamespace(create_confirmation_dialog=lambda *args: True)
    assert desktop_app.can_close(controller, window) is False


def test_idle_close_respects_confirmation():
    controller = SimpleNamespace(active_job=lambda: {"status": "completed"})
    for answer in (False, True):
        window = SimpleNamespace(create_confirmation_dialog=lambda *args, answer=answer: answer)
        assert desktop_app.can_close(controller, window) is answer


@pytest.mark.parametrize("fail", [False, True])
def test_native_shell_owns_server_and_cleans_up(tmp_path, monkeypatch, fail):
    calls = []

    class Event:
        def __iadd__(self, handler):
            return self

    server = SimpleNamespace(
        server_address=("127.0.0.1", 12345),
        serve_forever=lambda: calls.append("serve"),
        shutdown=lambda: calls.append("shutdown"),
        server_close=lambda: calls.append("close"),
    )

    def create(root, *, port, controller):
        assert root == tmp_path
        assert port == 0
        return server

    def create_window(title, url, **kwargs):
        assert url == "http://127.0.0.1:12345/"
        assert "js_api" not in kwargs
        return SimpleNamespace(events=SimpleNamespace(closing=Event(), loaded=Event()))

    def start(**kwargs):
        assert kwargs["gui"] == "edgechromium"
        assert kwargs["debug"] is False
        if fail:
            raise RuntimeError("missing WebView2")

    monkeypatch.setattr(desktop_app, "create_web_server", create)
    viewer = SimpleNamespace(create_window=create_window, start=start)
    if fail:
        with pytest.raises(RuntimeError, match="WebView2"):
            desktop_app.run_desktop(tmp_path, webview_module=viewer)
    else:
        desktop_app.run_desktop(tmp_path, webview_module=viewer)
    assert calls[-2:] == ["shutdown", "close"]


def test_cli_desktop_parser():
    from trace2task.cli import build_parser

    args = build_parser().parse_args(["desktop", "--project-root", "D:/MyProject/trace2task"])
    assert args.project_root == Path("D:/MyProject/trace2task")


def test_explicit_data_root_is_created(tmp_path):
    root = tmp_path / "separate-data"
    assert desktop_app.choose_data_root(root) == root.resolve()
    assert root.is_dir()


def test_packaged_app_rejects_data_inside_installation(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop_app.sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop_app.sys, "executable", str(tmp_path / "Trace2Task.exe"))
    with pytest.raises(ValueError, match="安装目录"):
        desktop_app.choose_data_root(tmp_path / "data")


def test_cli_uses_protected_desktop_launcher(tmp_path, monkeypatch):
    from trace2task.cli import main

    launcher = Mock()
    monkeypatch.setattr(desktop_app, "main", launcher)
    assert main(["desktop", "--project-root", str(tmp_path)]) == 0
    launcher.assert_called_once_with(["--project-root", str(tmp_path)])


@pytest.mark.parametrize("content", ['{broken', '[]', '{"data_root": null}',
                                     '{"data_root": ""}'])
def test_invalid_saved_settings_can_be_reselected(tmp_path, monkeypatch, content):
    import json

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    settings = tmp_path / "Trace2Task/desktop-settings.json"
    settings.parent.mkdir()
    settings.write_text(content, encoding="utf-8")
    selected = tmp_path / "data"
    selected.mkdir()
    dialog = SimpleNamespace(withdraw=Mock(), destroy=Mock())
    chooser = Mock(return_value=str(selected))
    monkeypatch.setitem(desktop_app.sys.modules, "tkinter", SimpleNamespace(
        Tk=lambda: dialog, filedialog=SimpleNamespace(askdirectory=chooser),
    ))
    assert desktop_app.choose_data_root() == selected.resolve()
    assert json.loads(settings.read_text(encoding="utf-8")) == {"data_root": str(selected)}
    dialog.destroy.assert_called_once()


def _mock_win32(monkeypatch):
    import ctypes

    api = SimpleNamespace(
        kernel32=SimpleNamespace(CreateMutexW=Mock(return_value=123),
                                 GetLastError=Mock(return_value=0), CloseHandle=Mock()),
        user32=SimpleNamespace(MessageBoxW=Mock()),
    )
    monkeypatch.setattr(ctypes, "windll", api, raising=False)
    return api


def test_early_configuration_failure_is_reported_and_logged(tmp_path, monkeypatch):
    api = _mock_win32(monkeypatch)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(desktop_app, "choose_data_root", Mock(side_effect=ValueError("bad folder")))
    with pytest.raises(ValueError, match="bad folder"):
        desktop_app.main([])
    api.user32.MessageBoxW.assert_called_once()
    assert "bad folder" in (tmp_path / "Trace2Task/logs/desktop.log").read_text(encoding="utf-8")
    api.kernel32.CloseHandle.assert_not_called()


def test_failed_startup_releases_mutex_and_records_error(tmp_path, monkeypatch):
    api = _mock_win32(monkeypatch)
    monkeypatch.setenv("TRACE2TASK_DATA_ROOT", "previous")
    monkeypatch.setattr(desktop_app, "run_desktop", Mock(side_effect=RuntimeError("WebView missing")))
    with pytest.raises(RuntimeError, match="WebView missing"):
        desktop_app.main(["--project-root", str(tmp_path)])
    api.kernel32.CloseHandle.assert_called_once_with(123)
    assert "WebView missing" in (tmp_path / "runs/desktop/desktop.log").read_text(encoding="utf-8")


def test_second_instance_does_not_start_server(tmp_path, monkeypatch):
    api = _mock_win32(monkeypatch)
    api.kernel32.GetLastError.return_value = 183
    run = Mock()
    monkeypatch.setattr(desktop_app, "run_desktop", run)
    desktop_app.main(["--project-root", str(tmp_path)])
    run.assert_not_called()
    api.kernel32.CloseHandle.assert_called_once_with(123)
