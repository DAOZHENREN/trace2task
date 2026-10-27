"""Camera protocol and full-loop checks with synthetic ADB, images and models."""
import copy
import json
import subprocess

import pytest
import test_android_execution
from test_chat_agent_bridge import Session, Stop, _reply

from trace2task.android_execution import AndroidExecutionBackend
from trace2task.chat_agent_runner import run_chat_agent
from trace2task.chat_model_adapter import ANDROID_CAMERA_GUIDANCE, ChatModelAdapter, plan_schema
from trace2task.execution_core import ExecutionCore
from trace2task.execution_protocol import ActionUnavailable, ObservationStale, UnifiedAction
from trace2task.windows_runner import EmergencyStopRequested

phone = test_android_execution.phone


def camera(**changes):
    args = {'start_x': .75, 'start_y': .4, 'end_x': .6, 'end_y': .4,
            'duration_ms': 300}
    return {'skill': 'camera_drag', 'args': {**args, **changes}}


def inputs(data):
    return [command[7:] for command in data['commands'] if command[5:7] == ['shell', 'input']]


@pytest.mark.parametrize('changes', [
    {'duration_ms': 0}, {'duration_ms': -1}, {'duration_ms': 1501}, {'duration_ms': True},
    {'duration_ms': 300.0}, {'duration_ms': '300'}, {'duration_ms': float('inf')},
    {'start_x': float('nan')}, {'start_y': float('inf')}, {'end_x': True},
    {'end_y': -0.1}, {'start_x': 1.01}, {'start_x': '0.5'},
    {'end_x': .75}, {'camera_region': None},
    {'camera_region': {'left': .45, 'top': .2, 'right': .9, 'bottom': .6}},
    {'button': 'left'}, {'yaw_degrees': 90}, {'shell': 'input tap 1 1'},
])
def test_camera_rejects_invalid_coordinates_and_extra_parameters(changes):
    with pytest.raises(ValueError):
        UnifiedAction.from_payload(camera(**changes))


@pytest.mark.parametrize('rotation,size,expected', [
    (0, (720, 1280), ['swipe', '540', '512', '432', '512', '300']),
    (1, (1280, 720), ['swipe', '960', '288', '768', '288', '300']),
    (2, (720, 1280), ['swipe', '540', '512', '432', '512', '300']),
    (3, (1280, 720), ['swipe', '960', '288', '768', '288', '300']),
])
def test_camera_uses_current_display_swipe_once_then_reobserves(phone, rotation, size, expected):
    driver, data = phone
    data.update(rotation=rotation, size=size)
    state, _ = driver.observe(0)
    core = ExecutionCore(AndroidExecutionBackend(driver), Stop(), driver.record)
    result = core.execute({'actions': [camera()]}, target=driver.target,
                          observation_id='camera', state=state)
    assert inputs(data) == [expected]
    assert result.status == 'reobserve' and core.attempts == 1
    assert result.receipt['effect'] == 'unverifiable'
    assert result.receipt['route'] == 'adb_camera_swipe'
    assert result.receipt['camera']['angle_degrees'] is None
    assert core.feedback['status'] == 'camera_reobserve'
    assert core.last_requests[0]['request']['operation'] == 'camera_drag'
    assert driver._touch_session is None


def test_camera_accepts_vertical_and_diagonal_swipes(phone):
    driver, data = phone
    core = ExecutionCore(AndroidExecutionBackend(driver), Stop(), driver.record)
    for index, changes in enumerate([{'end_x': .75, 'end_y': .5}, {'end_x': .6, 'end_y': .5}]):
        state, _ = driver.observe(index)
        core.execute({'actions': [camera(**changes)]}, target=driver.target,
                     observation_id=str(index), state=state)
    assert inputs(data) == [['swipe', '960', '288', '960', '360', '300'],
                            ['swipe', '960', '288', '768', '360', '300']]


def test_camera_discards_later_movement_and_done_and_counts_budget(phone):
    driver, data = phone
    state, _ = driver.observe(0)
    core = ExecutionCore(AndroidExecutionBackend(driver), Stop(), driver.record, max_actions=1)
    move = {'skill': 'joystick_move', 'args': {'start_x': .2, 'start_y': .8,
                                             'end_x': .2, 'end_y': .65, 'duration_ms': 9000}}
    result = core.execute({'actions': [camera(), move, {'done': True}]}, target=driver.target,
                          observation_id='one', state=state)
    assert result.status == 'reobserve' and len(result.steps) == 1 and len(inputs(data)) == 1
    assert any(e['kind'] == 'batch_boundary' and e['reason'] == 'camera_feedback'
               and e['remaining'] == 1 for e in data['events'])
    state, _ = driver.observe(1)
    result = core.execute({'actions': [camera()]}, target=driver.target,
                          observation_id='two', state=state)
    assert result.status == 'action_limit' and core.attempts == 1 and len(inputs(data)) == 1


def test_preceding_action_forces_new_observation_before_camera(phone):
    driver, data = phone
    state, _ = driver.observe(0)
    core = ExecutionCore(AndroidExecutionBackend(driver), Stop(), driver.record)
    result = core.execute({'actions': [{'skill': 'click', 'args': {'x': .1, 'y': .1}}, camera()]},
                          target=driver.target, observation_id='before', state=state)
    assert result.status == 'reobserve' and core.attempts == 1
    assert inputs(data) == [['tap', '128', '72']]
    assert not core.feedback['executed'] and len(result.steps) == 1


@pytest.mark.parametrize('problem', ['missing', 'mismatch', 'tampered', 'subpixel', 'contact'])
def test_camera_preflight_refuses_without_input(phone, problem):
    driver, data = phone
    state, path = driver.observe(0)
    payload = camera()
    if problem == 'missing':
        driver._last_observation = None
    elif problem == 'mismatch':
        state = {**state, 'sha256': 'changed'}
    elif problem == 'tampered':
        path.write_bytes(b'changed')
    elif problem == 'subpixel':
        payload = camera(end_x=.75001)
    else:
        driver._touch_session = object()
    with pytest.raises(ActionUnavailable):
        AndroidExecutionBackend(driver).prepare(UnifiedAction.from_payload(payload), state)
    assert inputs(data) == []


@pytest.mark.parametrize('change', ['rotation', 'focus', 'boot', 'command', 'stop'])
def test_camera_rechecks_scope_and_prepared_command_before_dispatch(phone, change):
    driver, data = phone
    state, _ = driver.observe(0)
    backend = AndroidExecutionBackend(driver)
    action = UnifiedAction.from_payload(camera())
    prepared = backend.prepare(action, state)
    stop = Stop()
    if change == 'rotation':
        data['rotation'] = 0
        error = ObservationStale
    elif change == 'focus':
        data['package'] = 'com.other.app'
        error = RuntimeError
    elif change == 'boot':
        data['boot'] = 'b' * 36
        error = RuntimeError
    elif change == 'command':
        prepared['command'][-1] = '9000'
        error = ValueError
    else:
        def stopped():
            raise EmergencyStopRequested('stop before camera')
        stop.raise_if_requested = stopped
        error = EmergencyStopRequested
    with pytest.raises(error):
        backend.dispatch(action, prepared, driver.target, stop)
    assert inputs(data) == []


def test_camera_timeout_stops_without_retry_or_later_action(phone):
    driver, data = phone
    state, _ = driver.observe(0)
    data['fail_input'] = True
    core = ExecutionCore(AndroidExecutionBackend(driver), Stop(), driver.record)
    with pytest.raises(subprocess.TimeoutExpired):
        core.execute({'actions': [camera(), {'skill': 'click', 'args': {'x': .1, 'y': .1}}]},
                     target=driver.target, observation_id='timeout', state=state)
    assert core.stopped and driver.broken and len(inputs(data)) == 1


def test_camera_schema_is_android_only_and_guidance_survives_followup(tmp_path):
    for options in ({'android': True}, {}, {'selected_windows': True}, {'browser': True}):
        schema = plan_schema(**{'selected_windows': False, **options})
        variants = schema['properties']['actions']['items']['anyOf']
        skills = {v['properties']['skill']['enum'][0] for v in variants}
        assert ('camera_drag' in skills) == bool(options.get('android'))
        if options.get('android'):
            args_schema = next(v['properties']['args'] for v in variants
                               if v['properties']['skill']['enum'] == ['camera_drag'])
            expected = {'start_x', 'start_y', 'end_x', 'end_y', 'duration_ms'}
            assert set(args_schema['properties']) == set(args_schema['required']) == expected
    original = camera()
    checked = UnifiedAction.from_payload(original)
    copied = copy.deepcopy(original)
    checked.args['start_x'] = 0
    assert original == copied
    session = Session([_reply([camera()]), _reply([camera()])])
    adapter = ChatModelAdapter(session, selected_windows=False, android=True)
    for history in ([], [{'executed': True, 'action': {'actions': [camera()]}}]):
        result = adapter.predict('look toward route', screenshot=tmp_path / 'image.png', history=history)
        assert result['status'] == 'predicted'
        assert result['prediction']['actions'] == [camera()]
    assert all(ANDROID_CAMERA_GUIDANCE in request['prompt'] for request in session.requests)


def test_model_loop_observes_after_camera_before_planning_and_completion_review(phone, tmp_path):
    driver, data = phone
    driver.start = lambda: None
    review = json.dumps({'verdict': 'complete', 'evidence': 'synthetic visible result', 'missing': ''})
    session = Session([_reply([camera(), {'skill': 'click', 'args': {'x': .1, 'y': .1}}]),
                       _reply(complete=True), review])
    statuses = []
    result = run_chat_agent(instruction='look at target', model='fake', reasoning_effort='low',
                            output_root=tmp_path / 'runs', emergency_stop=Stop(),
                            status_callback=statuses.append, executor_backend='android',
                            android_config={'serial': 'phone'}, android_driver=driver,
                            session=session, max_actions=3, max_seconds=30)
    assert result['actions'] == 1 and result['stop_reason'] == 'visual_completion_reviewed'
    assert len(inputs(data)) == 1 and len(session.requests) == 3
    assert len(data['processes']) == 4  # Before/after swipe, separate review and final audit capture.
    assert 'camera_reobserve' in session.requests[1]['prompt']
    assert any('转镜头滑动已发送' in status for status in statuses)
    assert result['history'][0]['effect'] == 'unverifiable' and not result['verified']
    assert driver.broken
