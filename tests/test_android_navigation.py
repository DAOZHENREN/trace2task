"""Synthetic map feedback and budgets; never connect to or control a real phone."""
import copy
import hashlib
import json
import math
import threading

import numpy as np
import pytest
from PIL import Image
from test_chat_agent_bridge import Session, _reply

from trace2task.android_execution import AndroidExecutionBackend
from trace2task.android_navigation import MarkerTracker, prepare_navigation, steering
from trace2task.android_touch import NavigationSuperseded
from trace2task.chat_agent_runner import run_chat_agent
from trace2task.execution_budget import BudgetStop, TimeBudgetExceeded
from trace2task.execution_core import ExecutionCore
from trace2task.execution_protocol import ActionUnavailable, UnifiedAction
from trace2task.run_interventions import RunInterventions
from trace2task.windows_runner import EmergencyStopRequested


def navigation(**changes):
    args = {'joystick': {'x': .2, 'y': .8, 'radius': .1},
            'map_region': {'left': .02, 'top': .02, 'right': .4, 'bottom': .4},
            'marker': {'x': 88/320, 'y': 40/240, 'radius': 4/240},
            'anchor': {'x': 64/320, 'y': 72/240}, 'stop_radius': 6/240,
            'camera_heading_deg': 0, 'duration_ms': 1000}
    args.update(changes)
    return {'skill': 'navigate_to', 'args': args}


def joystick(duration_ms=1000):
    return {'skill': 'joystick_move', 'args': {'start_x': .2, 'start_y': .8,
            'end_x': .2, 'end_y': .65, 'duration_ms': duration_ms}}


def picture(position=(88, 40), *, duplicate=None):
    image = Image.new('RGB', (320, 240), (30, 30, 30))
    pattern = np.random.default_rng(12).integers(0, 256, (9, 9, 3), dtype=np.uint8)
    marker = Image.fromarray(pattern)
    for item in (position, duplicate):
        if item is not None:
            image.paste(marker, (item[0]-4, item[1]-4))
    return image


class ClockStop:
    def __init__(self):
        self.now = 100.0
        self.stopped = False

    def start(self):
        pass

    def close(self):
        pass

    def raise_if_requested(self):
        if self.stopped:
            raise EmergencyStopRequested()

    def sleep(self, seconds):
        self.raise_if_requested()
        self.now += seconds
        self.raise_if_requested()


class Touch:
    def __init__(self, driver, stop, is_current):
        self.driver, self.stop, self.is_current = driver, stop, is_current
        self.state = driver.state()
        self.events = []
        self.released = threading.Event()
        self.deadline = None
        self.closed = False

    def open(self):
        self.check()

    def check(self):
        self.stop.raise_if_requested()
        if not self.is_current():
            raise NavigationSuperseded()
        if self.deadline is not None and self.driver.clock.now >= self.deadline:
            self.released.set()

    def begin(self, center, endpoint, duration):
        self.check()
        self.deadline = self.driver.clock.now + duration / 1000
        self.events.extend([{'event': 'down', 'point': center}, {'event': 'move', 'point': endpoint}])
        return True

    def move(self, endpoint):
        self.check()
        if self.released.is_set():
            return False
        self.events.append({'event': 'move', 'point': endpoint})
        return True

    def capture_region(self, region, path):
        if self.released.is_set():
            return None
        started = self.driver.clock.now
        current, path = self.driver.capture(path)
        if any(current[key] != self.state[key] for key in self.state):
            raise RuntimeError('scope changed')
        with Image.open(path) as full:
            crop = full.crop(region)
        crop.save(path)
        self.check()
        return {'screenshot': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'capture_ms': (self.driver.clock.now - started) * 1000,
                'image_kind': 'minimap_crop', 'crop_region': list(region)}

    def close(self):
        if self.closed:
            return
        self.events.append({'event': 'up'})
        self.released.set()
        self.closed = True


class Phone:
    def __init__(self, root, clock, frames):
        self.root, self.clock, self.frames = root, clock, list(frames)
        self.index = 0
        self.base_state = {'boot_id': 'a'*36, 'focus': 'com.example.game/.Main',
                           'rotation': 1, 'width': 320, 'height': 240}
        self.target = {'kind': 'android', 'serial': 'fake', 'package': 'com.example.game',
                       'transport_id': 7, 'boot_id': 'a'*36}
        self._last_observation = None
        self._touch_session = None
        self.touches, self.records, self.other_input = [], [], []
        self.capture_hook = None
        self.broken = False

    def start(self):
        pass

    def state(self):
        if self.broken:
            raise RuntimeError('broken')
        return dict(self.base_state)

    def record(self, kind, **data):
        self.records.append({'kind': kind, **data})

    def capture(self, path):
        if self.capture_hook:
            self.capture_hook(self.index)
        self.clock.now += .02
        frame = self.frames[min(self.index, len(self.frames)-1)]
        self.index += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.save(path)
        state = {**self.state(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        return state, path

    def observe(self, index):
        self._last_observation = self.capture(self.root / f'android-{index:04d}.png')
        return self._last_observation

    def create_touch_session(self, state, stop, *, is_current):
        self._touch_session = Touch(self, stop, is_current)
        self.touches.append(self._touch_session)
        return self._touch_session

    def _run(self, *command):
        self.other_input.append(command)

    def close(self):
        if self._touch_session:
            self._touch_session.close()
        self.broken = True


@pytest.fixture
def environment(tmp_path, monkeypatch):
    clock = ClockStop()
    monkeypatch.setattr('time.monotonic', lambda: clock.now)
    phone = Phone(tmp_path, clock, [picture(), picture(), picture((82, 47)),
                                  picture((76, 54)), picture((69, 64)), picture((66, 70))])
    return phone, clock


def execute(phone, stop, payload=None, *, core=None, index=0, context=None):
    state, _ = phone.observe(index)
    core = core or ExecutionCore(AndroidExecutionBackend(phone), stop, phone.record)
    result = core.execute({'actions': [payload or navigation()]}, target=phone.target,
                          observation_id=str(index), state=state, execution_context=context)
    return result, core


@pytest.mark.parametrize('path,value', [
    (('duration_ms',), True), (('duration_ms',), 1501), (('duration_ms',), 0),
    (('camera_heading_deg',), float('nan')), (('camera_heading_deg',), float('inf')),
    (('camera_heading_deg',), -1), (('marker', 'x'), True), (('marker', 'radius'), 0),
    (('joystick', 'radius'), .3), (('stop_radius',), .5), (('map_region', 'right'), .01),
    (('anchor', 'x'), .8),
])
def test_navigation_payload_is_strict(path, value):
    payload = navigation()
    target = payload['args']
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        UnifiedAction.from_payload(payload)


def test_unknown_fields_and_unbounded_joystick_are_rejected():
    for payload in [navigation(), joystick()]:
        payload['args']['shell'] = 'anything'
        with pytest.raises(ValueError):
            UnifiedAction.from_payload(payload)
    for duration in [1501, -1, True, 1.2]:
        with pytest.raises(ValueError):
            UnifiedAction.from_payload(joystick(duration))


def test_marker_tracker_rejects_loss_ambiguity_and_textureless_seed():
    tracker = MarkerTracker(picture(), navigation()['args'])
    found, error = tracker.locate(picture((84, 45)))
    assert error is None and found['position'] == [84, 45]
    assert tracker.locate(picture(None))[1] == 'target_lost'
    tracker = MarkerTracker(picture(), navigation()['args'])
    assert tracker.locate(picture(duplicate=(78, 40)))[1] == 'target_ambiguous'
    with pytest.raises(ActionUnavailable, match='特征'):
        MarkerTracker(picture(None), navigation()['args'])


def test_steering_camera_mapping_smoothing_slowdown_and_arrival():
    args, state = navigation()['args'], {'width': 320, 'height': 240}
    far, _, angle = steering([88, 40], args, state)
    assert far[0] > 64 and far[1] < 192
    turned = copy.deepcopy(args)
    turned['camera_heading_deg'] = 90
    left, _, _ = steering([64, 40], turned, state)
    assert left[0] < 64 and left[1] == 192
    near, _, _ = steering([64, 64], args, state)
    assert 0 < 192 - near[1] < 24
    assert steering([64, 72], args, state)[0] is None
    _, _, next_angle = steering([40, 90], args, state, angle)
    assert abs(next_angle-angle) <= math.pi / 6 + 1e-9


def test_navigation_tracks_corrects_and_releases_on_arrival(environment):
    phone, stop = environment
    result, core = execute(phone, stop)
    nav = result.receipt['navigation']
    assert result.status == 'reobserve' and core.attempts == 1
    assert nav['outcome'] == 'arrival_candidate' and nav['corrections'] >= 2
    assert nav['release_confirmed'] and phone.touches[0].closed
    assert phone.touches[0].events[-1]['event'] == 'up'
    assert not phone.other_input
    assert result.receipt['effect'] == 'unverifiable'


def test_already_near_target_does_not_press(environment):
    phone, stop = environment
    phone.frames = [picture((66, 70))]
    payload = navigation(marker={'x': 66/320, 'y': 70/240, 'radius': 4/240})
    result, core = execute(phone, stop, payload)
    assert result.receipt['navigation']['outcome'] == 'arrival_candidate'
    assert not result.receipt['navigation']['input_sent']
    assert phone.touches[0].events == [{'event': 'up'}]
    assert core.attempts == 1  # Observation/control attempts do not reset or evade budgets.


@pytest.mark.parametrize('frame,outcome', [
    (picture(None), 'target_lost'), (picture(duplicate=(78, 40)), 'target_ambiguous'),
    (picture((96, 32)), 'moving_away'),
])
def test_failure_after_press_releases_without_replay(environment, frame, outcome):
    phone, stop = environment
    phone.frames = [picture(), picture(), frame]
    result, _ = execute(phone, stop)
    assert result.receipt['navigation']['outcome'] == outcome
    assert len(phone.touches) == 1 and phone.touches[0].closed
    assert len(phone.touches[0].events) == 3  # DOWN, initial push, UP; no correction/retry.


def test_static_target_stops_for_no_progress(environment):
    phone, stop = environment
    phone.frames = [picture()]
    result, _ = execute(phone, stop)
    assert result.receipt['navigation']['outcome'] == 'no_progress'
    assert phone.touches[0].closed and stop.now < 101.2


def test_lost_target_before_down_closes_empty_helper_without_input(environment):
    phone, stop = environment
    phone.frames = [picture(), picture(None)]
    result, _ = execute(phone, stop)
    assert result.receipt['navigation']['outcome'] == 'target_lost'
    assert phone.touches[0].events == [{'event': 'up'}]


def test_three_failed_navigation_attempts_latch_without_hidden_retry(environment):
    phone, stop = environment
    phone.frames = [picture()]
    core = ExecutionCore(AndroidExecutionBackend(phone), stop, phone.record)
    for index in range(3):
        result, _ = execute(phone, stop, core=core, index=index)
        assert result.receipt['navigation']['outcome'] == 'no_progress'
    result, _ = execute(phone, stop, core=core, index=3)
    assert result.status == 'rejected' and len(phone.touches) == 3


def test_joystick_holds_not_slowly_drags_and_keeps_action_budget(environment):
    phone, stop = environment
    core = ExecutionCore(AndroidExecutionBackend(phone), stop, phone.record, max_actions=1)
    state, _ = phone.observe(0)
    result = core.execute({'actions': [joystick(), {'skill': 'click', 'args': {'x': .1, 'y': .1}}]},
                          target=phone.target, state=state, observation_id='one')
    assert result.status == 'reobserve' and core.attempts == 1 and not phone.other_input
    assert phone.touches[0].events == [
        {'event': 'down', 'point': (64, 192)}, {'event': 'move', 'point': (64, 156)}, {'event': 'up'}]
    result, _ = execute(phone, stop, joystick(), core=core, index=1)
    assert result.status == 'action_limit' and len(phone.touches) == 1


@pytest.mark.parametrize('kind', ['stop', 'budget', 'capture_error', 'scope_change'])
def test_interruptions_always_release_and_fail_closed(environment, kind):
    phone, clock = environment
    stop = BudgetStop(clock, 10) if kind == 'budget' else clock
    def change(index):
        if index != 2:
            return
        if kind == 'stop':
            clock.stopped = True
        elif kind == 'budget':
            clock.now += 11
        elif kind == 'scope_change':
            phone.base_state['focus'] = 'com.other.app/.Main'
        else:
            raise TimeoutError('capture failed')
    phone.capture_hook = change
    expected = {'stop': EmergencyStopRequested, 'budget': TimeBudgetExceeded,
                'capture_error': TimeoutError, 'scope_change': RuntimeError}[kind]
    core = ExecutionCore(AndroidExecutionBackend(phone), stop, phone.record)
    with pytest.raises(expected):
        execute(phone, stop, core=core)
    assert core.stopped and core.attempts == 1
    assert phone.touches[0].closed and phone.touches[0].events[-1]['event'] == 'up'
    assert not phone.other_input


def test_manual_takeover_releases_before_pause_ack(environment):
    phone, stop = environment
    interventions = RunInterventions()
    interventions.activate(phone.record)
    backend = AndroidExecutionBackend(phone, interventions=interventions)
    core = ExecutionCore(backend, stop, phone.record, interventions=interventions)
    def pause(index):
        if index == 2:
            interventions.request(request_id='pause-123', kind='pause')
    phone.capture_hook = pause
    result, _ = execute(phone, stop, core=core, context={'intervention_revision': 0})
    assert result.receipt['navigation']['outcome'] == 'intervened'
    assert phone.touches[0].closed and interventions.snapshot()['phase'] == 'pausing'


def test_prepared_snapshot_and_geometry_are_bound(environment):
    phone, _stop = environment
    state, path = phone.observe(0)
    action = UnifiedAction.from_payload(navigation())
    with pytest.raises(ActionUnavailable, match='原始截图'):
        prepare_navigation(action, state, None)
    path.write_bytes(b'tampered')
    with pytest.raises(ActionUnavailable, match='哈希'):
        prepare_navigation(action, state, phone._last_observation)
    assert not phone.touches


def test_existing_model_loop_reobserves_then_verifies_not_auto_complete(environment, tmp_path):
    phone, stop = environment
    review = json.dumps({'verdict': 'complete', 'evidence': 'visible interaction result', 'missing': ''})
    session = Session([_reply([navigation(), {'skill': 'click', 'args': {'x': .9, 'y': .8}}]),
                       _reply(complete=True), review])
    result = run_chat_agent(instruction='approach target', model='fake', reasoning_effort='low',
                            output_root=tmp_path / 'runs', emergency_stop=stop,
                            status_callback=lambda _: None, executor_backend='android',
                            android_config={'serial': 'fake'}, android_driver=phone,
                            session=session, max_actions=3, max_seconds=30)
    assert result['actions'] == 1 and result['stop_reason'] == 'visual_completion_reviewed'
    assert result['history'][0]['navigation']['outcome'] == 'arrival_candidate'
    assert len(session.requests) == 3 and not phone.other_input
    assert not result['verified']  # Same-model review, not independent evaluation.
    assert phone.broken


def test_minimap_crop_tracks_same_position_as_full_frame():
    full = MarkerTracker(picture(), navigation()['args'])
    cropped = MarkerTracker(picture(), navigation()['args'])
    moved = picture((82, 47))
    assert full.locate(moved) == cropped.locate(moved.crop(cropped.region), cropped=True)


def test_fresh_marker_after_helper_setup_determines_first_push(environment):
    phone, stop = environment
    phone.frames = [picture(), picture((79, 40))]
    result, _ = execute(phone, stop)
    # Correct from the fresh marker, not the older marker at (88, 40).
    assert phone.touches[0].events[1]['point'] == (74, 170)
    assert result.receipt['navigation']['release_confirmed']


def test_slow_feedback_rejected_before_down_and_latched(environment):
    phone, stop = environment
    phone.frames = [picture()]
    phone.capture_hook = lambda _: setattr(stop, 'now', stop.now + .6)
    core = ExecutionCore(AndroidExecutionBackend(phone), stop, phone.record)
    for index in range(3):
        result, _ = execute(phone, stop, core=core, index=index)
        assert result.receipt['navigation']['outcome'] == 'feedback_too_slow'
        assert not result.receipt['navigation']['input_sent']
    assert all(t.events == [{'event': 'up'}] for t in phone.touches)
    assert execute(phone, stop, core=core, index=3)[0].status == 'rejected'


def test_frame_delay_cannot_extend_lease_or_send_late_correction(environment):
    phone, stop = environment
    def delay(index):
        if index == 2:
            stop.now += 2
    phone.capture_hook = delay
    result, _ = execute(phone, stop)
    assert result.receipt['navigation']['release_confirmed']
    assert result.receipt['navigation']['corrections'] == 0
    assert phone.touches[0].events == [
        {'event': 'down', 'point': (64, 192)}, {'event': 'move', 'point': (78, 173)}, {'event': 'up'}]


def test_uncertain_release_stops_run_without_next_input(environment, monkeypatch):
    phone, stop = environment
    phone.frames = [picture(), picture(), picture((66, 70))]
    monkeypatch.setattr(Touch, 'close', lambda self: None)
    core = ExecutionCore(AndroidExecutionBackend(phone), stop, phone.record)
    with pytest.raises(RuntimeError, match='触点释放'):
        execute(phone, stop, core=core)
    assert core.stopped and not phone.other_input
