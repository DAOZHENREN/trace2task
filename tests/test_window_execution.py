from types import SimpleNamespace

import pytest

from trace2task.actions import ActionCall
from trace2task.execution_protocol import ActionUnavailable, ObservationStale
from trace2task.window_execution import WindowExecutionAdapter
from trace2task.windows_control import MotorResult


class Motor:
    def __init__(self, *, background=False):
        self.background = background
        self.current = SimpleNamespace(handle=7, process_id=41, client_width=100,
                                       client_height=80)
        self.session = SimpleNamespace(require_available=lambda: self.current)
        self.calls = []
        self.receipt_handle = 7
        self.receipt_mode = 'background' if background else 'foreground'

    def execute(self, action):
        self.calls.append(action.to_payload())
        return MotorResult(action.skill, self.receipt_handle, 12,
                           input_mode=self.receipt_mode)


def adapter(motor):
    stop = SimpleNamespace(raise_if_requested=lambda: None)
    bound = WindowExecutionAdapter(motor, stop)
    bound.bind(motor.current)
    return bound


def test_background_window_motor_preserves_mode_and_unverified_effect():
    motor = Motor(background=True)
    result = adapter(motor).execute(ActionCall('click', {'x': .3, 'y': .5}))
    assert result.input_mode == 'background'
    assert result.effect == 'unverifiable'
    assert result.receipt['window_handle'] == 7
    assert len(motor.calls) == 1


def test_window_change_before_dispatch_never_sends_input():
    motor = Motor()
    bound = adapter(motor)
    motor.current = SimpleNamespace(handle=7, process_id=41, client_width=120,
                                    client_height=80)
    with pytest.raises(ObservationStale):
        bound.execute(ActionCall('click', {'x': .3, 'y': .5}))
    assert motor.calls == []


def test_wrong_target_receipt_does_not_retry():
    motor = Motor()
    motor.receipt_handle = 99
    with pytest.raises(RuntimeError, match='receipt target differs'):
        adapter(motor).execute(ActionCall('click', {'x': .3, 'y': .5}))
    assert len(motor.calls) == 1


def test_wrong_delivery_mode_receipt_does_not_retry():
    motor = Motor(background=True)
    motor.receipt_mode = 'foreground'
    with pytest.raises(RuntimeError, match='delivery mode differs'):
        adapter(motor).execute(ActionCall('click', {'x': .3, 'y': .5}))
    assert len(motor.calls) == 1


def test_wait_receipt_is_not_an_application_effect():
    motor = Motor(background=True)
    result = adapter(motor).execute(ActionCall('wait', {'duration_ms': 100}))
    assert result.effect == 'confirmed'
    assert result.receipt['route'] == 'local_wait'
    assert result.receipt['delivery']['route'] == 'local_wait'
    assert len(motor.calls) == 1


def test_background_window_cannot_focus_target():
    motor = Motor(background=True)
    with pytest.raises(ActionUnavailable):
        adapter(motor).execute(ActionCall('focus_window', {}))
    assert motor.calls == []
