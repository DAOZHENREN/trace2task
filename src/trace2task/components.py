"""User-owned optional GPU runtime. Never install into the system Python."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from trace2task.local_process import stop_started_process


def app_root():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))


def settings_path(data):
    return Path(data) / 'components.json'


def settings(data):
    path = settings_path(data)
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise TypeError('本地组件配置损坏，请检查 components.json')
    return value


def gui_paths(data):
    saved = settings(data)
    python = Path(os.environ.get('TRACE2TASK_GUI_PYTHON') or saved.get('python') or
                  'D:/Models/Trace2Task-D-5970/.venv/Scripts/python.exe')
    models = Path(os.environ.get('TRACE2TASK_GUI_MODELS') or saved.get('models') or
                  'D:/Models/Trace2Task-GUI')
    return python, models


def trained_paths(data):
    saved = settings(data)
    bundle = Path(os.environ.get('TRACE2TASK_D_BUNDLE') or saved.get('trained_bundle') or
                  'D:/Models/Trace2Task-D-5970')
    return Path(saved.get('python') or bundle / '.venv/Scripts/python.exe'), bundle


def cached_torch(data, folder):
    """Reuse only the exact official Windows wheel, never an arbitrary local package."""
    name = 'torch-2.7.1+cu128-cp311-cp311-win_amd64.whl'
    expected = '138c66dcd0ed2f07aafba3ed8b7958e2bed893694990e0b4b55b6b2b4a336aa6'
    for directory in (folder / 'wheels', trained_paths(data)[1]):
        path = directory / name
        if path.is_file():
            with path.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() == expected:
                    return path
    return None


class ComponentManager:
    def __init__(self, data):
        self.data = Path(data)
        self.lock = threading.RLock()
        self.cancelled = threading.Event()
        self.state = {'status': 'idle', 'message': '尚未安装专用组件'}

    def status(self):
        with self.lock:
            result = dict(self.state)
        python, models = gui_paths(self.data)
        result.update(python=str(python), python_exists=python.is_file(), models=str(models),
                      default_directory=str(self.data / 'components'),
                      configured=bool(settings(self.data)))
        if result['status'] == 'idle' and result['configured']:
            result['message'] = '已登记本地组件；首次启动模型时检查权重和显存'
        log = self.data / 'runs/components/install.log'
        if log.exists():
            with log.open('rb') as stream:
                stream.seek(max(0, log.stat().st_size - 12000))
                result['log'] = stream.read().decode('utf-8', errors='replace')
        return result

    def start(self, action, directory='', model=''):
        from trace2task.local_gui_protocol import MODELS
        if action not in {'runtime', 'model', 'import_d', 'cancel'}:
            raise ValueError('未知组件操作')
        with self.lock:
            if action == 'cancel':
                self.cancelled.set()
                if self.state['status'] == 'running':
                    self.state['message'] = '正在取消；已有可用配置保持不变'
                return self.status()
            if self.state['status'] == 'running':
                raise RuntimeError('组件安装正在进行，请等待或取消')
            if action == 'model' and model not in MODELS:
                raise ValueError('请选择支持的公开模型')
            folder = Path(directory or str(self.data / 'components')).expanduser()
            if not folder.is_absolute() or folder == Path(folder.anchor):
                raise ValueError('请选择绝对路径下的专用组件文件夹，不能使用磁盘根目录')
            folder = folder.resolve()
            if getattr(sys, 'frozen', False) and folder.is_relative_to(Path(sys.executable).parent):
                raise ValueError('组件必须存放在安装目录之外')
            self.cancelled.clear()
            self.state = {'status': 'running', 'message': '正在准备组件', 'directory': str(folder)}
            threading.Thread(target=self._work, args=(action, folder, model), daemon=True).start()
        return self.status()

    def _run(self, args, env, log):
        if self.cancelled.is_set():
            raise InterruptedError('已取消')
        log.write(('\n> ' + ' '.join(map(str, args)) + '\n').encode('utf-8'))
        log.flush()
        process = subprocess.Popen(list(map(str, args)), stdout=log, stderr=log,
                                   cwd=self.data, env=env,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline = time.monotonic() + 7200
        try:
            while process.poll() is None:
                if self.cancelled.wait(.2):
                    raise InterruptedError('已取消；未启用不完整组件')
                if time.monotonic() > deadline:
                    raise TimeoutError('组件安装超过两小时，请检查网络后重试')
            if process.returncode:
                raise RuntimeError(f'组件步骤失败（退出码 {process.returncode}），详见下方日志')
        finally:
            stop_started_process(process)

    def _work(self, action, folder, model):
        try:
            log_path = self.data / 'runs/components/install.log'
            log_path.parent.mkdir(parents=True, exist_ok=True)
            folder.mkdir(parents=True, exist_ok=True)
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith(('UV_', 'PIP_', 'PYTHON')) and k != 'VIRTUAL_ENV'}
            env.update(PYTHONUTF8='1', UV_CACHE_DIR=str(folder / 'cache'),
                       UV_PYTHON_INSTALL_DIR=str(folder / 'python'), UV_LINK_MODE='copy',
                       UV_NO_CONFIG='1')
            with log_path.open('wb', buffering=0) as log:
                if action == 'runtime':
                    if shutil.disk_usage(folder).free < 12 * 1024**3:
                        raise RuntimeError('专用运行环境建议至少保留 12 GB 空闲空间')
                    uv = app_root() / 'vendor/uv.exe'
                    if not getattr(sys, 'frozen', False) and not uv.is_file():
                        uv = app_root() / 'build/desktop-vendor/uv.exe'
                    if not uv.is_file():
                        raise RuntimeError('安装包缺少组件安装器 uv.exe，请重新构建完整安装包')
                    runtime = folder / ('gpu-' + uuid.uuid4().hex[:12])
                    self._run([uv, 'python', 'install', '3.11.13', '--no-bin', '--no-registry'], env, log)
                    self._run([uv, 'venv', '--managed-python', '--python', '3.11.13', runtime], env, log)
                    python = runtime / 'Scripts/python.exe'
                    torch_source = cached_torch(self.data, folder) or 'torch==2.7.1+cu128'
                    self._run([uv, 'pip', 'install', '--python', python, '--only-binary', ':all:',
                               '--index-url', 'https://download.pytorch.org/whl/cu128',
                               torch_source, 'torchvision==0.22.1+cu128'], env, log)
                    self._run([uv, 'pip', 'install', '--python', python, '--only-binary', ':all:',
                               '--index-url', 'https://pypi.org/simple', '-r',
                               app_root() / 'scripts/local_gui/managed-requirements.txt'], env, log)
                    self._run([python, '-I', '-c',
                               ('import torch, transformers, peft, pygame; '
                               'assert torch.cuda.is_available(), "CUDA unavailable: check NVIDIA driver"; '
                               'x=torch.ones((64,64),device="cuda"); y=x@x; torch.cuda.synchronize(); '
                               'assert y[0,0].item()==64; '
                               'print("CUDA verified",torch.cuda.get_device_name(),torch.__version__)')], env, log)
                    current = settings(self.data)
                    current.update(python=str(python), models=current.get('models', str(folder / 'models')))
                    with self.lock:
                        if self.cancelled.is_set():
                            raise InterruptedError('已取消；未切换组件配置')
                        pending = settings_path(self.data).with_suffix('.pending')
                        pending.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding='utf-8')
                        os.replace(pending, settings_path(self.data))
                elif action == 'import_d':
                    for name in ('step-00005970.pt', 'frozen-launch.json', 'source', 'weights', 'processor'):
                        if not (folder / name).exists():
                            raise ValueError(f'D 模型包缺少 {name}')
                    with (folder / 'step-00005970.pt').open('rb') as stream:
                        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                    if digest != '4c07d912b881ccc69a28477aedf6c2b349f91965dc5dca89ebba8995987d02d3':
                        raise ValueError('D 模型检查点 SHA256 不匹配，拒绝导入')
                    with self.lock:
                        if self.cancelled.is_set():
                            raise InterruptedError('已取消')
                        current = settings(self.data)
                        current['trained_bundle'] = str(folder)
                        pending = settings_path(self.data).with_suffix('.pending')
                        pending.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding='utf-8')
                        os.replace(pending, settings_path(self.data))
                else:
                    python, models = gui_paths(self.data)
                    if not python.is_file():
                        raise RuntimeError('请先安装 GPU 运行环境')
                    self._run([python, app_root() / 'scripts/local_gui/download.py', '--root', models,
                               '--endpoint', 'https://huggingface.co', '--models', model], env, log)
            with self.lock:
                self.state.update(status='completed', message='组件准备完成；请停止旧模型服务后重新启动以生效')
        except InterruptedError as error:
            with self.lock:
                self.state.update(status='cancelled', message=str(error))
        except Exception as error:  # noqa: BLE001 -- surface worker failures in the UI
            with self.lock:
                self.state.update(status='failed', message=str(error))
