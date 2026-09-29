"""No native input: run both real model decoders through the shared core."""
import json
from types import SimpleNamespace

import pytest

from trace2task.cua_execution import CuaExecutionBackend
from trace2task.execution_core import ExecutionCore
from trace2task.execution_protocol import (
    COORDINATE_SPACE,
    PROTOCOL_VERSION,
    ActionPlan,
    ActionUnavailable,
)
from trace2task.local_gui_protocol import decode

TARGET = {'pid': 10, 'window_id': 20}
BOUNDS = {'x': 1, 'y': 2, 'width': 500, 'height': 300}
CLICK = {'skill': 'click', 'args': {'x': .5, 'y': .5}}


class FakeForeground:
    """Deterministic Win32-focus substitute; never changes real foreground."""

    def __init__(self):
        self.foreground = 99
        self.focus_succeeds = True
        self.focus_calls = []
        self.windows = {
            20: SimpleNamespace(handle=20, process_id=10, is_minimized=False, is_visible=True),
            99: SimpleNamespace(handle=99, process_id=999, is_minimized=False, is_visible=True),
        }

    def foreground_handle(self):
        return self.foreground

    def get_window(self, handle):
        return self.windows.get(handle)

    def focus_window(self, handle):
        self.focus_calls.append(handle)
        if self.focus_succeeds:
            self.foreground = handle
        return self.focus_succeeds


@pytest.fixture
def harness():
    sent, records = [], []
    def require(target):
        if target != TARGET:
            raise ValueError('Unauthorized target')
    scope = SimpleNamespace(require_window=require,
                            require_app=lambda _: (_ for _ in ()).throw(ValueError('Unauthorized app')))
    driver = SimpleNamespace(
        window=lambda _: {**TARGET, 'bounds': BOUNDS},
        call=lambda name, payload: sent.append((name, payload)) or {'effect': 'unverifiable'})
    stop = SimpleNamespace(raise_if_requested=lambda: None, sleep=lambda _: None)
    backend = CuaExecutionBackend(driver, scope, foreground=FakeForeground())
    core = ExecutionCore(backend, stop, lambda kind, **kw: records.append({'type': kind, **kw}))
    state = {**TARGET, 'session': 'test', 'window_bounds': dict(BOUNDS),
             'screenshot_width': 500, 'screenshot_height': 300,
             'elements_complete': False, 'elements': []}
    return core, state, sent, records


def execute(harness, actions, observation='frame-0'):
    core, state, _, _ = harness
    return core.execute({'actions': actions}, target=TARGET, observation_id=observation, state=state)


@pytest.mark.parametrize(('model', 'tool', 'action', 'scale'), [
    ('gui-owl-2b', 'computer_use', 'left_click', 1000),
    ('mai-ui-2b', 'mobile_use', 'click', 999),
])
def test_native_adapter_to_identical_cua_dispatch(harness, model, tool, action, scale):
    text = '<tool_call>' + json.dumps({'name': tool, 'arguments': {
        'action': action, 'coordinate': [scale/2, scale/2]}}) + '</tool_call>'
    prediction = decode(model, text, cua=True)
    outcome = execute(harness, prediction['actions'])
    assert outcome.status == 'delivered'
    name, wire = harness[2][0]
    assert name == 'click' and (wire['x'], wire['y']) == (250, 150)
    assert wire['delivery_mode'] == 'background'
    assert (wire['pid'], wire['window_id']) == (10, 20)
    assert outcome.receipt['effect'] == 'unverifiable'
    assert harness[3][0]['protocol_version'] == '1'


def test_audit_keeps_normalized_action_and_exact_cua_request(harness):
    result = execute(harness, [CLICK])
    assert result.status == 'delivered'
    assert harness[0].last_plan['actions'] == [
        {'skill': 'click', 'args': {'x': .5, 'y': .5, 'button': 'left'}}]
    request = harness[0].last_requests[0]
    assert request['action_index'] == 1 and request['status'] == 'delivered'
    assert request['request']['backend'] == 'cua'
    assert request['request']['operation'] == 'click'
    assert request['request']['args'] == harness[2][0][1]
    assert request['request']['args']['x'] == 250
    assert harness[3][-2]['executor_request'] == request['request']


def test_preflight_rejection_has_normalized_plan_but_no_executor_request(harness):
    result = execute(harness, [{'skill': 'move_cursor', 'args': {'x': .2, 'y': .3}}])
    assert result.status == 'rejected'
    assert harness[0].last_plan['actions'][0]['skill'] == 'move_cursor'
    assert harness[0].last_requests == []
    assert not harness[2]


def test_native_owl_hover_reaches_cua_adapter_then_rejects_without_input(harness):
    text = '<tool_call>' + json.dumps({'name': 'computer_use', 'arguments': {
        'action': 'mouse_move', 'coordinate': [200, 300]}}) + '</tool_call>'
    prediction = decode('gui-owl-2b', text)
    assert prediction['actions'] == [{'skill': 'move_cursor', 'args': {'x': .2, 'y': .3}}]
    outcome = execute(harness, prediction['actions'])
    assert outcome.status == 'rejected'
    assert 'background hover' in outcome.reason
    assert harness[0].feedback['executed'] is False
    assert not harness[2]


def test_cua_delivery_warning_is_feedback_not_a_failed_or_retried_action(harness):
    sent = harness[2]
    harness[0].backend.driver.call = lambda name, payload: sent.append((name, payload)) or {
        'effect': 'unverifiable',
        'escalation': {'reason': 'delivery_failed', 'target': 'foreground'},
    }
    outcome = execute(harness, [CLICK])
    assert outcome.status == 'delivered'
    assert outcome.receipt['adapter_advisory'] == 'delivery_path_warning'
    assert len(sent) == 1
    assert not any(event['type'] == 'foreground_retry' for event in harness[3])


def test_cua_reports_driver_capabilities_independently_of_model(harness):
    backend, state = harness[0].backend, harness[1]
    capabilities = backend.capabilities(state)
    assert 'switch_window' in capabilities['available_skills']
    assert 'launch_app' in capabilities['available_skills']
    assert 'move_cursor' in capabilities['unavailable_skills']


@pytest.mark.parametrize('actions', [[], [{'done': 1}], [{'done': True}, CLICK],
    [CLICK]*9, [{'skill': 'click', 'args': {'x': float('nan'), 'y': 0}}],
    [{'skill': 'click', 'args': {'x': True, 'y': 0}}],
    [{'skill': 'press_key', 'args': {'key': 'f', 'script': 'unsafe'}}]])
def test_bad_plan_never_dispatches(harness, actions):
    with pytest.raises(ValueError):
        execute(harness, actions)
    assert not harness[2]


def test_full_group_permissions_checked_before_first_action(harness):
    with pytest.raises(ValueError, match='Unauthorized'):
        execute(harness, [CLICK, {'skill': 'switch_window', 'args': {'pid': 10, 'window_id': 21}}])
    assert not harness[2]


def test_batch_keeps_delivered_prefix_when_later_capability_is_unavailable(harness):
    harness[1].update(elements_complete=True, elements=[])
    outcome = execute(harness, [CLICK, {'skill': 'type_text', 'args': {'text': '123'}}])
    assert outcome.status == 'rejected' and len(harness[2]) == 1
    expected_click = {'skill': 'click', 'args': {**CLICK['args'], 'button': 'left'}}
    assert [step['action'] for step in outcome.steps] == [expected_click]
    assert harness[0].feedback['executed'] is False
    outcome = execute(harness, [CLICK, CLICK], 'frame-1')
    assert outcome.status == 'delivered' and len(harness[2]) == 3
    assert [step['action'] for step in outcome.steps] == [expected_click, expected_click]
    assert not any(e['type'] == 'discard_remaining' for e in harness[3])


def test_visual_content_change_does_not_discard_remainder(harness):
    sent = harness[2]
    def changed_ui(tool, payload):
        sent.append((tool, payload))
        # The application may redraw while its authorized target and geometry stay fixed.
        harness[1]['visual_frame'] = len(sent)
        return {'effect': 'unverifiable'}
    harness[0].backend.driver.call = changed_ui
    outcome = execute(harness, [CLICK, CLICK])
    assert outcome.status == 'delivered'
    assert len(sent) == 2 and len(outcome.steps) == 2


def test_batch_done_is_requested_only_after_all_actions_are_delivered(harness):
    outcome = execute(harness, [CLICK, CLICK, {'done': True}])
    assert outcome.status == 'completion_requested'
    assert len(outcome.steps) == 2 and len(harness[2]) == 2
    assert not any(event['type'] == 'discard_remaining' for event in harness[3])


def test_uncertain_later_dispatch_stops_batch_without_replay(harness):
    sent = harness[2]
    def fail_second(tool, payload):
        sent.append((tool, payload))
        if len(sent) == 2:
            raise RuntimeError('transport timeout')
        return {'effect': 'unverifiable'}
    harness[0].backend.driver.call = fail_second
    with pytest.raises(RuntimeError, match='transport timeout'):
        execute(harness, [CLICK, CLICK, CLICK])
    assert len(sent) == 2
    assert len(harness[0].last_steps) == 1
    assert harness[0].stopped
    assert harness[3][-1]['status'] == 'interrupted_or_unknown'


def test_target_change_is_a_batch_boundary_not_visual_change(harness):
    other = {'pid': 11, 'window_id': 21}
    harness[0].backend.scope.require_window = lambda _target: None
    harness[0].backend.scope.switch = lambda _target: other
    outcome = execute(harness, [
        {'skill': 'switch_window', 'args': other}, CLICK])
    assert outcome.status == 'reobserve'
    assert outcome.target == other
    assert len(outcome.steps) == 1
    assert not harness[2]


def test_action_limit_can_stop_with_delivered_batch_prefix(harness):
    harness[0].max_actions = 1
    outcome = execute(harness, [CLICK, CLICK])
    assert outcome.status == 'action_limit'
    assert len(outcome.steps) == 1 and len(harness[2]) == 1


def test_consumed_observation_cannot_be_replayed(harness):
    execute(harness, [CLICK])
    with pytest.raises(ValueError, match='consumed'):
        execute(harness, [CLICK])
    assert len(harness[2]) == 1


@pytest.mark.parametrize('change', ['geometry', 'identity'])
def test_stale_observation_is_not_executed(harness, change):
    if change == 'geometry':
        harness[1]['window_bounds']['width'] += 1
        assert execute(harness, [CLICK]).status == 'reobserve'
        assert not harness[0].stopped
    else:
        harness[1]['window_id'] += 1
        with pytest.raises(RuntimeError, match='窗口身份'):
            execute(harness, [CLICK])
    assert not harness[2]


def test_live_window_geometry_change_requires_fresh_observation(harness):
    harness[0].backend.driver.window = lambda _: {
        **TARGET, 'bounds': {**BOUNDS, 'x': BOUNDS['x'] + 10}}
    result = execute(harness, [CLICK])
    assert result.status == 'reobserve'
    assert not harness[2]
    assert any(event['type'] == 'execution_result' and event['executed'] is False
               for event in harness[3])


def test_unknown_effect_is_not_retried(harness):
    harness[0].backend.driver.call = lambda *args: {'effect': 'partial'}
    with pytest.raises(RuntimeError):
        execute(harness, [CLICK])
    assert harness[3][-1]['status'] == 'interrupted_or_unknown'
    assert harness[3][-1]['executed'] is None


def test_window_title_is_never_a_text_control(harness):
    core, state, sent, _ = harness
    state.update(elements_complete=True, elements=[{
        'role': 'Window', 'enabled': True, 'actions': ['set_value'], 'element_token': 'snapshot:0'}])
    assert core.backend.capabilities(state)['text_input_requires_coordinate'] is True
    assert execute(harness, [{'skill': 'type_text', 'args': {'text': 'abc'}}]).status == 'rejected'
    assert not sent


def test_coordinate_text_works_without_control_tree(harness):
    action = {'skill': 'type_text', 'args': {'text': '测试', 'x': .3, 'y': .7}}
    result = execute(harness, [action])
    assert result.status == 'delivered'
    name, payload = harness[2][0]
    assert name == 'type_text'
    assert (payload['x'], payload['y']) == (150, 210)
    assert payload['text'] == '测试' and payload['delivery_mode'] == 'background'
    assert 'element_token' not in payload
    assert (payload['pid'], payload['window_id']) == (10, 20)


def test_bare_text_uses_cua_current_focused_control_without_tree(harness):
    result = execute(harness, [{'skill': 'type_text', 'args': {'text': '测试'}}])
    assert result.status == 'delivered'
    name, payload = harness[2][0]
    assert name == 'type_text'
    assert payload == {
        'pid': 10, 'window_id': 20, 'session': 'test', 'delivery_mode': 'background',
        'text': '测试',
    }
    assert result.receipt['effect'] == 'unverifiable'
    assert 'x' not in payload and 'y' not in payload and 'element_token' not in payload


@pytest.mark.parametrize('args', [
    {'text': 'test', 'x': .3}, {'text': 'test', 'x': True, 'y': .2},
    {'text': 'test', 'x': 1.1, 'y': .2}, {'text': 'test', 'x': .2, 'y': float('nan')},
    {'text': 'test', 'x': .2, 'y': .2, 'element_token': 'invented'},
])
def test_bad_coordinate_text_never_dispatches(harness, args):
    with pytest.raises(ValueError):
        execute(harness, [{'skill': 'type_text', 'args': args}])
    assert not harness[2]


def test_three_capability_rejections_stop(harness):
    harness[1].update(elements_complete=True, elements=[])
    for i, expected in enumerate(['rejected', 'rejected', 'capability_rejection_limit']):
        assert execute(harness, [{'skill': 'type_text', 'args': {'text': 'abc'}}], str(i)).status == expected
    assert not harness[2]


def test_stop_before_dispatch_and_action_budget(harness):
    core = harness[0]
    core.max_actions = 1
    assert execute(harness, [CLICK]).status == 'delivered'
    assert execute(harness, [CLICK], 'frame-1').status == 'action_limit'
    assert len(harness[2]) == 1


def test_done_is_not_success_or_an_os_action(harness):
    assert execute(harness, [{'done': True}]).status == 'completion_requested'
    assert not harness[2]
    assert ActionPlan.from_prediction({'actions': [{'done': True}]}).done
    assert not harness[0].stopped  # read-only reviewer may reject the claim


def test_plan_protocol_envelope_is_versioned_and_legacy_input_remains_compatible(harness):
    legacy = {'actions': [CLICK]}
    canonical = ActionPlan.from_prediction(legacy).to_payload()
    assert canonical['protocol_version'] == PROTOCOL_VERSION
    assert canonical['coordinate_space'] == COORDINATE_SPACE
    assert ActionPlan.from_prediction(canonical) == ActionPlan.from_prediction(legacy)
    assert harness[0].execute(canonical, target=TARGET,
                              observation_id='canonical', state=harness[1]).status == 'delivered'


@pytest.mark.parametrize('change', [
    {'protocol_version': '999'},
    {'coordinate_space': 'absolute_screen_pixels'},
    {'unexpected': True},
])
def test_unknown_plan_protocol_is_rejected_before_native_input(harness, change):
    value = {'protocol_version': PROTOCOL_VERSION, 'coordinate_space': COORDINATE_SPACE,
             'actions': [CLICK], **change}
    with pytest.raises(ValueError):
        harness[0].execute(value, target=TARGET, observation_id='bad-version', state=harness[1])
    assert not harness[2]


def test_repeated_clicks_are_model_decisions_not_code_progress_judgments(harness):
    for i in range(5):
        assert execute(harness, [CLICK], str(i)).status == 'delivered'
    assert len(harness[2]) == 5
    assert harness[0].feedback is None


def test_explicit_background_refusal_retries_same_action_once_and_keeps_effect_pending(harness):
    receipt = {'code': 'background_unavailable', 'effect': 'unverifiable',
               'path': 'ax', 'uia_status': 'unavailable', 'verified': False}
    calls = []
    def reply(name, payload):
        calls.append((name, payload))
        return receipt if payload['delivery_mode'] == 'background' else {'effect': 'unverifiable'}
    harness[0].backend.driver.call = reply
    result = execute(harness, [CLICK])
    # ``delivered`` means the driver accepted the foreground submission.  It
    # does not mean the application changed or the task succeeded.
    assert result.status == 'delivered' and result.background_refusal == receipt
    assert result.delivery_mode == 'foreground'
    assert result.receipt['effect'] == 'unverifiable'
    assert len(calls) == 2 and calls[0][0] == calls[1][0] == 'click'
    assert calls[0][1]['delivery_mode'] == 'background'
    assert calls[1][1] == {**calls[0][1], 'delivery_mode': 'foreground'}
    assert [item['status'] for item in harness[0].last_requests] == ['refused', 'delivered']
    assert [item['request']['args'] for item in harness[0].last_requests] == [
        calls[0][1], calls[1][1]]
    assert harness[0].backend.foreground.focus_calls == [20, 99]
    assert harness[0].backend.foreground.foreground_handle() == 99
    assert [e['type'] for e in harness[3]].count('foreground_retry') == 1
    entry = next(e for e in harness[3] if e['type'] == 'execution_result')
    assert entry['executed'] is True and entry['background_refusal'] == receipt
    assert entry['effect'] == 'unverifiable'
    assert 'task_success_verified' not in entry


def test_background_refusal_switches_that_window_to_foreground_for_later_actions(harness):
    calls = []
    def reply(name, payload):
        calls.append((name, payload))
        if payload['delivery_mode'] == 'background':
            return {'code': 'background_unavailable', 'effect': 'unverifiable'}
        return {'effect': 'unverifiable'}
    harness[0].backend.driver.call = reply
    first = execute(harness, [CLICK], 'frame-0')
    second = execute(harness, [CLICK], 'frame-1')
    assert first.status == second.status == 'delivered'
    assert first.delivery_mode == second.delivery_mode == 'foreground'
    assert [payload['delivery_mode'] for _, payload in calls] == [
        'background', 'foreground', 'foreground']
    assert harness[0].backend.foreground_targets == {(10, 20)}
    assert harness[0].backend.foreground.foreground_handle() == 99
    assert [event['type'] for event in harness[3]].count('foreground_retry') == 1


def test_background_only_mode_never_steals_focus_or_retries(harness):
    harness[0].backend.allow_foreground_retry = False
    calls = []
    harness[0].backend.driver.call = lambda name, payload: (
        calls.append((name, payload)) or
        {'code': 'background_unavailable', 'effect': 'unverifiable'})
    with pytest.raises(RuntimeError, match='未自动切前台'):
        execute(harness, [CLICK])
    assert [payload['delivery_mode'] for _, payload in calls] == ['background']
    assert harness[0].backend.foreground.focus_calls == []
    assert not any(event['type'] == 'foreground_retry' for event in harness[3])
    entry = next(e for e in harness[3] if e['type'] == 'execution_result')
    assert entry['executed'] is False
    assert entry['delivery_mode_requested'] == 'background'


def test_foreground_retry_failure_stops_without_third_call(harness):
    receipt = {'code': 'background_unavailable', 'effect': 'unverifiable'}
    calls = []
    def reply(name, payload):
        calls.append((name, payload))
        return receipt
    harness[0].backend.driver.call = reply
    with pytest.raises(RuntimeError, match='后台输入不可用'):
        execute(harness, [CLICK])
    assert [payload['delivery_mode'] for _, payload in calls] == ['background', 'foreground']
    assert harness[0].stopped
    entry = next(e for e in harness[3] if e['type'] == 'execution_result')
    assert entry['executed'] is None and entry['background_refusal'] == receipt


def test_focus_denial_prevents_foreground_cua_call(harness):
    calls = []
    def reply(name, payload):
        calls.append((name, payload))
        return {'code': 'background_unavailable', 'effect': 'unverifiable'}
    harness[0].backend.driver.call = reply
    foreground = harness[0].backend.foreground
    foreground.focus_succeeds = False

    with pytest.raises(RuntimeError):
        execute(harness, [CLICK])

    # Windows denied the preflight focus, so no foreground/global-input call
    # may be sent to Cua.
    assert [payload['delivery_mode'] for _, payload in calls] == ['background']
    assert foreground.focus_calls == [20]
    assert foreground.foreground_handle() == 99


def test_refusal_does_not_front_if_window_changes_before_retry(harness):
    calls = []
    def reply(name, payload):
        calls.append((name, payload))
        harness[1]['window_bounds']['width'] += 1
        return {'code': 'background_unavailable', 'effect': 'unverifiable'}
    harness[0].backend.driver.call = reply
    result = execute(harness, [CLICK])
    assert result.status == 'reobserve'
    assert result.background_refusal is not None
    assert len(calls) == 1 and calls[0][1]['delivery_mode'] == 'background'


def test_unrecognized_driver_code_is_unknown_not_delivered(harness):
    harness[0].backend.driver.call = lambda *args: {'code': 'new_error', 'effect': 'unverifiable'}
    with pytest.raises(RuntimeError, match='非成功'):
        execute(harness, [CLICK])
    assert harness[3][-1]['executed'] is None


@pytest.mark.parametrize('receipt', [
    {'status': 'refused', 'effect': 'unverifiable'},
    {'error': 'background path unavailable', 'effect': 'unverifiable'},
    {'code': 'background_not_available', 'effect': 'unverifiable'},
])
def test_only_exact_background_unavailable_code_can_trigger_foreground_retry(harness, receipt):
    calls = []
    def reply(name, payload):
        calls.append((name, payload))
        return receipt
    harness[0].backend.driver.call = reply
    with pytest.raises(RuntimeError):
        execute(harness, [CLICK])
    assert [payload['delivery_mode'] for _, payload in calls] == ['background']
    assert not any(event['type'] == 'foreground_retry' for event in harness[3])


def test_stop_between_validation_and_dispatch_latches_core(harness):
    count = 0
    def stop():
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError('stop requested')
    harness[0].stop.raise_if_requested = stop
    with pytest.raises(RuntimeError, match='stop requested'):
        execute(harness, [CLICK])
    assert not harness[2]
    harness[0].stop.raise_if_requested = lambda: None
    with pytest.raises(RuntimeError, match='core is stopped'):
        execute(harness, [CLICK], 'fresh-frame')
    assert not harness[2]


def test_transport_timeout_never_allows_automatic_resume(harness):
    calls = []
    def timeout(*args):
        calls.append(args)
        raise RuntimeError('transport timeout')
    harness[0].backend.driver.call = timeout
    with pytest.raises(RuntimeError, match='transport timeout'):
        execute(harness, [CLICK])
    with pytest.raises(RuntimeError, match='core is stopped'):
        execute(harness, [CLICK], 'new-frame')
    assert len(calls) == 1


def test_text_token_requires_fresh_complete_editable_control(harness):
    state = harness[1]
    state.update(elements_complete=True, elements=[{
        'role': 'Edit', 'enabled': True, 'actions': ['set_value'], 'element_token': 'snapshot:1'}])
    assert execute(harness, [{'skill': 'type_text', 'args': {'text': 'abc'}}]).status == 'delivered'
    assert harness[2][0][1]['element_token'] == 'snapshot:1'
    state['elements'].append(dict(state['elements'][0], element_token='snapshot:2'))
    with pytest.raises(ActionUnavailable):
        harness[0].backend.prepare(ActionPlan.from_prediction({'actions': [
            {'skill': 'type_text', 'args': {'text': 'abc'}}]}).actions[0], state)
