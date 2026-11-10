"""OSWorld environment boundary; shared Trace2Task loops own every model call."""
import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path

from trace2task.execution_protocol import ActionUnavailable, ObservationStale


def deployment():
    path = Path(os.environ.get('TRACE2TASK_OSWORLD_CONFIG', 'UNCONFIGURED-OSWORLD'))
    value = json.loads(path.read_text())
    return path, value


def catalog():
    try:
        _, cfg = deployment()
        result = json.loads(Path(cfg['catalog']).read_text())
        result['configured'] = True
        return result
    except (OSError, ValueError, KeyError) as exc:
        return {'configured': False, 'tasks': [], 'error': str(exc)}


def selection(value):
    if not isinstance(value, dict) or set(value) != {'task_id', 'max_actions'}:
        raise ValueError('OSWorld requires task_id and max_actions')
    if type(value['max_actions']) is not int or not 1 <= value['max_actions'] <= 500:
        raise ValueError('OSWorld action budget must be 1..500')
    task = next((t for t in catalog()['tasks'] if t['id'] == value['task_id']), None)
    if task is None:
        raise ValueError('OSWorld task is not in the configured catalog')
    return dict(value), task


def native_action(action):
    """Map validated unified GUI operations to the existing bounded guest codec."""
    a = action.args
    def xy(x, y):
        return [round(x * 1000), round(y * 1000)]
    if action.skill in {'click', 'double_click', 'move_cursor'}:
        name = 'mouse_move' if action.skill == 'move_cursor' else 'double_click' if action.skill == 'double_click' else {'left': 'left_click', 'right': 'right_click', 'middle': 'middle_click'}[a['button']]
        if action.skill == 'double_click' and a['button'] != 'left':
            raise ActionUnavailable('Only left double-click is supported by this guest codec')
        return {'action': name, 'coordinate': xy(a['x'], a['y'])}
    if action.skill == 'drag':
        if a['button'] != 'left':
            raise ActionUnavailable('Guest codec only supports left drag')
        return [{'action': 'mouse_move', 'coordinate': xy(a['start_x'], a['start_y'])},
                {'action': 'left_click_drag', 'coordinate': xy(a['end_x'], a['end_y'])}]
    if action.skill == 'type_text':
        if 'x' in a:
            raise ActionUnavailable('Click the field first; text is a separate action')
        return {'action': 'type', 'text': a['text']}
    if action.skill in {'press_key', 'hotkey'}:
        return {'action': 'key', 'keys': a['keys'] if action.skill == 'hotkey' else [a['key']]}
    if action.skill == 'scroll':
        if a['direction'] not in {'up', 'down'} or a['by'] != 'line':
            raise ActionUnavailable('Existing guest codec supports vertical line scrolling only')
        return {'action': 'scroll', 'pixels': a['amount'] * (1 if a['direction'] == 'up' else -1)}
    if action.skill == 'wait':
        return None
    raise ActionUnavailable('OSWorld has no admitted GUI route: ' + action.skill)


def worker_environment(cfg):
    env = {k: v for k, v in os.environ.items() if k not in {
        'PYTHONPATH', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
        'http_proxy', 'https_proxy', 'all_proxy'}}
    bins = cfg.get('runtime_bin_dirs', [])
    if any(not Path(folder).is_absolute() or not Path(folder).is_dir() for folder in bins):
        raise ValueError('OSWorld runtime_bin_dirs must contain existing absolute directories')
    env.update(PYTHONUNBUFFERED='1', TMPDIR=cfg['tmp'])
    if bins:
        env['PATH'] = os.pathsep.join([*bins, env.get('PATH', os.defpath)])
    return env


class OSWorldDriver:
    def __init__(self, root, record, stop, config):
        self.root, self.record, self.stop = Path(root), record, stop
        self.config, self.task = selection(config)
        self.target = {'kind': 'osworld', 'task_id': self.task['id']}
        self.process = None
        self.messages = queue.Queue()
        self.failed = False
        self.counter = 0

    def start(self):
        import fcntl
        path, cfg = deployment()
        self.lease = Path(cfg['lock']).open('a+')
        try:
            fcntl.flock(self.lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lease.close()
            raise RuntimeError('Another mainline OSWorld guest owns the environment lease') from None
        worker = Path(__file__).resolve().parents[2] / 'scripts/osworld/worker.py'
        self.env_root = self.root / 'osworld'
        self.env_root.mkdir()
        self.log = (self.env_root/'worker.log').open('ab')
        env = worker_environment(cfg)
        self.process = subprocess.Popen([cfg['python'], str(worker), '--deployment', str(path),
            '--task', self.task['id'], '--output', str(self.env_root)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=self.log, text=True, env=env)
        def read():
            for line in self.process.stdout:
                try:
                    self.messages.put(json.loads(line))
                except ValueError:
                    self.messages.put({'ok': False, 'error': 'Invalid worker response'})
            self.messages.put({'ok': False, 'error': f'Environment worker exited; see {self.env_root / "worker.log"}'})
        threading.Thread(target=read, daemon=True).start()
        ready = self._receive(900)
        if ready['instruction'] != self.task['instruction']:
            raise RuntimeError('Task instruction differs from catalog')
        self.record('osworld_ready', **ready, source=catalog()['source'])

    def _receive(self, timeout, cancellable=True):
        until = time.monotonic() + timeout
        while True:
            if cancellable:
                self.stop.raise_if_requested()
            if time.monotonic() > until:
                self.failed = True
                raise TimeoutError('OSWorld worker deadline; outcome unknown, no retry')
            try:
                value = self.messages.get(timeout=.2)
            except queue.Empty:
                continue
            if not value.get('ok'):
                self.failed = True
                raise RuntimeError(value.get('error', 'OSWorld worker failed'))
            return value.get('result', value)

    def request(self, op, **payload):
        self.stop.raise_if_requested()
        if self.failed:
            raise RuntimeError('Environment failed; no automatic retry')
        self.process.stdin.write(json.dumps({'op': op, **payload}) + '\n')
        self.process.stdin.flush()
        return self._receive(120)

    def observe(self):
        self.counter += 1
        return self.request('observe', index=self.counter)

    def evaluate(self):
        result = self.request('evaluate')
        self.record('environment_evaluation', source=catalog()['source'], **result)
        return result

    def close(self):
        # EOF asks the owned worker to finish its in-flight operation and clean
        # its guest. Never kill a process/container discovered only by port.
        if self.process is not None:
            try:
                self.process.stdin.close()
                returncode = self.process.wait(timeout=960)
                if returncode:
                    raise RuntimeError(f'OSWorld worker exited {returncode}; inspect cleanup evidence')
            finally:
                self.log.close()
        if hasattr(self, 'lease') and not self.lease.closed:
            self.lease.close()


class OSWorldObservation:
    def __init__(self, driver):
        self.driver = driver

    def observe(self, target, index):
        if target is not None and target != self.driver.target:
            raise ObservationStale('OSWorld task changed')
        state = self.driver.observe()
        return dict(self.driver.target), state, Path(state['path'])

    @staticmethod
    def context(target, state, backend):
        return {'target': target, 'capabilities': backend.capabilities(state)}


class OSWorldExecutionBackend:
    default_delivery_mode = 'isolated_osworld_guest'
    allow_foreground_retry = False

    def __init__(self, driver):
        self.driver = driver

    def authorize(self, action, target):
        if target != self.driver.target:
            raise ValueError('OSWorld task/guest identity differs')

    def check_current(self, target, state):
        self.authorize(None, target)
        if self.driver.failed:
            raise RuntimeError('OSWorld transport failed')

    def prepare(self, action, state, execution_context=None):
        return native_action(action)

    def request_view(self, action, prepared, target, **kwargs):
        return {'backend': 'osworld', 'operation': action.skill, 'native_action': prepared,
                'delivery_mode': self.default_delivery_mode, 'target': target}

    def dispatch(self, action, prepared, target, stop):
        if prepared is None:
            stop.sleep(action.args['duration_ms'] / 1000)
            return {'effect': 'confirmed', 'route': 'local_wait'}, target
        receipt = self.driver.request('act', action=prepared)
        if not receipt['executed']:
            raise ActionUnavailable(receipt['reason'])
        return {'effect': 'unverifiable', 'route': 'official_guest_gui', 'native': receipt,
                'delivery': {'mode': self.default_delivery_mode}}, target

    def capabilities(self, state):
        return {'available_skills': ['click', 'double_click', 'move_cursor', 'drag', 'type_text',
                                    'press_key', 'hotkey', 'scroll', 'wait'], 'unavailable_skills': {},
                'constraints': ['Drag uses the pinned guest fixed duration of 1000 ms, left button only',
                    'Scroll supports vertical line units only; click before typing',
                    'No terminal or arbitrary command execution']}


def finish_evaluation(driver, result):
    if result.get('stop_reason') in {'error', 'emergency_stop', 'time_limit'}:
        result['environment_evaluation'] = {'valid': False, 'raw_score': None,
                                            'invalid_reason': 'Run interrupted or failed; not scored'}
        return
    value = driver.evaluate()
    result['environment_evaluation'] = value
    result['agent_reported_complete'] = result.get('task_complete', False)
    result['verified'] = bool(value['valid'])
    result['task_complete'] = bool(value['valid'] and value['binary_reward'])
