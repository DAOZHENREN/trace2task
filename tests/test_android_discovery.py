import pytest

from trace2task import android_discovery as discovery


@pytest.fixture(autouse=True)
def no_real_vendor_inspection(monkeypatch):
    monkeypatch.setattr(discovery, '_redfinger_ports', list)


def test_discovery_uses_existing_servers_only(monkeypatch):
    calls = []
    def host(port, command):
        calls.append((port, command))
        if port == 5038:
            raise ConnectionRefusedError()
        return '0029' if command == 'host:version' else (
            '127.0.0.1:54146 device product:x model:Cloud_Phone transport_id:7\n'
            'usb offline transport_id:8\nlocked unauthorized transport_id:9\n')
    monkeypatch.setattr(discovery, '_host', host)
    result = discovery.discover({'adb': '/tools/adb.exe', 'server_port': 5037})
    assert [d['state'] for d in result['devices']] == ['device', 'offline', 'unauthorized']
    assert result['devices'][0]['server_port'] == 5037
    assert result['devices'][0]['model'] == 'Cloud Phone'
    assert calls == [(5037, 'host:version'), (5037, 'host:devices-l'), (5038, 'host:version')]
    assert result['input_actions'] == result['model_calls'] == 0


def test_custom_port_and_incompatible_server_are_not_restarted(monkeypatch):
    calls = []
    def host(port, command):
        calls.append((port, command))
        return ('0020' if port == 5037 else '0029') if command == 'host:version' else ''
    monkeypatch.setattr(discovery, '_host', host)
    result = discovery.discover({'server_port': 5040})
    assert (5040, 'host:devices-l') in calls
    assert (5037, 'host:devices-l') not in calls
    assert any('版本不兼容' in w for w in result['warnings'])


@pytest.mark.parametrize('port', [0, 65536, '5037', True])
def test_invalid_ports_rejected(port):
    with pytest.raises(ValueError):
        discovery.discover({'server_port': port})


def test_app_inspection_is_bound_readonly_and_never_launches(tmp_path, monkeypatch):
    adb = tmp_path / 'adb.exe'
    adb.touch()
    monkeypatch.setattr(discovery, '_host', lambda *args: 'phone device transport_id:12')
    calls = []
    def shell(port, transport, command):
        calls.append((port, transport, command))
        if command.startswith('cmd package'):
            return '2 activities found:\n  Activity #0:\n    com.tencent.nrc/com.epicgames.ue4.GameActivity\n  Activity #1:\n    com.android.deskclock/.DeskClock\n'
        return 'mCurrentFocus=Window{abc u0 com.tencent.nrc/com.epicgames.ue4.GameActivity}'
    monkeypatch.setattr(discovery, '_shell', shell)
    result = discovery.inspect_device({'adb': str(adb), 'serial': 'phone', 'server_port': 5037})
    assert result['current_package'] == 'com.tencent.nrc'
    assert {'package': 'com.tencent.nrc', 'label': '洛克王国世界'} in result['apps']
    assert len(calls) == 2
    assert all(c[:2] == (5037, 12) for c in calls)
    assert result['input_actions'] == 0


def test_offline_selection_never_reads_apps(tmp_path, monkeypatch):
    adb = tmp_path / 'adb.exe'
    adb.touch()
    monkeypatch.setattr(discovery, '_host', lambda *args: 'phone offline transport_id:12')
    monkeypatch.setattr(discovery, '_shell', lambda *args: pytest.fail('must not access offline device'))
    with pytest.raises(RuntimeError, match='离线'):
        discovery.inspect_device({'adb': str(adb), 'serial': 'phone', 'server_port': 5037})


def test_smart_socket_handles_fragmented_response():
    class Sock:
        def __init__(self):
            self.parts = iter([b'O', b'K', b'A', b'Y'])
            self.sent = None
        def recv(self, count):
            return next(self.parts)
        def sendall(self, data):
            self.sent = data
    sock = Sock()
    discovery._request(sock, 'host:devices-l')
    assert sock.sent == b'000ehost:devices-l'


def test_vendor_channel_discovered_without_connect(monkeypatch):
    monkeypatch.setattr(discovery, '_redfinger_ports', lambda: [60378])
    def closed(*args):
        raise ConnectionRefusedError()
    monkeypatch.setattr(discovery, '_host', closed)
    result = discovery.discover()
    assert result['devices'][0]['source'] == 'redfinger'
    assert result['devices'][0]['serial'] == '127.0.0.1:60378'
    assert result['devices'][0]['state'] == 'forwarded'


def test_selection_attaches_only_current_vendor_port(tmp_path, monkeypatch):
    from types import SimpleNamespace
    adb = tmp_path / 'adb.exe'
    adb.touch()
    monkeypatch.setattr(discovery, '_redfinger_ports', lambda: [60378])
    monkeypatch.setattr(discovery, '_host', lambda *args: '0029')
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout=b'Android Debug Bridge version 1.0.41')
    monkeypatch.setattr(discovery.subprocess, 'run', run)
    monkeypatch.setattr(discovery, 'inspect_device', lambda value: value)
    config = {'adb': str(adb), 'serial': '127.0.0.1:60378', 'server_port': 5038}
    assert discovery.attach_redfinger(config)['serial'] == config['serial']
    assert calls == [[str(adb), 'version'], [str(adb), '-P', '5038', 'connect', config['serial']]]
    with pytest.raises(RuntimeError, match='通道已变化'):
        discovery.attach_redfinger({**config, 'serial': '127.0.0.1:11111'})
    assert len(calls) == 2


def test_busy_or_incompatible_dedicated_server_is_not_replaced(tmp_path, monkeypatch):
    adb = tmp_path / 'adb.exe'
    adb.touch()
    monkeypatch.setattr(discovery, '_redfinger_ports', lambda: [60378])
    monkeypatch.setattr(discovery.subprocess, 'run', lambda *a, **kw: pytest.fail('must not spawn adb'))
    config = {'adb': str(adb), 'serial': '127.0.0.1:60378', 'server_port': 5038}
    monkeypatch.setattr(discovery, '_host', lambda *args: '0020')
    with pytest.raises(RuntimeError, match='不兼容'):
        discovery.attach_redfinger(config)
    def timeout(*args):
        raise TimeoutError()
    monkeypatch.setattr(discovery, '_host', timeout)
    monkeypatch.setattr(discovery, '_dedicated_port_free', lambda: False)
    with pytest.raises(RuntimeError, match='无响应'):
        discovery.attach_redfinger(config)


def test_discovery_http_routes_require_csrf_and_do_not_create_jobs(tmp_path, monkeypatch):
    import json
    import threading
    from urllib.error import HTTPError
    from urllib.request import ProxyHandler, Request, build_opener

    from test_web_console import _csrf_headers

    from trace2task.web_console import create_web_server

    calls = []
    monkeypatch.setattr(discovery, 'discover', lambda value: calls.append('discover') or {'devices': []})
    monkeypatch.setattr(discovery, 'inspect_device', lambda value: calls.append('inspect') or {'apps': []})
    monkeypatch.setattr(discovery, 'attach_redfinger', lambda value: calls.append('attach') or {'apps': []})
    server = create_web_server(tmp_path, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    opener = build_opener(ProxyHandler({}))
    try:
        with pytest.raises(HTTPError):
            opener.open(Request(base + '/api/android/devices', data=b'{}'))
        assert not calls
        headers = {'Content-Type': 'application/json', **_csrf_headers(opener, base)}
        for path, body in [('devices', {}), ('device-apps', {}), ('device-apps', {'source': 'redfinger'})]:
            with opener.open(Request(base + '/api/android/' + path, data=json.dumps(body).encode(), headers=headers)) as response:
                assert response.status == 200
        assert calls == ['discover', 'inspect', 'attach']
        assert server.RequestHandlerClass.controller.active_job() is None
    finally:
        server.shutdown()
        server.server_close()
