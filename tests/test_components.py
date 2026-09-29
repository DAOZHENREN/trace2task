import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from trace2task import components
from trace2task.desktop_app import can_close


def test_saved_runtime_and_explicit_override(tmp_path, monkeypatch):
    monkeypatch.delenv('TRACE2TASK_GUI_PYTHON', raising=False)
    monkeypatch.delenv('TRACE2TASK_GUI_MODELS', raising=False)
    (tmp_path / 'components.json').write_text(json.dumps({'python': 'saved/python.exe', 'models': 'saved/models'}))
    assert components.gui_paths(tmp_path) == (Path('saved/python.exe'), Path('saved/models'))
    monkeypatch.setenv('TRACE2TASK_GUI_PYTHON', 'override/python.exe')
    assert components.gui_paths(tmp_path)[0] == Path('override/python.exe')


def test_invalid_request_never_starts(tmp_path):
    manager = components.ComponentManager(tmp_path)
    for action, directory, model in [('oops', '', ''), ('runtime', 'relative', ''),
                                     ('runtime', tmp_path.anchor, ''), ('model', '', 'untrusted')]:
        with pytest.raises(ValueError):
            manager.start(action, directory, model)
    assert manager.state['status'] == 'idle'


def test_duplicate_and_cancel(tmp_path):
    manager = components.ComponentManager(tmp_path)
    manager.state['status'] = 'running'
    with pytest.raises(RuntimeError):
        manager.start('runtime')
    manager.start('cancel')
    assert manager.cancelled.is_set()


@pytest.mark.parametrize('fail,cancel', [(False, False), (True, False), (False, True)])
def test_publish_only_after_cuda_check(tmp_path, monkeypatch, fail, cancel):
    root = tmp_path / 'app'
    (root / 'vendor').mkdir(parents=True)
    (root / 'vendor/uv.exe').touch()
    monkeypatch.setattr(components, 'app_root', lambda: root)
    monkeypatch.setattr(components, 'cached_torch', lambda *_: None)
    monkeypatch.setattr(components.shutil, 'disk_usage', lambda _: SimpleNamespace(free=20 * 1024**3))
    saved = {'python': 'old/python.exe', 'models': 'old/models'}
    path = tmp_path / 'components.json'
    path.write_text(json.dumps(saved))
    manager = components.ComponentManager(tmp_path)
    commands = []
    def run(args, env, log):
        commands.append(list(map(str, args)))
        assert env['UV_PYTHON_INSTALL_DIR'].startswith(str(tmp_path))
        if '-I' in args:
            assert 'torch.cuda.synchronize' in args[-1]
            if fail:
                raise RuntimeError('no CUDA')
            if cancel:
                manager.cancelled.set()
    monkeypatch.setattr(manager, '_run', run)
    manager._work('runtime', tmp_path / 'components', '')
    after = json.loads(path.read_text())
    if fail or cancel:
        assert after == saved
    else:
        assert after['python'] != saved['python']
        assert after['models'] == saved['models']
        assert '--managed-python' in commands[1]


def test_close_blocks_component_install():
    controller = SimpleNamespace(components=SimpleNamespace(status=lambda: {'status': 'running'}))
    window = SimpleNamespace(create_confirmation_dialog=lambda *args: True)
    assert can_close(controller, window) is False


def test_d_import_refuses_untrusted_checkpoint(tmp_path):
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    for name in ('step-00005970.pt', 'frozen-launch.json', 'source', 'weights', 'processor'):
        (bundle / name).touch()
    manager = components.ComponentManager(tmp_path)
    manager._work('import_d', bundle, '')
    assert manager.state['status'] == 'failed'
    assert 'SHA256' in manager.state['message']
    assert not (tmp_path / 'components.json').exists()


def test_component_http_requires_csrf(tmp_path):
    import re
    import threading
    from urllib.error import HTTPError
    from urllib.request import ProxyHandler, Request, build_opener

    from trace2task.web_console import create_web_server

    server = create_web_server(tmp_path, port=0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    url = f'http://127.0.0.1:{server.server_address[1]}'
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(url + '/api/components') as response:
            assert json.load(response)['status'] == 'idle'
        with pytest.raises(HTTPError):
            opener.open(Request(url + '/api/components', data=b'{"action":"cancel"}',
                                headers={'Content-Type': 'application/json'}))
        with opener.open(url) as response:
            token = re.search('name="trace2task-csrf" content="([^"]+)"', response.read().decode())[1]
        with opener.open(Request(url + '/api/components', data=b'{"action":"cancel"}',
                                 headers={'Content-Type': 'application/json',
                                          'X-Trace2Task-CSRF': token, 'Origin': url})) as response:
            assert json.load(response)['status'] == 'idle'
        with opener.open(url + '/components.js') as response:
            assert response.status == 200
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
