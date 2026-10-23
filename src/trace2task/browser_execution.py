"""Isolated, pixel-only Ancient Beast backend for the shared execution loop.

The Node worker owns a temporary headless Edge instance, never the user's browser.
No model-supplied URL, script, selector, DOM read or OS input is available.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from PIL import Image

from trace2task.execution_protocol import ActionUnavailable, ObservationStale

GAME_URL = 'https://ancientbeast.com/'
BROWSER_TARGET = {'kind': 'browser', 'game': 'ancientbeast'}
VIEWPORT = (1280, 800)
SKILLS = ('click', 'double_click', 'move_cursor', 'press_key', 'scroll', 'wait')
KEYS = {'escape': 'Escape', 'enter': 'Enter', 'space': 'Space', 'tab': 'Tab',
        'backspace': 'Backspace', 'delete': 'Delete', 'up': 'ArrowUp', 'down': 'ArrowDown',
        'left': 'ArrowLeft', 'right': 'ArrowRight', 'home': 'Home', 'end': 'End',
        'page_up': 'PageUp', 'page_down': 'PageDown',
        **{key: key for key in 'abcdefghijklmnopqrstuvwxyz0123456789'}}


def browser_runtime():
    """Source-build requirement; never install dependencies or choose a different driver."""
    root = Path(__file__).resolve().parents[2]
    package = root / 'frontend/package.json'
    worker = Path(__file__).with_name('browser_worker.mjs')
    node = shutil.which('node')
    if (not node or not package.is_file() or not worker.is_file()
            or not (root / 'frontend/node_modules/playwright/package.json').is_file()):
        raise ValueError('浏览器后端需要源码版的 Node、frontend Playwright 和已安装的 Edge；不会自动安装或回退前台')
    return node, worker, package


class BrowserDriver:
    def __init__(self, root, record, stop):
        self.root, self.record, self.stop = Path(root), record, stop
        self.process = None
        self.messages = queue.Queue()
        self.stderr = deque(maxlen=20)
        self.request_id = 0
        self.broken = False

    def start(self):
        node, worker, package = browser_runtime()
        self.process = subprocess.Popen(
            [node, str(worker), str(package), str(self.root.resolve())],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace', bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._read_errors, daemon=True).start()
        ready = self._receive(0, 65)
        if ready.get('headless') is not True or (ready.get('width'), ready.get('height')) != VIEWPORT:
            self.broken = True
            raise RuntimeError('浏览器未确认隔离无头模式和固定截图尺寸')
        self.record('browser_started', target=BROWSER_TARGET, **ready)

    def _read(self):
        try:
            for line in self.process.stdout:
                self.messages.put(json.loads(line))
        except (ValueError, OSError) as error:
            self.messages.put(error)
        finally:
            self.messages.put(None)

    def _read_errors(self):
        for line in self.process.stderr:
            self.stderr.append(line.strip())

    def _receive(self, request_id, timeout):
        deadline = time.monotonic() + timeout
        try:
            while True:
                self.stop.raise_if_requested()
                if time.monotonic() >= deadline:
                    raise TimeoutError('浏览器调用超时；效果未知，不自动重试')
                try:
                    reply = self.messages.get(timeout=.1)
                except queue.Empty:
                    continue
                if not isinstance(reply, dict) or reply.get('id') != request_id:
                    raise RuntimeError('浏览器响应中断或身份不匹配；不重复发送输入')
                if reply.get('ok') is not True:
                    raise RuntimeError('浏览器拒绝或失败：' + str(reply.get('error', 'unknown')))
                return reply
        except BaseException:
            self.broken = True
            raise

    def call(self, op, **args):
        if self.broken or self.process is None or self.process.poll() is not None:
            raise RuntimeError('浏览器会话已停止；需要显式启动新任务')
        self.stop.raise_if_requested()
        self.request_id += 1
        packet = {'id': self.request_id, 'op': op, **args}
        self.record('browser_request', request=packet)
        try:
            self.process.stdin.write(json.dumps(packet) + '\n')
            self.process.stdin.flush()
            reply = self._receive(self.request_id, 20)
        except BaseException:
            self.broken = True
            raise
        self.record('browser_reply', reply=reply)
        return reply['result']

    def close(self):
        process = self.process
        if process is None:
            return
        # EOF lets the worker close its own context/browser. Bound cleanup even on a stuck call.
        try:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            if process.poll() is None:
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                   capture_output=True, timeout=5,
                                   creationflags=subprocess.CREATE_NO_WINDOW, check=False)
                else:
                    process.kill()
                process.wait(timeout=5)
        finally:
            self.broken = True
            if self.stderr:
                self.record('browser_diagnostics', messages=list(self.stderr))


class BrowserObservation:
    def __init__(self, driver):
        self.driver = driver

    def observe(self, target, index):
        if target != BROWSER_TARGET:
            raise ValueError('浏览器目标必须是本次独立 Ancient Beast 页面')
        state = self.driver.call('observe')
        filename = state.get('filename')
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise RuntimeError('浏览器截图路径无效')
        path = (self.driver.root / filename).resolve()
        if not path.is_relative_to(self.driver.root.resolve()):
            raise RuntimeError('浏览器截图超出运行目录')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != state.get('sha256'):
            raise RuntimeError('浏览器截图哈希不匹配')
        with Image.open(path) as image:
            if image.size != VIEWPORT or (state.get('width'), state.get('height')) != VIEWPORT:
                raise RuntimeError('浏览器截图尺寸不匹配')
        return dict(BROWSER_TARGET), state, path

    @staticmethod
    def context(target, state, backend):
        return {'target': dict(target), 'url': state['url'],
                'scope': 'isolated browser viewport; no desktop or other pages',
                'capabilities': backend.capabilities(state)}


class BrowserExecutionBackend:
    default_delivery_mode = 'isolated_browser'
    allow_foreground_retry = False

    def __init__(self, driver):
        self.driver = driver

    def authorize(self, action, target):
        if target != BROWSER_TARGET:
            raise ValueError('只授权本次独立 Ancient Beast 页面')
        if action is not None and action.skill in {'switch_window', 'launch_app', 'focus_window'}:
            raise ValueError('浏览器模式不授权窗口切换或应用启动')

    def check_current(self, target, state):
        self.authorize(None, target)
        current = self.driver.call('state')
        if any(current.get(key) != state.get(key) for key in ('generation', 'url', 'width', 'height')):
            raise ObservationStale('浏览器页面或尺寸已变化；未发送输入，请重新截图')

    def prepare(self, action, state, execution_context=None):
        args = action.args
        if action.skill == 'wait':
            if args['duration_ms'] > 5000:
                raise ActionUnavailable('浏览器单次等待最多 5 秒')
            return None
        if action.skill in {'click', 'double_click', 'move_cursor'}:
            command = {'kind': 'move' if action.skill == 'move_cursor' else 'click',
                       'x': min(VIEWPORT[0] - 1, int(args['x'] * VIEWPORT[0])),
                       'y': min(VIEWPORT[1] - 1, int(args['y'] * VIEWPORT[1]))}
            if action.skill != 'move_cursor':
                if args['button'] not in {'left', 'right'}:
                    raise ActionUnavailable('浏览器游戏仅支持左键和右键')
                command.update(button=args['button'], count=2 if action.skill == 'double_click' else 1)
        elif action.skill == 'press_key':
            if args['key'] not in KEYS:
                raise ActionUnavailable('按键不在游戏白名单；不提供浏览器快捷键或开发者工具')
            command = {'kind': 'key', 'key': KEYS[args['key']]}
        elif action.skill == 'scroll':
            if 'x' in args:
                raise ActionUnavailable('浏览器滚动使用当前页面指针；先显式移动再滚动')
            amount = args['amount'] * (40 if args['by'] == 'line' else 640)
            if amount > 4000:
                raise ActionUnavailable('浏览器单次滚动最多 4000 CSS 像素；请缩小动作')
            dx = amount * (-1 if args['direction'] == 'left' else 1) if args['direction'] in {'left', 'right'} else 0
            dy = amount * (-1 if args['direction'] == 'up' else 1) if args['direction'] in {'up', 'down'} else 0
            command = {'kind': 'scroll', 'dx': dx, 'dy': dy}
        else:
            raise ActionUnavailable(f'浏览器首版不支持 {action.skill}；没有发送输入')
        return {'generation': state['generation'], 'action': command}

    def request_view(self, action, prepared, target, *, delivery_mode=None):
        return {'backend': 'browser', 'operation': 'action' if prepared else 'wait',
                'args': prepared or action.args, 'delivery_mode': self.default_delivery_mode}

    def dispatch(self, action, prepared, target, stop):
        if prepared is None:
            stop.sleep(action.args['duration_ms'] / 1000)
            return {'effect': 'confirmed', 'route': 'local_wait'}, target
        reply = self.driver.call('action', **prepared)
        if reply.get('stale'):
            # The worker independently rejects a navigation race before dispatch.
            raise RuntimeError('页面在预检后发生导航；驱动确认未发送输入，停止任务且不重试')
        return reply, target

    def retry_foreground(self, action, prepared, target, stop):
        raise ActionUnavailable('浏览器模式绝不回退到前台或系统键鼠')

    def capabilities(self, state):
        return {'available_skills': list(SKILLS), 'coordinate_space': 'browser_viewport',
                'delivery_mode': self.default_delivery_mode, 'allowed_keys': list(KEYS),
                'no_dom_or_game_state': True}
