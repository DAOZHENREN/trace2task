import pytest
from PIL import Image

from trace2task.cua_desktop import DESKTOP_TARGET, CuaDesktopExecutionBackend, CuaDesktopObservation
from trace2task.execution_core import ExecutionCore
from trace2task.execution_protocol import ActionUnavailable, ObservationStale, UnifiedAction


class Driver:
    def __init__(self, root):
        self.root = root
        self.width = 200
        self.calls = []

    def call(self, tool, payload):
        self.calls.append((tool, payload))
        if tool == 'get_screen_size':
            return {'width': self.width, 'height': 100, 'scale_factor': 1.5}
        if tool == 'get_desktop_state':
            Image.new('RGB', (200, 100)).save(payload['screenshot_out_file'])
            return {'display': 'primary', 'screen_width': 200, 'screen_height': 100, 'scale_factor': 1.5}
        return {'effect': 'unverifiable'}

    def start(self):
        pass

    def close(self):
        pass


class Stop:
    def start(self):
        pass

    def close(self):
        pass

    def raise_if_requested(self):
        pass

    def sleep(self, seconds):
        pass


POST_CLICK_ERROR = (
    'Cua returned a non-JSON error: foreground_unavailable: exact target HWND '
    '0x5b0d12 or a verified same-process post-action window was not foreground '
    'after the click (actual foreground HWND 0xa20d80)\n')


def test_cross_app_click_reobserves_without_retry_or_remaining_batch(tmp_path):
    class SwitchingDriver(Driver):
        def call(self, tool, payload):
            if tool == 'click':
                self.calls.append((tool, payload))
                raise RuntimeError(POST_CLICK_ERROR)
            return super().call(tool, payload)

    driver = SwitchingDriver(tmp_path)
    target, state, _ = CuaDesktopObservation(driver).observe(None, 0)
    events = []
    core = ExecutionCore(CuaDesktopExecutionBackend(driver), Stop(),
                         lambda kind, **data: events.append((kind, data)))
    result = core.execute({'actions': [
        {'skill': 'double_click', 'args': {'x': .5, 'y': .25}},
        {'skill': 'type_text', 'args': {'text': 'must not send'}},
    ]}, target=target, observation_id='one', state=state)
    assert result.status == 'reobserve'
    assert core.feedback['executed'] is None
    assert core.last_requests[0]['status'] == 'unknown'
    assert not result.steps
    assert [name for name, _ in driver.calls if not name.startswith('get_')] == ['click']
    assert not any(data.get('executed') is True for _, data in events)
    CuaDesktopObservation(driver).observe(None, 1)
    assert (tmp_path / '0001.png').is_file()


@pytest.mark.parametrize('message,operation', [
    ('Cua returned a non-JSON error: other error', 'click'),
    (POST_CLICK_ERROR.replace('0xa20d80', '0x0'), 'click'),
    (POST_CLICK_ERROR.replace('0xa20d80', '0x5b0d12'), 'click'),
    (POST_CLICK_ERROR, 'type_text'),
])
def test_other_desktop_errors_still_stop(tmp_path, message, operation):
    class FailingDriver(Driver):
        def call(self, tool, payload):
            raise RuntimeError(message)

    from trace2task.execution_protocol import PostActionReobserve

    with pytest.raises(RuntimeError) as caught:
        CuaDesktopExecutionBackend(FailingDriver(tmp_path)).dispatch(
            None, (operation, {}), DESKTOP_TARGET, Stop())
    assert not isinstance(caught.value, PostActionReobserve)


def test_desktop_batch_maps_physical_pixels_and_preserves_order(tmp_path):
    driver = Driver(tmp_path)
    target, state, path = CuaDesktopObservation(driver).observe(None, 0)
    core = ExecutionCore(CuaDesktopExecutionBackend(driver), Stop(), lambda *a, **k: None)
    result = core.execute({'actions': [
        {'skill': 'click', 'args': {'x': .5, 'y': .25}},
        {'skill': 'type_text', 'args': {'text': 'hello'}},
        {'skill': 'scroll', 'args': {'direction': 'down', 'amount': 2, 'by': 'line', 'x': 1, 'y': 1}},
    ]}, target=target, observation_id='one', state=state)
    assert result.status == 'delivered'
    inputs = [(name, payload) for name, payload in driver.calls if not name.startswith('get_')]
    assert [name for name, _ in inputs] == ['click', 'type_text', 'scroll']
    assert inputs[0][1]['x'] == 100 and inputs[0][1]['y'] == 25
    assert inputs[2][1]['x'] == 199 and inputs[2][1]['y'] == 99
    assert all(p['target'] == DESKTOP_TARGET and 'pid' not in p for _, p in inputs)
    assert path.is_file()


def test_resolution_change_reobserves_without_input(tmp_path):
    driver = Driver(tmp_path)
    target, state, _ = CuaDesktopObservation(driver).observe(None, 0)
    driver.width = 300
    with pytest.raises(ObservationStale):
        CuaDesktopExecutionBackend(driver).check_current(target, state)
    assert all(name.startswith('get_') for name, _ in driver.calls)


def test_desktop_drag_hover_and_unsupported_action(tmp_path):
    driver = Driver(tmp_path)
    _, state, _ = CuaDesktopObservation(driver).observe(None, 0)
    backend = CuaDesktopExecutionBackend(driver)
    tool, payload = backend.prepare(UnifiedAction.from_payload({'skill': 'drag', 'args': {
        'start_x': 0, 'start_y': 0, 'end_x': 1, 'end_y': 1, 'duration_ms': 700}}), state)
    assert tool == 'drag' and payload['to_x'] == 199 and payload['duration_ms'] == 700
    tool, payload = backend.prepare(UnifiedAction.from_payload({'skill': 'move_cursor', 'args': {'x': .5, 'y': .5}}), state)
    assert tool == 'move_cursor' and payload['target'] == DESKTOP_TARGET
    with pytest.raises(ActionUnavailable):
        backend.prepare(UnifiedAction('launch_app', {'app_id': 0}), state)


@pytest.mark.parametrize('provider', ['api', 'codex'])
@pytest.mark.parametrize('cross_app_click', [False, True])
def test_chat_providers_complete_shared_cua_desktop_loop(tmp_path, provider, cross_app_click):
    import json
    from types import SimpleNamespace

    from trace2task.chat_agent_runner import run_chat_agent

    class Session:
        def __init__(self):
            self.replies = iter([
                {'task_complete': False, 'reason': 'type', 'actions': [
                    ({'skill': 'double_click', 'args': {'x': .5, 'y': .5}} if cross_app_click
                     else {'skill': 'type_text', 'args': {'text': 'hello'}})]},
                {'task_complete': True, 'reason': 'visible', 'actions': []},
                {'verdict': 'complete', 'evidence': 'hello visible', 'missing': ''},
            ])

        def reset_thread(self):
            pass

        def run_turn(self, **request):
            assert 'current selected window' not in request['prompt']
            return json.dumps(next(self.replies))

    class SwitchingDriver(Driver):
        def call(self, tool, payload):
            if tool == 'click':
                self.calls.append((tool, payload))
                raise RuntimeError(POST_CLICK_ERROR)
            return super().call(tool, payload)

    driver = SwitchingDriver(tmp_path) if cross_app_click else Driver(tmp_path)
    result = run_chat_agent(instruction='type hello', model='fake', reasoning_effort='low',
        output_root=tmp_path, emergency_stop=Stop(), status_callback=lambda message: None,
        executor_backend='cua', cua_target=DESKTOP_TARGET, session=Session(),
        api_config=SimpleNamespace() if provider == 'api' else None, cua_driver=driver)
    assert result['actions'] == (0 if cross_app_click else 1)
    assert result['stop_reason'] == 'visual_completion_reviewed'
    if cross_app_click:
        assert [name for name, _ in driver.calls].count('click') == 1
        assert [name for name, _ in driver.calls].count('get_desktop_state') == 3
        assert result['model_io'][0]['execution']['executed'] is None
