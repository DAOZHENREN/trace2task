"""Synthetic transport tests; no Android/Windows input or model calls."""
import io
import subprocess
from types import SimpleNamespace

import pytest
from PIL import Image
from test_chat_agent_bridge import Session, Stop, _reply

from trace2task.android_execution import SKILLS, AndroidDriver, AndroidExecutionBackend
from trace2task.chat_agent_runner import run_chat_agent
from trace2task.chat_model_adapter import ChatModelAdapter, plan_schema
from trace2task.execution_core import ExecutionCore
from trace2task.execution_protocol import ActionUnavailable, ObservationStale, UnifiedAction


def action(skill, **args):
    return UnifiedAction.from_payload({'skill': skill, 'args': args})


@pytest.fixture
def phone(tmp_path, monkeypatch):
    data = {'rotation': 1, 'package': 'com.example.game', 'boot': 'a' * 36,
            'transport': 7, 'commands': [], 'fail_input': False, 'size': (1280, 720)}
    def run(command, **kwargs):
        data['commands'].append(command)
        args = command[5:]
        if command[3:] == ['devices', '-l']:
            value = f"phone device transport_id:{data['transport']}".encode()
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
    monkeypatch.setattr(subprocess, 'run', run)
    driver = AndroidDriver(tmp_path, lambda *a, **kw: None, Stop(), adb='adb',
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


def test_timeout_never_retries(phone):
    driver, data = phone
    state = driver.state()
    data['fail_input'] = True
    core = ExecutionCore(AndroidExecutionBackend(driver), Stop(), lambda *a, **kw: None)
    with pytest.raises(subprocess.TimeoutExpired):
        core.execute({'actions': [action('click', x=.2, y=.2).to_payload()]},
                     target=driver.target, state=state, observation_id='first')
    assert core.stopped and driver.broken
    assert len([c for c in data['commands'] if c[5:7] == ['shell', 'input']]) == 1


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
    driver, _ = phone
    schema = plan_schema(selected_windows=False, android=True)
    variants = schema['properties']['actions']['items']['anyOf']
    assert {x['properties']['skill']['enum'][0] for x in variants} == set(SKILLS)
    session = Session([_reply([action('wait', duration_ms=1).to_payload()])])
    adapter = ChatModelAdapter(session, selected_windows=False, android=True)
    adapter.predict('observe', screenshot=tmp_path / 'image.png', history=[])
    assert 'selected Android phone display' in session.requests[0]['prompt']
    assert 'primary desktop' not in session.requests[0]['prompt']
    driver.start = lambda: None
    result = run_chat_agent(instruction='test', model='fake', reasoning_effort='low',
                            output_root=tmp_path / 'runs', emergency_stop=Stop(), status_callback=lambda _: None,
                            executor_backend='android', android_config={'serial': 'phone'}, android_driver=driver,
                            session=Session([_reply([action('click', x=.1, y=.1).to_payload()])]), max_actions=1)
    assert result['mode'] == 'android_baseline'
    assert result['actions'] == 1 and result['stop_reason'] == 'action_limit'
    assert result['history'][0]['effect'] == 'unverifiable'
    assert driver.broken
