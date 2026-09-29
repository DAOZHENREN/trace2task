"""Bridge legacy multi-action desktop planning to the unified Win32 backend.

The desktop planner owns batch boundaries.  This adapter handles one delivered
action at a time without asking the model to change its JSON response format or
bypassing the backend's target, capability, and receipt checks.
"""

from dataclasses import dataclass
from time import perf_counter

from trace2task.execution_core import dispatch_backend_once
from trace2task.execution_protocol import UnifiedAction
from trace2task.win32_execution import Win32ExecutionBackend


@dataclass(frozen=True)
class DesktopDelivery:
    skill: str
    window_handle: int
    elapsed_ms: float
    screen_position: tuple[int, int] | None
    input_mode: str
    effect: str
    receipt: dict


class DesktopExecutionAdapter:
    """Retain desktop batch policy while sharing the strict backend boundary."""

    def __init__(self, motor, stop, *, foreground_handle, screen_size):
        self.motor = motor
        self.stop = stop
        self.backend = Win32ExecutionBackend(
            motor, foreground_handle=foreground_handle, screen_size=screen_size,
        )

    def execute(self, action):
        self.stop.raise_if_requested()
        window = self.motor.session.window
        if window is None:
            raise RuntimeError('Desktop action has no bound observation; no input sent')
        target = {'foreground_handle': window.handle,
                  'screen_size': (window.client_width, window.client_height)}
        unified = UnifiedAction.from_payload(action.to_payload())
        self.backend.authorize(unified, target)
        prepared = self.backend.prepare(unified, target)
        self.backend.check_current(target, target)
        self.stop.raise_if_requested()
        started = perf_counter()
        # Once dispatched, an exception is an uncertain outcome: never retry it.
        receipt, next_target = dispatch_backend_once(
            self.backend, unified, prepared, target, self.stop,
        )
        if next_target != target:
            raise RuntimeError('Desktop backend changed target after dispatch; effect unknown')
        delivery = receipt.get('delivery') or {}
        elapsed_ms = receipt.get('elapsed_ms')
        if not isinstance(elapsed_ms, (int, float)):
            elapsed_ms = (perf_counter() - started) * 1000
        return DesktopDelivery(
            skill=unified.skill,
            window_handle=target['foreground_handle'],
            elapsed_ms=elapsed_ms,
            screen_position=receipt.get('screen_position'),
            input_mode=delivery.get('mode', self.backend.default_delivery_mode),
            effect=receipt['effect'],
            receipt=receipt,
        )
