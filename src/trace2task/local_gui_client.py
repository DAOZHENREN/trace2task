"""Local-only resident model client, with no environment/system proxy usage."""
import base64
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from trace2task.local_gui_protocol import MODELS
from trace2task.local_process import stop_started_process

_lock = threading.Lock()

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # A local service may never redirect screenshots elsewhere.

def _data_root():
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))
    return Path(os.environ.get('TRACE2TASK_DATA_ROOT', str(root)))


def cancel_gui(request_id):
    """Cancel only the named request, without unloading the resident model."""
    token_file = _data_root() / 'runs/local-gui/service.token'
    opener = build_opener(ProxyHandler({}), NoRedirect())
    request = Request('http://127.0.0.1:8768/cancel',
        data=json.dumps({'request_id': request_id}).encode(),
        headers={'Content-Type': 'application/json', 'X-Local-Token': token_file.read_text().strip()})
    with opener.open(request, timeout=5) as response:
        return json.load(response)


def archive_prediction(folder, body, result, logs):
    """Keep wire payloads and service-side actual prompts together; never copy credentials."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder/'request.json').write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding='utf-8')
    (folder/'response.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    if result.get('output_directory'):
        source = Path(result['output_directory']).resolve()
        if not source.is_relative_to(Path(logs).resolve()):
            raise ValueError('Model archive is outside the local service log directory')
        for name in ('input.json', 'formatted-prompt.txt', 'tokenized-input.json', 'raw-output.json',
                     'prediction.json', 'outcome.json', 'metrics.json', 'error.log', 'screenshot.png', 'annotated.png'):
            path = source/name
            if path.is_file() and path.resolve().is_relative_to(source):
                shutil.copy2(path, folder/name)


def predict_gui(task, *, model, image=None, history=None, step_index=0, cua_context=None,
                request_id=None, audit_dir=None):
    if model not in MODELS:
        raise ValueError('Unknown local model')
    with _lock:
        root = Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[2]))
        data = Path(os.environ.get('TRACE2TASK_DATA_ROOT',str(root)))
        logs = data/'runs/local-gui'
        logs.mkdir(parents=True,exist_ok=True)
        token_file = logs/'service.token'
        opener=build_opener(ProxyHandler({}), NoRedirect())
        url='http://127.0.0.1:8768'
        started_process = None
        try:
            with opener.open(url+'/health',timeout=2) as response:
                health=json.load(response)
        except (URLError,OSError):
            if not token_file.exists():
                token_file.write_text(secrets.token_hex(32),encoding='ascii')
            python=Path(os.environ.get('TRACE2TASK_GUI_PYTHON','D:/Models/Trace2Task-D-5970/.venv/Scripts/python.exe'))
            if not python.is_file():
                raise RuntimeError('Set TRACE2TASK_GUI_PYTHON to the CUDA Python environment')
            with (logs/'server.log').open('ab') as out:
                started_process = subprocess.Popen([str(python),str(root/'scripts/local_gui/server.py'),
                    '--output',str(logs),'--token-file',str(token_file)],stdout=out,stderr=out,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),cwd=root)
            deadline=time.monotonic()+30
            while True:
                try:
                    with opener.open(url+'/health',timeout=2) as response:
                        health=json.load(response)
                    break
                except (URLError,OSError):
                    if time.monotonic()>deadline:
                        stop_started_process(started_process)
                        raise RuntimeError('Local model service did not start; see runs/local-gui/server.log')
                    if started_process.poll() is not None:
                        raise RuntimeError('Local model service exited; see runs/local-gui/server.log')
                    time.sleep(.2)
        try:
            if health.get('service')!='trace2task-local-gui' or health.get('protocol')!=1:
                raise RuntimeError('Port 8768 belongs to another service; no screenshot sent')
            if request_id is not None and not health.get('capabilities', {}).get('cancel'):
                raise RuntimeError('本地模型服务仍是旧版，请关闭并重新启动服务以启用取消生成和完整日志')
        except Exception:
            stop_started_process(started_process)
            raise
        if image is None:
            import io

            import pygame

            from trace2task.desktop_runner import DesktopSession
            from trace2task.windows_capture import GdiWindowCapture
            from trace2task.windows_control import Win32Backend, physical_dpi_context
            capture=GdiWindowCapture(desktop=True)
            with physical_dpi_context(capture.user32):
                size=lambda:(capture.user32.GetSystemMetrics(0),capture.user32.GetSystemMetrics(1))
                frame=capture.capture(DesktopSession(Win32Backend(),size).observe())
            buffer=io.BytesIO()
            pygame.image.save(frame,buffer,'screenshot.png')
            image=base64.b64encode(buffer.getvalue()).decode()
        body = {'model':model,'task':task,'image':image, 'history':history or [],
                'step_index':step_index,'cua_context':cua_context, 'request_id':request_id or secrets.token_hex(16)}
        if audit_dir:
            archive_prediction(audit_dir, body, {'status':'pending', 'request_id':body['request_id']}, logs)
        request=Request(url+'/predict',data=json.dumps(body,ensure_ascii=False).encode(),
            headers={'Content-Type':'application/json','X-Local-Token':token_file.read_text().strip()})
        try:
            with opener.open(request,timeout=300) as response:
                result = json.load(response)
        except HTTPError as error:
            try:
                result = json.loads(error.read())
            except (ValueError, UnicodeDecodeError):
                result = {'error': f'Local model HTTP {error.code}'}
            result.update(status='error',request_id=body['request_id'])
        except (URLError, OSError) as error:
            result = {'status':'error', 'request_id':body['request_id'], 'error':str(error)}
            # A timed-out HTTP client must not leave untracked GPU work running.
            try:
                result['cancel_receipt'] = cancel_gui(body['request_id'])
            except Exception as cancel_error:  # noqa: BLE001 - cancellation receipt is diagnostic only
                result['cancel_error'] = str(cancel_error)
        if audit_dir:
            archive_prediction(audit_dir, body, result, logs)
            result['audit_directory'] = str(audit_dir)
        return result
