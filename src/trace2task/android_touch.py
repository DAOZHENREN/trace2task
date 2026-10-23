"""Pinned-transport, single-gesture client for the device-side 1500 ms lease.

No automatic reconnect or replay. UP/EOF cleanup deliberately bypasses a stop
request: cancellation must release our own contact, not suppress its release.
"""
from __future__ import annotations

import base64
import hashlib
import json
import queue
import re
import shlex
import subprocess
import threading
import time
import zlib
from pathlib import Path

from PIL import Image


class NavigationSuperseded(RuntimeError):
    """A human intervention invalidated this movement, not the whole run."""


def helper_artifact():
    folder = Path(__file__).with_name('android_touch')
    metadata = json.loads((folder / 'build.json').read_text(encoding='utf-8'))
    jar = folder / 'bounded-touch.jar'
    if (metadata.get('protocol') != 'T2T1' or metadata.get('max_duration_ms') != 1500
            or hashlib.sha256(jar.read_bytes()).hexdigest() != metadata.get('jar_sha256')
            or hashlib.sha256((folder / 'BoundedTouch.java').read_bytes()).hexdigest()
            != metadata.get('source_sha256')):
        raise RuntimeError('Android touch helper build is missing or stale; rebuild before input')
    return jar, metadata


class BoundedTouchSession:
    """One contact, one lease, one process; constructed only after budget admission."""

    def __init__(self, driver, state, stop, *, is_current=lambda: True):
        self.driver, self.state, self.stop = driver, dict(state), stop
        self.is_current = is_current
        self.events = []
        self.released = threading.Event()
        self._done = threading.Event()
        self._release_requested = threading.Event()
        self._write_lock = threading.Lock()
        self._messages = queue.Queue()
        self._interruption = None
        self._process = None
        self._reader = self._watcher = None
        self._started_at = time.monotonic()

    def _record(self, event, **fields):
        item = {'event': event, 'elapsed_ms': round((time.monotonic() - self._started_at) * 1000, 2),
                **fields}
        self.events.append(item)
        self.driver.record('android_touch', target=dict(self.driver.target), **item)

    def check(self):
        if self._interruption is not None:
            raise self._interruption
        self.stop.raise_if_requested()
        if not self.is_current():
            raise NavigationSuperseded('人工干预已使移动计划失效；触点已请求释放')

    def open(self):
        self.check()
        focus = self.state.get('focus')
        if (not isinstance(focus, str) or not re.fullmatch(
                re.escape(self.driver.package) + r'/[A-Za-z0-9_.$]+', focus)):
            raise RuntimeError('Invalid bound Android activity; no helper command sent')
        activity = focus.split('/', 1)[1]
        jar, metadata = helper_artifact()
        remote = '/data/local/tmp/trace2task-touch-' + metadata['jar_sha256'][:16] + '.jar'
        if getattr(self.driver, '_touch_helper_path', None) != remote:
            self.driver._run('push', str(jar), remote)
            actual = self.driver._run('shell', 'sha256sum', remote).decode().split()
            if not actual or actual[0].lower() != metadata['jar_sha256']:
                raise RuntimeError('Android touch helper digest mismatch; no input sent')
            self.driver._touch_helper_path = remote
            self.driver.record('android_touch_helper', remote=remote, **metadata)
        self.check()
        args = [self.driver.adb, '-P', str(self.driver.port), '-t', str(self.driver.transport),
                'shell', '-T', 'app_process', '-Djava.class.path=' + remote, '/data/local/tmp',
                'org.trace2task.input.BoundedTouch', str(self.state['width']),
                str(self.state['height']), str(self.state['rotation']), self.driver.package,
                shlex.quote(activity), self.state['boot_id']]
        self._process = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', bufsize=1,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        self._reader = threading.Thread(target=self._read, name='android-touch-receipts', daemon=True)
        self._reader.start()
        self._watcher = threading.Thread(target=self._watch, name='android-touch-stop', daemon=True)
        self._watcher.start()
        self._await('READY', timeout=4)
        return self

    def _read(self):
        try:
            for line in iter(lambda: self._process.stdout.readline(2 * 1024 * 1024), ''):
                if line.startswith('T2T1 '):
                    if not line.endswith('\n'):
                        self._messages.put('ERROR oversized_or_truncated_response')
                        self.release()
                        return
                    message = line[5:].strip()
                    if message.startswith('RELEASED '):
                        self.released.set()
                    self._messages.put(message)
                # Android runtime warnings are not protocol or model instructions.
        finally:
            self._messages.put('EOF')

    def _watch(self):
        while not self._done.wait(.02):
            try:
                self.check()
            except Exception as error:  # noqa: BLE001 - any broken stop/guard must release the contact
                self._interruption = error
                self.release()
                return

    def _send(self, command):
        with self._write_lock:
            if self._process is None or self._process.poll() is not None:
                return False
            self._process.stdin.write(command + '\n')
            self._process.stdin.flush()
        return True

    def _await(self, expected, *, timeout=1):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.check()
            try:
                message = self._messages.get(timeout=.02)
            except queue.Empty:
                continue
            if message == expected:
                self._record('ack', message=message)
                return True
            if expected == 'FRAME' and message.startswith('FRAME '):
                return message[6:]
            if message.startswith('RELEASED ') and expected != 'READY':
                self._record('released', reason=message[9:])
                if message[9:] == 'capture_error':
                    raise RuntimeError('Android minimap capture failed; contact released')
                return False
            raise RuntimeError(f'Android touch helper stopped without expected {expected}: {message[:120]}')
        raise TimeoutError(f'Android touch helper did not acknowledge {expected}; no retry')

    def begin(self, center, endpoint, duration_ms):
        self.check()
        # The pinned device process checks the bound activity/display immediately
        # before DOWN and every MOVE. It cannot survive a reboot or reconnect.
        if type(duration_ms) is not int or not 1 <= duration_ms <= 1500:
            raise ValueError('Sustained touch duration must be 1..1500 ms')
        remaining = getattr(self.stop, 'deadline', None)
        if remaining is not None:
            duration_ms = min(duration_ms, int(max(0, remaining - time.monotonic()) * 1000))
            if duration_ms < 1:
                self.stop.raise_if_requested()
                raise TimeoutError('No time budget remains for a new contact')
        self._record('start_requested', center=list(center), endpoint=list(endpoint),
                     duration_ms=duration_ms)
        if not self._send('START {} {} {} {} {}'.format(duration_ms, *center, *endpoint)):
            raise RuntimeError('Android touch process exited before START; no retry')
        return self._await('STARTED')

    def capture_region(self, region, path):
        """Fresh, device-guarded minimap pixels, without repeated ADB round trips."""
        self.check()
        left, top, right, bottom = region
        width, height = right-left, bottom-top
        if (any(type(value) is not int for value in region)
                or not 0 <= left < right <= self.state['width']
                or not 0 <= top < bottom <= self.state['height']
                or width > 512 or height > 512):
            raise ValueError('Invalid bounded minimap crop')
        path = Path(path)
        if not path.resolve().is_relative_to(self.driver.root.resolve()):
            raise ValueError('Minimap snapshot must stay in this run directory')
        if self.released.is_set():
            return None
        started = time.monotonic()
        if not self._send(f'FRAME {left} {top} {width} {height}'):
            raise RuntimeError('Android touch transport ended before frame; no retry')
        encoded = self._await('FRAME', timeout=2)
        if encoded is False:
            return None
        compressed = base64.b64decode(encoded, validate=True)
        decoder = zlib.decompressobj()
        pixels = decoder.decompress(compressed, width * height + 1)
        if (len(pixels) != width * height or not decoder.eof
                or decoder.unconsumed_tail or decoder.unused_data):
            raise RuntimeError('Android minimap frame has unexpected geometry or format')
        self.check()
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.frombytes('L', (width, height), pixels).save(path)
        return {'screenshot': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'image_kind': 'minimap_luminance_crop', 'crop_region': list(region),
                'capture_ms': round((time.monotonic() - started) * 1000, 2)}

    def move(self, endpoint):
        self.check()
        if self.released.is_set():
            return False
        self._record('move_requested', endpoint=list(endpoint))
        if not self._send('MOVE {} {}'.format(*endpoint)):
            if self.released.is_set():
                return False
            raise RuntimeError('Android touch transport ended before MOVE; no retry')
        return self._await('MOVED')

    def release(self):
        """Best-effort UP plus EOF, including after cancellation or broken ADB capture."""
        if self._release_requested.is_set():
            return
        self._release_requested.set()
        try:
            self._record('release_requested')
            self._send('UP')
        except (OSError, ValueError):
            pass  # The independent phone-side lease remains bounded to 1500 ms.
        finally:
            try:
                if self._process is not None:
                    self._process.stdin.close()
            except (OSError, ValueError):
                pass

    def close(self):
        if self._done.is_set():
            return
        self.release()
        if self._process is not None:
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                # Kill this host ADB client only, never the server or another session.
                self._process.kill()
                self._process.wait(timeout=1)
        self._done.set()
        if self._watcher is not None:
            self._watcher.join(timeout=.2)
        if self._reader is not None:
            self._reader.join(timeout=.2)
        self._record('closed', release_confirmed=self.released.is_set())
