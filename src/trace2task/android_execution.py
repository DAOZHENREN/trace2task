"""Pixel-only, single-touch Android backend. No connect, launch or shell tool for models.

ADB input uses current display coordinates (not physical portrait coordinates).
The selected transport and boot must remain unchanged. Input delivery is never
reported as visual success. Multi-touch is intentionally not advertised.
Dedicated movement gestures use a single-contact helper with a device-side
deadline and heartbeat watchdog; ordinary ADB tap/long-press/swipe semantics stay unchanged.
"""
from __future__ import annotations

import hashlib
import io
import re
import subprocess
import time
from pathlib import Path

from PIL import Image

from trace2task.execution_protocol import ActionUnavailable, ObservationStale, UnifiedAction

SKILLS = ('click', 'hold_mouse', 'drag', 'press_key', 'wait', 'joystick_move', 'camera_drag')
STATE_KEYS = ('boot_id', 'focus', 'rotation', 'width', 'height')
SCREENSHOT_TIMEOUT_SECONDS = 60
SCREENSHOT_MAX_ATTEMPTS = 3


class AndroidAppNotFocused(RuntimeError):
    """A handoff may wait for this app; it must not capture another app."""


class AndroidDriver:
    def __init__(self, root, record, stop, *, adb, serial, package, server_port=5038, launch_app=False):
        if (not isinstance(serial, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]+', serial)
                or not isinstance(package, str)
                or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+', package)
                or type(server_port) is not int or not 1 <= server_port <= 65535):
            raise ValueError('Explicit Android serial, package and server port required')
        self.root, self.record, self.stop = Path(root), record, stop
        if type(launch_app) is not bool:
            raise ValueError('launch_app must be a boolean')
        self.launch_app = launch_app
        self.adb, self.serial, self.package, self.port = str(adb), serial, package, server_port
        self.transport = None
        self.boot_id = None
        self.broken = False
        self._touch_session = None
        self._last_observation = None
        self.target = {'kind': 'android', 'serial': serial, 'package': package,
                       'server_port': server_port}

    def _run(self, *args, device=True):
        if self.broken:
            raise RuntimeError('Android session stopped; no automatic reconnect or retry')
        self.stop.raise_if_requested()
        command = [self.adb, '-P', str(self.port)]
        if device:
            if self.transport is None:
                raise RuntimeError('Android transport not bound')
            command += ['-t', str(self.transport)]
        command += list(args)
        screenshot = device and args == ('exec-out', 'screencap', '-p')
        try:
            if screenshot:
                return self._read_screenshot(command)
            return subprocess.run(command, capture_output=True, check=True,
                                  timeout=20, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
        except subprocess.TimeoutExpired:
            # Only a pixel read may recover; an uncertain input is never retried.
            if not screenshot:
                self.broken = True
            raise
        except (OSError, subprocess.SubprocessError):
            self.broken = True
            raise

    def _read_screenshot(self, command):
        deadline = time.monotonic() + SCREENSHOT_TIMEOUT_SECONDS
        with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
            try:
                while True:
                    self.stop.raise_if_requested()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, SCREENSHOT_TIMEOUT_SECONDS)
                    try:
                        output, error = process.communicate(timeout=min(.25, remaining))
                    except subprocess.TimeoutExpired:
                        continue
                    self.stop.raise_if_requested()
                    if process.returncode:
                        raise subprocess.CalledProcessError(process.returncode, command, output, error)
                    return output
            finally:
                if process.poll() is None:
                    # Reap only this read-only client; never reset ADB or reconnect.
                    process.kill()
                    process.communicate()

    def _check_screenshot_transport(self):
        # A CLI `devices` query could start or replace a lost/incompatible daemon.
        # Query the already-running server directly before allowing another read.
        from trace2task.android_discovery import _host

        self.stop.raise_if_requested()
        try:
            if int(_host(self.port, 'host:version'), 16) != 41:
                raise RuntimeError('ADB 服务版本不兼容')
            rows = _host(self.port, 'host:devices-l').splitlines()
        except (OSError, RuntimeError, ValueError) as error:
            self.broken = True
            raise RuntimeError('截图重试已停止：无法核验原 ADB 服务；未重启或重新连接') from error
        matches = [re.search(r'\btransport_id:(\d+)\b', row) for row in rows
                   if row.split()[:2] == [self.serial, 'device']]
        if len(matches) != 1 or matches[0] is None or int(matches[0][1]) != self.transport:
            self.broken = True
            raise RuntimeError('截图重试已停止：所选手机离线或 ADB 通道身份已变化；未重新连接')

    def bind(self):
        if self.transport is not None or self.broken:
            raise RuntimeError('Android driver cannot be restarted')
        rows = self._run('devices', '-l', device=False).decode().splitlines()
        matches = [re.search(r'\btransport_id:(\d+)\b', row) for row in rows
                   if row.split()[:2] == [self.serial, 'device']]
        if len(matches) != 1 or matches[0] is None:
            raise RuntimeError(f'手机 {self.serial} 未连接、离线或未授权；请先点击「连接云手机」，再检查设备')
        self.transport = int(matches[0][1])
        boot = self._run('shell', 'cat', '/proc/sys/kernel/random/boot_id').decode().strip()
        if not re.fullmatch(r'[0-9a-f-]{36}', boot):
            raise RuntimeError('Android boot identity could not be verified')
        self.boot_id = boot
        self.target.update(transport_id=self.transport, boot_id=boot)

    def launcher(self):
        raw = self._run('shell', 'cmd', 'package', 'resolve-activity', '--brief',
                        '-a', 'android.intent.action.MAIN', '-c', 'android.intent.category.LAUNCHER',
                        '-p', self.package).decode(errors='replace')
        components = [line.strip() for line in raw.splitlines()
                      if re.fullmatch(re.escape(self.package) + r'/[A-Za-z0-9_.$]+', line.strip())]
        if len(components) != 1:
            raise RuntimeError(f'授权应用 {self.package} 未安装或没有唯一启动入口；请核对包名')
        return components[0]

    def start(self):
        self.bind()
        if self.launch_app:
            component = self.launcher()
            # Fixed explicit component only: no shell string, force-stop, reset or model-supplied intent.
            response = self._run('shell', 'am', 'start', '-W', '-n', component).decode(errors='replace')
            self.record('android_app_launch', target=dict(self.target), component=component, response=response)
            if 'Error:' in response or 'Exception' in response:
                raise RuntimeError(f'无法打开授权应用 {self.package}: {response[:500]}')
            for attempt in range(20):
                try:
                    state = self.state()
                    break
                except RuntimeError as error:
                    if 'not focused' not in str(error) or attempt == 19:
                        raise
                    self.stop.sleep(.5)
        state = self.state()
        self.boot_id = state['boot_id']
        self.target.update(transport_id=self.transport, boot_id=self.boot_id)
        self.root.mkdir(parents=True, exist_ok=True)
        self.record('android_started', target=self.target, state=state)

    def state(self):
        boot = self._run('shell', 'cat', '/proc/sys/kernel/random/boot_id').decode().strip()
        if not re.fullmatch(r'[0-9a-f-]{36}', boot) or (self.boot_id and boot != self.boot_id):
            self.broken = True
            raise RuntimeError('Android boot identity changed or could not be verified')
        windows = self._run('shell', 'dumpsys', 'window').decode(errors='replace')
        focus = re.search(r'mCurrentFocus=Window\{[^\n]*?\bu\d+\s+([^\s}]+)', windows)
        if not focus or focus[1].split('/')[0] != self.package:
            raise AndroidAppNotFocused('Selected Android package is not focused; no input authorized')
        info = self._run('shell', 'dumpsys', 'input').decode(errors='replace')
        values = {}
        for field in ('Width', 'Height', 'Orientation'):
            found = re.findall(r'Surface' + field + r':\s*(\d+)', info)
            if len(found) != 1:
                raise RuntimeError('Android display geometry is unavailable or ambiguous')
            values[field] = int(found[0])
        width, height, rotation = values['Width'], values['Height'], values['Orientation']
        if rotation not in (0, 1, 2, 3) or not (1 <= width <= 8192 and 1 <= height <= 8192):
            raise RuntimeError('Unsupported Android display geometry')
        if rotation % 2:
            width, height = height, width
        return {'boot_id': boot, 'focus': focus[1], 'rotation': rotation,
                'width': width, 'height': height}

    def observe(self, index):
        self._last_observation = self.capture(self.root / f'android-{index:04d}.png')
        return self._last_observation

    def capture(self, path):
        for attempt in range(1, SCREENSHOT_MAX_ATTEMPTS + 1):
            if attempt > 1:
                self._check_screenshot_transport()
            before = self.state()  # Checks the bound boot identity and authorized foreground app.
            if attempt > 1:
                self.record('android_capture_retry', attempt=attempt,
                            max_attempts=SCREENSHOT_MAX_ATTEMPTS,
                            timeout_seconds=SCREENSHOT_TIMEOUT_SECONDS)
            try:
                data = self._run('exec-out', 'screencap', '-p')
                break
            except subprocess.TimeoutExpired as error:
                self.stop.raise_if_requested()
                self.record('android_capture_timeout', attempt=attempt,
                            max_attempts=SCREENSHOT_MAX_ATTEMPTS,
                            timeout_seconds=SCREENSHOT_TIMEOUT_SECONDS)
                if attempt == SCREENSHOT_MAX_ATTEMPTS:
                    self.broken = True
                    raise TimeoutError(
                        f'手机截图连续 {attempt} 次超过 {SCREENSHOT_TIMEOUT_SECONDS} 秒，'
                        '已停止本次任务；请检查云手机连接') from error
                self.stop.sleep(1)
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if image.format != 'PNG' or image.size != (before['width'], before['height']):
                raise RuntimeError('Android screenshot does not match current display geometry')
        after = self.state()
        if after != before:
            raise ObservationStale('Android target changed during capture; no input sent')
        path = Path(path)
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError('Android snapshot must stay in this run directory')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if attempt > 1:
            self.record('android_capture_recovered', attempt=attempt, screenshot=str(path))
        return {**before, 'sha256': hashlib.sha256(data).hexdigest()}, path

    def create_touch_session(self, state, stop, *, is_current):
        from trace2task.android_touch import BoundedTouchSession
        if self._touch_session is not None:
            raise RuntimeError('A previous Android contact has not been released')
        self._touch_session = BoundedTouchSession(self, state, stop, is_current=is_current)
        return self._touch_session

    def close(self):
        try:
            if self._touch_session is not None:
                self._touch_session.close()
                self._touch_session = None
        finally:
            # Never disconnect the device, stop its app or shut down the ADB server.
            self.broken = True


class AndroidObservation:
    def __init__(self, driver):
        self.driver = driver

    def intervention_snapshot(self, identifier, phase):
        if not re.fullmatch(r'takeover-[1-8]', identifier) or phase not in {'before', 'after'}:
            raise ValueError('Invalid Android handoff snapshot')
        state, path = self.driver.capture(self.driver.root / 'interventions' / f'{identifier}-{phase}.png')
        return {'path': str(path), 'sha256': state['sha256'], 'package': self.driver.package,
                'width': state['width'], 'height': state['height'], 'rotation': state['rotation']}

    def observe(self, target, index):
        if target != self.driver.target:
            raise ValueError('Android target mismatch')
        state, path = self.driver.observe(index)
        return dict(target), state, path

    @staticmethod
    def context(target, state, backend):
        return {'target': target, 'scope': 'selected Android foreground package, pixels only',
                'width': state['width'], 'height': state['height'],
                'capabilities': backend.capabilities(state)}


class AndroidExecutionBackend:
    default_delivery_mode = 'remote_android'
    allow_foreground_retry = False

    def __init__(self, driver, *, interventions=None, experimental_navigation=False):
        self.driver = driver
        self.interventions = interventions
        # Internal research/tests only; the workbench never enables this controller.
        self.skills = SKILLS + (('navigate_to',) if experimental_navigation else ())
        self.navigation_failures = 0
        self.navigation_revision = None

    def authorize(self, action, target):
        if target != self.driver.target:
            raise ValueError('Only the bound Android device and package are authorized')
        if action is not None and action.skill not in self.skills:
            raise ActionUnavailable('Unsupported Android action; no desktop fallback')

    def check_current(self, target, state):
        self.authorize(None, target)
        current = self.driver.state()
        if any(current[k] != state[k] for k in STATE_KEYS):
            raise ObservationStale('Android focus or rotation changed; capture again')

    def prepare(self, action, state, execution_context=None):
        action = UnifiedAction.from_payload(action.to_payload())
        args = action.args
        if action.skill not in self.skills or args.get('button', 'left') != 'left':
            raise ActionUnavailable('Android supports single-touch only, no keyboard/text/desktop tools')
        if action.skill != 'joystick_move' and args.get('duration_ms', 0) > 1500:
            raise ActionUnavailable('Android actions/waits other than joystick_move are bounded to 1500 ms')
        if action.skill == 'camera_drag':
            from trace2task.android_camera import prepare_camera
            if getattr(self.driver, '_touch_session', None) is not None:
                raise ActionUnavailable('转镜头前必须释放移动触点；不支持多指同时操作')
            return prepare_camera(action, state, getattr(self.driver, '_last_observation', None))
        if action.skill in {'joystick_move', 'navigate_to'}:
            from trace2task.android_navigation import prepare_navigation
            revision = (execution_context or {}).get('intervention_revision')
            if (self.interventions is not None and self.interventions.current(revision)
                    and revision != self.navigation_revision):
                self.navigation_revision = revision
                self.navigation_failures = 0
            if self.navigation_failures >= 3:
                raise ActionUnavailable('连续三次导航无有效进展；需要重新定位或人工接管，不再自动移动')
            prepared = prepare_navigation(action, state, getattr(self.driver, '_last_observation', None))
            prepared['intervention_revision'] = (execution_context or {}).get('intervention_revision')
            return prepared
        def xy(x, y):
            return [str(min(state['width'] - 1, int(x * state['width']))),
                    str(min(state['height'] - 1, int(y * state['height'])))]
        if action.skill == 'wait':
            return None
        if action.skill == 'click':
            command = ['tap', *xy(args['x'], args['y'])]
        elif action.skill == 'hold_mouse':
            point = xy(args['x'], args['y'])
            command = ['swipe', *point, *point, str(args['duration_ms'])]
        elif action.skill == 'drag':
            command = ['swipe', *xy(args['start_x'], args['start_y']),
                       *xy(args['end_x'], args['end_y']), str(args['duration_ms'])]
        else:
            if args['key'] != 'escape':
                raise ActionUnavailable('Only escape (Android BACK) is authorized')
            command = ['keyevent', '4']
        return {'state': {k: state[k] for k in STATE_KEYS}, 'command': command}

    def request_view(self, action, prepared, target, *, delivery_mode=None):
        operation = ('camera_drag' if action.skill == 'camera_drag'
                     else 'bounded_navigation' if action.skill in {'joystick_move', 'navigate_to'}
                     else 'input' if prepared else 'wait')
        return {'backend': 'android', 'operation': operation,
                'args': prepared or action.args, 'delivery_mode': self.default_delivery_mode}

    def dispatch(self, action, prepared, target, stop):
        self.authorize(action, target)
        if prepared is None:
            stop.sleep(action.args['duration_ms'] / 1000)
            return {'effect': 'confirmed', 'route': 'local_wait'}, target
        self.check_current(target, prepared['state'])
        # Rebuild the command rather than trusting a raw shell argument vector.
        context = {'intervention_revision': prepared.get('intervention_revision')}
        checked = self.prepare(action, prepared['state'], context)
        if checked != prepared:
            raise ValueError('Android prepared command mismatch')
        stop.raise_if_requested()
        if action.skill in {'joystick_move', 'navigate_to'}:
            from trace2task.android_navigation import execute_navigation
            revision = prepared['intervention_revision']
            receipt = execute_navigation(
                self.driver, action, prepared, stop,
                is_current=lambda: self.interventions is None or self.interventions.current(revision),
            )
            outcome = receipt['navigation']['outcome']
            progress = receipt['navigation']['progress_px']
            if (outcome in {'target_lost', 'target_ambiguous', 'moving_away', 'no_progress', 'feedback_too_slow'}
                    or action.skill == 'navigate_to' and outcome == 'duration_limit'
                    and (progress is None or progress < 1.5)):
                self.navigation_failures += 1
            elif action.skill == 'navigate_to' and outcome != 'intervened':
                self.navigation_failures = 0
            return receipt, target
        self.driver._run('shell', 'input', *checked['command'])
        if action.skill == 'camera_drag':
            return {'effect': 'unverifiable', 'route': 'adb_camera_swipe',
                    'delivery': {'mode': self.default_delivery_mode}, 'reobserve': True,
                    'camera': checked['camera'],
                    'note': 'One swipe returned; inspect a fresh screenshot for direction and extent. '
                            'Do not reuse the old camera heading or assume task completion.'}, target
        return {'effect': 'unverifiable', 'route': 'adb_single_touch',
                'delivery': {'mode': self.default_delivery_mode},
                'note': 'Input command returned; re-observe to establish visual effect'}, target

    def retry_foreground(self, action, prepared, target, stop):
        raise ActionUnavailable('Android never falls back to Windows input')

    def capabilities(self, state):
        capabilities = {'available_skills': list(self.skills), 'coordinate_space': 'android_screenshot',
                'delivery_mode': self.default_delivery_mode, 'allowed_keys': ['escape'],
                'duration_limits_ms': {'hold_mouse': 1500, 'drag': 1500, 'wait': 1500,
                                       'joystick_move': None, 'camera_drag': 1500},
                'multi_touch': False, 'no_hidden_game_state': True,
                'camera': {'gesture': 'single continuous swipe, then release',
                           'fresh_observation_before_and_after': True,
                           'angle_calibration': 'unknown; inspect visible response after each swipe'}}
        if 'navigate_to' in self.skills:
            capabilities['duration_limits_ms']['navigate_to'] = 1500
            capabilities['navigation'] = {'frame': 'visible_minimap', 'max_duration_ms': 1500,
                               'radius_unit': 'fraction_of_shorter_screenshot_side',
                               'heading': 'camera heading clockwise from minimap up',
                               'arrival': 'marker within stop_radius of fixed player anchor; candidate only',
                               'release_on': ['stop', 'time_budget', 'intervention', 'target_lost',
                                              'no_progress', 'scope_change', 'heartbeat_timeout']}
        return capabilities
