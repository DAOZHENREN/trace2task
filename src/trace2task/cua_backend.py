"""Experimental window-scoped Cua transport. No foreground fallback or retries."""
import json
import os
import subprocess
import time
from pathlib import Path

from trace2task.cua_window import inspect_window

DEFAULT_DRIVER = 'D:/Tools/cua-driver-probe/v0.28.2/cua-driver-rs-0.28.2-windows-x86_64/cua-driver.exe'
EXCLUDED_APPS = {'trace2task.exe', 'chatgpt.exe', 'codex.exe', 'cua-driver.exe'}


def valid_window_bounds(value):
    return (isinstance(value, dict)
            and all(type(value.get(k)) is int for k in ('x', 'y', 'width', 'height'))
            and value['width'] > 0 and value['height'] > 0)


def response_value(stdout, returncode=0):
    if returncode:
        raise RuntimeError(f'Cua process failed ({returncode}): {stdout[:500]}')
    try:
        value = json.loads(stdout)
    except ValueError:
        raise RuntimeError('Cua returned a non-JSON error: ' + stdout[:500]) from None
    if not isinstance(value, dict):
        raise RuntimeError('Invalid Cua response')  # noqa: TRY004 - transport protocol failure
    if value.get('isError') or value.get('error') or value.get('refusal') or value.get('status') in {'refused', 'error', 'failed'}:
        raise RuntimeError('Cua refused/failed: ' + json.dumps(value, ensure_ascii=False)[:1000])
    return value


def action_request(call, state):
    """Translate normalized WINDOW screenshot coordinates, never desktop coordinates."""
    if not isinstance(state, dict):
        raise ValueError('Cua 缺少有效窗口状态；未执行')  # noqa: TRY004 - action validation contract
    if any(type(state.get(key)) is not int or state[key] <= 0 for key in ('pid', 'window_id')):
        raise ValueError('Cua 返回无效窗口身份；未执行')
    if not isinstance(state.get('session'), str) or not state['session']:
        raise ValueError('Cua 缺少有效会话身份；未执行')

    def pixels(key):
        value = state.get(key)
        if type(value) is not int or value <= 0:
            raise ValueError('Cua 返回无效截图尺寸；未执行')
        return value

    args = call.args
    common = {k: state[k] for k in ('pid', 'window_id')}
    common.update(session=state['session'], delivery_mode='background')
    if call.skill == 'drag':
        width, height = pixels('screenshot_width'), pixels('screenshot_height')
        return 'drag', {**common,
            'from_x': min(width-1, int(args['start_x']*width)),
            'from_y': min(height-1, int(args['start_y']*height)),
            'to_x': min(width-1, int(args['end_x']*width)),
            'to_y': min(height-1, int(args['end_y']*height)),
            'button': args['button'], 'duration_ms': args['duration_ms']}
    if call.skill == 'scroll':
        return 'scroll', {**common, **args}
    if call.skill in {'click', 'double_click'}:
        width, height = pixels('screenshot_width'), pixels('screenshot_height')
        return 'click', {**common, 'x': min(width-1, int(args['x']*width)),
                         'y': min(height-1, int(args['y']*height)),
                         'button': args['button'], 'count': 2 if call.skill == 'double_click' else 1}
    if call.skill == 'type_text':
        # Do not guess which of several editable controls owns input focus.
        elements = state.get('elements')
        if (not state.get('elements_complete') or not isinstance(elements, list)
                or any(not isinstance(element, dict) for element in elements)):
            raise ValueError('Cua 文本控件树不完整；未输入文本')
        editable = [element for element in elements if element.get('enabled') is True
                    and isinstance(element.get('actions'), list)
                    and 'set_value' in element['actions']
                    and isinstance(element.get('element_token'), str)
                    and element['element_token']]
        if len(editable) != 1:
            raise ValueError('Cua 文本目标不唯一或控件树不完整；未输入文本')
        return 'type_text', {**common, 'element_token': editable[0]['element_token'], 'text': args['text']}
    if call.skill == 'press_key':
        return 'press_key', {**common, 'key': {'enter':'return', 'page_up':'pageup', 'page_down':'pagedown'}.get(args['key'], args['key'])}
    if call.skill == 'hotkey':
        keys = args['keys']
        if any(k not in {'ctrl', 'shift', 'alt'} for k in keys[:-1]) or keys[-1] in {'ctrl', 'shift', 'alt'}:
            raise ValueError('Cua 组合键必须是修饰键加一个普通按键')
        return 'press_key', {**common, 'key': {'enter':'return', 'page_up':'pageup', 'page_down':'pagedown'}.get(keys[-1], keys[-1]), 'modifiers': keys[:-1]}
    raise ValueError(f'Cua 实验后端暂不支持 {call.skill}；未执行')


class CuaBackend:
    def __init__(self, root, record):
        self.root, self.record = Path(root), record
        self.driver = Path(os.environ.get('TRACE2TASK_CUA_DRIVER', DEFAULT_DRIVER))
        self.pipe = r'\\.\pipe\trace2task-' + self.root.name
        self.process = None
        self.env = {**os.environ, 'CUA_DRIVER_RS_TELEMETRY_ENABLED': 'false'}

    def start(self):
        if not self.driver.is_file():
            raise RuntimeError('未找到 Cua Driver；设置 TRACE2TASK_CUA_DRIVER')
        with (self.root/'cua-server.log').open('ab') as stream:
            self.process = subprocess.Popen([str(self.driver), 'serve', '--socket', self.pipe],
                stdout=stream, stderr=stream, env=self.env, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        # Read-only readiness retries are safe; input is never retried.
        deadline = time.monotonic()+10
        while True:
            try:
                return self.call('list_windows', {})
            except RuntimeError:
                if self.process.poll() is not None or time.monotonic() >= deadline:
                    raise
                time.sleep(.2)

    def call(self, tool, payload):
        start = time.perf_counter()
        try:
            result = subprocess.run([str(self.driver), 'call', tool, '--socket', self.pipe],
                input=json.dumps(payload, ensure_ascii=False), capture_output=True, encoding='utf-8',
                timeout=20, env=self.env, check=False,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except subprocess.TimeoutExpired:
            self.record('cua_timeout', tool=tool, request=payload, effect='unknown')
            raise RuntimeError('Cua 调用超时，效果未知；不重试，请检查目标窗口') from None
        self.record('cua_reply', tool=tool, request=payload, stdout=result.stdout,
                    stderr=result.stderr, returncode=result.returncode,
                    elapsed_ms=round((time.perf_counter()-start)*1000, 2))
        return response_value(result.stdout or result.stderr, result.returncode)

    def observe(self, target, step):
        path = (self.root / f'{step:04d}.png').resolve()
        state = self.call('get_window_state', {**target, 'session':self.root.name,
                          'screenshot_out_file':str(path), 'max_dimension':1920})
        if (state.get('pid'), state.get('window_id')) != (target['pid'], target['window_id']):
            raise RuntimeError('Cua returned another target')
        if not path.is_file() or not state.get('screenshot_width') or not state.get('screenshot_height'):
            raise RuntimeError('Cua 缺少有效窗口截图')
        if not valid_window_bounds(state.get('window_bounds')):
            raise RuntimeError('Cua 缺少有效窗口位置/尺寸；未执行任何输入')
        state['session'] = self.root.name
        return state, path

    def windows(self):
        return [w for w in self.call('list_windows', {}).get('windows', [])
                if not w.get('minimized') and w.get('app_name', '').lower()
                not in EXCLUDED_APPS]

    @staticmethod
    def _usable_window(window):
        label = f"{window.get('title', '')} (PID={window['pid']}, HWND={window['window_id']})"
        if window.get('app_name', '').lower() in EXCLUDED_APPS:
            raise RuntimeError(f'该窗口属于控制程序或驱动内部窗口，不能作为任务目标：{label}')
        if window.get('minimized'):
            raise RuntimeError(f'选中的窗口或其宿主已最小化，请先还原：{label}')
        return window

    def window(self, target):
        """Resolve only an exact window or its proven native root, not a lookalike."""
        if (not isinstance(target, dict) or set(target) != {'pid', 'window_id'}
                or any(type(v) is not int or v <= 0 for v in target.values())):
            raise ValueError('请先选择有效的 Cua 目标窗口身份')
        try:
            rows = self.call('list_windows', {}).get('windows', [])
            matches = [w for w in rows if all(w.get(k) == v for k, v in target.items())]
            if len(matches) == 1:
                return self._usable_window(matches[0])
            if matches:
                raise RuntimeError('Cua 返回重复窗口身份；未绑定不明确的目标')
            native = inspect_window(target)
            if any(native.get(k) != v for k, v in target.items()):
                raise RuntimeError('原生核验返回的窗口身份不同；未绑定其他窗口')
            self._usable_window(native)
            root = native['root_target']
            roots = [w for w in rows if all(w.get(k) == v for k, v in root.items())]
            if root == target or len(roots) != 1:
                raise RuntimeError(
                    f"窗口仍存在，但 Cua 未提供可用宿主（PID={target['pid']}, "
                    f"HWND={target['window_id']}）；未改用同名或同进程的其他窗口")
            effective = self._usable_window(roots[0])
            self.record('window_binding', selected=target, effective=root,
                        reason='verified_native_ancestor', title=native.get('title', ''))
            return effective
        except RuntimeError as error:
            self.record('window_validation_failed', selected=target, error=str(error))
            raise

    def bind(self, target):
        if isinstance(target, dict) and set(target) == {'launch_path'}:
            apps = self.call('list_apps', {}).get('apps', [])
            matches = [a for a in apps if a.get('launch_path') == target['launch_path']
                       and a.get('launch_path')]
            if not matches:
                raise ValueError('应用不在当前启动目录中；请刷新应用列表')
            before = {(w['pid'], w['window_id']) for w in self.windows()}
            receipt = self.call('launch_app', target)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                windows = self.windows()
                # Launch PID is not a window identity (stub/UWP processes).
                reported = {w.get('window_id') for w in receipt.get('windows', [])}
                candidates = [w for w in windows if w['window_id'] in reported]
                if not candidates:
                    candidates = [w for w in windows if (w['pid'], w['window_id']) not in before
                                  and w.get('app_name', '').lower() == matches[0].get('name', '').lower()]
                if len(candidates) == 1:
                    return {k: candidates[0][k] for k in ('pid', 'window_id')}
                if len(candidates) > 1:
                    break
                time.sleep(.2)
            raise RuntimeError('应用已请求启动，但不能唯一确定其窗口；请刷新并手动选择窗口，不会重复启动')
        window = self.window(target)
        return {k: window[k] for k in ('pid', 'window_id')}

    def close(self):
        if self.process and self.process.poll() is None:
            # Only the private daemon this instance created and its workers.
            subprocess.run(['taskkill.exe','/PID',str(self.process.pid),'/T','/F'],
                           capture_output=True, check=False,
                           creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.process.wait(timeout=5)
