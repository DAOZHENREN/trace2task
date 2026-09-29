"""Model-format -> unified protocol -> background driver, without native input."""
import json

import pytest

from trace2task.cua_backend import action_request, response_value
from trace2task.cua_execution import CuaExecutionBackend
from trace2task.execution_protocol import ActionUnavailable, DriverRefusal, UnifiedAction
from trace2task.local_gui_protocol import decode, prompt


def parse(model, args):
    tool = 'computer_use' if model == 'gui-owl-2b' else 'mobile_use'
    raw = '<tool_call>' + json.dumps({'name': tool, 'arguments': args}) + '</tool_call>'
    return UnifiedAction.from_payload(decode(model, raw, cua=True)['actions'][0])


STATE = {'pid': 1, 'window_id': 2, 'session': 'test',
         'screenshot_width': 1000, 'screenshot_height': 500}


@pytest.mark.parametrize(('model', 'args', 'direction', 'amount', 'by'), [
    ('gui-owl-2b', {'action': 'scroll', 'pixels': -3}, 'down', 3, 'line'),
    ('gui-owl-2b', {'action': 'scroll', 'pixels': 4}, 'up', 4, 'line'),
    ('gui-owl-2b', {'action': 'hscroll', 'pixels': -2}, 'left', 2, 'line'),
    ('gui-owl-2b', {'action': 'hscroll', 'pixels': 2}, 'right', 2, 'line'),
    ('mai-ui-2b', {'action': 'scroll', 'direction': 'down', 'amount': 2, 'by': 'page'}, 'down', 2, 'page'),
])
def test_scroll_to_cua_units(model, args, direction, amount, by):
    name, wire = action_request(parse(model, args), STATE)
    assert name == 'scroll'
    assert wire == {'pid': 1, 'window_id': 2, 'session': 'test', 'delivery_mode': 'background',
                    'direction': direction, 'amount': amount, 'by': by}


@pytest.mark.parametrize(('model', 'kind', 'end', 'scale'), [
    ('gui-owl-2b', 'left_click_drag', 'coordinate', 1000),
    ('mai-ui-2b', 'drag', 'end_coordinate', 999),
])
def test_explicit_drag_endpoints_to_cua_pixels(model, kind, end, scale):
    action = parse(model, {'action': kind, 'start_coordinate': [scale*.2, scale*.4],
                          end: [scale*.8, scale*.6], 'duration_ms': 350})
    name, wire = action_request(action, STATE)
    assert name == 'drag' and wire['delivery_mode'] == 'background'
    assert (wire['from_x'], wire['from_y'], wire['to_x'], wire['to_y']) == (200, 200, 800, 300)
    assert wire['duration_ms'] == 350


@pytest.mark.parametrize('args', [
    {'action': 'left_click_drag', 'coordinate': [700, 300]},
    {'action': 'left_click_drag', 'start_coordinate': [False, 2], 'coordinate': [3, 4]},
    {'action': 'left_click_drag', 'start_coordinate': [-1, 2], 'coordinate': [3, 4]},
    {'action': 'left_click_drag', 'start_coordinate': [1, 2], 'coordinate': [3, 4], 'duration_ms': 9000},
    {'action': 'scroll', 'pixels': 0}, {'action': 'scroll', 'pixels': True},
    {'action': 'scroll', 'pixels': 51}, {'action': 'scroll', 'pixels': 1.5},
])
def test_invalid_or_implicit_motion_is_not_guessed(args):
    with pytest.raises(ValueError):
        parse('gui-owl-2b', args)


def test_mobile_swipe_not_silently_reversed_into_wheel_scroll():
    with pytest.raises(ValueError):
        parse('mai-ui-2b', {'action': 'swipe', 'direction': 'up'})


def test_coordinate_scroll_is_preserved_then_rejected_not_silently_ignored():
    action = parse('gui-owl-2b', {'action': 'scroll', 'pixels': -3, 'coordinate': [500, 500]})
    assert action.args['x'] == .5
    with pytest.raises(ActionUnavailable):
        action_request(action, STATE)


def test_known_refusal_is_not_delivery_even_if_exit_code_zero():
    raw = {'code': 'background_unavailable', 'effect': 'unverifiable', 'verified': False}
    with pytest.raises(DriverRefusal) as error:
        response_value(json.dumps(raw), 0)
    assert error.value.receipt == raw
    with pytest.raises(RuntimeError):
        response_value('{"code":"unknown_code","effect":"confirmed"}', 0)


def test_owl_prompt_preserves_native_tool_schema_in_both_modes():
    from trace2task.local_gui_owl_prompt import OWL_PROMPT

    for cua in (False, True):
        text = prompt('gui-owl-2b', cua=cua)
        assert text.startswith(OWL_PROMPT)
        schema = text.rsplit('<tools>', 1)[1].split('</tools>', 1)[0]
        assert 'left_click_drag' in schema
        assert 'mouse_move' in schema
        assert 'triple_click' in schema
        assert 'start_coordinate' not in schema


def test_owl_native_drag_uses_only_delivered_cursor_as_start():
    raw = '<tool_call>' + json.dumps({'name': 'computer_use', 'arguments': {
        'action': 'left_click_drag', 'coordinate': [700, 300]}}) + '</tool_call>'
    action = UnifiedAction.from_payload(decode(
        'gui-owl-2b', raw, cua=True, cursor_position=[.2, .4])['actions'][0])
    name, wire = action_request(action, STATE)
    assert name == 'drag'
    assert (wire['from_x'], wire['from_y'], wire['to_x'], wire['to_y']) == (200, 200, 700, 150)


def test_cua_hover_is_rejected_without_faking_native_pointer_move():
    raw = '<tool_call>' + json.dumps({'name': 'computer_use', 'arguments': {
        'action': 'mouse_move', 'coordinate': [100, 200]}}) + '</tool_call>'
    action = UnifiedAction.from_payload(decode('gui-owl-2b', raw, cua=True)['actions'][0])
    assert action.to_payload() == {'skill': 'move_cursor', 'args': {'x': .1, 'y': .2}}
    with pytest.raises(ActionUnavailable, match='background hover'):
        CuaExecutionBackend(None, None).prepare(action, STATE)
