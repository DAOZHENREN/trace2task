"""A persistent ChatGPT WebView2 window, separate from the task executor.

The child owns its browser profile. Its parent receives navigation status only,
over anonymous pipes; cookies, page contents and login tokens are not exported.
This module does not implement a ChatGPT inference provider.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

from trace2task.local_process import stop_started_process

CHATGPT_URL = 'https://chatgpt.com/'


def page_host(url):
    """Never publish conversation paths, OAuth codes or URL fragments."""
    try:
        parsed = urlsplit(str(url))
        return (parsed.hostname or '') if parsed.scheme == 'https' else ''
    except ValueError:
        return ''


class ChatGPTBrowser:
    def __init__(self, project_root):
        self.profile = Path(project_root).resolve() / 'auth/chatgpt-browser'
        self._lock = threading.RLock()
        self._process = None
        self._reader = None
        self._state = {'status': 'closed', 'navigation': 'idle', 'host': '', 'error': ''}

    def snapshot(self):
        with self._lock:
            running = (self._process is not None and self._process.poll() is None
                       and self._state['status'] != 'closed')
            state = dict(self._state)
            if not running and state['status'] not in {'closed', 'error'}:
                state.update(status='closed', navigation='idle')
            return {**state, 'running': running, 'profile_present': self.profile.is_dir(),
                    'model_connection': 'unverified'}

    def open(self):
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                if self._state['status'] != 'closed':
                    self._send('show')
                    return self.snapshot()
                try:
                    self._process.wait(timeout=2)
                except subprocess.TimeoutExpired as error:
                    raise RuntimeError('浏览器正在退出，请稍后重新打开') from error
            if sys.platform != 'win32':
                raise RuntimeError('ChatGPT 内置浏览器目前需要 Windows WebView2')
            self._close_streams()
            args = ['--profile', str(self.profile), '--stdio']
            if getattr(sys, 'frozen', False):
                command = [sys.executable, '--chatgpt-browser', *args]
            else:
                source = str(Path(__file__).resolve().parents[1])
                bootstrap = (f'import sys; sys.path.insert(0, {source!r}); '
                             'from trace2task.chatgpt_browser import main; main()')
                command = [sys.executable, '-u', '-c', bootstrap, *args]
            self._state = {'status': 'starting', 'navigation': 'loading',
                           'host': 'chatgpt.com', 'error': ''}
            try:
                self._process = subprocess.Popen(
                    command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, text=True, encoding='utf-8', bufsize=1,
                    env={**os.environ, 'PYTHONUTF8': '1'},
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
            except OSError:
                self._state.update(status='error', error='无法启动内置浏览器进程')
                raise
            self._reader = threading.Thread(target=self._read, args=(self._process,),
                                            daemon=True, name='chatgpt-browser-status')
            self._reader.start()
            return self.snapshot()

    def _read(self, process):
        try:
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict) or event.get('status') not in {
                        'starting', 'open', 'closed', 'error'}:
                    continue
                with self._lock:
                    if process is not self._process:
                        return
                    self._state = {
                        'status': event['status'],
                        'navigation': event.get('navigation') if event.get('navigation') in {
                            'idle', 'loading', 'ready', 'failed'} else 'idle',
                        'host': page_host('https://' + str(event.get('host', ''))),
                        'error': str(event.get('error', ''))[:240],
                    }
        finally:
            with self._lock:
                if process is self._process and self._state['status'] not in {'closed', 'error'}:
                    self._state.update(status='error', navigation='failed',
                                       error='浏览器进程意外退出，请重新打开窗口')

    def _send(self, command):
        try:
            self._process.stdin.write(command + '\n')
            self._process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as error:
            raise RuntimeError('浏览器连接已关闭，请稍后重新打开') from error

    def _close_streams(self):
        if self._process is not None:
            for stream in (self._process.stdin, self._process.stdout):
                if stream is not None:
                    stream.close()
        self._process = None

    def close(self):
        # Let WebView2 flush cookies and storage before resorting to tree cleanup.
        with self._lock:
            process = self._process
            if process is not None and process.poll() is None:
                try:
                    self._send('close')
                except RuntimeError:
                    pass
        if process is not None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                stop_started_process(process)
                process.wait(timeout=5)
        with self._lock:
            if process is self._process:
                self._close_streams()
                self._state = {'status': 'closed', 'navigation': 'idle', 'host': '', 'error': ''}
        return self.snapshot()


def run_browser(profile, *, emit, commands=None, webview_module=None):
    if webview_module is None:
        import webview as webview_module
    from webview.menu import Menu, MenuAction

    profile = Path(profile).resolve()
    profile.mkdir(parents=True, exist_ok=True)
    webview_module.settings.update(OPEN_EXTERNAL_LINKS_IN_BROWSER=False, ALLOW_FILE_URLS=False,
                                   ALLOW_DOWNLOADS=False, IGNORE_SSL_ERRORS=False)
    window = webview_module.create_window('Trace2Task · ChatGPT — chatgpt.com', CHATGPT_URL,
                                          width=1000, height=780, min_size=(640, 480),
                                          text_select=True)

    def attach_navigation():
        # before_show runs on the WebView2 UI thread, after the native control exists.
        native = window.native

        def starting(sender, args):
            host = page_host(args.Uri)
            if not host:
                args.Cancel = True
                emit(status='open', navigation='failed', host='', error='仅支持 HTTPS 网页')
                return
            native.Text = 'Trace2Task · ChatGPT — ' + host
            emit(status='open', navigation='loading', host=host, error='')

        def completed(sender, args):
            host = page_host(sender.Source)
            success = args.IsSuccess and (not args.HttpStatusCode or args.HttpStatusCode < 400)
            error = '' if success else (f'页面加载失败：HTTP {args.HttpStatusCode}'
                                       if args.IsSuccess else f'页面加载失败：{args.WebErrorStatus}')
            emit(status='open', navigation='ready' if success else 'failed', host=host, error=error)

        native.webview.NavigationStarting += starting
        native.webview.NavigationCompleted += completed

    window.events.before_show += attach_navigation
    window.events.closed += lambda: emit(status='closed', navigation='idle', host='', error='')

    def read_commands():
        for line in commands:
            if line.strip() == 'show':
                window.restore()
                window.show()
            elif line.strip() == 'close':
                window.destroy()
                return
        window.destroy()  # Parent exit/pipe EOF closes only this owned window.

    menu = [Menu('浏览', [
        MenuAction('后退', lambda: window.evaluate_js('history.back()')),
        MenuAction('前进', lambda: window.evaluate_js('history.forward()')),
        MenuAction('刷新', lambda: window.evaluate_js('location.reload()')),
        MenuAction('ChatGPT 首页', lambda: window.load_url(CHATGPT_URL)),
        MenuAction('关闭窗口（保留登录）', window.destroy),
    ])]
    def start_reader():
        # pywebview's start callback uses a non-daemon thread. Do not leave it
        # waiting on stdin after the user closes the native window with X.
        threading.Thread(target=read_commands, daemon=True, name='chatgpt-browser-commands').start()

    webview_module.start(start_reader if commands is not None else None,
                         gui='edgechromium', debug=False, private_mode=False,
                         storage_path=str(profile), menu=menu)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--stdio', action='store_true')
    args = parser.parse_args(argv)
    lock = threading.Lock()

    def emit(**event):
        if args.stdio:
            with lock:
                try:
                    print(json.dumps(event, ensure_ascii=False), flush=True)
                except OSError:
                    pass

    try:
        run_browser(args.profile, emit=emit, commands=sys.stdin if args.stdio else None)
    except Exception as error:
        emit(status='error', navigation='failed', host='',
             error='内置浏览器启动失败：' + type(error).__name__)
        raise


if __name__ == '__main__':
    main()
