"""Explicit native-console lifecycle for the known local model services."""
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from trace2task.local_gui_client import NoRedirect
from trace2task.local_gui_protocol import MODELS
from trace2task.local_process import stop_started_process
from trace2task.model_registry import MODEL_PROFILES, profile_for

_lock = threading.Lock()


def managed_processes():
    # Inspect command lines, never kill by port or the generic python.exe name.
    script = "Get-CimInstance Win32_Process | Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress"
    result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', script],
                            capture_output=True, text=True, encoding='utf-8', errors='replace',
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=True)
    payload = json.loads(result.stdout or '[]')
    # ConvertTo-Json emits an object, rather than an array, when exactly one
    # process matches.  Treat malformed output as no managed process instead of
    # accidentally iterating its property names.
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        return []
    return [process for process in payload if isinstance(process, dict) and is_model_process(process)]


def is_model_process(process):
    command = (process.get('CommandLine') or '').replace('\\', '/').lower()
    arguments = [quoted or bare for quoted, bare in re.findall(r'"([^\"]*)"|(\S+)', command)]
    name = (process.get('Name') or '').lower()
    if name in {'python.exe', 'pythonw.exe'}:
        return any('/trace2task/' in arg and arg.endswith((
            '/scripts/local_gui/server.py', '/scripts/trained_model/preview.py')) for arg in arguments)
    return (name == 'llama-server.exe' and (
            'd:/models/qwen3-vl-8b-instruct/qwen3vl-8b-instruct-q4_k_m.gguf' in arguments or
            any((p.alias in arguments or (p.id == 'qwen3-vl-8b-instruct' and
                 'trace2task-gui-summary-qwen8b' in arguments)) and any(
                arg.endswith('/' + p.prebuilt_gguf.files[0][0].lower()) if p.prebuilt_gguf
                else arg.endswith(f'/gguf/{p.id}/model-bf16.gguf') for arg in arguments)
                for p in MODEL_PROFILES.values() if 'llama-server' in p.engines)))


def process_service(process):
    """Classify only a process we already accepted as owned by Trace2Task."""
    if not is_model_process(process):
        return None
    command = (process.get('CommandLine') or '').replace('\\', '/').lower()
    if ('/scripts/local_gui/server.py' in command or any(p.alias in command for p in MODEL_PROFILES.values())
            or 'trace2task-gui-summary-qwen8b' in command):
        return 'gui'
    if '/scripts/trained_model/preview.py' in command:
        return 'trained_d'
    return 'qwen3-vl-8b-instruct'


def _service_for_model(model):
    return 'gui' if model in MODELS else model


def _stop_processes(processes):
    for process in processes:
        subprocess.run(
            ['taskkill.exe', '/PID', str(process['ProcessId']),
             *(['/T'] if process_service(process) == 'gui' else []), '/F'],
            capture_output=True,
            check=False,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )


def service_busy():
    return _lock.locked()


def control_service(action, model, data_root, backend=None):
    if action not in {'start', 'stop'} or model not in {*MODELS, 'trained_d'}:
        raise ValueError('未知本地模型或服务操作')
    if not _lock.acquire(blocking=False):
        raise RuntimeError('本地服务正在启动或关闭，请稍候')
    try:
        # Validate the selected runtime before touching an already-running
        # service.  A bad bundle path must never take down a healthy model.
        if action == 'start':
            root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))
            logs = Path(data_root) / 'runs/local-gui'
            from trace2task.components import gui_paths, trained_paths
            trained_python, bundle = trained_paths(data_root)
            python, models = gui_paths(data_root)
            required = []
            if model in MODELS:
                from trace2task.local_gui_llama import (
                    BACKENDS,
                    read_backend,
                    required_runtime_paths,
                )
                backend = backend if backend is not None else read_backend(logs)
                if backend not in BACKENDS:
                    raise ValueError('Unknown GUI inference backend')
                profile_for(model, backend)
                token = logs / 'service.token'
                args = [str(python), str(root / 'scripts/local_gui/server.py'), '--output', str(logs), '--token-file', str(token), '--root', str(models)]
                required = [python, Path(args[1])]
                required.extend(required_runtime_paths(models, model) if backend == 'llama-server'
                                else [models / model / 'verified.json'])
                port = 8768
            elif model == 'trained_d':
                args = [str(trained_python), str(root / 'scripts/trained_model/preview.py'),
                        '--bundle', str(bundle), '--output', str(Path(data_root) / 'runs/trained-model-preview'), '--serve']
                required = [Path(args[0]), Path(args[1]), bundle / 'frozen-launch.json',
                            bundle / 'step-00005970.pt', bundle / 'weights', bundle / 'processor']
                port = 8767
            missing = [str(path) for path in required if not path.exists()]
            if missing:
                raise RuntimeError('本地模型运行环境不存在：' + missing[0])
            if model in MODELS and backend == 'llama-server':
                from trace2task.local_gui_llama import runtime_paths, verify_weights
                verify_weights(runtime_paths(models, model), model)
        # Do not kill every local model merely because the user selected one.
        # The shared GUI server owns all MODELS. D and the legacy standalone
        # 8081 Qwen process stay separate; migration never kills that process.
        service = _service_for_model(model)
        processes = [p for p in managed_processes() if process_service(p) == service]
        _stop_processes(processes)
        deadline = time.monotonic() + 10
        while any(process_service(p) == service for p in managed_processes()):
            if time.monotonic() > deadline:
                raise RuntimeError('部分模型服务未能关闭，请检查进程权限')
            time.sleep(.3)
        if action == 'stop':
            return {'message': f'已关闭本地模型服务（清理 {len(processes)} 个进程），权重和日志已保留。'}
        logs.mkdir(parents=True, exist_ok=True)
        if model in MODELS:
            (logs / 'backend.json').write_text(json.dumps({'backend': backend}), encoding='utf-8')
        if model in MODELS and not token.exists():
            token.write_text(secrets.token_hex(32), encoding='ascii')
        import socket
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1', port)) == 0:
                raise RuntimeError(f'端口 {port} 被其他程序占用，未关闭该程序')
        process = None
        try:
            with (logs / 'lifecycle.log').open('ab') as out:
                process = subprocess.Popen(args, cwd=root, stdout=out, stderr=out,
                                           env={**os.environ, 'PYTHONUTF8': '1'},
                                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            opener = build_opener(ProxyHandler({}), NoRedirect())
            deadline = time.monotonic() + 180
            url = f'http://127.0.0.1:{port}'
            while True:
                try:
                    with opener.open(url + '/health', timeout=2) as response:
                        health = json.load(response)
                    # The D server binds before its frozen checkpoint finishes
                    # loading.  Do not advertise it as ready while a prediction
                    # would still receive a 503.
                    if model != 'trained_d' or health.get('status') == 'ready':
                        break
                except OSError:
                    pass
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('服务未就绪，请查看 runs/local-gui/lifecycle.log')
                time.sleep(.5)
            if model in MODELS:
                if health.get('service') != 'trace2task-local-gui':
                    raise RuntimeError('服务身份不匹配')
                request = Request(url + '/load', data=json.dumps({'model': model}).encode(),
                                  headers={'Content-Type': 'application/json', 'X-Local-Token': token.read_text().strip()})
                try:
                    with opener.open(request, timeout=180) as response:
                        json.load(response)
                except HTTPError as error:
                    detail = json.loads(error.read()).get('error', '模型加载失败')
                    raise RuntimeError(detail) from None
            label = f' / {backend}' if model in MODELS else ''
            return {'message': f'{model}{label} 本地模型服务已就绪（端口 {port}）。'}
        except Exception:
            # The child was created by this request.  Do not leave a failed
            # loader resident, and never target an unrelated process by port.
            stop_started_process(process)
            raise
    finally:
        _lock.release()
