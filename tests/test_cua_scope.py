import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from trace2task.cua_scope import CuaScope, normalize_scope

CALCULATOR = {'pid': 10, 'window_id': 20}
NOTEPAD = {'pid': 11, 'window_id': 21}
REALTEK = {'pid': 10, 'window_id': 22}  # Shared ApplicationFrameHost.exe PID!


class Driver:
    def __init__(self):
        self.rows = [dict(**CALCULATOR, title='计算器', app_name='ApplicationFrameHost.exe'),
                     dict(**NOTEPAD, title='记事本', app_name='Notepad.exe'),
                     dict(**REALTEK, title='Realtek', app_name='ApplicationFrameHost.exe')]
        self.calls = []
        self.binds = []
    def windows(self): return self.rows
    def window(self, target):
        matches = [row for row in self.rows
                   if (row.get('pid'), row.get('window_id')) ==
                   (target.get('pid'), target.get('window_id'))]
        if len(matches) != 1 or matches[0].get('minimized'):
            raise RuntimeError('selected window is unavailable')
        return dict(matches[0])
    resolve_window = window
    def call(self, name, payload):
        self.calls.append((name, payload))
        assert name == 'list_apps'
        return {'apps': [{'name':'记事本', 'launch_path':'notepad.exe'},
                         {'name':'无关应用', 'launch_path':'unrelated.exe'}]}
    def bind(self, target):
        self.binds.append(target)
        if 'launch_path' in target:
            assert target['launch_path'] == 'notepad.exe'
            return NOTEPAD
        assert target in [CALCULATOR, NOTEPAD, REALTEK]
        window = self.window(target)
        return {key: window[key] for key in ('pid', 'window_id')}


def test_single_legacy_target_has_no_full_catalog_or_shared_pid_neighbor():
    driver = Driver()
    scope = CuaScope(driver, CALCULATOR)
    current = scope.start()
    context = scope.context(current)
    assert len(context['windows']) == 1
    assert context['windows'][0]['title'] == '计算器'
    assert context['apps'] == []
    assert driver.calls == []  # No full installed-app enumeration needed.
    assert 'Realtek' not in json.dumps(context)
    with pytest.raises(ValueError):
        scope.switch(REALTEK)
    with pytest.raises(ValueError):
        scope.switch(NOTEPAD)
    with pytest.raises(ValueError):
        scope.launch(0)
    assert driver.binds == [CALCULATOR]


def test_multiple_windows_and_nonzero_initial():
    scope = CuaScope(Driver(), {'targets':[CALCULATOR, NOTEPAD], 'initial_index':1})
    assert scope.start() == NOTEPAD
    assert {w['window_id'] for w in scope.context(NOTEPAD)['windows']} == {20,21}
    assert scope.switch(CALCULATOR) == CALCULATOR


def test_selected_launchable_app_only_added_after_explicit_launch():
    driver = Driver()
    scope = CuaScope(driver, {'targets':[CALCULATOR, {'launch_path':'notepad.exe'}], 'initial_index':0})
    scope.start()
    context = scope.context(CALCULATOR)
    assert context['apps'] == [{'app_id':0, 'name':'记事本'}]
    assert len(context['windows']) == 1
    assert scope.launch(0) == NOTEPAD
    assert len(scope.context(NOTEPAD)['windows']) == 2
    assert '无关应用' not in json.dumps(scope.context(NOTEPAD), ensure_ascii=False)
    with pytest.raises(ValueError): scope.launch(1)


@pytest.mark.parametrize('value', [None, {}, {'targets':[],'initial_index':0},
    {'targets':[CALCULATOR],'initial_index':1}, {'targets':[CALCULATOR],'initial_index':True},
    {'targets':[CALCULATOR,CALCULATOR],'initial_index':0}, {'pid':True,'window_id':20},
    {'targets':[CALCULATOR]*13,'initial_index':0}, {'launch_path':''}])
def test_invalid_scope_never_becomes_global(value):
    with pytest.raises(ValueError): normalize_scope(value)


def test_missing_selected_window_fails_without_using_another_same_process():
    driver = Driver()
    driver.rows = [driver.rows[-1]]
    with pytest.raises(RuntimeError): CuaScope(driver, CALCULATOR).start()
    assert driver.binds == [CALCULATOR]


def test_runner_passes_only_selected_scope_and_blocks_invented_switch(tmp_path, monkeypatch):
    from trace2task import cua_runner, local_gui_client
    driver = Driver()
    driver.start = lambda: None
    driver.close = lambda: None
    def observe(target, step):
        image = tmp_path/f'{step}.png'
        image.write_bytes(b'fake PNG not passed to a real model')
        return {'window_bounds':{}}, image
    driver.observe = observe
    monkeypatch.setattr(cua_runner,'CuaBackend',lambda *a:driver)
    received = []
    def predict(task, **kwargs):
        received.append(kwargs['execution_context'])
        return {'status':'predicted','prediction':{'actions':[{'skill':'switch_window','args':REALTEK}]}}
    monkeypatch.setattr(local_gui_client,'predict_gui',predict)
    stop = SimpleNamespace(start=lambda:None,close=lambda:None,raise_if_requested=lambda:None,sleep=lambda _:None)
    result = cua_runner.run_cua_local(instruction='test', output_root=tmp_path,
        emergency_stop=stop,status_callback=lambda _:None,model='qwen3-vl-2b',cua_target=CALCULATOR)
    assert result['actions'] == 0 and result['stop_reason'] == 'error'
    assert len(received[0]['windows']) == 1 and received[0]['apps'] == []
    assert driver.binds == [CALCULATOR, CALCULATOR]
    assert 'Realtek' not in json.dumps(received)


class RunnerDriver(Driver):
    """Fully fake Cua transport for runner regressions; never emits native input."""

    def __init__(self, tmp_path, *, effect='confirmed', fail_capture_at=None, failure_message=None):
        super().__init__()
        self.tmp_path = tmp_path
        self.bounds = {'x': 0, 'y': 0, 'width': 100, 'height': 100}
        self.effect = effect
        self.fail_capture_at = fail_capture_at
        self.failure_message = failure_message
        self.observed_steps = []
        self.input_calls = []

    def start(self):
        return None

    def close(self):
        return None

    def observe(self, target, step):
        self.observed_steps.append(step)
        if step == self.fail_capture_at:
            raise RuntimeError('simulated capture failure')
        image = self.tmp_path / f'capture-{step}.png'
        from PIL import Image
        Image.new('RGB', (100, 100), (step * 40 % 255, 0, 0)).save(image)
        return {
            'pid': target['pid'], 'window_id': target['window_id'], 'session': 'test',
            'screenshot_width': 100, 'screenshot_height': 100,
            'window_bounds': dict(self.bounds), 'elements_complete': True, 'elements': [],
        }, image

    def window(self, target):
        value = super().window(target)
        value['bounds'] = dict(self.bounds)
        return value

    def call(self, name, payload):
        self.input_calls.append((name, payload))
        if self.failure_message:
            raise RuntimeError(self.failure_message)
        return {'effect': self.effect}


class FakeForeground:
    """No-native-input focus fixture for foreground fallback runner tests."""

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


def run_cua_with_predictions(tmp_path, monkeypatch, driver, predictions, model='qwen3-vl-2b', reviews=None,
                             foreground=None):
    from trace2task import cua_runner, local_gui_client
    from trace2task.cua_execution import CuaExecutionBackend

    prediction_calls = []

    def predict(task, **kwargs):
        prediction_calls.append(kwargs)
        if kwargs.get('execution_context', {}).get('purpose') == 'verify_completion':
            if reviews is not None:
                return reviews.pop(0)
            return {'status': 'reviewed', 'verification': {
                'verdict': 'unknown', 'evidence': 'test screenshot', 'missing': 'cannot confirm'}}
        return {'status': 'predicted', 'prediction': {'actions': predictions.pop(0)}}

    monkeypatch.setattr(cua_runner, 'CuaBackend', lambda *args: driver)
    # Runner defaults to the real Win32 helper.  Tests use a deterministic
    # foreground helper so no test can switch the actual desktop focus.
    monkeypatch.setattr(cua_runner, 'CuaExecutionBackend',
                        lambda transport, scope: CuaExecutionBackend(
                            transport, scope, foreground=foreground or FakeForeground()))
    monkeypatch.setattr(local_gui_client, 'predict_gui', predict)
    stop = SimpleNamespace(start=lambda: None, close=lambda: None,
                           raise_if_requested=lambda: None, sleep=lambda _: None)
    result = cua_runner.run_cua_local(
        instruction='test', output_root=tmp_path, emergency_stop=stop,
        status_callback=lambda _: None, model=model, cua_target=CALCULATOR)
    return result, prediction_calls


def test_false_completion_is_reviewed_then_replanned(tmp_path, monkeypatch):
    driver = RunnerDriver(tmp_path)
    result, calls = run_cua_with_predictions(tmp_path, monkeypatch, driver,
        [[{'done': True}], [CLICK], [{'done': True}]], reviews=[
            {'status': 'reviewed', 'verification': {'verdict': 'incomplete',
                'evidence': 'input is empty', 'missing': 'text not entered'}},
            {'status': 'reviewed', 'verification': {'verdict': 'complete',
                'evidence': 'requested result visible', 'missing': ''}}])
    assert result['stop_reason'] == 'visual_completion_reviewed'
    assert result['task_complete'] is True and result['verified'] is False
    assert len(driver.input_calls) == 1  # neither verification ever dispatches input
    assert calls[2]['execution_context']['execution_feedback']['status'] == 'completion_rejected'
    assert calls[2]['history'] == []
    assert driver.observed_steps == [0, 1, 2, 3, 4]
    assert len({r['output_directory'] for r in result['model_io']}) == 5
    assert result['model_io'][1]['purpose'] == 'verify_completion'
    assert result['model_io'][1]['verification']['verdict'] == 'incomplete'
    assert 'execution' not in result['model_io'][1]


@pytest.mark.parametrize('reply', [
    {'status': 'error', 'error': 'bad verification output'},
    {'status': 'predicted', 'prediction': {'actions': [{'skill': 'click', 'args': {'x': .5, 'y': .5}}]}},
    {'status': 'reviewed', 'verification': {'verdict': 'complete', 'evidence': '', 'missing': ''}},
    {'status': 'reviewed', 'verification': {'verdict': 'unknown', 'evidence': 'offscreen', 'missing': 'past submission'}}])
def test_ambiguous_or_invalid_review_stops_without_replay(tmp_path, monkeypatch, reply):
    driver = RunnerDriver(tmp_path)
    result, _ = run_cua_with_predictions(tmp_path, monkeypatch, driver,
        [[CLICK], [{'done': True}]], reviews=[reply])
    assert result['stop_reason'] == 'completion_unverifiable'
    assert result['task_complete'] is False
    assert len(driver.input_calls) == 1


def test_repeated_false_completion_has_bounded_recovery(tmp_path, monkeypatch):
    driver = RunnerDriver(tmp_path)
    review = {'status': 'reviewed', 'verification': {
        'verdict': 'incomplete', 'evidence': 'empty input', 'missing': 'requested text'}}
    result, _ = run_cua_with_predictions(tmp_path, monkeypatch, driver,
        [[{'done': True}], [{'done': True}]], reviews=[review, review])
    assert result['stop_reason'] == 'completion_rejected'
    assert not driver.input_calls


CLICK = {'skill': 'click', 'args': {'x': .5, 'y': .5, 'button': 'left'}}


def test_unverifiable_delivery_reobserves_then_replans_without_replaying_click(tmp_path, monkeypatch):
    driver = RunnerDriver(tmp_path, effect='unverifiable')
    result, predictions = run_cua_with_predictions(
        tmp_path, monkeypatch, driver, [[CLICK], [{'done': True}]],
    )

    assert driver.observed_steps == [0, 1, 2]  # Extra read-only completion review frame.
    assert len(predictions) == 3
    assert predictions[0]['image'] != predictions[1]['image']
    assert predictions[1]['history'][-1]['effect'] == 'unverifiable'
    assert [name for name, _ in driver.input_calls] == ['click']
    assert result['actions'] == 1
    assert result['task_complete'] is False
    assert result['history'] == [{
        'step_index': 0, 'action': {'actions': [CLICK]},
        'executed': True, 'effect': 'unverifiable', 'delivery_mode_requested': 'background',
    }]
    trace_events = [json.loads(line)['type'] for line in Path(result['trace_path']).read_text(encoding='utf-8').splitlines()]
    delivered = trace_events.index('delivered')
    pending = trace_events.index('effect_pending')
    second_capture = trace_events.index('capture', delivered + 1)
    reobservation = trace_events.index('effect_reobservation')
    second_model_input = trace_events.index('model_input', delivered + 1)
    assert delivered < pending < second_capture < reobservation < second_model_input


def test_explicit_background_refusal_uses_one_foreground_submission_then_reobserves(tmp_path, monkeypatch):
    class ForegroundFallbackDriver(RunnerDriver):
        def call(self, name, payload):
            self.input_calls.append((name, payload))
            if payload['delivery_mode'] == 'background':
                return {'code': 'background_unavailable', 'effect': 'unverifiable',
                        'verified': False}
            return {'effect': 'unverifiable', 'delivery': {'mode': 'foreground'}}

    driver = ForegroundFallbackDriver(tmp_path)
    result, predictions = run_cua_with_predictions(
        tmp_path, monkeypatch, driver, [[CLICK], [{'done': True}]],
    )

    # The second call is the one permitted fallback, not a second model action.
    assert [payload['delivery_mode'] for _, payload in driver.input_calls] == ['background', 'foreground']
    assert result['actions'] == 1 and result['foreground_fallbacks'] == 1
    assert result['task_complete'] is False
    assert result['history'] == [{
        'step_index': 0, 'action': {'actions': [CLICK]},
        'executed': True, 'effect': 'unverifiable', 'delivery_mode_requested': 'foreground',
    }]
    assert predictions[1]['history'][-1]['effect'] == 'unverifiable'
    trace_events = [json.loads(line)['type'] for line in Path(result['trace_path']).read_text(encoding='utf-8').splitlines()]
    assert trace_events.count('foreground_retry') == 1
    assert 'effect_pending' in trace_events


def test_foreground_focus_denial_is_known_no_input_not_transport_unknown(tmp_path, monkeypatch):
    class RefusingBackgroundDriver(RunnerDriver):
        def call(self, name, payload):
            self.input_calls.append((name, payload))
            return {'code': 'background_unavailable', 'effect': 'unverifiable'}

    driver = RefusingBackgroundDriver(tmp_path)
    foreground = FakeForeground()
    foreground.focus_succeeds = False
    result, predictions = run_cua_with_predictions(
        tmp_path, monkeypatch, driver, [[CLICK]], foreground=foreground,
    )

    assert result['stop_reason'] == 'foreground_unavailable'
    assert result['actions'] == 0 and result['history'] == []
    assert result['foreground_fallbacks'] == 1
    assert [payload['delivery_mode'] for _, payload in driver.input_calls] == ['background']
    assert foreground.focus_calls == [20]
    execution = result['model_io'][0]['execution']
    assert execution['status'] == 'foreground_unavailable'
    assert execution['executed'] is False
    assert len(predictions) == 1


@pytest.mark.parametrize('effect', ['confirmed', 'unverifiable'])
def test_repeated_action_is_allowed_with_fresh_comparison_images(tmp_path, monkeypatch, effect):
    driver = RunnerDriver(tmp_path, effect=effect)
    result, predictions = run_cua_with_predictions(
        tmp_path, monkeypatch, driver, [[CLICK], [CLICK], [CLICK], [CLICK], [{'done': True}]],
    )

    assert len(driver.input_calls) == 4
    assert result['actions'] == 4
    assert result['stop_reason'] == 'completion_unverifiable'
    assert 'previous_image' not in predictions[0]
    assert predictions[1]['previous_image'] == predictions[0]['image']
    assert predictions[2]['previous_image'] == predictions[1]['image']
    assert 'previous_image' not in predictions[-1]  # read-only completion review


def test_background_refusal_foreground_failure_is_unknown_and_stops(tmp_path, monkeypatch):
    from trace2task.execution_protocol import DriverRefusal
    driver = RunnerDriver(tmp_path)
    def refused(*args):
        raise DriverRefusal({'code': 'background_unavailable', 'effect': 'unverifiable'})
    driver.call = refused
    result, predictions = run_cua_with_predictions(tmp_path, monkeypatch, driver, [[CLICK]])
    assert result['stop_reason'] == 'error'
    assert result['actions'] == 0 and result['history'] == []
    # Count the attempted fallback even though its result remains unknown.
    assert result['foreground_fallbacks'] == 1
    assert len(predictions) == 1
    execution = result['model_io'][0]['execution']
    assert execution['status'] == 'interrupted_or_unknown'
    assert execution['executed'] is None
    assert execution['background_refusal']['code'] == 'background_unavailable'


@pytest.mark.parametrize(
    ('driver_kwargs', 'expected_error'),
    [
        ({'failure_message': 'simulated Cua timeout'}, 'simulated Cua timeout'),
        ({'effect': 'unknown'}, '动作效果未知'),
        ({'effect': 'error'}, '动作效果未知'),
    ],
)
def test_driver_error_after_first_action_attempt_does_not_trigger_second_plan(
    tmp_path, monkeypatch, driver_kwargs, expected_error,
):
    driver = RunnerDriver(tmp_path, **driver_kwargs)
    result, predictions = run_cua_with_predictions(tmp_path, monkeypatch, driver, [[CLICK]])

    assert driver.observed_steps == [0]
    assert len(predictions) == 1
    assert [name for name, _ in driver.input_calls] == ['click']
    assert result['actions'] == 0
    assert result['stop_reason'] == 'error'
    assert expected_error in result['error']


def test_capture_failure_after_unverifiable_delivery_sends_no_additional_action(tmp_path, monkeypatch):
    driver = RunnerDriver(tmp_path, effect='unverifiable', fail_capture_at=1)
    result, predictions = run_cua_with_predictions(tmp_path, monkeypatch, driver, [[CLICK]])

    assert driver.observed_steps == [0, 1]
    assert len(predictions) == 1
    assert [name for name, _ in driver.input_calls] == ['click']
    assert result['actions'] == 1
    assert result['stop_reason'] == 'error'


@pytest.mark.parametrize('model', ['gui-owl-2b', 'mai-ui-2b'])
def test_rejected_text_is_feedback_not_executed_history(tmp_path, monkeypatch, model):
    driver = RunnerDriver(tmp_path)
    result, predictions = run_cua_with_predictions(tmp_path, monkeypatch, driver,
        [[{'skill': 'type_text', 'args': {'text': '123*456='}}], [CLICK], [{'done': True}]], model)
    assert result['actions'] == 1
    assert [tool for tool, _ in driver.input_calls] == ['click']
    assert predictions[1]['history'] == []
    context = predictions[1]['execution_context']
    assert context['execution_feedback']['executed'] is False
    assert context['execution_feedback']['action']['skill'] == 'type_text'
    assert 'capabilities' not in context
    assert predictions[2]['execution_context']['execution_feedback'] is None
    assert driver.observed_steps == [0, 1, 2, 3]
    assert result['stop_reason'] == 'completion_unverifiable'
    assert result['model_io'][0]['execution']['status'] == 'rejected'
    assert result['model_io'][1]['execution']['executed'] is True
    assert Path(result['model_io'][1]['execution_path']).is_file()


@pytest.mark.parametrize('model', ['gui-owl-2b', 'mai-ui-2b'])
def test_incomplete_tree_bare_text_is_dispatched_as_focused_control_route(tmp_path, monkeypatch, model):
    class IncompleteTreeDriver(RunnerDriver):
        def observe(self, target, step):
            state, image = super().observe(target, step)
            state.update(elements_complete=False, elements=[])
            return state, image

    driver = IncompleteTreeDriver(tmp_path, effect='unverifiable')
    result, predictions = run_cua_with_predictions(
        tmp_path, monkeypatch, driver,
        [[{'skill': 'type_text', 'args': {'text': '123*456='}}], [{'done': True}]], model,
    )
    assert [tool for tool, _ in driver.input_calls] == ['type_text']
    payload = driver.input_calls[0][1]
    assert payload['text'] == '123*456=' and payload['delivery_mode'] == 'background'
    assert not {'x', 'y', 'element_token'} & payload.keys()
    assert result['actions'] == 1 and result['history'][0]['effect'] == 'unverifiable'
    assert predictions[1]['history'][0]['executed'] is True
    assert 'capabilities' not in predictions[1]['execution_context']
