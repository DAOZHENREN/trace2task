"""Synthetic transport tests; no Android/Windows input or model calls."""
import io
import json
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
from PIL import Image
from test_chat_agent_bridge import Session, Stop, _reply

from trace2task import android_discovery, android_execution, execution_budget
from trace2task.android_execution import SKILLS, AndroidDriver, AndroidExecutionBackend
from trace2task.chat_agent_runner import run_chat_agent
from trace2task.chat_model_adapter import ChatModelAdapter, plan_schema
from trace2task.execution_core import ExecutionCore
from trace2task.execution_protocol import ActionUnavailable, ObservationStale, UnifiedAction
from trace2task.windows_runner import EmergencyStopRequested


def action(skill, **args):
    return UnifiedAction.from_payload({'skill': skill, 'args': args})


@pytest.fixture
def phone(tmp_path, monkeypatch):
    data = {'rotation': 1, 'package': 'com.example.game', 'boot': 'a' * 36,
            'transport': 7, 'commands': [], 'fail_input': False, 'size': (1280, 720),
            'serial': 'phone', 'device_state': 'device', 'clock': 0.0,
            'capture_timeouts': 0, 'processes': [], 'events': [], 'timeouts': [], 'host_queries': []}
    def run(command, **kwargs):
        data['commands'].append(command)
        data['timeouts'].append(kwargs.get('timeout'))
        args = command[5:]
        if command[3:] == ['devices', '-l']:
            value = f"{data['serial']} {data['device_state']} transport_id:{data['transport']}".encode()
        elif args == ['shell', 'cat', '/proc/sys/kernel/random/boot_id']:
            value = data['boot'].encode()
        elif args == ['shell', 'dumpsys', 'window']:
            value = f"mCurrentFocus=Window{{abc u0 {data['package']}/.Main}}".encode()
        elif args[:4] == ['shell', 'cmd', 'package', 'resolve-activity']:
            value = b'com.example.game/.Main'
        elif args[:4] == ['shell', 'am', 'start', '-W']:
            data['package'] = 'com.example.game'
            value = b'Status: ok'
        elif args == ['shell', 'dumpsys', 'input']:
            value = f"SurfaceWidth: 720px\nSurfaceHeight: 1280px\nSurfaceOrientation: {data['rotation']}".encode()
        elif args == ['exec-out', 'screencap', '-p']:
            image = io.BytesIO()
            Image.new('RGB', data['size']).save(image, 'PNG')
            value = image.getvalue()
        elif args[:2] == ['shell', 'input']:
            if data['fail_input']:
                raise subprocess.TimeoutExpired(command, 20)
            value = b''
        else:
            raise AssertionError(command)
        return SimpleNamespace(stdout=value)

    class CaptureProcess:
        def __init__(self, command, **kwargs):
            assert command[5:] == ['exec-out', 'screencap', '-p']
            self.command = command
            self.output = run(command).stdout
            self.stall = len(data['processes']) < data['capture_timeouts']
            self.returncode = None
            self.killed = self.closed = False
            data['processes'].append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True

        def poll(self):
            return self.returncode

        def kill(self):
            self.killed = True
            self.returncode = -9
            if data.get('on_kill'):
                data['on_kill']()

        def communicate(self, timeout=None):
            if self.killed:
                return b'partial screenshot', b''
            if self.stall:
                data['clock'] += timeout
                raise subprocess.TimeoutExpired(self.command, timeout, output=b'partial screenshot')
            self.returncode = data.get('capture_exitcode', 0)
            if data.get('on_capture'):
                data['on_capture']()
            return self.output, b'device offline' if self.returncode else b''

    monkeypatch.setattr(subprocess, 'run', run)
    monkeypatch.setattr(subprocess, 'Popen', CaptureProcess)
    def host(port, command):
        data['host_queries'].append((port, command))
        if command == 'host:version':
            return data.get('server_version', '0029')
        assert command == 'host:devices-l'
        return f"{data['serial']} {data['device_state']} transport_id:{data['transport']}"
    monkeypatch.setattr(android_discovery, '_host', host)
    monkeypatch.setattr(android_execution, 'time', SimpleNamespace(monotonic=lambda: data['clock']))
    driver = AndroidDriver(tmp_path, lambda kind, **kw: data['events'].append({'kind': kind, **kw}), Stop(), adb='adb',
                           serial='phone', package='com.example.game')
    driver.start()
    return driver, data


def test_capture_binds_transport_rotation_and_boot(phone):
    driver, data = phone
    state, path = driver.observe(0)
    assert (state['width'], state['height']) == (1280, 720)
    assert Image.open(path).size == (1280, 720)
    assert driver.target['transport_id'] == 7
    assert all(c[3:5] == ['-t', '7'] for c in data['commands'][1:])
    assert not any('input' in c and 'shell' in c and 'dumpsys' not in c for c in data['commands'])


@pytest.mark.parametrize('skill,args,command', [
    ('click', {'x': 1, 'y': .5}, ['tap', '1279', '360']),
    ('hold_mouse', {'x': .5, 'y': .5, 'duration_ms': 100}, ['swipe', '640', '360', '640', '360', '100']),
    ('drag', {'start_x': 0, 'start_y': 0, 'end_x': 1, 'end_y': 1, 'duration_ms': 200},
     ['swipe', '0', '0', '1279', '719', '200']),
    ('press_key', {'key': 'escape'}, ['keyevent', '4']),
])
def test_input_semantics(phone, skill, args, command):
    driver, data = phone
    backend = AndroidExecutionBackend(driver)
    item = action(skill, **args)
    prepared = backend.prepare(item, driver.state())
    receipt, _ = backend.dispatch(item, prepared, driver.target, Stop())
    assert data['commands'][-1][5:] == ['shell', 'input', *command]
    assert receipt['effect'] == 'unverifiable'


@pytest.mark.parametrize('skill,args', [
    ('click', {'x': 0, 'y': 0, 'button': 'right'}),
    ('hold_mouse', {'x': .5, 'y': .5, 'duration_ms': 100, 'button': 'right'}),
    ('drag', {'start_x': .1, 'start_y': .1, 'end_x': .2, 'end_y': .2,
              'duration_ms': 100, 'button': 'right'}),
    ('press_key', {'key': 'home'}), ('type_text', {'text': 'abc'}),
    ('wait', {'duration_ms': 1501}), ('double_click', {'x': 0, 'y': 0}),
])
def test_unsupported_actions(phone, skill, args):
    driver, data = phone
    state = driver.state()
    count = len(data['commands'])
    with pytest.raises(ActionUnavailable):
        AndroidExecutionBackend(driver).prepare(action(skill, **args), state)
    assert len(data['commands']) == count


def test_changed_rotation_focus_boot_and_geometry_fail_closed(phone):
    driver, data = phone
    backend = AndroidExecutionBackend(driver)
    state = driver.state()
    data['rotation'] = 0
    with pytest.raises(ObservationStale):
        backend.check_current(driver.target, state)
    with pytest.raises(RuntimeError, match='geometry'):
        driver.observe(0)
    data['package'] = 'com.other.app'
    with pytest.raises(RuntimeError, match='focused'):
        driver.state()
    data['boot'] = 'b' * 36
    with pytest.raises(RuntimeError, match='identity'):
        driver.state()
    assert driver.broken


def test_movement_duration_guidance_is_retained_in_followup_planning(tmp_path):
    from trace2task.chat_model_adapter import ANDROID_MOVEMENT_GUIDANCE

    move = action('joystick_move', start_x=.2, start_y=.8, end_x=.2, end_y=.65, duration_ms=9000)
    session = Session([_reply([move.to_payload()]), _reply([move.to_payload()])])
    adapter = ChatModelAdapter(session, selected_windows=False, android=True)
    for history in ([], [{'executed': True, 'action': {'actions': [move.to_payload()]}}]):
        result = adapter.predict('synthetic route', screenshot=tmp_path / 'image.png', history=history)
        assert result['prediction']['actions'][0]['args']['duration_ms'] == 9000
    assert all(ANDROID_MOVEMENT_GUIDANCE in r['prompt'] for r in session.requests)
    assert 'Continue this task' in session.requests[1]['prompt']


def test_input_timeout_never_retries(phone):
    driver, data = phone
    state = driver.state()
    data['fail_input'] = True
    core = ExecutionCore(AndroidExecutionBackend(driver), Stop(), lambda *a, **kw: None)
    with pytest.raises(subprocess.TimeoutExpired):
        core.execute({'actions': [action('click', x=.2, y=.2).to_payload()]},
                     target=driver.target, state=state, observation_id='first')
    assert core.stopped and driver.broken
    assert len([c for c in data['commands'] if c[5:7] == ['shell', 'input']]) == 1
    assert data['timeouts'][-1] == 20


def test_screenshot_timeout_rechecks_identity_and_recovers_without_repeating_input(phone):
    driver, data = phone
    item = action('click', x=.2, y=.2)
    core = ExecutionCore(AndroidExecutionBackend(driver), Stop(), lambda *a, **kw: None)
    core.execute({'actions': [item.to_payload()]}, target=driver.target,
                 state=driver.state(), observation_id='before-capture')
    data['capture_timeouts'] = 1
    _, path = driver.observe(0)
    assert Image.open(path).format == 'PNG'
    assert data['clock'] == 60 and not driver.broken
    assert len(data['processes']) == 2 and data['processes'][0].killed
    assert all(p.closed for p in data['processes'])
    commands = data['commands']
    images = [i for i, command in enumerate(commands) if command[5:] == ['exec-out', 'screencap', '-p']]
    assert [command[3:] for command in commands[images[0] + 1:images[1]]] == [
        ['-t', '7', 'shell', 'cat', '/proc/sys/kernel/random/boot_id'],
        ['-t', '7', 'shell', 'dumpsys', 'window'], ['-t', '7', 'shell', 'dumpsys', 'input']]
    assert data['host_queries'] == [(5038, 'host:version'), (5038, 'host:devices-l')]
    assert sum(command[5:7] == ['shell', 'input'] for command in commands) == 1
    assert [event['kind'] for event in data['events'][1:]] == [
        'android_capture_timeout', 'android_capture_retry', 'android_capture_recovered']


def test_screenshot_retry_is_bounded_and_partial_output_is_not_archived(phone):
    driver, data = phone
    data['capture_timeouts'] = 10
    with pytest.raises(TimeoutError, match='连续 3 次超过 60 秒'):
        driver.observe(0)
    assert data['clock'] == 180 and driver.broken
    assert len(data['processes']) == 3
    assert all(p.killed and p.closed for p in data['processes'])
    assert not (driver.root / 'android-0000.png').exists()
    assert not any(c[5:7] == ['shell', 'input'] for c in data['commands'])


@pytest.mark.parametrize('key,value,reason', [
    ('transport', 8, '通道身份'), ('serial', 'other-phone', '通道身份'),
    ('device_state', 'offline', '离线'), ('boot', 'b' * 36, 'identity'),
    ('package', 'com.other.app', 'not focused'),
])
def test_screenshot_retry_refuses_changed_device_or_unauthorized_app(phone, key, value, reason):
    driver, data = phone
    data.update(capture_timeouts=1, on_kill=lambda: data.update({key: value}))
    with pytest.raises(RuntimeError, match=reason):
        driver.observe(0)
    assert len(data['processes']) == 1
    assert not (driver.root / 'android-0000.png').exists()
    assert not any(e['kind'] == 'android_capture_retry' for e in data['events'])
    assert not any('connect' in c or 'kill-server' in c for c in data['commands'])


def test_screenshot_retry_requires_successful_identity_checks(phone, monkeypatch):
    driver, data = phone
    data['capture_timeouts'] = 1
    def unavailable(*args):
        raise TimeoutError('server unavailable')
    monkeypatch.setattr(android_discovery, '_host', unavailable)
    with pytest.raises(RuntimeError, match='无法核验原 ADB 服务'):
        driver.observe(0)
    assert len(data['processes']) == 1 and driver.broken
    assert sum(c[3:] == ['devices', '-l'] for c in data['commands']) == 1


@pytest.mark.parametrize('version', ['0028', 'invalid'])
def test_screenshot_retry_does_not_replace_an_incompatible_adb_server(phone, version):
    driver, data = phone
    data.update(capture_timeouts=1, server_version=version)
    with pytest.raises(RuntimeError, match='无法核验原 ADB 服务'):
        driver.observe(0)
    assert len(data['processes']) == 1 and driver.broken
    assert data['host_queries'] == [(5038, 'host:version')]
    assert sum(c[3:] == ['devices', '-l'] for c in data['commands']) == 1


@pytest.mark.parametrize('kind', ['stop', 'budget'])
def test_screenshot_wait_keeps_stop_and_time_budget_responsive(phone, monkeypatch, kind):
    driver, data = phone
    data['capture_timeouts'] = 10
    class Cancel(Stop):
        def raise_if_requested(self):
            if data['clock'] >= .5:
                raise EmergencyStopRequested('stop')
    if kind == 'budget':
        monkeypatch.setattr(execution_budget, 'time', android_execution.time)
        driver.stop = execution_budget.BudgetStop(Stop(), 30)
        error, elapsed = execution_budget.TimeBudgetExceeded, 30
    else:
        driver.stop = Cancel()
        error, elapsed = EmergencyStopRequested, .5
    with pytest.raises(error):
        driver.observe(0)
    assert data['clock'] == elapsed
    assert len(data['processes']) == 1 and data['processes'][0].killed
    assert not (driver.root / 'android-0000.png').exists()
    assert not any(e['kind'] == 'android_capture_retry' for e in data['events'])


def test_stop_between_screenshot_attempts_prevents_retry(phone):
    driver, data = phone
    data['capture_timeouts'] = 1
    class CancelBetween(Stop):
        def sleep(self, seconds):
            raise EmergencyStopRequested('stop before retry')
    driver.stop = CancelBetween()
    with pytest.raises(EmergencyStopRequested):
        driver.observe(0)
    assert len(data['processes']) == 1


def test_recovered_screenshot_still_requires_post_capture_scope_check(phone):
    driver, data = phone
    data.update(capture_timeouts=1, on_capture=lambda: data.update(package='com.other.app'))
    with pytest.raises(RuntimeError, match='not focused'):
        driver.observe(0)
    assert len(data['processes']) == 2
    assert not (driver.root / 'android-0000.png').exists()
    assert not any(e['kind'] == 'android_capture_recovered' for e in data['events'])


def test_screenshot_command_failure_is_not_retried(phone):
    driver, data = phone
    data['capture_exitcode'] = 1
    with pytest.raises(subprocess.CalledProcessError):
        driver.observe(0)
    assert len(data['processes']) == 1 and driver.broken


def test_agent_continues_after_screenshot_retry_and_reports_recovery(phone, tmp_path):
    _, data = phone
    data['capture_timeouts'] = 1
    statuses = []
    result = run_chat_agent(instruction='synthetic', model='fake', reasoning_effort='low',
        output_root=tmp_path / 'runs', emergency_stop=Stop(), status_callback=statuses.append,
        executor_backend='android', android_config={'adb': 'adb', 'serial': 'phone', 'package': 'com.example.game'},
        session=Session([_reply([action('click', x=.1, y=.1).to_payload()])]), max_actions=1)
    assert result['actions'] == 1 and result['stop_reason'] == 'action_limit'
    assert len(result['model_io']) == 1 and result['model_io'][0]['step_index'] == 0
    assert sum(c[5:7] == ['shell', 'input'] for c in data['commands']) == 1
    assert any('第 1/2 次重试截图' in text for text in statuses)
    assert '截图已恢复，继续当前任务。' in statuses
    from pathlib import Path
    events = [json.loads(line)['type'] for line in Path(result['trace_path']).read_text(encoding='utf-8').splitlines()]
    assert events.count('android_capture_timeout') == events.count('android_capture_recovered') == 1


def test_screenshot_reader_preserves_bytes_across_pipe_waits(tmp_path):
    driver = AndroidDriver(tmp_path, lambda *a, **kw: None, Stop(), adb='unused',
                           serial='synthetic', package='com.example.game')
    result = driver._read_screenshot([sys.executable, '-u', '-c',
        "import sys,time;sys.stdout.buffer.write(b'first');sys.stdout.flush();time.sleep(.4);sys.stdout.buffer.write(b'second')"])
    assert result == b'firstsecond'


def test_screenshot_reader_cancels_and_reaps_its_client(tmp_path, monkeypatch):
    processes, original = [], subprocess.Popen
    def spawn(*args, **kwargs):
        child = original(*args, **kwargs)
        processes.append(child)
        return child
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    started = time.monotonic()
    class Cancel(Stop):
        def raise_if_requested(self):
            if time.monotonic() - started >= .5:
                raise EmergencyStopRequested('stop')
    driver = AndroidDriver(tmp_path, lambda *a, **kw: None, Cancel(), adb='unused',
                           serial='synthetic', package='com.example.game')
    with pytest.raises(EmergencyStopRequested):
        driver._read_screenshot([sys.executable, '-u', '-c', 'import time;time.sleep(30)'])
    assert time.monotonic() - started < 5
    assert len(processes) == 1 and processes[0].poll() is not None


def test_command_tampering_and_wrong_target(phone):
    driver, _ = phone
    backend = AndroidExecutionBackend(driver)
    item = action('click', x=.2, y=.2)
    prepared = backend.prepare(item, driver.state())
    prepared['command'] = ['keyevent', '3']
    with pytest.raises(ValueError, match='mismatch'):
        backend.dispatch(item, prepared, driver.target, Stop())
    with pytest.raises(ValueError):
        backend.authorize(item, {'kind': 'desktop'})
    with pytest.raises(ActionUnavailable):
        backend.retry_foreground(item, None, driver.target, Stop())


def test_schema_prompt_and_shared_loop(phone, tmp_path):
    driver, data = phone
    schema = plan_schema(selected_windows=False, android=True)
    variants = schema['properties']['actions']['items']['anyOf']
    assert {x['properties']['skill']['enum'][0] for x in variants} == set(SKILLS)
    for item in variants:
        args_schema = item['properties']['args']
        assert 'button' not in args_schema['properties']
        assert 'button' not in args_schema['required']
        assert args_schema['additionalProperties'] is False
    # Building the Android projection must not remove desktop button choices.
    desktop = plan_schema(selected_windows=False)['properties']['actions']['items']['anyOf']
    for item in desktop:
        if item['properties']['skill']['enum'][0] in {'click', 'hold_mouse', 'drag'}:
            args_schema = item['properties']['args']
            assert 'right' in args_schema['properties']['button']['enum']
            assert 'button' in args_schema['required']
    durations = {item['properties']['skill']['enum'][0]:
                 item['properties']['args']['properties'].get('duration_ms') for item in variants}
    assert durations['joystick_move'] == {'type': 'integer', 'minimum': 1}
    assert 'navigate_to' not in durations
    limits = AndroidExecutionBackend(driver).capabilities(driver.state())['duration_limits_ms']
    assert limits == {'joystick_move': None,
                      'hold_mouse': 1500, 'drag': 1500, 'wait': 1500, 'camera_drag': 1500}
    session = Session([_reply([action('wait', duration_ms=1).to_payload()])])
    adapter = ChatModelAdapter(session, selected_windows=False, android=True)
    adapter.predict('observe', screenshot=tmp_path / 'image.png', history=[])
    assert 'selected Android phone display' in session.requests[0]['prompt']
    assert 'primary desktop' not in session.requests[0]['prompt']
    assert 'No fixed duration cap' in session.requests[0]['prompt']
    assert 'All timed actions except joystick_move are limited to 1500 ms' in session.requests[0]['prompt']
    driver.start = lambda: None
    proposed = [
        {'skill': 'click', 'args': {'x': .1, 'y': .1}},
        {'skill': 'hold_mouse', 'args': {'x': .2, 'y': .2, 'duration_ms': 100}},
        {'skill': 'drag', 'args': {'start_x': .3, 'start_y': .3, 'end_x': .4, 'end_y': .4,
                                 'duration_ms': 200}},
    ]
    result = run_chat_agent(instruction='test', model='fake', reasoning_effort='low',
                            output_root=tmp_path / 'runs', emergency_stop=Stop(), status_callback=lambda _: None,
                            executor_backend='android', android_config={'serial': 'phone'}, android_driver=driver,
                            session=Session([_reply(proposed)]), max_actions=3)
    assert result['mode'] == 'android_baseline'
    assert result['actions'] == 3 and result['stop_reason'] == 'action_limit'
    assert result['history'][0]['effect'] == 'unverifiable'
    assert 'button' not in result['model_io'][0]['raw_output']
    assert all(item['action']['actions'][0]['args']['button'] == 'left' for item in result['history'])
    assert [c[7:] for c in data['commands'] if c[5:7] == ['shell', 'input']] == [
        ['tap', '128', '72'],
        ['swipe', '256', '144', '256', '144', '100'],
        ['swipe', '384', '216', '512', '288', '200'],
    ]
    assert driver.broken


def test_retired_navigation_cannot_enter_default_backend(phone):
    from test_android_navigation import navigation

    driver, data = phone
    backend = AndroidExecutionBackend(driver)
    item = UnifiedAction.from_payload(navigation())
    state = driver.state()
    capabilities = backend.capabilities(state)
    assert 'navigate_to' not in capabilities['available_skills']
    assert 'navigation' not in capabilities
    with pytest.raises(ActionUnavailable):
        backend.authorize(item, driver.target)
    with pytest.raises(ActionUnavailable):
        backend.prepare(item, state)
    with pytest.raises(ActionUnavailable):
        backend.dispatch(item, None, driver.target, Stop())
    assert not any(c[5:7] == ['shell', 'input'] for c in data['commands'])
