import json
import re
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from trace2task import chatgpt_browser as browser
from trace2task.web_console import WebConsoleController, create_web_server


@pytest.mark.parametrize('url, expected', [
    ('https://chatgpt.com/c/private-conversation?code=private#secret', 'chatgpt.com'),
    ('https://auth.openai.com/callback?code=private', 'auth.openai.com'),
    ('file:///C:/private.html', ''), ('http://127.0.0.1:8765/', ''), ('https://[invalid', ''),
])
def test_status_contains_only_https_hostname(url, expected):
    assert browser.page_host(url) == expected


def test_browser_lifecycle_reuses_child_and_preserves_profile(tmp_path, monkeypatch):
    """Exercise pipe EOF, native-close reporting and reopen using a real child."""
    spawn = subprocess.Popen
    children = []

    def fake_window(command, **options):
        child = spawn([sys.executable, '-u', '-c', '''
import json, sys
def emit(status):
    print(json.dumps(dict(status=status, navigation='ready', host='chatgpt.com', error='')), flush=True)
emit('open')
for command in sys.stdin:
    if command.strip() == 'close':
        break
emit('closed')
'''], **options)
        children.append(child)
        return child

    monkeypatch.setattr(browser.sys, 'platform', 'win32')
    monkeypatch.setattr(browser.subprocess, 'Popen', fake_window)
    owner = browser.ChatGPTBrowser(tmp_path)
    owner.profile.mkdir(parents=True)
    saved = owner.profile / 'existing-profile-marker'
    saved.write_text('preserve', encoding='utf-8')
    try:
        owner.open()
        deadline = time.monotonic() + 5
        while owner.snapshot()['status'] == 'starting' and time.monotonic() < deadline:
            time.sleep(.01)
        assert owner.snapshot()['status'] == 'open'
        assert owner.snapshot()['model_connection'] == 'unverified'
        owner.open()
        assert len(children) == 1
        assert owner.close()['running'] is False
        assert children[0].poll() == 0
        owner.open()
        assert len(children) == 2
        assert saved.read_text(encoding='utf-8') == 'preserve'
    finally:
        owner.close()


def test_native_profile_is_persistent_without_a_python_api(tmp_path, monkeypatch):
    class Event:
        def __init__(self):
            self.handlers = []

        def __iadd__(self, handler):
            self.handlers.append(handler)
            return self

    events = SimpleNamespace(before_show=Event(), closed=Event())
    native = SimpleNamespace(webview=SimpleNamespace(NavigationStarting=Event(), NavigationCompleted=Event()))
    window = SimpleNamespace(events=events, native=native, destroy=Mock(), evaluate_js=Mock(),
                             load_url=Mock())
    created = Mock(return_value=window)
    observed = []

    def start(*args, **kwargs):
        assert kwargs['private_mode'] is False
        assert kwargs['storage_path'] == str(tmp_path.resolve())
        assert kwargs['gui'] == 'edgechromium'
        assert kwargs['debug'] is False
        for handler in events.before_show.handlers:
            handler()
        for handler in native.webview.NavigationStarting.handlers:
            navigation = SimpleNamespace(Uri='https://auth.openai.com/login?secret=token', Cancel=False)
            handler(None, navigation)
            assert navigation.Cancel is False
            assert native.Text.endswith('auth.openai.com')
            blocked = SimpleNamespace(Uri='file:///C:/private.html', Cancel=False)
            handler(None, blocked)
            assert blocked.Cancel is True
        for handler in native.webview.NavigationCompleted.handlers:
            handler(SimpleNamespace(Source='https://chatgpt.com/'),
                    SimpleNamespace(IsSuccess=False, HttpStatusCode=0, WebErrorStatus='CannotConnect'))
            assert observed[-1]['navigation'] == 'failed'
            handler(SimpleNamespace(Source='https://chatgpt.com/'),
                    SimpleNamespace(IsSuccess=True, HttpStatusCode=403))
            assert observed[-1]['navigation'] == 'failed'
            handler(SimpleNamespace(Source='https://chatgpt.com/'),
                    SimpleNamespace(IsSuccess=True, HttpStatusCode=200))
            assert observed[-1]['navigation'] == 'ready'

    monkeypatch.setitem(sys.modules, 'webview.menu', SimpleNamespace(Menu=Mock(), MenuAction=Mock()))
    viewer = SimpleNamespace(settings={}, create_window=created, start=start)
    marker = tmp_path / 'keep-session'
    marker.write_text('existing', encoding='utf-8')
    browser.run_browser(tmp_path, emit=lambda **event: observed.append(event), webview_module=viewer)
    assert 'js_api' not in created.call_args.kwargs
    assert viewer.settings['IGNORE_SSL_ERRORS'] is False
    assert 'token' not in json.dumps(observed)
    assert marker.read_text(encoding='utf-8') == 'existing'


def test_browser_http_controls_require_console_origin_and_shutdown_owned_child(tmp_path):
    controller = WebConsoleController(tmp_path)
    owner = SimpleNamespace(snapshot=Mock(return_value={'status': 'closed', 'running': False}),
                            open=Mock(return_value={'status': 'starting'}),
                            close=Mock(return_value={'status': 'closed'}))
    controller.chatgpt_browser = owner
    server = create_web_server(tmp_path, port=0, controller=controller)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(base + '/api/chatgpt-browser') as response:
            assert json.loads(response.read())['running'] is False
        owner.open.assert_not_called()
        with opener.open(base + '/') as response:
            html = response.read().decode()
        token = re.search(r'name="trace2task-csrf" content="([^"]+)"', html).group(1)

        def post(action, **headers):
            return opener.open(Request(base + '/api/chatgpt-browser',
                                       data=json.dumps({'action': action}).encode(),
                                       headers={'Content-Type': 'application/json', **headers}))

        with pytest.raises(HTTPError) as rejected:
            post('open', Origin='https://chatgpt.com')
        assert rejected.value.code == 400
        rejected.value.close()
        owner.open.assert_not_called()
        headers = {'Origin': base, 'X-Trace2Task-CSRF': token}
        with post('open', **headers) as response:
            assert json.loads(response.read())['status'] == 'starting'
        owner.open.assert_called_once()
        controller.active_job = lambda: {'status': 'running'}
        with pytest.raises(HTTPError) as busy:
            post('open', **headers)
        assert busy.value.code == 409
        busy.value.close()
        owner.open.assert_called_once()
        with post('close', **headers):
            pass
        owner.close.assert_called_once()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert owner.close.call_count == 2


def test_packaged_browser_dispatch_does_not_start_task_controller(monkeypatch):
    from trace2task import desktop_app

    launch = Mock()
    monkeypatch.setattr(browser, 'main', launch)
    desktop_app.main(['--chatgpt-browser', '--profile', 'profile', '--stdio'])
    launch.assert_called_once_with(['--profile', 'profile', '--stdio'])
