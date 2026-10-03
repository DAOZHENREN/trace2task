"""Owned, authenticated llama-server transport; llama.cpp owns all KV caching."""
import atexit
import base64
import hashlib
import io
import json
import os
import secrets
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from trace2task.local_process import stop_started_process
from trace2task.model_registry import GUI_CONTEXT_TOKENS, profile_for

BACKENDS = {'transformers', 'llama-server'}
SUMMARY_ALIAS = 'trace2task-gui-summary-qwen8b'


def summary_model_path(root=None):
    override = os.environ.get('TRACE2TASK_SUMMARY_GGUF')
    if override:
        return Path(override)
    root = root or os.environ.get('TRACE2TASK_GUI_MODELS', 'D:/Models/Trace2Task-GUI')
    return runtime_paths(root, 'qwen3-vl-8b-instruct')[1]


def read_backend(output):
    path = Path(output) / 'backend.json'
    backend = json.loads(path.read_text(encoding='utf-8'))['backend'] if path.exists() else 'llama-server'
    if backend not in BACKENDS:
        raise ValueError('Unknown GUI inference backend')
    return backend


def runtime_paths(root, key):
    profile = profile_for(key, 'llama-server')
    folder = Path(root) / 'gguf' / key
    names = ('model-BF16.gguf', 'mmproj-F16.gguf')
    if profile.prebuilt_gguf:
        preset = profile.prebuilt_gguf
        names = tuple(name for name, _ in preset.files)
        override = os.environ.get(preset.directory_env)
        if override:
            folder = Path(override)
        elif not all((folder / name).is_file() for name in names):
            existing = Path(preset.directory)
            if all((existing / name).is_file() for name in names):
                folder = existing
    return [Path(os.environ.get('TRACE2TASK_LLAMA_SERVER', 'D:/Tools/llama-b11026/bin/llama-server.exe')),
            *(folder / name for name in names)]


def required_runtime_paths(root, key):
    paths = runtime_paths(root, key)
    # Official prebuilt SHA-256 values are pinned in the registry. Converted
    # checkpoints instead require the local converter's provenance manifest.
    return paths if profile_for(key).prebuilt_gguf else [*paths, paths[1].parent / 'verified-gguf.json']


def llama_precision(key):
    preset = profile_for(key, 'llama-server').prebuilt_gguf
    return {'dtype': preset.quantization if preset else 'BF16 text / F16 vision',
            'kv_cache_type': preset.cache_type if preset else 'f16'}


def verify_weights(paths, key='gui-owl-2b'):
    profile = profile_for(key, 'llama-server')
    if profile.prebuilt_gguf:
        expected = dict(profile.prebuilt_gguf.files)
    else:
        folder = paths[1].parent
        report = json.loads((folder / 'verified-gguf.json').read_text(encoding='utf-8'))
        if (report.get('model') != profile.repository or report.get('revision') != profile.revision):
            raise ValueError('Unexpected GGUF checkpoint provenance')
        expected = {item['file']: item['sha256'] for item in report['files']}
    for path in paths[1:]:
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != expected.get(path.name):
                raise ValueError('GGUF checksum mismatch: ' + path.name)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError('Local llama-server redirects are forbidden')


class ContextFull(RuntimeError):
    pass


class GenerationCancelled(RuntimeError):
    pass


def wire_messages(messages, images):
    """Convert image markers in order; never send file paths or external URLs."""
    result = deepcopy(messages)
    pictures = iter(images)
    for message in result:
        if isinstance(message['content'], list):
            for index, part in enumerate(message['content']):
                if part.get('type') == 'image':
                    buffer = io.BytesIO()
                    try:
                        picture = next(pictures)
                    except StopIteration as error:
                        raise ValueError('Missing conversation image') from error
                    picture.save(buffer, format='PNG')
                    message['content'][index] = {
                        'type': 'image_url', 'image_url': {
                            'url': 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')}}
    if next(pictures, None) is not None:
        raise ValueError('Extra conversation image')
    return result


class LlamaRuntime:
    def __init__(self, root, output, port=8769):
        self.root, self.output, self.port = Path(root), Path(output), port
        self.process = None
        self.process_key = None
        self.key = secrets.token_hex(32)
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        atexit.register(self.stop)

    def stop(self):
        process = self.process
        stop_started_process(process)
        if process is not None:
            # taskkill acknowledges termination before CUDA cleanup/socket release
            # necessarily finishes. Switching models must wait for the owned PID.
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError('Owned llama worker has not exited; model switch aborted') from error
        self.process = None
        self.process_key = None

    def request(self, path, payload=None, timeout=180):
        request = Request(f'http://127.0.0.1:{self.port}{path}',
                          data=None if payload is None else json.dumps(payload).encode('utf-8'),
                          headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.key})
        try:
            with self.opener.open(request, timeout=timeout) as response:
                return json.load(response)
        except HTTPError as error:
            detail = error.read().decode('utf-8', errors='replace')
            # Only the engine's explicit context-size failure permits image eviction/retry.
            try:
                error_type = json.loads(detail).get('error', {}).get('type')
            except (ValueError, AttributeError):
                error_type = None
            if error.code == 400 and error_type == 'exceed_context_size_error':
                raise ContextFull(detail) from None
            raise RuntimeError(f'llama-server HTTP {error.code}: {detail}') from None

    def load(self, key, cancelled=None):
        summary = key == 'summary-qwen8b'
        paths = ([runtime_paths(self.root, 'gui-owl-2b')[0], summary_model_path(self.root)] if summary
                 else runtime_paths(self.root, key))
        for path in paths:
            if not path.is_file():
                raise RuntimeError('Missing llama-server runtime/model file: ' + str(path))
        running = self.process is not None and self.process.poll() is None
        if running and self.process_key == key:
            return
        if cancelled is not None and cancelled.is_set():
            raise GenerationCancelled('cancelled_before_load')
        # Validate the new files BEFORE unloading a healthy owned worker.
        if not summary:
            verify_weights(paths, key)
        if cancelled is not None and cancelled.is_set():
            raise GenerationCancelled('cancelled_during_verification')
        if running:
            self.stop()
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1', self.port)) == 0:
                raise RuntimeError(f'Port {self.port} is occupied; the unrelated process was not stopped')
        self.output.mkdir(parents=True, exist_ok=True)
        token_file = self.output / 'llama-service.token'
        token_file.write_text(self.key, encoding='ascii')
        executable, model = paths[:2]
        cache_type = 'q8_0' if summary else llama_precision(key)['kv_cache_type']
        args = [str(executable), '-m', str(model),
                *([] if summary else ['--mmproj', str(paths[2])]),
                '--host', '127.0.0.1', '--port', str(self.port), '--alias', SUMMARY_ALIAS if summary else profile_for(key).alias,
                '--api-key-file', str(token_file), '-ngl', '99', '-c', str(GUI_CONTEXT_TOKENS),
                '-np', '1', '-b', '512', '-ub', '128', '-fa', 'on',
                '-ctk', cache_type, '-ctv', cache_type, '--image-min-tokens', '1024',
                '--image-max-tokens', '1024', '--no-context-shift', '--jinja']
        with (self.output / 'llama-server.log').open('ab') as log:
            self.process = subprocess.Popen(args, stdout=log, stderr=log,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline = time.monotonic() + 180
        try:
            while self.process.poll() is None and time.monotonic() < deadline:
                if cancelled is not None and cancelled.is_set():
                    raise GenerationCancelled('cancelled_during_load')
                try:
                    if self.request('/health', timeout=1).get('status') == 'ok':
                        self.process_key = key
                        return
                except (OSError, URLError, RuntimeError):
                    pass
                time.sleep(.2)
            raise RuntimeError('llama-server did not become ready; see llama-server.log')
        except Exception:
            self.stop()
            raise

    @contextmanager
    def summary_worker(self, cancelled):
        # Separate owned child/alias, never the user's independent 8081 Qwen service.
        if not summary_model_path(self.root).is_file():
            raise RuntimeError('Summary GGUF is missing; conversation was not changed')
        if cancelled.is_set():
            raise GenerationCancelled('cancelled_before_summary')
        previous_key = self.process_key
        if previous_key is None or previous_key == 'summary-qwen8b':
            raise RuntimeError('Summary requires an active GUI model')
        self.stop()
        try:
            self.load('summary-qwen8b', cancelled)
            yield
        finally:
            self.stop()
            if not cancelled.is_set():
                self.load(previous_key, cancelled)

    def complete(self, payload, cancelled):
        # Non-streaming API. Cancellation kills only our own child, including prefill,
        # then the next request reloads it. No late result can become a desktop action.
        if cancelled.is_set():
            raise GenerationCancelled('cancelled_before_generation')
        result, failure = [], []
        finished = threading.Event()

        def run():
            try:
                result.append(self.request('/v1/chat/completions', payload))
            except Exception as error:  # noqa: BLE001 - propagate worker errors to the caller
                failure.append(error)
            finally:
                finished.set()

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        while not finished.wait(.1):
            if cancelled.is_set():
                self.stop()
                worker.join(timeout=3)
                raise GenerationCancelled('cancelled_during_generation')
        if cancelled.is_set():
            raise GenerationCancelled('cancelled_during_generation')
        if failure:
            raise failure[0]
        return result[0]
