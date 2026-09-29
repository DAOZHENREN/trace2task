"""Window-scoped adapter for the existing foreground/background Win32 motor."""

from dataclasses import dataclass

from trace2task.actions import WINDOWS_MOTOR_SKILLS, ActionCall
from trace2task.execution_core import dispatch_backend_once
from trace2task.execution_protocol import (
    ActionUnavailable,
    ForegroundUnavailable,
    ObservationStale,
    UnifiedAction,
)


@dataclass(frozen=True)
class WindowDelivery:
    skill: str
    window_handle: int
    elapsed_ms: float
    screen_position: tuple[int, int] | None
    input_mode: str
    effect: str
    receipt: dict


class WindowExecutionBackend:
    """Keep window binding and motor capabilities independent of model format."""

    def __init__(self, motor):
        self.motor = motor
        self.session = motor.session
        self.default_delivery_mode = 'background' if motor.background else 'foreground'

    @staticmethod
    def _identity(window):
        if window is None:
            raise ValueError('No bound window observation; no input sent')
        return (window.handle, window.process_id, window.client_width, window.client_height)

    def authorize(self, action, target):
        self._identity(target)

    def prepare(self, action, state, execution_context=None):
        if action.skill not in self.capabilities(state)['available_skills']:
            raise ActionUnavailable(f'Window motor cannot execute {action.skill!r} in this mode')
        return ActionCall.from_payload(action.to_payload())

    def check_current(self, target, state):
        if self._identity(target) != self._identity(state):
            raise ObservationStale('Window observation changed before input; no old coordinates sent')
        current = self.session.require_available()
        if self._identity(current) != self._identity(target):
            raise ObservationStale('Selected window identity or client size changed; no input sent')

    def dispatch(self, action, prepared, target, stop):
        if not isinstance(prepared, ActionCall) or prepared.skill != action.skill:
            raise RuntimeError('Prepared window action changed; no input sent')
        stop.raise_if_requested()
        result = self.motor.execute(prepared)
        if result.window_handle != target.handle:
            raise RuntimeError('Window motor receipt target differs from observed window; effect unknown')
        if result.input_mode != self.default_delivery_mode:
            raise RuntimeError('Window motor receipt delivery mode differs from requested mode; effect unknown')
        route = 'local_wait' if action.skill == 'wait' else 'win32_window_input'
        receipt = {
            'effect': 'confirmed' if action.skill == 'wait' else 'unverifiable',
            'route': route,
            'delivery': {'mode': result.input_mode, 'route': route},
            'window_handle': result.window_handle,
            'elapsed_ms': result.elapsed_ms,
            'screen_position': result.screen_position,
        }
        return receipt, target

    def retry_foreground(self, action, prepared, target, stop):
        raise ForegroundUnavailable('Window delivery cannot be retried after an uncertain input')

    def capabilities(self, state=None):
        skills = set(WINDOWS_MOTOR_SKILLS)
        if self.motor.background:
            skills.discard('focus_window')
        return {'available_skills': sorted(skills),
                'delivery_mode': self.default_delivery_mode,
                'coordinate_space': 'selected_window_normalized_0_1',
                'note': 'Supported means the motor has an input route, not that the application '
                        'accepted it. Background window messages may not reach child controls; '
                        'observe the target again before inferring an effect.'}


class WindowExecutionAdapter:
    """Offer the old batch runtime an ``execute`` method backed by strict receipts."""

    def __init__(self, motor, stop):
        self.backend = WindowExecutionBackend(motor)
        self.stop = stop
        self.bound_window = None

    def bind(self, window):
        self.backend._identity(window)
        self.bound_window = window

    def execute(self, action):
        self.stop.raise_if_requested()
        target = self.bound_window
        unified = UnifiedAction.from_payload(action.to_payload())
        self.backend.authorize(unified, target)
        prepared = self.backend.prepare(unified, target)
        self.backend.check_current(target, target)
        self.stop.raise_if_requested()
        receipt, next_target = dispatch_backend_once(
            self.backend, unified, prepared, target, self.stop,
        )
        if next_target != target:
            raise RuntimeError('Window backend changed target after dispatch; effect unknown')
        return WindowDelivery(
            skill=unified.skill,
            window_handle=target.handle,
            elapsed_ms=receipt['elapsed_ms'],
            screen_position=receipt['screen_position'],
            input_mode=receipt['delivery']['mode'],
            effect=receipt['effect'],
            receipt=receipt,
        )
