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

from trace2task.local_gui_protocol import MODELS, validate_prompt_profile
from trace2task.local_process import stop_started_process

_lock = threading.Lock()

# This is deliberately a compact, derived representation of a confirmed task
# pack, not a way to upload arbitrary task-pack files to the resident model.
# Keep this contract independent from Cua's target/capability context.
EXPERIENCE_CONTEXT_KEYS = frozenset(
    {"task_id", "goal", "completion", "state_index", "candidate_state",
     "candidate_is_unverified", "nearby_states", "outgoing_transitions",
     "candidate_terminals", "human_guidance"}
)
EXPERIENCE_CONTEXT_MAX_CHARS = 12_000

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # A local service may never redirect screenshots elsewhere.


def validate_experience_context(value):
    """Validate the bounded, prompt-safe projection of a confirmed experience.

    The server applies the same validation because its loopback endpoint is an
    independent trust boundary.  ``None`` intentionally remains a no-op so
    existing local-model callers keep their previous request shape.
    """
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != EXPERIENCE_CONTEXT_KEYS:
        raise ValueError("经验上下文格式无效")
    task_id = value.get("task_id")
    goal = value.get("goal")
    completion = value.get("completion")
    guidance = value.get("human_guidance")
    if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 200:
        raise ValueError("经验上下文 task_id 无效")
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("经验上下文 goal 无效")
    if not isinstance(completion, dict) or not completion:
        raise ValueError("经验上下文 completion 无效")
    if not isinstance(value.get("state_index"), list) or not value["state_index"]:
        raise ValueError("经验上下文 state_index 无效")
    if value.get("candidate_state") is not None and not isinstance(value["candidate_state"], dict):
        raise ValueError("经验上下文 candidate_state 无效")
    if value.get("candidate_is_unverified") is not True:
        raise ValueError("经验上下文必须标明状态尚未核验")
    for name in ("nearby_states", "outgoing_transitions", "candidate_terminals"):
        if not isinstance(value.get(name), list):
            raise ValueError(f"经验上下文 {name} 无效")  # noqa: TRY004 -- validation API uses ValueError
    if guidance is not None and not isinstance(guidance, dict):
        raise ValueError("经验上下文 human_guidance 无效")
    normalized = {**value, "task_id": task_id.strip(), "goal": goal.strip()}
    try:
        encoded = json.dumps(
            normalized, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError("经验上下文必须是有限 JSON 数据") from error
    if len(encoded) > EXPERIENCE_CONTEXT_MAX_CHARS:
        raise ValueError(
            f"经验上下文超过 {EXPERIENCE_CONTEXT_MAX_CHARS} 字符预算"
        )
    return normalized

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
                     'prediction.json', 'outcome.json', 'metrics.json', 'error.log', 'screenshot.png',
                     'previous-screenshot.png', 'annotated.png'):
            path = source/name
            if path.is_file() and path.resolve().is_relative_to(source):
                shutil.copy2(path, folder/name)


def predict_gui(task, *, model, image=None, history=None, step_index=0, cua_context=None,
                execution_context=None,
                request_id=None, audit_dir=None, previous_image=None, execution_feedback=None,
                experience_context=None, prompt_profile=None, cursor_position=None):
    if model not in MODELS:
        raise ValueError('Unknown local model')
    if cua_context is not None and execution_context is not None:
        raise ValueError('Use either execution_context or legacy cua_context, not both')
    context = execution_context if execution_context is not None else cua_context
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
            from trace2task.components import gui_paths
            python, models = gui_paths(data)
            if not python.is_file():
                raise RuntimeError('请在“本地组件管理”中安装 GPU 运行环境')
            with (logs/'server.log').open('ab') as out:
                started_process = subprocess.Popen([str(python),str(root/'scripts/local_gui/server.py'),
                    '--output',str(logs),'--token-file',str(token_file),'--root',str(models)],stdout=out,stderr=out,
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
            if prompt_profile is not None and not health.get('capabilities', {}).get('editable_prompts'):
                raise RuntimeError('本地模型服务仍是旧版；请点击“关闭本地模型服务”后重新启动，避免忽略自定义提示词')
            if model == 'gui-owl-2b' and not health.get('capabilities', {}).get('official_owl_adapter'):
                raise RuntimeError('本地模型服务仍是旧版；请关闭并重新启动服务以使用官方动作适配器')
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
        context_key = ('execution_context' if health.get('capabilities', {}).get('generic_execution_context')
                       else 'cua_context')
        body = {'model':model,'task':task,'image':image, 'history':history or [],
                'step_index':step_index,context_key:context,
                'execution_feedback':execution_feedback,
                'request_id':request_id or secrets.token_hex(16)}
        if cursor_position is not None:
            body['cursor_position'] = cursor_position
        experience_context = validate_experience_context(experience_context)
        if experience_context is not None:
            body['experience_context'] = experience_context
        if prompt_profile is not None:
            body['prompt_profile'] = validate_prompt_profile(prompt_profile)
        if previous_image is not None:
            body['previous_image'] = previous_image
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
