"""Win32 adapter for the model-independent execution boundary.

This module deliberately knows nothing about model response formats.  It turns
an already-normalized :class:`UnifiedAction` into an existing ``ActionCall`` and
uses ``WindowsMotorExecutor`` only after the observation that produced the plan
has been revalidated.  A successful Win32 API call is delivery evidence, not
application-effect evidence.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from trace2task.actions import WINDOWS_MOTOR_SKILLS, ActionCall
from trace2task.execution_protocol import (
    ActionUnavailable,
    ForegroundUnavailable,
    ObservationStale,
    UnifiedAction,
)

# ``focus_window`` changes the binding which the model was shown.  Desktop
# tasks bind the foreground window at observation time, so the unified plan may
# not silently retarget it.  All remaining ActionCall skills are supported by
# WindowsMotorExecutor in foreground mode.
SUPPORTED_SKILLS = (frozenset(WINDOWS_MOTOR_SKILLS) - {"focus_window"}) | {"move_cursor", "scroll"}


class Win32ExecutionBackend:
    """Foreground-only ``ExecutionBackend`` for ``WindowsMotorExecutor``.

    ``target`` and ``state`` use the same trusted observation identity::

        {"foreground_handle": 123, "screen_size": (1920, 1080)}

    The model never supplies either value.  ``screen_size`` must describe the
    primary desktop in physical pixels so normalized coordinates cannot be
    replayed after a resolution change.
    """

    default_delivery_mode = "foreground"

    def __init__(
        self,
        motor: Any,
        *,
        foreground_handle: Callable[[], int] | None = None,
        screen_size: Callable[[], tuple[int, int]] | None = None,
    ) -> None:
        self.motor = motor
        session = getattr(motor, "session", None)
        if foreground_handle is None:
            backend = getattr(session, "backend", None)
            foreground_handle = getattr(backend, "foreground_handle", None)
        if screen_size is None:
            screen_size = getattr(session, "size", None)
        if not callable(foreground_handle) or not callable(screen_size):
            raise TypeError(
                "Win32ExecutionBackend needs foreground_handle() and screen_size(); "
                "pass them explicitly for a non-DesktopSession motor"
            )
        self._foreground_handle = foreground_handle
        self._screen_size = screen_size

    @staticmethod
    def _identity(value: Any, *, label: str) -> tuple[int, tuple[int, int]]:
        if not isinstance(value, dict) or set(value) != {"foreground_handle", "screen_size"}:
            raise ValueError(f"{label} must contain exactly foreground_handle and screen_size")
        handle, size = value["foreground_handle"], value["screen_size"]
        if type(handle) is not int or handle <= 0:
            raise ValueError(f"{label}.foreground_handle must be a positive integer")
        if (not isinstance(size, (tuple, list)) or len(size) != 2
                or any(type(dimension) is not int or dimension <= 0 for dimension in size)):
            raise ValueError(f"{label}.screen_size must be two positive integers")
        return handle, (size[0], size[1])

    def authorize(self, action, target):
        # Validate the trusted target even for a rejected model action: do not
        # let a malformed orchestration state reach native input later.
        self._identity(target, label="target")

    def prepare(self, action, state, execution_context=None):
        # Rebuild through the long-standing ActionCall validator.  This keeps
        # the motor's constraints (keys, text length, normalized coordinates,
        # duration limits) authoritative even when a new model adapter exists.
        if action.skill not in SUPPORTED_SKILLS:
            raise ActionUnavailable(
                f"Win32 foreground backend does not support {action.skill!r} in a bound desktop plan"
            )
        if action.skill == "type_text" and {"x", "y"} <= set(action.args):
            raise ActionUnavailable("Win32 text input cannot target a screenshot coordinate; no input sent")
        if action.skill in {"move_cursor", "scroll"}:
            if action.skill == "scroll" and action.args["by"] != "line":
                raise ActionUnavailable("Win32 wheel does not provide page-unit scrolling")
            if action.skill == "scroll" and not {"x", "y"} <= set(action.args):
                cursor = (execution_context or {}).get("cursor_position")
                if (not isinstance(cursor, (list, tuple)) or len(cursor) != 2
                        or any(type(value) not in (int, float) or not math.isfinite(value)
                               or not 0 <= value <= 1 for value in cursor)):
                    raise ActionUnavailable(
                        "Win32 wheel scroll needs a delivered cursor position; move the cursor to the target first"
                    )
                return UnifiedAction.from_payload({"skill": "scroll", "args": {
                    **action.args, "x": cursor[0], "y": cursor[1],
                }})
            return action
        return ActionCall.from_payload(action.to_payload())

    def request_view(self, action, prepared, target, *, delivery_mode=None):
        """The action received by the Win32 motor or pointer route."""
        return {'backend': 'win32',
                'operation': ('pointer_input' if action.skill in {'move_cursor', 'scroll'}
                              else 'motor_execute'),
                'action': prepared.to_payload(), 'delivery_mode': 'foreground'}

    def check_current(self, target, state):
        expected_handle, expected_size = self._identity(target, label="target")
        observed_handle, observed_size = self._identity(state, label="state")
        if (observed_handle, observed_size) != (expected_handle, expected_size):
            raise ObservationStale("Observed foreground window or screen size changed; no stale coordinates were sent")
        live_handle = self._foreground_handle()
        live_size = self._screen_size()
        if type(live_handle) is not int or tuple(live_size) != expected_size:
            raise ObservationStale("Foreground window or primary screen size changed; no stale coordinates were sent")
        if live_handle != expected_handle:
            raise ObservationStale("Foreground window changed; no stale coordinates were sent")

    def dispatch(self, action, prepared, target, stop):
        if action.skill in {"move_cursor", "scroll"}:
            if (not isinstance(prepared, UnifiedAction) or prepared.skill != action.skill
                    or any(prepared.args.get(key) != value for key, value in action.args.items())
                    or (action.skill == "move_cursor" and prepared != action)
                    or (action.skill == "scroll" and set(prepared.args) - set(action.args) not in (
                        set(), {"x", "y"}))):
                raise RuntimeError("Prepared Win32 pointer action changed; no input sent")
            stop.raise_if_requested()
            window = self.motor.session.require_foreground()
            if window.handle != target["foreground_handle"]:
                raise ObservationStale("Foreground target changed before pointer input; no input sent")
            position = self.motor.session.normalized_to_screen(
                window, prepared.args["x"], prepared.args["y"]
            )
            self.motor.session.backend.set_cursor_position(*position)
            if action.skill == "scroll":
                self.motor.session.backend.send_mouse_wheel(
                    prepared.args["direction"], prepared.args["amount"], prepared.args["by"]
                )
            return ({
                "effect": "unverifiable",
                "route": "win32_foreground_pointer",
                "delivery": {"mode": "foreground", "route": "win32_foreground_pointer"},
                "screen_position": position,
            }, dict(target))
        if not isinstance(prepared, ActionCall):
            raise TypeError("Win32 action was not prepared as an ActionCall")
        if prepared.skill != action.skill:
            raise RuntimeError("Prepared Win32 action identity changed; no input sent")
        stop.raise_if_requested()
        # Exactly one call.  If it raises, ExecutionCore records the result as
        # interrupted/unknown and must not retry it.
        result = self.motor.execute(prepared)
        input_mode = getattr(result, "input_mode", None)
        if input_mode != "foreground":
            raise RuntimeError("Win32 unified backend requires foreground motor delivery; no result accepted")
        if getattr(result, "window_handle", None) != target["foreground_handle"]:
            raise RuntimeError("Win32 motor receipt target differs from the observed foreground; effect unknown")
        if prepared.skill == "wait":
            receipt = {
                "effect": "confirmed",
                "route": "local_wait",
                "delivery": {"mode": "local", "route": "local_wait"},
                "elapsed_ms": getattr(result, "elapsed_ms", None),
            }
        else:
            receipt = {
                # OS submission is not proof that the target app accepted or
                # displayed the result.  The next observation decides that.
                "effect": "unverifiable",
                "route": "win32_foreground_input",
                "delivery": {"mode": "foreground", "route": "win32_foreground_input"},
                "elapsed_ms": getattr(result, "elapsed_ms", None),
                "window_handle": getattr(result, "window_handle", None),
                "screen_position": getattr(result, "screen_position", None),
            }
        return receipt, dict(target)

    def retry_foreground(self, action, prepared, target, stop):
        # This adapter never starts with a background attempt, hence accepting
        # this call would turn an uncertain native input into an unsafe replay.
        raise ForegroundUnavailable("Win32 foreground delivery is not retryable; re-observe before another action")

    def capabilities(self, state=None):
        return {
            "available_skills": sorted(SUPPORTED_SKILLS),
            "coordinate_space": "primary_desktop_normalized_0_1",
            "delivery_mode": "foreground",
            "note": "Win32 delivery means foreground input was submitted; observe again before inferring an application effect.",
        }
