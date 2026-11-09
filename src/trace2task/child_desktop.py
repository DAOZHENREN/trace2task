"""Run the existing Cua protocol against a daemon owned by a Windows RDP child session."""
import ctypes
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from ctypes import wintypes
from pathlib import Path

from PIL import Image

from trace2task.cua_backend import CuaBackend
from trace2task.execution_protocol import ObservationStale


def child_session_id():
    result = wintypes.ULONG()
    wts = ctypes.WinDLL('wtsapi32', use_last_error=True)
    if not wts.WTSGetChildSessionId(ctypes.byref(result)):
        error = ctypes.get_last_error()
        if error == 1168:
            return None
        raise ctypes.WinError(error)
    return None if result.value == 0xffffffff else result.value


def windows_session_exists(session_id):
    # WTSGetChildSessionId can still return the last ID after logoff. Query
    # the recorded session directly; a disconnected logon still exists.
    wts = ctypes.WinDLL('wtsapi32', use_last_error=True)
    buffer = ctypes.c_void_p()
    size = wintypes.DWORD()
    if wts.WTSQuerySessionInformationW(None, session_id, 8, ctypes.byref(buffer), ctypes.byref(size)):
        wts.WTSFreeMemory(buffer)
        return True
    error = ctypes.get_last_error()
    if error in (2, 7022):  # ERROR_FILE_NOT_FOUND / ERROR_CTX_WINSTATION_NOT_FOUND
        return False
    raise ctypes.WinError(error)


class _ChildDesktopHost:
    """The workbench-owned RDP connection and its private daemon."""

    def __init__(self, driver):
        self.driver = driver.resolve()
        self.nonce = uuid.uuid4().hex
        self.pipe = r'\\.\pipe\trace2task-child-' + self.nonce
        self.session_id = None
        self.bound_session_id = None
        self.driver_pid = None
        self.viewer_hwnd = None
        self.has_picture = False
        self.process = None
        self.host_dir = None
        self._terminate_requested = False

    def start(self, client):
        if os.name != 'nt' or not self.driver.is_file():
            raise RuntimeError('Windows 后台桌面需要 Windows 和本机 Cua Driver')
        client.check_stop()
        self.host_dir = tempfile.TemporaryDirectory(prefix='trace2task-child-', ignore_cleanup_errors=True)
        executable = Path(self.host_dir.name) / 'ChildDesktopHost.exe'
        compiler = Path(os.environ['SystemRoot']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        source = Path(__file__).resolve().parent / 'native' / 'ChildDesktopHost.cs'
        if not compiler.is_file():
            raise RuntimeError('找不到 Windows 自带的 64 位 .NET Framework C# 编译器')
        subprocess.run([str(compiler), '/nologo', '/target:exe', '/r:System.Windows.Forms.dll',
                        '/r:System.Drawing.dll', '/r:Microsoft.CSharp.dll', f'/out:{executable}', str(source)],
                       check=True, capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        client.check_stop()
        log = Path(self.host_dir.name) / 'child-desktop.log'
        with log.open('wb') as output:
            self.process = subprocess.Popen([str(executable), str(self.driver), self.nonce,
                'login' if client.show_login else 'silent'],
                stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT, env=client.env,
                creationflags=subprocess.CREATE_NO_WINDOW)
        deadline = time.monotonic() + (195 if client.show_login else 85)
        with log.open('r', encoding='utf-8-sig', errors='replace') as output:
            while time.monotonic() < deadline:
                client.check_stop()
                # Sample process exit before draining the log so the final ERROR
                # cannot land between an EOF read and our exit check.
                exited = self.process.poll() is not None
                for line in output:
                    line = line.rstrip()
                    if line.startswith('RECONNECT '):
                        _, session, nonce = line.split()
                        self.nonce = uuid.UUID(hex=nonce).hex
                        self.pipe = r'\\.\pipe\trace2task-child-' + self.nonce
                        client.progress(f'正在重新连接已保留的后台桌面 {int(session)}。')
                    elif line.startswith('BOUND '):
                        self.bound_session_id = int(line.split()[1])
                    elif line == 'CONNECTED':
                        client.progress('已连接后台桌面，正在等待 Windows 登录。')
                    elif line == 'LOGGED_IN':
                        deadline = time.monotonic() + 45
                        client.progress('Windows 已登录后台桌面，正在连接 Cua 驱动。')
                    if line.startswith('ERROR '):
                        detail = line[6:].strip()
                        if detail == 'RDP_LOGIN_TIMEOUT':
                            raise RuntimeError('等待 Windows 登录后台桌面超时，尚未取得桌面画面。')
                        if detail.startswith('RDP_KEYBOARD_SLOTS '):
                            session = detail.split()[-1]
                            raise RuntimeError(
                                f'Windows 后台桌面 {session} 的虚拟键盘启动失败。检测到 Interception 键盘过滤驱动'
                                '和已占用的 10 个键盘编号。请保存工作并重新启动 Windows，登录后再启动后台桌面。'
                                '保持工作台打开，后续任务会复用同一桌面。')
                        if 'already occupied' in detail:
                            raise RuntimeError('Windows 子会话已被其他任务占用')
                        if 'sessions are disabled' in detail:
                            raise RuntimeError('Windows 子会话未启用，请管理员启用 Child Sessions 后再试')
                        if 'before Windows finished initializing' in detail:
                            raise RuntimeError('Windows 在子桌面就绪前断开了连接，任务未发送输入。' + detail)
                        raise RuntimeError('Windows 后台桌面启动失败：' + detail)
                    if line.startswith('READY '):
                        _, session, pid, hwnd = line.split()
                        self.session_id = int(session)
                        self.driver_pid = int(pid)
                        self.viewer_hwnd = int(hwnd)
                        if not self.connected():
                            raise RuntimeError('Windows 后台桌面连接已中断')
                        client.progress(f'后台桌面 {self.session_id} 的 Cua 驱动已连接，正在获取画面。')
                        return
                if exited:
                    raise RuntimeError('Windows 后台桌面已退出，请查看 child-desktop.log')
                client.stop.sleep(.15)
        raise RuntimeError('等待 Windows 子会话登录超时，请检查本地登录策略和任务日志')

    def connected(self):
        return (self.process is not None and self.process.poll() is None and self.session_id is not None
                and child_session_id() == self.session_id)

    def viewer(self, mode):
        if not self.connected() or not self.viewer_hwnd:
            raise RuntimeError('后台桌面已断开，请重新连接')
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        hwnd = wintypes.HWND(self.viewer_hwnd)
        owner = wintypes.DWORD()
        if not user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner)) or owner.value != self.process.pid:
            raise RuntimeError('后台桌面窗口已变化，请刷新')
        if mode in ('view', 'control'):
            user32.AllowSetForegroundWindow(owner.value)
        reply = ctypes.c_size_t()
        if (not user32.SendMessageTimeoutW(hwnd, 0x84D2, {'view': 1, 'control': 2, 'return': 3, 'hide': 4}[mode],
                                           0, 2, 4000, ctypes.byref(reply)) or reply.value != 1):
            raise RuntimeError('后台桌面窗口未确认切换，请重试')

    def copy_log(self, root):
        if self.host_dir:
            log = Path(self.host_dir.name) / 'child-desktop.log'
            if log.is_file():
                shutil.copyfile(log, root / log.name)

    def close(self):
        # The native host also cleans up on EOF if the Python process exits unexpectedly.
        if self.process:
            self.process.stdin.close()
            timed_out = False
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                timed_out = True
            if not (self.session_id or self.bound_session_id) and self.host_dir:
                # Cancellation may precede the startup reader consuming BOUND.
                log = Path(self.host_dir.name) / 'child-desktop.log'
                if log.is_file():
                    for line in log.read_text(encoding='utf-8-sig', errors='replace').splitlines():
                        fields = line.split()
                        if len(fields) >= 2 and fields[0] in ('BOUND', 'READY') and fields[1].isascii() and fields[1].isdigit():
                            self.bound_session_id = int(fields[1])
                            break
            if timed_out and not (self.session_id or self.bound_session_id):
                self.process.kill()
                self.process.wait(timeout=5)
                if self.host_dir:
                    self.host_dir.cleanup()
                raise RuntimeError('宿主退出超时，启动期间未取得可核对的后台会话编号')
        return self.check_closed()

    def check_closed(self):
        session_id = self.session_id or self.bound_session_id
        if session_id:
            try:
                if windows_session_exists(session_id):
                    return 'logoff_pending'
            except OSError:
                return 'logoff_unknown'
        # Only reap a slow helper after Windows confirms its session is gone.
        if self.process and self.process.poll() is None:
            if not self._terminate_requested:
                self.process.kill()
                self._terminate_requested = True
            return 'closing'
        if self.host_dir:
            self.host_dir.cleanup()
        return None


class ChildDesktopWorkspace:
    """Own one child desktop across tasks. Only the current task can send input."""

    def __init__(self):
        self._state = threading.Lock()
        self._io = threading.RLock()
        self._host = None
        self._retiring = None
        self._task = None
        self._generation = 0
        self._closing = False
        self._shutdown = threading.Event()
        self._agent_turn = threading.Event()
        self._agent_turn.set()
        self._revision = 0

    def status(self):
        with self._state:
            pending = self._check_retiring()
            if pending:
                return {'status': pending, 'session_id': self._retiring.session_id or self._retiring.bound_session_id,
                        'generation': self._generation}
            host = self._host
            if self._closing:
                state = 'closing'
            elif self._task is not None:
                state = 'in_use' if host and host.has_picture and self._task.session_id else 'connecting'
            elif host is None:
                state = 'closed'
            elif host.session_id is None:
                state = 'connecting'
            elif host.process is None or host.process.poll() is not None:
                state = 'disconnected'
            else:
                state = 'idle' if host.has_picture else 'waiting_picture'
            return {'status': state, 'session_id': host.session_id if host else None,
                    'generation': self._generation, **({} if self._agent_turn.is_set() else {'manual_control': True})}

    def _check_retiring(self):
        # Caller holds _state. A refresh checks the original ID without
        # issuing another logoff (Windows can reuse session numbers).
        if self._retiring:
            pending = self._retiring.check_closed()
            if pending:
                return pending
            self._retiring = None

    def _drop_host(self):
        with self._state:
            host = self._host
            self._agent_turn.set()
        if host:
            # Retain a confirmed identity even if local helper cleanup raises.
            pending = host.session_id or host.bound_session_id
            try:
                pending = host.close()
            finally:
                with self._state:
                    self._host = None
                    if pending:
                        self._retiring = host

    def _connect(self, client):
        if self._host and not self._host.connected():
            self._drop_host()
        if self._host:
            return True
        with self._state:
            if self._check_retiring():
                raise RuntimeError('上一后台会话的注销尚未确认，请在后台桌面管理中刷新状态')
            self._host = host = _ChildDesktopHost(client.driver)
            self._generation += 1
        client.progress('正在连接 Windows 后台桌面；如果 Windows 要求验证，请在系统窗口确认。')
        try:
            host.start(client)
        except BaseException:
            try:
                host.copy_log(client.root)
            finally:
                try:
                    self._drop_host()
                except Exception as error:  # noqa: BLE001 - preserve the original startup failure
                    client.record('cleanup_error', error=str(error))
            raise
        return False

    def present(self, mode, generation):
        if mode not in ('view', 'control', 'return', 'hide'):
            raise ValueError('未知后台桌面操作')
        with self._io:
            with self._state:
                if self._closing or self._shutdown.is_set() or type(generation) is not int or generation != self._generation:
                    raise ValueError('后台桌面已变化，请刷新')
            if self._host is None or not self._host.connected():
                if mode not in ('view', 'control') or self._task is not None:
                    raise RuntimeError('后台桌面尚未就绪')
                # Opening the native viewer never creates a model task.
                with tempfile.TemporaryDirectory(prefix='trace2task-view-') as folder:
                    client = ChildDesktopDriver(folder, lambda *a, **k: None, _ViewerStop(self._shutdown), workspace=self)
                    self._connect(client)
            human = self._agent_turn.is_set() and mode == 'control'
            if human:
                self._agent_turn.clear()
                self._revision += 1
            # On an uncertain acknowledgement keep the executor paused until an
            # explicit hand-back has disabled the native client input.
            self._host.viewer(mode)
            if mode == 'return':
                self._agent_turn.set()
            if self._task and (human or mode == 'return'):
                self._task.progress('用户已接管后台桌面，模型操作已暂停。' if human else '用户已交还后台桌面，模型将重新观察。')
            return self.status()

    def acquire(self, client):
        with self._state:
            if self._closing or self._shutdown.is_set():
                raise RuntimeError('后台桌面正在关闭')
            if self._task is not None:
                raise RuntimeError('后台桌面已有任务正在使用')
            self._task = client
        with self._io:
            host = None
            try:
                if self._task is not client:
                    raise RuntimeError('后台桌面任务已结束')
                client.check_stop()
                reused = self._connect(client)
                host = self._host
                if not self._agent_turn.is_set():
                    host.viewer('return')
                    self._agent_turn.set()
                client.driver, client.pipe, client.session_id = host.driver, host.pipe, host.session_id
                if os.name == 'nt' and host.host_dir and ctypes.windll.shell32.IsUserAnAdmin():
                    client.cli_host = Path(host.host_dir.name) / 'ChildDesktopHost.exe'
                client._wait_for_picture(host, require_drawn=not host.has_picture)
                host.has_picture = True
                client.record('child_desktop_ready', session_id=host.session_id,
                              driver_pid=host.driver_pid, reused=reused)
                client.progress(f'已{"复用" if reused else "启动"}后台桌面 {host.session_id}，已取得本任务的首张截图。',
                                screenshot=str((client.root / 'child-ready.png').resolve()))
                return reused
            except BaseException:
                try:
                    try:
                        self.release(client)
                    finally:
                        if host and host.session_id is None:
                            self._drop_host()
                except Exception as cleanup_error:  # noqa: BLE001 - preserve the original connection failure
                    client.record('cleanup_error', error=str(cleanup_error))
                raise

    def release(self, client):
        with self._io:
            if self._task is not client:
                return
            try:
                if self._host:
                    self._host.copy_log(client.root)
            finally:
                with self._state:
                    client.session_id = None
                    self._task = None

    def call(self, client, tool, payload):
        while True:
            with self._io:
                if self._task is not client or self._host is None:
                    raise RuntimeError('本任务已结束或尚未取得后台桌面，未发送新操作')
                client.check_stop()
                if client.session_id != self._host.session_id or not self._host.connected():
                    raise RuntimeError('后台桌面连接已中断，未发送新操作')
                if self._agent_turn.is_set():
                    if tool == 'get_desktop_state':
                        client.frame_revision = self._revision
                    elif client.frame_revision != self._revision:
                        raise ObservationStale('人工接管后画面已变化，请重新截图规划')
                    return CuaBackend.call(client, tool, payload)
            self._agent_turn.wait(.15)

    def close_idle(self, generation):
        with self._state:
            if self._task or self._closing or self._shutdown.is_set():
                raise RuntimeError('请等待当前后台桌面任务结束后再关闭')
            if not self._host or type(generation) is not int or generation != self._generation:
                raise ValueError('后台桌面已变化，请刷新后再关闭')
            self._closing = True
        try:
            with self._io:
                self._drop_host()
        finally:
            with self._state:
                self._closing = False
        return self.status()

    def shutdown(self):
        self._shutdown.set()
        with self._io:
            try:
                if self._task is not None:
                    self.release(self._task)
            finally:
                self._drop_host()


class _ViewerStop:
    def __init__(self, shutdown):
        self.shutdown = shutdown

    def raise_if_requested(self):
        if self.shutdown.is_set():
            raise RuntimeError('工作台正在退出')

    def sleep(self, seconds):
        self.shutdown.wait(seconds)
        self.raise_if_requested()


class ChildDesktopDriver(CuaBackend):
    """Per-run capture, cancellation and audit on a shared Windows desktop."""

    def __init__(self, root, record, stop, *, workspace=None, show_login=True, on_progress=None):
        super().__init__(root, record)
        self.stop = stop
        self.workspace = workspace if workspace is not None else ChildDesktopWorkspace()
        self._private_workspace = workspace is None
        self.show_login = show_login
        self.on_progress = on_progress
        self.session_id = None
        self.cli_host = None
        self.frame_revision = None

    def call_command(self, tool):
        if self.cli_host:
            return [str(self.cli_host), 'call', str(self.driver), tool, self.workspace._host.nonce]
        return super().call_command(tool)

    def progress(self, message, *, screenshot=None):
        self.record('child_desktop_progress', message=message, **({'screenshot': screenshot} if screenshot else {}))
        if self.on_progress:
            self.on_progress(message, screenshot)

    def check_stop(self):
        if self.workspace._shutdown.is_set():
            raise RuntimeError('工作台正在退出，后台桌面已停止接受操作')
        self.stop.raise_if_requested()

    def start(self):
        return self.workspace.acquire(self)

    def _wait_for_picture(self, host, *, require_drawn):
        # Winlogon can report success several seconds before Explorer/DWM can draw a frame.
        path = (self.root / 'child-ready.png').resolve()
        deadline = time.monotonic() + 35
        reason = '桌面尚未绘制'
        while True:
            self.check_stop()
            try:
                state = self.call('get_desktop_state', {'session': self.root.name, 'screenshot_out_file': str(path)})
                with Image.open(path) as frame:
                    if (state.get('display') == 'primary'
                            and frame.size == (state.get('screen_width'), state.get('screen_height'))
                            and (not require_drawn or any(lo != hi for lo, hi in frame.convert('RGB').getextrema()))):
                        return
            except (RuntimeError, OSError) as error:
                if not host.connected():
                    raise
                reason = str(error)
            if time.monotonic() >= deadline:
                raise RuntimeError('后台桌面登录成功，但截图未就绪：' + reason)
            self.stop.sleep(.75)

    def call(self, tool, payload):
        return self.workspace.call(self, tool, payload)

    def close(self):
        try:
            self.workspace.release(self)
        finally:
            if self._private_workspace:
                self.workspace.shutdown()
