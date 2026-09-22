import json

import pytest

from trace2task.actions import ActionCall
from trace2task.cua_backend import action_request, response_value


@pytest.mark.parametrize('value', ['not found', '[]', '{"status":"refused"}', '{"isError":true}', '{"refusal":{"code":"stale"}}'])
def test_exit_zero_is_not_success(value):
    with pytest.raises(RuntimeError):
        response_value(value)


def state():
    return {'pid': 1,'window_id': 2,'session': 'test','screenshot_width': 1000,'screenshot_height': 600,'elements_complete': True,'elements': []}


def test_normalized_window_coordinate():
    tool, value=action_request(ActionCall('click',{'x': .5,'y': 1}),state())
    assert (tool,value['x'],value['y'])==('click',500,599)
    assert value['delivery_mode']=='background'
    assert (value['pid'],value['window_id'])==(1,2)


def test_ambiguous_text_refused():
    with pytest.raises(ValueError):
        action_request(ActionCall('type_text',{'text': 'test'}),state())


def test_text_uses_snapshot_token():
    value=state()
    value['elements']=[{'enabled': True,'actions': ['set_value'],'element_token': 's00000001:0'}]
    assert action_request(ActionCall('type_text',{'text': 'test'}),value)[1]['element_token']=='s00000001:0'


def test_invalid_cua_screenshot_size_refuses_coordinate_input():
    value = state()
    value['screenshot_width'] = 0
    with pytest.raises(ValueError):
        action_request(ActionCall('click', {'x': .5, 'y': .5}), value)


@pytest.mark.parametrize('elements', [
    [{'enabled': True, 'actions': 'set_value', 'element_token': 'x'}],
    [None],
])
def test_malformed_cua_control_tree_refuses_text_input(elements):
    value = state()
    value['elements'] = elements
    with pytest.raises(ValueError):
        action_request(ActionCall('type_text', {'text': 'test'}), value)


def test_drag_uses_window_pixels_without_foreground_fallback():
    tool, payload = action_request(ActionCall('drag',{'start_x': 0,'start_y': 0,'end_x': 1,'end_y': 1,'duration_ms': 100}),state())
    assert tool == 'drag'
    assert (payload['to_x'], payload['to_y']) == (999,599)
    assert payload['delivery_mode'] == 'background'


def test_scroll_explicit_units():
    from trace2task.local_gui_protocol import cua_action
    call = cua_action({'skill':'scroll','args':{'direction':'down','amount':3,'by':'line'}})
    assert action_request(call,state())[1]['by'] == 'line'
    with pytest.raises(ValueError):
        cua_action({'skill':'scroll','args':{'direction':'down','amount':120,'by':'wheel'}})


def test_window_selection_is_fresh_and_exact(tmp_path):
    from trace2task.cua_backend import CuaBackend
    backend = CuaBackend(tmp_path, lambda *a, **kw: None)
    backend.call = lambda tool, payload: {'windows': [{'pid': 10, 'window_id': 20}]}
    assert backend.bind({'pid': 10,'window_id': 20}) == {'pid': 10,'window_id': 20}
    with pytest.raises(RuntimeError):
        backend.bind({'pid': 11,'window_id': 20})


def test_extended_actions_not_enabled_in_win32():
    from trace2task.local_gui_protocol import decode
    text = '{"actions":[{"skill":"switch_window","args":{"pid":1,"window_id":2}}]}'
    with pytest.raises(ValueError):
        decode('qwen3-vl-2b',text)
    assert decode('qwen3-vl-2b',text,cua=True)['actions'][0]['skill'] == 'switch_window'


def test_unverifiable_is_preserved():
    assert response_value(json.dumps({'effect':'unverifiable'}))['effect']=='unverifiable'
