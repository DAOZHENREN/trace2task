from __future__ import annotations

import ctypes
import os
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from trace2task.actions import SPECIAL_KEYS, normalize_key
from trace2task.recording import TraceWriter, make_run_dir
from trace2task.windows_capture import WindowFrameCapture
from trace2task.windows_control import (
    VK_CODES,
    WindowInfo,
    WindowLookupError,
    WindowSafetyError,
    WindowsBackend,
    WindowSelector,
    WindowSession,
    configure_physical_dpi_api,
    physical_dpi_context,
)

WM_HOTKEY = 0x0312
PM_REMOVE = 0x0001
MOD_NOREPEAT = 0x4000
SUCCESS_HOTKEY_ID = 0x545201
CANCEL_HOTKEY_ID = 0x545202
VK_F8 = 0x77
VK_F9 = 0x78
MOUSE_VIRTUAL_KEYS = {"left": 0x01, "right": 0x02, "middle": 0x04}
CONTROL_KEY_CODES = {
    **{chr(code).casefold(): code for code in range(ord("A"), ord("Z") + 1)},
    **{str(number): ord(str(number)) for number in range(10)},
    **{key: value for key, value in VK_CODES.items() if key in SPECIAL_KEYS},
}


@dataclass(frozen=True)
class InputSnapshot:
    keys_down: frozenset[str]
    buttons_down: frozenset[str]
    cursor_position: tuple[int, int]
    success_requested: bool = False
    cancel_requested: bool = False


class InputMonitor(Protocol):
    def start(self) -> None: ...

    def poll(self) -> InputSnapshot: ...

    def close(self) -> None: ...


class Win32InputMonitor:
    """Poll raw key/button state while reserving two configurable controls."""

    def __init__(self, *, success_key: str = "f8", cancel_key: str = "f9") -> None:
        if os.name != "nt":
            raise RuntimeError("The Windows input monitor is available only on Windows")
        self.success_key = normalize_key(success_key)
        self.cancel_key = normalize_key(cancel_key)
        if self.success_key == self.cancel_key:
            raise ValueError("success_key and cancel_key must be different")
        self._success_virtual_key = CONTROL_KEY_CODES[self.success_key]
        self._cancel_virtual_key = CONTROL_KEY_CODES[self.cancel_key]
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.RegisterHotKey.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self.user32.RegisterHotKey.restype = wintypes.BOOL
        self.user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.UnregisterHotKey.restype = wintypes.BOOL
        self.user32.PeekMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self.user32.PeekMessageW.restype = wintypes.BOOL
        self.user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.user32.GetAsyncKeyState.restype = wintypes.SHORT
        self.user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        self.user32.GetCursorPos.restype = wintypes.BOOL
        configure_physical_dpi_api(self.user32)
        self._started = False
        self._success_key_down = False
        self._cancel_key_down = False

    def start(self) -> None:
        if self._started:
            return
        if not self.user32.RegisterHotKey(
            None,
            SUCCESS_HOTKEY_ID,
            MOD_NOREPEAT,
            self._success_virtual_key,
        ):
            raise RuntimeError(
                f"Could not reserve {self.success_key.upper()} as the recording-success hotkey"
            )
        if not self.user32.RegisterHotKey(
            None,
            CANCEL_HOTKEY_ID,
            MOD_NOREPEAT,
            self._cancel_virtual_key,
        ):
            self.user32.UnregisterHotKey(None, SUCCESS_HOTKEY_ID)
            raise RuntimeError(
                f"Could not reserve {self.cancel_key.upper()} as the recording-cancel hotkey"
            )
        self._success_key_down = bool(
            self.user32.GetAsyncKeyState(self._success_virtual_key) & 0x8000
        )
        self._cancel_key_down = bool(
            self.user32.GetAsyncKeyState(self._cancel_virtual_key) & 0x8000
        )
        self._started = True

    def poll(self) -> InputSnapshot:
        if not self._started:
            raise RuntimeError("Input monitor must be started before polling")
        success_requested = False
        cancel_requested = False
        message = wintypes.MSG()
        while self.user32.PeekMessageW(
            ctypes.byref(message),
            None,
            WM_HOTKEY,
            WM_HOTKEY,
            PM_REMOVE,
        ):
            if message.wParam == SUCCESS_HOTKEY_ID:
                success_requested = True
            elif message.wParam == CANCEL_HOTKEY_ID:
                cancel_requested = True

        success_key_down = bool(
            self.user32.GetAsyncKeyState(self._success_virtual_key) & 0x8000
        )
        cancel_key_down = bool(
            self.user32.GetAsyncKeyState(self._cancel_virtual_key) & 0x8000
        )
        success_requested = success_requested or (
            success_key_down and not self._success_key_down
        )
        cancel_requested = cancel_requested or (
            cancel_key_down and not self._cancel_key_down
        )
        self._success_key_down = success_key_down
        self._cancel_key_down = cancel_key_down

        keys_down = frozenset(
            key
            for key, virtual_key in CONTROL_KEY_CODES.items()
            if key not in {self.success_key, self.cancel_key}
            if self.user32.GetAsyncKeyState(virtual_key) & 0x8000
        )
        buttons_down = frozenset(
            button
            for button, virtual_key in MOUSE_VIRTUAL_KEYS.items()
            if self.user32.GetAsyncKeyState(virtual_key) & 0x8000
        )
        cursor = wintypes.POINT()
        with physical_dpi_context(self.user32):
            if not self.user32.GetCursorPos(ctypes.byref(cursor)):
                raise ctypes.WinError(ctypes.get_last_error())
        return InputSnapshot(
            keys_down=keys_down,
            buttons_down=buttons_down,
            cursor_position=(int(cursor.x), int(cursor.y)),
            success_requested=success_requested,
            cancel_requested=cancel_requested,
        )

    def close(self) -> None:
        if not self._started:
            return
        self.user32.UnregisterHotKey(None, SUCCESS_HOTKEY_ID)
        self.user32.UnregisterHotKey(None, CANCEL_HOTKEY_ID)
        self._success_key_down = False
        self._cancel_key_down = False
        self._started = False


@dataclass(frozen=True)
class WindowRecordResult:
    mode: str
    task_id: str
    success: bool
    input_events: int
    focus_losses: int
    stop_reason: str
    trace_path: str


class WindowRecorder:
    """Record raw target-window input transitions with a screenshot per event."""

    def __init__(
        self,
        session: WindowSession,
        capture: WindowFrameCapture,
        monitor: InputMonitor,
        *,
        poll_hz: int = 120,
        max_seconds: float = 300,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        status_callback: Callable[[str], None] = print,
    ) -> None:
        if poll_hz <= 0:
            raise ValueError("poll_hz must be positive")
        if max_seconds <= 0:
            raise ValueError("max_seconds must be positive")
        self.session = session
        self.capture = capture
        self.monitor = monitor
        self.poll_interval = 1 / poll_hz
        self.max_seconds = max_seconds
        self.clock = clock
        self.sleeper = sleeper
        self.status_callback = status_callback

    def record(self, *, task_id: str, output_root: Path) -> WindowRecordResult:
        if not task_id.strip():
            raise ValueError("task_id must not be empty")
        input_events = 0
        focus_losses = 0
        stop_reason = "cancelled"
        success = False
        success_key = getattr(self.monitor, "success_key", "f8")
        cancel_key = getattr(self.monitor, "cancel_key", "f9")
        success_label = success_key.upper()
        cancel_label = cancel_key.upper()
        previous: InputSnapshot | None = None
        was_foreground = True
        started = self.clock()
        writer: TraceWriter | None = None
        self.monitor.start()
        try:
            initial_window, previous = self._wait_for_active_target(started)
            run_dir = make_run_dir(output_root, "windows-human")
            writer = TraceWriter(
                run_dir,
                task_id=task_id.strip(),
                seed=0,
                source="windows_human",
            )
            writer.record(
                "start",
                self.capture.capture(initial_window),
                details={
                    "window": asdict(initial_window),
                    "capture": "target_client_area",
                    "coordinate_space": "physical_pixels",
                },
            )
            self.status_callback(
                f"Recording '{initial_window.title}'. Press {success_label} to mark success "
                f"or {cancel_label} to cancel."
            )
            previous_geometry = self._geometry(initial_window)
            while True:
                if self.clock() - started >= self.max_seconds:
                    stop_reason = "timeout"
                    break
                snapshot = self.monitor.poll()
                if snapshot.cancel_requested:
                    stop_reason = "cancelled"
                    break

                try:
                    window = self.session.resolve()
                except WindowLookupError:
                    stop_reason = "target_closed"
                    break
                is_foreground = self.session.backend.foreground_handle() == window.handle
                if snapshot.success_requested:
                    if not is_foreground or window.is_minimized or not window.is_visible:
                        self.status_callback(
                            f"{success_label} ignored because the target window is not active."
                        )
                    else:
                        writer.record(
                            "success_marker",
                            self.capture.capture(window),
                            details={"window": asdict(window), "control_hotkey": success_key},
                        )
                        success = True
                        stop_reason = "success_marked"
                        break

                if not is_foreground or window.is_minimized or not window.is_visible:
                    if was_foreground:
                        focus_losses += 1
                        self.status_callback("Recording paused: target window lost focus.")
                    was_foreground = False
                    previous = snapshot
                    self.sleeper(self.poll_interval)
                    continue
                if not was_foreground:
                    self.status_callback("Recording resumed: target window is active.")
                    was_foreground = True
                    previous = snapshot
                    previous_geometry = self._geometry(window)
                    self.sleeper(self.poll_interval)
                    continue

                geometry = self._geometry(window)
                if geometry != previous_geometry:
                    writer.record(
                        "window_changed",
                        self.capture.capture(window),
                        details={
                            "window": asdict(window),
                            "previous_geometry": list(previous_geometry),
                            "current_geometry": list(geometry),
                        },
                    )
                    previous_geometry = geometry

                if previous is not None:
                    raw_events = self._input_transitions(previous, snapshot, window)
                    for raw_input in raw_events:
                        writer.record(
                            "windows_input",
                            self.capture.capture(window),
                            details={"raw_input": raw_input, "window": asdict(window)},
                        )
                        input_events += 1
                previous = snapshot
                self.sleeper(self.poll_interval)
        except KeyboardInterrupt as error:
            if writer is None:
                raise RuntimeError("Recording interrupted before the target became active") from error
            stop_reason = "keyboard_interrupt"
        finally:
            self.monitor.close()

        if writer is None:
            raise RuntimeError("Recording ended before the target window became active")
        trace = writer.finish(
            success=success,
            extra={
                "input_event_count": input_events,
                "focus_losses": focus_losses,
                "stop_reason": stop_reason,
                "window_selector": asdict(self.session.selector),
                "initial_window": asdict(initial_window),
                "capture_method": "target_client_area",
                "coordinate_space": "physical_pixels",
                "success_hotkey": success_key,
                "cancel_hotkey": cancel_key,
            },
        )
        return WindowRecordResult(
            mode="windows_record",
            task_id=task_id.strip(),
            success=success,
            input_events=input_events,
            focus_losses=focus_losses,
            stop_reason=stop_reason,
            trace_path=str(trace.trace_path),
        )

    def _wait_for_active_target(self, started: float) -> tuple[WindowInfo, InputSnapshot]:
        cancel_key = getattr(self.monitor, "cancel_key", "f9").upper()
        try:
            window = self.session.focus()
        except WindowSafetyError:
            window = self.session.resolve()
            self.status_callback(
                f"Switch to '{window.title}' to begin recording. "
                f"{cancel_key} cancels before start."
            )
        while True:
            if self.clock() - started >= self.max_seconds:
                raise RuntimeError("Timed out waiting for the target window to become active")
            snapshot = self.monitor.poll()
            if snapshot.cancel_requested:
                raise RuntimeError("Recording cancelled before the target window became active")
            window = self.session.resolve()
            if (
                self.session.backend.foreground_handle() == window.handle
                and window.is_visible
                and not window.is_minimized
            ):
                return window, snapshot
            self.sleeper(self.poll_interval)

    @staticmethod
    def _geometry(window: WindowInfo) -> tuple[int, int, int, int, int]:
        return (
            window.client_left,
            window.client_top,
            window.client_width,
            window.client_height,
            window.dpi,
        )

    @classmethod
    def _input_transitions(
        cls,
        previous: InputSnapshot,
        current: InputSnapshot,
        window: WindowInfo,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for key in sorted(current.keys_down - previous.keys_down):
            events.append({"device": "keyboard", "event": "down", "key": key})
        for key in sorted(previous.keys_down - current.keys_down):
            events.append({"device": "keyboard", "event": "up", "key": key})
        for button in sorted(current.buttons_down - previous.buttons_down):
            events.append(
                cls._mouse_event("down", button, current.cursor_position, window)
            )
        for button in sorted(previous.buttons_down - current.buttons_down):
            events.append(cls._mouse_event("up", button, current.cursor_position, window))
        return events

    @staticmethod
    def _mouse_event(
        event: str,
        button: str,
        position: tuple[int, int],
        window: WindowInfo,
    ) -> dict[str, Any]:
        x, y = position
        inside = (
            window.client_left <= x < window.client_left + window.client_width
            and window.client_top <= y < window.client_top + window.client_height
        )
        normalized = None
        if inside and window.client_width and window.client_height:
            normalized = [
                round((x - window.client_left) / window.client_width, 6),
                round((y - window.client_top) / window.client_height, 6),
            ]
        return {
            "device": "mouse",
            "event": event,
            "button": button,
            "screen_position": [x, y],
            "normalized_position": normalized,
            "inside_target": inside,
        }


def record_window_trace(
    selector: WindowSelector,
    *,
    task_id: str,
    output_root: Path,
    poll_hz: int = 120,
    max_seconds: float = 300,
    backend: WindowsBackend,
    capture: WindowFrameCapture,
    monitor: InputMonitor,
) -> WindowRecordResult:
    session = WindowSession(selector, backend)
    return WindowRecorder(
        session,
        capture,
        monitor,
        poll_hz=poll_hz,
        max_seconds=max_seconds,
    ).record(task_id=task_id, output_root=output_root)
