from __future__ import annotations

import ctypes
import os
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from trace2task.actions import ActionCall, normalize_key

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001
MOUSE_FLAGS = {
    "left": (0x0002, 0x0004),
    "right": (0x0008, 0x0010),
    "middle": (0x0020, 0x0040),
}
VK_CODES = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "shift": 0x10,
    "ctrl": 0x11,
    "alt": 0x12,
    "escape": 0x1B,
    "space": 0x20,
    "page_up": 0x21,
    "page_down": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "insert": 0x2D,
    "delete": 0x2E,
    **{f"f{number}": 0x6F + number for number in range(1, 13)},
}
EXTENDED_VIRTUAL_KEYS = {
    VK_CODES[key]
    for key in ("page_up", "page_down", "end", "home", "left", "up", "right", "down", "insert", "delete")
}


class _KeybdInput(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("ki", _KeybdInput), ("mi", _MouseInput)]


class _Input(ctypes.Structure):
    _anonymous_ = ("payload",)
    _fields_ = [("type", wintypes.DWORD), ("payload", _InputUnion)]


@dataclass(frozen=True)
class WindowInfo:
    handle: int
    title: str
    process_id: int
    process_name: str
    client_left: int
    client_top: int
    client_width: int
    client_height: int
    dpi: int
    is_visible: bool
    is_minimized: bool
    is_foreground: bool


@dataclass(frozen=True)
class WindowSelector:
    handle: int | None = None
    title_contains: str | None = None
    process_name: str | None = None

    def __post_init__(self) -> None:
        if self.handle is None and not self.title_contains and not self.process_name:
            raise ValueError("A window selector requires a handle, title, or process name")
        if self.handle is not None and self.handle <= 0:
            raise ValueError("Window handle must be a positive integer")

    def matches(self, window: WindowInfo) -> bool:
        if self.handle is not None and window.handle != self.handle:
            return False
        if self.title_contains and self.title_contains.casefold() not in window.title.casefold():
            return False
        if self.process_name:
            expected = Path(self.process_name).stem.casefold()
            actual = Path(window.process_name).stem.casefold()
            if expected != actual:
                return False
        return True

    def describe(self) -> str:
        parts = []
        if self.handle is not None:
            parts.append(f"handle={self.handle}")
        if self.title_contains:
            parts.append(f"title contains {self.title_contains!r}")
        if self.process_name:
            parts.append(f"process={self.process_name!r}")
        return ", ".join(parts)


class WindowLookupError(RuntimeError):
    """Raised when a selector does not resolve to exactly one target window."""


class WindowSafetyError(RuntimeError):
    """Raised before input whenever the selected window is not safe to control."""


class WindowsBackend(Protocol):
    def list_windows(self) -> list[WindowInfo]: ...

    def get_window(self, handle: int) -> WindowInfo | None: ...

    def foreground_handle(self) -> int: ...

    def focus_window(self, handle: int) -> bool: ...

    def set_cursor_position(self, x: int, y: int) -> None: ...

    def send_mouse_button(self, button: str, is_down: bool) -> None: ...

    def send_key(self, virtual_key: int, is_down: bool) -> None: ...


class Win32Backend:
    """Small ctypes wrapper around read-only window APIs and SendInput."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("The Windows control adapter is available only on Windows")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._enum_callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HWND,
            wintypes.LPARAM,
        )
        self.user32.EnumWindows.argtypes = [self._enum_callback_type, wintypes.LPARAM]
        self.user32.EnumWindows.restype = wintypes.BOOL
        self.user32.IsWindow.argtypes = [wintypes.HWND]
        self.user32.IsWindow.restype = wintypes.BOOL
        self.user32.IsWindowVisible.argtypes = [wintypes.HWND]
        self.user32.IsWindowVisible.restype = wintypes.BOOL
        self.user32.IsIconic.argtypes = [wintypes.HWND]
        self.user32.IsIconic.restype = wintypes.BOOL
        self.user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        self.user32.GetWindowTextLengthW.restype = ctypes.c_int
        self.user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.user32.GetWindowTextW.restype = ctypes.c_int
        self.user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user32.GetClientRect.restype = wintypes.BOOL
        self.user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        self.user32.ClientToScreen.restype = wintypes.BOOL
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        self.user32.SetForegroundWindow.restype = wintypes.BOOL
        self.user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        self.user32.SetCursorPos.restype = wintypes.BOOL
        self.user32.SendInput.argtypes = [
            wintypes.UINT,
            ctypes.POINTER(_Input),
            ctypes.c_int,
        ]
        self.user32.SendInput.restype = wintypes.UINT
        get_dpi = getattr(self.user32, "GetDpiForWindow", None)
        if get_dpi:
            get_dpi.argtypes = [wintypes.HWND]
            get_dpi.restype = wintypes.UINT

        self.kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL

    def list_windows(self) -> list[WindowInfo]:
        windows: list[WindowInfo] = []
        def collect(handle: int, _: int) -> bool:
            info = self.get_window(int(handle))
            if info and info.is_visible and info.title and info.client_width and info.client_height:
                windows.append(info)
            return True

        callback = self._enum_callback_type(collect)
        if not self.user32.EnumWindows(callback, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        return sorted(windows, key=lambda window: (window.process_name.casefold(), window.title))

    def get_window(self, handle: int) -> WindowInfo | None:
        if not self.user32.IsWindow(wintypes.HWND(handle)):
            return None
        title_length = self.user32.GetWindowTextLengthW(wintypes.HWND(handle))
        title_buffer = ctypes.create_unicode_buffer(title_length + 1)
        self.user32.GetWindowTextW(wintypes.HWND(handle), title_buffer, len(title_buffer))

        process_id = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(wintypes.HWND(handle), ctypes.byref(process_id))
        process_name = self._process_name(process_id.value)

        client_rect = wintypes.RECT()
        if not self.user32.GetClientRect(wintypes.HWND(handle), ctypes.byref(client_rect)):
            return None
        top_left = wintypes.POINT(client_rect.left, client_rect.top)
        bottom_right = wintypes.POINT(client_rect.right, client_rect.bottom)
        if not self.user32.ClientToScreen(wintypes.HWND(handle), ctypes.byref(top_left)):
            return None
        if not self.user32.ClientToScreen(wintypes.HWND(handle), ctypes.byref(bottom_right)):
            return None

        get_dpi = getattr(self.user32, "GetDpiForWindow", None)
        dpi = int(get_dpi(wintypes.HWND(handle))) if get_dpi else 96
        foreground = int(self.user32.GetForegroundWindow() or 0)
        return WindowInfo(
            handle=handle,
            title=title_buffer.value,
            process_id=int(process_id.value),
            process_name=process_name,
            client_left=int(top_left.x),
            client_top=int(top_left.y),
            client_width=max(0, int(bottom_right.x - top_left.x)),
            client_height=max(0, int(bottom_right.y - top_left.y)),
            dpi=dpi or 96,
            is_visible=bool(self.user32.IsWindowVisible(wintypes.HWND(handle))),
            is_minimized=bool(self.user32.IsIconic(wintypes.HWND(handle))),
            is_foreground=foreground == handle,
        )

    def _process_name(self, process_id: int) -> str:
        process = self.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
        if not process:
            return ""
        try:
            size = wintypes.DWORD(32_768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not self.kernel32.QueryFullProcessImageNameW(
                process,
                0,
                buffer,
                ctypes.byref(size),
            ):
                return ""
            return Path(buffer.value).name
        finally:
            self.kernel32.CloseHandle(process)

    def foreground_handle(self) -> int:
        return int(self.user32.GetForegroundWindow() or 0)

    def focus_window(self, handle: int) -> bool:
        return bool(self.user32.SetForegroundWindow(wintypes.HWND(handle)))

    def set_cursor_position(self, x: int, y: int) -> None:
        if not self.user32.SetCursorPos(x, y):
            raise ctypes.WinError(ctypes.get_last_error())

    def send_mouse_button(self, button: str, is_down: bool) -> None:
        flags = MOUSE_FLAGS[button][0 if is_down else 1]
        item = _Input(type=INPUT_MOUSE)
        item.mi = _MouseInput(0, 0, 0, flags, 0, 0)
        self._send_input(item)

    def send_key(self, virtual_key: int, is_down: bool) -> None:
        flags = KEYEVENTF_EXTENDEDKEY if virtual_key in EXTENDED_VIRTUAL_KEYS else 0
        if not is_down:
            flags |= KEYEVENTF_KEYUP
        item = _Input(type=INPUT_KEYBOARD)
        item.ki = _KeybdInput(virtual_key, 0, flags, 0, 0)
        self._send_input(item)

    def _send_input(self, item: _Input) -> None:
        sent = self.user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(_Input))
        if sent != 1:
            raise ctypes.WinError(ctypes.get_last_error())


class WindowSession:
    """Resolve a stable selector and guard every coordinate/input operation."""

    def __init__(self, selector: WindowSelector, backend: WindowsBackend) -> None:
        self.selector = selector
        self.backend = backend
        self._resolved_handle: int | None = None

    def resolve(self) -> WindowInfo:
        if self._resolved_handle is not None:
            current = self.backend.get_window(self._resolved_handle)
            if current is not None and self.selector.matches(current):
                return current
            self._resolved_handle = None

        matches = [window for window in self.backend.list_windows() if self.selector.matches(window)]
        if not matches:
            raise WindowLookupError(f"No window matches {self.selector.describe()}")
        if len(matches) > 1:
            choices = "; ".join(
                f"{window.handle}: {window.process_name} - {window.title}" for window in matches
            )
            raise WindowLookupError(
                f"Window selector is ambiguous ({self.selector.describe()}): {choices}"
            )
        self._resolved_handle = matches[0].handle
        return matches[0]

    def focus(self) -> WindowInfo:
        window = self._require_available(self.resolve())
        if not self.backend.focus_window(window.handle):
            raise WindowSafetyError(f"Windows refused to focus target window {window.handle}")
        if self.backend.foreground_handle() != window.handle:
            raise WindowSafetyError(f"Target window {window.handle} did not become foreground")
        refreshed = self.backend.get_window(window.handle)
        return self._require_available(refreshed or window)

    def require_foreground(self) -> WindowInfo:
        window = self._require_available(self.resolve())
        if self.backend.foreground_handle() != window.handle:
            raise WindowSafetyError(
                f"Target window {window.handle} lost focus; input was not sent"
            )
        return window

    def normalized_to_screen(self, window: WindowInfo, x: float, y: float) -> tuple[int, int]:
        if not 0 <= x <= 1 or not 0 <= y <= 1:
            raise WindowSafetyError("Normalized mouse coordinates must stay between 0 and 1")
        if window.client_width <= 0 or window.client_height <= 0:
            raise WindowSafetyError("Target window has no usable client area")
        screen_x = window.client_left + min(window.client_width - 1, int(x * window.client_width))
        screen_y = window.client_top + min(window.client_height - 1, int(y * window.client_height))
        return screen_x, screen_y

    @staticmethod
    def _require_available(window: WindowInfo) -> WindowInfo:
        if not window.is_visible:
            raise WindowSafetyError(f"Target window {window.handle} is not visible")
        if window.is_minimized:
            raise WindowSafetyError(f"Target window {window.handle} is minimized")
        return window


@dataclass(frozen=True)
class MotorResult:
    skill: str
    window_handle: int
    elapsed_ms: float
    screen_position: tuple[int, int] | None = None


class WindowsMotorExecutor:
    """Execute validated skills only while the selected window owns foreground focus."""

    def __init__(
        self,
        session: WindowSession,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        max_hold_ms: int = 5_000,
        max_wait_ms: int = 10_000,
    ) -> None:
        self.session = session
        self.sleeper = sleeper
        self.max_hold_ms = max_hold_ms
        self.max_wait_ms = max_wait_ms

    def execute(self, action: ActionCall | dict[str, Any]) -> MotorResult:
        call = action if isinstance(action, ActionCall) else ActionCall.from_payload(action)
        started = time.perf_counter()
        position: tuple[int, int] | None = None
        if call.skill == "focus_window":
            window = self.session.focus()
        else:
            window = self.session.require_foreground()
            if call.skill in {"click", "double_click"}:
                position = self.session.normalized_to_screen(
                    window,
                    call.args["x"],
                    call.args["y"],
                )
                self.session.backend.set_cursor_position(*position)
                clicks = 2 if call.skill == "double_click" else 1
                for click_index in range(clicks):
                    self._click(call.args["button"])
                    if click_index + 1 < clicks:
                        self.sleeper(0.08)
            elif call.skill == "press_key":
                self._press(call.args["key"])
            elif call.skill == "hold_key":
                duration = call.args["duration_ms"]
                if duration > self.max_hold_ms:
                    raise WindowSafetyError(
                        f"Requested key hold {duration}ms exceeds executor limit {self.max_hold_ms}ms"
                    )
                self._hold(call.args["key"], duration)
            elif call.skill == "hotkey":
                self._hotkey(call.args["keys"])
            elif call.skill == "wait":
                duration = call.args["duration_ms"]
                if duration > self.max_wait_ms:
                    raise WindowSafetyError(
                        f"Requested wait {duration}ms exceeds executor limit {self.max_wait_ms}ms"
                    )
                self.sleeper(duration / 1_000)
            else:
                raise AssertionError(f"Unhandled motor skill: {call.skill}")
        return MotorResult(
            skill=call.skill,
            window_handle=window.handle,
            elapsed_ms=round((time.perf_counter() - started) * 1_000, 3),
            screen_position=position,
        )

    def _click(self, button: str) -> None:
        self.session.backend.send_mouse_button(button, True)
        try:
            self.sleeper(0.02)
        finally:
            self.session.backend.send_mouse_button(button, False)

    def _press(self, key: str) -> None:
        virtual_key = virtual_key_for(key)
        self.session.backend.send_key(virtual_key, True)
        try:
            self.sleeper(0.02)
        finally:
            self.session.backend.send_key(virtual_key, False)

    def _hold(self, key: str, duration_ms: int) -> None:
        virtual_key = virtual_key_for(key)
        self.session.backend.send_key(virtual_key, True)
        try:
            self.sleeper(duration_ms / 1_000)
        finally:
            self.session.backend.send_key(virtual_key, False)

    def _hotkey(self, keys: list[str]) -> None:
        virtual_keys = [virtual_key_for(key) for key in keys]
        pressed: list[int] = []
        try:
            for virtual_key in virtual_keys:
                self.session.backend.send_key(virtual_key, True)
                pressed.append(virtual_key)
        finally:
            for virtual_key in reversed(pressed):
                self.session.backend.send_key(virtual_key, False)


def virtual_key_for(key: str) -> int:
    normalized = normalize_key(key)
    if len(normalized) == 1:
        return ord(normalized.upper())
    return VK_CODES[normalized]


def list_window_records(
    *,
    title_contains: str | None = None,
    process_name: str | None = None,
    backend: WindowsBackend | None = None,
) -> dict[str, Any]:
    active_backend = backend or Win32Backend()
    windows = active_backend.list_windows()
    if title_contains or process_name:
        selector = WindowSelector(
            title_contains=title_contains,
            process_name=process_name,
        )
        windows = [window for window in windows if selector.matches(window)]
    return {"count": len(windows), "windows": [asdict(window) for window in windows]}
