import ctypes
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from trace2task import desktop_app
from trace2task import desktop_elevation as elevation


@pytest.fixture
def win32(monkeypatch):
    def execute(pointer):
        pointer._obj.hProcess = 123
        return 1

    def exit_code(handle, pointer):
        pointer._obj.value = 7
        return 1

    api = SimpleNamespace(
        shell32=SimpleNamespace(IsUserAnAdmin=Mock(return_value=0),
                                ShellExecuteExW=Mock(side_effect=execute)),
        kernel32=SimpleNamespace(GetLastError=Mock(return_value=5),
                                 WaitForSingleObject=Mock(return_value=0),
                                 GetExitCodeProcess=Mock(side_effect=exit_code),
                                 CloseHandle=Mock()),
    )
    monkeypatch.setattr(ctypes, 'windll', api, raising=False)
    monkeypatch.setattr(ctypes, 'WinError', lambda code: OSError(code, 'Win32 failure'),
                        raising=False)
    monkeypatch.setattr(elevation.sys, 'frozen', False, raising=False)
    return api


def test_admin_does_not_relaunch(win32):
    win32.shell32.IsUserAnAdmin.return_value = 1
    assert elevation.ensure_admin(['--elevated-child']) is None
    win32.shell32.ShellExecuteExW.assert_not_called()


@pytest.mark.parametrize('frozen', [False, True])
def test_elevation_preserves_arguments_and_waits_for_child(win32, monkeypatch, frozen):
    monkeypatch.setattr(elevation.sys, 'frozen', frozen)
    arguments = ['--project-root', 'D:\\项目 空格\\data', '--development', '--smoke-test']
    assert elevation.ensure_admin(arguments) == 7
    info = win32.shell32.ShellExecuteExW.call_args.args[0]._obj
    assert info.lpVerb == 'runas'
    assert info.lpFile == elevation.sys.executable
    expected = [*arguments, '--elevated-child']
    if not frozen:
        root = str(Path(elevation.__file__).resolve().parents[1])
        bootstrap = (f'import sys; sys.path.insert(0, {root!r}); '
                     'from trace2task.desktop_app import main; main()')
        expected = ['-c', bootstrap, *expected]
    assert info.lpParameters == subprocess.list2cmdline(expected)
    assert info.lpDirectory == elevation.os.getcwd()
    assert info.fMask == 0x40
    assert info.cbSize == ctypes.sizeof(elevation.ShellExecuteInfo)
    win32.kernel32.WaitForSingleObject.assert_called_once_with(123, 0xFFFFFFFF)
    win32.kernel32.CloseHandle.assert_called_once_with(123)


def test_failed_elevation_cannot_loop(win32):
    with pytest.raises(RuntimeError, match='不会循环'):
        elevation.ensure_admin(['--elevated-child'])
    win32.shell32.ShellExecuteExW.assert_not_called()


@pytest.mark.parametrize('code', [1223, 5])
def test_launch_failure_never_continues_unelevated(win32, code):
    win32.shell32.ShellExecuteExW.side_effect = None
    win32.shell32.ShellExecuteExW.return_value = 0
    win32.kernel32.GetLastError.return_value = code
    with pytest.raises(RuntimeError if code == 1223 else OSError):
        elevation.ensure_admin([])
    win32.kernel32.WaitForSingleObject.assert_not_called()
    win32.kernel32.CloseHandle.assert_not_called()


@pytest.mark.parametrize('failure', ['wait', 'exit_code'])
def test_child_handle_is_closed_on_error(win32, failure):
    if failure == 'wait':
        win32.kernel32.WaitForSingleObject.return_value = 0xFFFFFFFF
    else:
        win32.kernel32.GetExitCodeProcess.side_effect = None
        win32.kernel32.GetExitCodeProcess.return_value = 0
    with pytest.raises(OSError):
        elevation.ensure_admin([])
    win32.kernel32.CloseHandle.assert_called_once_with(123)


def test_missing_child_handle_fails_closed(win32):
    win32.shell32.ShellExecuteExW.side_effect = lambda pointer: 1
    with pytest.raises(RuntimeError, match='有效句柄'):
        elevation.ensure_admin([])
    win32.kernel32.WaitForSingleObject.assert_not_called()


def test_parent_exits_before_creating_data_or_server(win32, monkeypatch):
    choose = Mock()
    run = Mock()
    monkeypatch.setattr(desktop_app, 'choose_data_root', choose)
    monkeypatch.setattr(desktop_app, 'run_desktop', run)
    with pytest.raises(SystemExit) as result:
        desktop_app.main([])
    assert result.value.code == 7
    choose.assert_not_called()
    run.assert_not_called()
