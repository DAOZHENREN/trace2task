"""Explicit 3090 cloud-phone profile and loopback SSH connection; never device input."""
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

from trace2task.model_api import _NoRedirects

PROFILE_ID = 'qwen3-vl-3090'
MODEL = 'Qwen3-VL-8B-Instruct'
BASE_URL = 'http://127.0.0.1:18998/v1'
DESTINATION = '3090 服务器 · 本机配置的 SSH 隧道'
_lock = threading.Lock()
_tunnel = None


def ssh_host():
    """Keep deployment identity in local configuration, never in public source."""
    host = os.environ.get('TRACE2TASK_ANDROID_SSH_HOST', '').strip()
    if not host:
        path = Path(os.environ.get('TRACE2TASK_ANDROID_MODEL_PROFILE') or
                    Path(__file__).resolve().parents[2] / 'android-model-profile.json')
        try:
            with path.open('rb') as stream:
                raw = stream.read(4097)
            if len(raw) > 4096:
                raise ValueError('profile too large')
            value = json.loads(raw)
            host = value.get('ssh_host', '') if isinstance(value, dict) else ''
        except (OSError, ValueError) as error:
            raise RuntimeError('请配置 TRACE2TASK_ANDROID_SSH_HOST 或本机 android-model-profile.json') from error
    if not isinstance(host, str) or not re.fullmatch(r'(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9_.-]*', host):
        raise ValueError('SSH 目标必须是明确的主机别名或 user@host，不能包含选项或命令')
    return host


def profile():
    return {'id': PROFILE_ID, 'label': '3090 · Qwen3-VL-8B · 云手机',
            'model': MODEL, 'base_url': BASE_URL, 'destination': DESTINATION,
            'context_window_tokens': 4096, 'timeout_seconds': 180,
            'mobileforge': 'D:/Tools/trace2task-android/MobileForge',
            'android': {'adb': 'D:/Tools/trace2task-android/sdk/platform-tools/adb.exe',
                        'serial': '', 'server_port': 5037, 'package': ''}}


def validate_selection(value, *, provider, executor, model, base_url, mobileforge):
    if (value != PROFILE_ID or provider != 'api' or executor != 'android' or not isinstance(base_url, str)
            or model != MODEL or base_url.rstrip('/') != BASE_URL or not mobileforge):
        raise ValueError('3090 云手机配置不匹配；请重新选择预设，或切换到自定义配置')


def status():
    result = {'connected': False, 'model': MODEL, 'base_url': BASE_URL,
              'destination': DESTINATION, 'input_actions': 0, 'model_calls': 0}
    try:
        with build_opener(ProxyHandler({}), _NoRedirects()).open(
                BASE_URL.removesuffix('/v1') + '/health', timeout=1.5) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError('模型健康响应过大')
        health = json.loads(raw)
        if not isinstance(health, dict) or health.get('status') != 'ok' or health.get('model') != MODEL:
            raise ValueError('端口上的模型身份或状态不匹配')
        result.update(connected=True, message='3090 模型已连接；未发送截图或设备输入')
    except (OSError, ValueError) as error:
        result.update(error=f'3090 模型未就绪：{error}。请检查 VPN、SSH 隧道和服务器模型服务。')
    return result


def connect():
    """Reuse healthy connections; otherwise create only this fixed SSH forward."""
    global _tunnel
    with _lock:
        current = status()
        if current['connected']:
            return {**current, 'reused': True}
        with socket.socket() as sock:
            sock.settimeout(.5)
            if sock.connect_ex(('127.0.0.1', 18998)) == 0:
                raise RuntimeError('本机 18998 已占用但模型未就绪；不会覆盖或停止现有连接，请先检查它')
        if _tunnel is not None and _tunnel.poll() is None:
            raise RuntimeError('上一次 SSH 连接仍在建立，请稍后检查；不会重复创建隧道')
        host = ssh_host()
        ssh = shutil.which('ssh')
        if not ssh:
            raise RuntimeError('未找到 OpenSSH 客户端')
        process = subprocess.Popen([
            ssh, '-N', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ConnectTimeout=10', '-o', 'ExitOnForwardFailure=yes',
            '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=3',
            '-L', '127.0.0.1:18998:127.0.0.1:8098', host,
        ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        _tunnel = process
        try:
            deadline = time.monotonic() + 12
            while process.poll() is None and time.monotonic() < deadline:
                current = status()
                if current['connected']:
                    return {**current, 'reused': False}
                time.sleep(.25)
            raise RuntimeError('无法连接 3090 模型；请确认 VPN、SSH 密钥/已信任主机及远端服务。未操作手机。')
        except Exception:
            # Never close an externally created/shared tunnel. This handle is ours.
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            _tunnel = None
            raise
