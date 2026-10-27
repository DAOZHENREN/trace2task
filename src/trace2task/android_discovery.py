"""Read existing loopback ADB servers without starting/restarting their daemons."""
import json
import os
import re
import shutil
import socket
import subprocess
from pathlib import Path

from trace2task.android_workbench import validate_config

DEFAULT_ADB = 'D:/Tools/trace2task-android/sdk/platform-tools/adb.exe'


def _powershell(script):
    powershell = str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
    return subprocess.run([powershell, '-NoProfile', '-NonInteractive', '-Command', script],
                          check=True, capture_output=True, timeout=8,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout


def _redfinger_ports():
    """OS socket metadata only, not window scraping or a scan of arbitrary ports."""
    if os.name != 'nt':
        return []
    script = "$ids=@(Get-Process -Name RedFingerPro -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id); @((Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object { $_.OwningProcess -in $ids -and $_.LocalAddress -in @('127.0.0.1','0.0.0.0') } | Select-Object -ExpandProperty LocalPort -Unique)) | ConvertTo-Json -Compress"
    raw = _powershell(script)
    ports = json.loads(raw.decode('utf-8-sig') or '[]')
    if isinstance(ports, int):
        ports = [ports]
    return sorted({_port(p) for p in (ports or [])})


def _dedicated_port_free():
    if os.name != 'nt':
        return False
    # On Windows an unused localhost port can time out instead of refusing.
    # Verify OS ownership before allowing adb to start its dedicated daemon.
    raw = _powershell("@(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object { $_.LocalPort -eq 5038 }).Count")
    return raw.strip() == b'0'


def _read(sock, count):
    data = b''
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise RuntimeError('ADB 服务提前断开连接')
        data += chunk
    return data


def _request(sock, command):
    raw = command.encode('utf-8')
    sock.sendall(f'{len(raw):04x}'.encode() + raw)
    status = _read(sock, 4)
    if status == b'FAIL':
        raise RuntimeError(_read(sock, int(_read(sock, 4), 16)).decode(errors='replace'))
    if status != b'OKAY':
        raise RuntimeError('端口不是兼容的 ADB 服务')


def _host(port, command):
    with socket.create_connection(('127.0.0.1', port), timeout=1) as sock:
        _request(sock, command)
        return _read(sock, int(_read(sock, 4), 16)).decode(errors='replace')


def _port(value):
    if type(value) is not int or not 1 <= value <= 65535:
        raise ValueError('ADB 服务端口必须是 1–65535 的整数')
    return value


def discover(value=None):
    value = value or {}
    port = _port(value.get('server_port', 5037))
    adb = value.get('adb') or (DEFAULT_ADB if Path(DEFAULT_ADB).is_file() else shutil.which('adb')) or ''
    if not isinstance(adb, str):
        raise TypeError('ADB 路径必须是字符串')
    devices, warnings = [], []
    for candidate in dict.fromkeys((5037, 5038, port)):
        try:
            # The bundled platform-tools speaks server protocol 41. Never let a
            # mismatched CLI kill a vendor-owned older daemon.
            if int(_host(candidate, 'host:version'), 16) != 41:
                warnings.append(f'ADB 服务 {candidate} 版本不兼容；未重启或修改服务')
                continue
            rows = _host(candidate, 'host:devices-l').splitlines()
            for row in rows:
                fields = row.split()
                if len(fields) < 2:
                    continue
                serial, state = fields[:2]
                if not re.fullmatch(r'[A-Za-z0-9_.:-]+', serial):
                    continue
                model = next((f[6:].replace('_', ' ') for f in fields[2:] if f.startswith('model:')), '')
                devices.append({'serial': serial, 'state': state, 'model': model,
                                'server_port': candidate, 'adb': adb})
        except (OSError, RuntimeError, ValueError) as error:
            warnings.append(f'ADB 服务 {candidate} 不可用：{error}')
    try:
        for forwarded_port in _redfinger_ports():
            serial = f'127.0.0.1:{forwarded_port}'
            if any(d['serial'] == serial and d['state'] == 'device' for d in devices):
                continue
            devices.append({'serial': serial, 'state': 'forwarded', 'model': '红手指现有通道',
                            'server_port': 5038, 'adb': adb, 'source': 'redfinger'})
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        warnings.append(f'无法读取红手指通道：{error}')
    return {'devices': devices, 'warnings': warnings, 'input_actions': 0, 'model_calls': 0}


def attach_redfinger(value):
    """Attach a discovered loopback channel only while its port is vendor-owned."""
    config = validate_config({**value, 'package': 'com.android.placeholder'})
    match = re.fullmatch(r'127\.0\.0\.1:(\d+)', config['serial'])
    if not match or int(match[1]) not in _redfinger_ports():
        raise RuntimeError('红手指通道已变化，请重新刷新并选择设备')
    config['server_port'] = 5038
    try:
        version = _host(5038, 'host:version')
    except ConnectionRefusedError:
        version = None  # May start our dedicated server, never the vendor's.
    except (OSError, RuntimeError, ValueError) as error:
        if _dedicated_port_free():
            version = None
        else:
            raise RuntimeError('本机 ADB 5038 服务无响应；未覆盖或重启它') from error
    if version is not None and int(version, 16) != 41:
        raise RuntimeError('本机 ADB 5038 版本不兼容；未覆盖或重启它')
    options = {'capture_output': True, 'timeout': 20,
               'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)}
    cli = subprocess.run([config['adb'], 'version'], check=True, **options).stdout
    if b'Android Debug Bridge version 1.0.41' not in cli:
        raise RuntimeError('需要 ADB 1.0.41；未修改现有服务')
    subprocess.run([config['adb'], '-P', '5038', 'connect', config['serial']], check=True, **options)
    return inspect_device(config)


def _shell(port, transport, command):
    # Fixed read-only commands only; never take a shell string from the request.
    with socket.create_connection(('127.0.0.1', port), timeout=5) as sock:
        _request(sock, f'host:transport-id:{transport}')
        _request(sock, 'shell:' + command)
        data = bytearray()
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > 262144:
                raise RuntimeError('ADB 响应过大')
        return data.decode(errors='replace')


def inspect_device(value):
    config = validate_config({**value, 'package': 'com.android.placeholder'})
    port, serial = config['server_port'], config['serial']
    rows = _host(port, 'host:devices-l').splitlines()
    matches = [re.search(r'\btransport_id:(\d+)\b', row) for row in rows
               if row.split()[:2] == [serial, 'device']]
    if len(matches) != 1 or matches[0] is None:
        raise RuntimeError('所选设备已离线或未授权，请在红手指连接后刷新设备')
    transport = int(matches[0][1])
    activities = _shell(port, transport,
                        'cmd package query-activities --brief -a android.intent.action.MAIN -c android.intent.category.LAUNCHER')
    packages = sorted(set(re.findall(r'^\s*([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)/[A-Za-z0-9_.$]+\s*$',
                                    activities, re.MULTILINE)))
    windows = _shell(port, transport, 'dumpsys window')
    focus = re.search(r'mCurrentFocus=Window\{[^\n]*?\bu\d+\s+([^\s}]+)', windows)
    current = focus[1].split('/')[0] if focus else ''
    names = {'com.tencent.nrc': '洛克王国世界', 'com.miHoYo.hkrpg': '崩坏：星穹铁道',
             'com.android.deskclock': '时钟', 'com.android.calculator2': '计算器'}
    return {'apps': [{'package': p, 'label': names.get(p, p)} for p in packages],
            'current_package': current if current in packages else '',
            'input_actions': 0, 'model_calls': 0}
