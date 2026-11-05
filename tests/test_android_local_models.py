"""Real runner/decoder/ADB adapter with synthetic transport and model responses."""
import base64
import io
import json
from pathlib import Path

import pytest
import test_android_execution
from PIL import Image
from test_chat_agent_bridge import Stop

from trace2task import local_gui_client, trained_model_runner
from trace2task.execution_budget import TimeBudgetExceeded
from trace2task.local_gui_protocol import decode

phone = test_android_execution.phone

CLICK = {'skill': 'click', 'args': {'x': .5, 'y': .25, 'button': 'left'}}


def run_phone(phone, tmp_path, monkeypatch, predict, *, model='qwen3-vl-2b', **options):
    driver, _ = phone
    # The supplied transport was started by the fixture; production uses AndroidDriver.start.
    monkeypatch.setattr(driver, 'start', lambda: None)
    monkeypatch.setattr(local_gui_client, 'predict_gui', predict)
    def desktop_forbidden(*args, **kwargs):
        pytest.fail('Android local models must not initialize a Windows input/capture driver')
    monkeypatch.setattr(trained_model_runner, 'Win32Backend', desktop_forbidden)
    monkeypatch.setattr(trained_model_runner, 'GdiWindowCapture', desktop_forbidden)
    return trained_model_runner.run_trained_desktop(
        instruction='Tap the visible control', model=model, executor_backend='android',
        android_config={'serial': 'phone', 'package': 'com.example.game'}, android_driver=driver,
        output_root=tmp_path / 'runs', emergency_stop=Stop(), status_callback=lambda _: None,
        approve=None, continuous=True, predictor=predict, **options)


@pytest.mark.parametrize('model,raw', [
    ('qwen3-vl-2b', json.dumps({'actions': [CLICK]})),
    ('qwen3-vl-8b-instruct', json.dumps({'actions': [CLICK]})),
    ('mai-ui-2b', '<tool_call>{"name":"mobile_use","arguments":{"action":"click","coordinate":[499.5,249.75]}}</tool_call>'),
    ('gui-owl-2b', '<tool_call>{"name":"computer_use","arguments":{"action":"left_click","coordinate":[500,250]}}</tool_call>'),
    ('gui-decisions-2b', '{"action":"click","x":0.5,"y":0.25}'),
    ('D-5970', None),
])
@pytest.mark.parametrize('size,rotation', [((1280, 720), 1), ((720, 1280), 0)])
def test_phone_pixels_and_local_model_coordinates_match(
        phone, tmp_path, monkeypatch, model, raw, size, rotation):
    driver, data = phone
    data.update(size=size, rotation=rotation)
    requests = []
    def predict(task, **kwargs):
        requests.append(kwargs)
        assert Image.open(io.BytesIO(base64.b64decode(kwargs['image']))).size == size
        return {'status': 'predicted', 'prediction': decode(model, raw) if raw else {'actions': [CLICK]}}
    result = run_phone(phone, tmp_path, monkeypatch, predict, model=model, max_actions=1, max_seconds=60)
    commands = [c[5:] for c in data['commands'] if c[5:7] == ['shell', 'input']]
    assert commands == [['shell', 'input', 'tap', str(size[0] // 2), str(size[1] // 4)]]
    assert result['actions'] == 1 and result['executor_backend'] == 'android'
    assert result['history'][0]['delivery_mode_requested'] == 'remote_android'
    assert not result['verified'] and not result['task_complete']
    assert Path(result['final_screenshot']).is_file()
    assert driver.broken  # All exits release the bound device-side resources.
    if model != 'D-5970':
        assert requests[0]['execution_context']['target']['kind'] == 'android'
        assert requests[0]['execution_context']['capabilities']['allowed_keys'] == ['escape']


def test_native_swipe_reobserves_and_rejects_desktop_input(phone, tmp_path, monkeypatch):
    _, data = phone
    plans = [decode('gui-decisions-2b', '{"action":"swipe","x":0.2,"y":0.8,"x2":0.8,"y2":0.2}'),
             {'actions': [{'skill': 'type_text', 'args': {'text': 'must not type'}}]}]
    def predict(task, **kwargs):
        return {'status': 'predicted', 'prediction': plans.pop(0)}
    result = run_phone(phone, tmp_path, monkeypatch, predict, max_actions=2)
    inputs = [c[5:] for c in data['commands'] if c[5:7] == ['shell', 'input']]
    assert inputs == [['shell', 'input', 'swipe', '256', '576', '1024', '144', '500']]
    assert result['actions'] == 1
    assert result['stop_reason'] == 'error'
    assert 'Unsupported Android action' in result['error']
    assert len(data['processes']) == 2  # first observation and reobservation before rejection


def test_rotation_change_during_local_inference_discards_old_point(phone, tmp_path, monkeypatch):
    _, data = phone
    def predict(task, **kwargs):
        data.update(rotation=0, size=(720, 1280))
        return {'status': 'predicted', 'prediction': {'actions': [CLICK]}}
    result = run_phone(phone, tmp_path, monkeypatch, predict, max_actions=1)
    assert result['actions'] == 0
    assert result['model_io'][0]['execution']['status'] == 'reobserve'
    assert not any(c[5:7] == ['shell', 'input'] for c in data['commands'])


def test_time_budget_stops_late_local_prediction_and_releases_driver(phone, tmp_path, monkeypatch):
    from trace2task import execution_budget
    _, data = phone
    monkeypatch.setattr(execution_budget.time, 'monotonic', lambda: data['clock'])
    def predict(task, **kwargs):
        data['clock'] = 61
        return {'status': 'predicted', 'prediction': {'actions': [CLICK]}}
    # Return-side stop check discards the just-finished answer before any dispatch.
    result = run_phone(phone, tmp_path, monkeypatch, predict, max_actions=1, max_seconds=60)
    assert result['stop_reason'] == 'time_limit'
    assert result['actions'] == 0 and phone[0].broken
    assert not any(c[5:7] == ['shell', 'input'] for c in data['commands'])


def test_model_stop_exception_is_persisted_without_phone_input(phone, tmp_path, monkeypatch):
    def predict(task, **kwargs):
        raise TimeBudgetExceeded('test stop')
    result = run_phone(phone, tmp_path, monkeypatch, predict, max_actions=1)
    assert result['stop_reason'] == 'time_limit'
    assert result['actions'] == 0 and phone[0].broken
