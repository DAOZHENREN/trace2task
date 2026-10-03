"""Request normal Windows UAC elevation before opening the desktop controller."""
import ctypes
import os
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path


class ShellExecuteInfo(ctypes.Structure):
    _fields_ = [
        ('cbSize', wintypes.DWORD), ('fMask', wintypes.ULONG), ('hwnd', wintypes.HWND),
        ('lpVerb', wintypes.LPCWSTR), ('lpFile', wintypes.LPCWSTR),
        ('lpParameters', wintypes.LPCWSTR), ('lpDirectory', wintypes.LPCWSTR),
        ('nShow', ctypes.c_int), ('hInstApp', wintypes.HINSTANCE),
        ('lpIDList', ctypes.c_void_p), ('lpClass', wintypes.LPCWSTR),
        ('hkeyClass', wintypes.HKEY), ('dwHotKey', wintypes.DWORD),
        ('hIcon', wintypes.HANDLE), ('hProcess', wintypes.HANDLE),
    ]


def ensure_admin(arguments):
    """Return None when already elevated, otherwise the elevated child's exit code.

    Cancellation is an error: never silently continue at Medium integrity.
    No scheduled tasks, saved credentials, or UAC policy changes are involved.
    """
    shell = ctypes.windll.shell32
    if shell.IsUserAnAdmin():
        return None
    if '--elevated-child' in arguments:
        raise RuntimeError('管理员权限未生效，已停止启动；不会循环请求提权。')
    child_args = [*arguments, '--elevated-child']
    if not getattr(sys, 'frozen', False):
        # Shell elevation need not preserve PYTHONPATH. Pin the same source tree.
        source_root = str(Path(__file__).resolve().parents[1])
        bootstrap = (f'import sys; sys.path.insert(0, {source_root!r}); '
                     'from trace2task.desktop_app import main; main()')
        child_args = ['-c', bootstrap, *child_args]
    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x40  # SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = 'runas'
    info.lpFile = sys.executable
    info.lpParameters = subprocess.list2cmdline(child_args)
    info.lpDirectory = os.getcwd()
    info.nShow = 1  # The user explicitly launched the interactive application.
    execute = shell.ShellExecuteExW
    execute.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    execute.restype = wintypes.BOOL
    if not execute(ctypes.byref(info)):
        code = ctypes.windll.kernel32.GetLastError()
        if code == 1223:
            raise RuntimeError('已取消管理员授权；Trace2Task 未启动，也未降级为普通权限。')
        raise ctypes.WinError(code)
    if not info.hProcess:
        raise RuntimeError('管理员进程未返回有效句柄，无法确认启动结果。')
    kernel = ctypes.windll.kernel32
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    try:
        # Keep -Wait / smoke-test semantics; the launcher owns no server or mutex.
        if kernel.WaitForSingleObject(info.hProcess, 0xFFFFFFFF) != 0:
            raise ctypes.WinError(kernel.GetLastError())
        exit_code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(info.hProcess, ctypes.byref(exit_code)):
            raise ctypes.WinError(kernel.GetLastError())
        return exit_code.value
    finally:
        kernel.CloseHandle(info.hProcess)
