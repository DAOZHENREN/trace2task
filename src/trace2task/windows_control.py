from __future__ import annotations

import ctypes
import math
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from trace2task.actions import ActionCall, is_runtime_text_placeholder, normalize_key

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_SCANCODE = 0x0008
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = wintypes.HANDLE(-4)
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_MOUSEMOVE = 0x0200
SMTO_BLOCK = 0x0001
SMTO_ABORTIFHUNG = 0x0002
CWP_SKIPINVISIBLE = 0x0001
CWP_SKIPDISABLED = 0x0002
CWP_SKIPTRANSPARENT = 0x0004
MAPVK_VK_TO_VSC = 0
MOUSE_FLAGS = {
    "left": (0x0002, 0x0004),
    "right": (0x0008, 0x0010),
    "middle": (0x0020, 0x0040),
}
WINDOW_MOUSE_MESSAGES = {
    "left": (0x0201, 0x0202, 0x0203, 0x0001),
    "right": (0x0204, 0x0205, 0x0206, 0x0002),
    "middle": (0x0207, 0x0208, 0x0209, 0x0010),
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


def configure_physical_dpi_api(user32: Any) -> None:
    """Configure the optional Windows 10 thread-DPI API on one user32 handle."""
    setter = getattr(user32, "SetThreadDpiAwarenessContext", None)
    if setter is not None:
        setter.argtypes = [wintypes.HANDLE]
        setter.restype = wintypes.HANDLE


@contextmanager
def physical_dpi_context(user32: Any) -> Iterator[None]:
    """Run coordinate-sensitive Win32 calls in physical per-monitor pixels."""
    setter = getattr(user32, "SetThreadDpiAwarenessContext", None)
    if setter is None:
        yield
        return
    previous = setter(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
    if not previous:
        # Older/locked-down systems can reject the context switch. Preserve the
        # old behavior rather than failing unrelated window discovery entirely.
        yield
        return
    try:
        yield
    finally:
        setter(previous)


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


class _GuiThreadInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


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

    def send_virtual_key(self, virtual_key: int, is_down: bool) -> None: ...

    def send_text(self, text: str) -> None: ...

    def post_window_mouse_button(
        self,
        handle: int,
        client_x: int,
        client_y: int,
        button: str,
        is_down: bool,
        *,
        double: bool = False,
    ) -> None: ...

    def post_window_mouse_move(
        self,
        handle: int,
        client_x: int,
        client_y: int,
        button: str,
    ) -> None: ...

    def post_window_key(self, handle: int, virtual_key: int, is_down: bool) -> None: ...

    def post_window_text(self, handle: int, text: str) -> None: ...

    def send_window_key(
        self,
        handle: int,
        virtual_key: int,
        is_down: bool,
        *,
        timeout_ms: int = 1_000,
    ) -> int: ...


class Win32Backend:
    """Small ctypes wrapper around window APIs, SendInput, and directed messages."""

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
        self.user32.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self.user32.PostMessageW.restype = wintypes.BOOL
        self.user32.SendMessageTimeoutW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        self.user32.SendMessageTimeoutW.restype = wintypes.LPARAM
        self.user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
        self.user32.MapVirtualKeyW.restype = wintypes.UINT
        self.user32.ChildWindowFromPointEx.argtypes = [
            wintypes.HWND,
            wintypes.POINT,
            wintypes.UINT,
        ]
        self.user32.ChildWindowFromPointEx.restype = wintypes.HWND
        self.user32.MapWindowPoints.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.POINTER(wintypes.POINT),
            wintypes.UINT,
        ]
        self.user32.MapWindowPoints.restype = ctypes.c_int
        self.user32.GetGUIThreadInfo.argtypes = [
            wintypes.DWORD,
            ctypes.POINTER(_GuiThreadInfo),
        ]
        self.user32.GetGUIThreadInfo.restype = wintypes.BOOL
        self.user32.IsChild.argtypes = [wintypes.HWND, wintypes.HWND]
        self.user32.IsChild.restype = wintypes.BOOL
        get_dpi = getattr(self.user32, "GetDpiForWindow", None)
        if get_dpi:
            get_dpi.argtypes = [wintypes.HWND]
            get_dpi.restype = wintypes.UINT
        configure_physical_dpi_api(self.user32)
        self._posted_key_targets: dict[tuple[int, int], int] = {}
        self._sent_key_targets: dict[tuple[int, int], int] = {}
        self._posted_mouse_targets: dict[tuple[int, str], tuple[int, int]] = {}
        self._posted_alt_handles: set[int] = set()
        self._sent_alt_handles: set[int] = set()

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
        with physical_dpi_context(self.user32):
            return self._get_window_physical(handle)

    def _get_window_physical(self, handle: int) -> WindowInfo | None:
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
        with physical_dpi_context(self.user32):
            if not self.user32.SetCursorPos(x, y):
                raise ctypes.WinError(ctypes.get_last_error())

    def send_mouse_button(self, button: str, is_down: bool) -> None:
        flags = MOUSE_FLAGS[button][0 if is_down else 1]
        item = _Input(type=INPUT_MOUSE)
        item.mi = _MouseInput(0, 0, 0, flags, 0, 0)
        self._send_input(item)

    def send_key(self, virtual_key: int, is_down: bool) -> None:
        scan_code = int(self.user32.MapVirtualKeyW(virtual_key, MAPVK_VK_TO_VSC))
        if not scan_code:
            raise WindowSafetyError(f"Could not map virtual key 0x{virtual_key:02x} to a scan code")
        flags = KEYEVENTF_SCANCODE
        if virtual_key in EXTENDED_VIRTUAL_KEYS:
            flags |= KEYEVENTF_EXTENDEDKEY
        if not is_down:
            flags |= KEYEVENTF_KEYUP
        item = _Input(type=INPUT_KEYBOARD)
        item.ki = _KeybdInput(0, scan_code, flags, 0, 0)
        self._send_input(item)

    def send_virtual_key(self, virtual_key: int, is_down: bool) -> None:
        """Send the legacy virtual-key form of SendInput for compatibility probing."""
        flags = KEYEVENTF_EXTENDEDKEY if virtual_key in EXTENDED_VIRTUAL_KEYS else 0
        if not is_down:
            flags |= KEYEVENTF_KEYUP
        item = _Input(type=INPUT_KEYBOARD)
        item.ki = _KeybdInput(virtual_key, 0, flags, 0, 0)
        self._send_input(item)

    def send_text(self, text: str) -> None:
        """Type Unicode text through SendInput without depending on the active keyboard layout."""
        for unit in self._utf16_units(text):
            down = _Input(type=INPUT_KEYBOARD)
            down.ki = _KeybdInput(0, unit, KEYEVENTF_UNICODE, 0, 0)
            up = _Input(type=INPUT_KEYBOARD)
            up.ki = _KeybdInput(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0)
            self._send_input(down)
            self._send_input(up)

    def _send_input(self, item: _Input) -> None:
        sent = self.user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(_Input))
        if sent != 1:
            raise ctypes.WinError(ctypes.get_last_error())

    def post_window_mouse_button(
        self,
        handle: int,
        client_x: int,
        client_y: int,
        button: str,
        is_down: bool,
        *,
        double: bool = False,
    ) -> None:
        key = (handle, button)
        if is_down:
            target, point = self._deepest_child_at(handle, client_x, client_y)
            packed_point = self._pack_client_point(point.x, point.y)
            self._posted_mouse_targets[key] = (target, packed_point)
        else:
            stored = self._posted_mouse_targets.pop(key, None)
            if stored is None:
                target, point = self._deepest_child_at(handle, client_x, client_y)
                packed_point = self._pack_client_point(point.x, point.y)
            else:
                target, packed_point = stored
        down_message, up_message, double_message, button_flag = WINDOW_MOUSE_MESSAGES[button]
        if is_down:
            try:
                self._post_message(target, WM_MOUSEMOVE, 0, packed_point)
                message = double_message if double else down_message
                self._post_message(target, message, button_flag, packed_point)
            except Exception:
                self._posted_mouse_targets.pop(key, None)
                raise
        else:
            self._post_message(target, up_message, 0, packed_point)

    def post_window_mouse_move(
        self,
        handle: int,
        client_x: int,
        client_y: int,
        button: str,
    ) -> None:
        key = (handle, button)
        stored = self._posted_mouse_targets.get(key)
        if stored is None:
            raise RuntimeError(f"Mouse button {button!r} is not held for target {handle}")
        target, _ = stored
        point = wintypes.POINT(client_x, client_y)
        if target != handle:
            self.user32.MapWindowPoints(
                wintypes.HWND(handle),
                wintypes.HWND(target),
                ctypes.byref(point),
                1,
            )
        packed_point = self._pack_client_point(point.x, point.y)
        button_flag = WINDOW_MOUSE_MESSAGES[button][3]
        self._post_message(target, WM_MOUSEMOVE, button_flag, packed_point)
        self._posted_mouse_targets[key] = (target, packed_point)

    def post_window_key(self, handle: int, virtual_key: int, is_down: bool) -> None:
        key = (handle, virtual_key)
        if is_down:
            target = self._keyboard_target(handle)
            self._posted_key_targets[key] = target
        else:
            target = self._posted_key_targets.pop(key, None)
            if target is None:
                target = self._keyboard_target(handle)
        scan_code = int(self.user32.MapVirtualKeyW(virtual_key, MAPVK_VK_TO_VSC))
        flags = 1 | (scan_code << 16)
        if virtual_key in EXTENDED_VIRTUAL_KEYS:
            flags |= 1 << 24
        is_alt = virtual_key == VK_CODES["alt"]
        is_system = is_alt or handle in self._posted_alt_handles
        if is_system:
            flags |= 1 << 29
        if not is_down:
            flags |= (1 << 30) | (1 << 31)
        message = (
            WM_SYSKEYDOWN
            if is_system and is_down
            else WM_SYSKEYUP
            if is_system
            else WM_KEYDOWN
            if is_down
            else WM_KEYUP
        )
        try:
            self._post_message(target, message, virtual_key, flags)
        except Exception:
            if is_down:
                self._posted_key_targets.pop(key, None)
            elif is_alt:
                self._posted_alt_handles.discard(handle)
            raise
        if is_alt:
            if is_down:
                self._posted_alt_handles.add(handle)
            else:
                self._posted_alt_handles.discard(handle)

    def post_window_text(self, handle: int, text: str) -> None:
        """Deliver Unicode text to the focused child of a background-compatible window."""
        target = self._keyboard_target(handle)
        for unit in self._utf16_units(text):
            self._post_message(target, WM_CHAR, unit, 1)

    def send_window_key(
        self,
        handle: int,
        virtual_key: int,
        is_down: bool,
        *,
        timeout_ms: int = 1_000,
    ) -> int:
        """Synchronously deliver one key transition to the focused target HWND."""
        if not 1 <= timeout_ms <= 10_000:
            raise ValueError("Window-message timeout must be between 1 and 10000ms")
        key = (handle, virtual_key)
        if is_down:
            target = self._keyboard_target(handle)
            self._sent_key_targets[key] = target
        else:
            target = self._sent_key_targets.pop(key, None)
            if target is None:
                target = self._keyboard_target(handle)
        scan_code = int(self.user32.MapVirtualKeyW(virtual_key, MAPVK_VK_TO_VSC))
        flags = 1 | (scan_code << 16)
        if virtual_key in EXTENDED_VIRTUAL_KEYS:
            flags |= 1 << 24
        is_alt = virtual_key == VK_CODES["alt"]
        is_system = is_alt or handle in self._sent_alt_handles
        if is_system:
            flags |= 1 << 29
        if not is_down:
            flags |= (1 << 30) | (1 << 31)
        message = (
            WM_SYSKEYDOWN
            if is_system and is_down
            else WM_SYSKEYUP
            if is_system
            else WM_KEYDOWN
            if is_down
            else WM_KEYUP
        )
        try:
            self._send_message(target, message, virtual_key, flags, timeout_ms)
        except Exception:
            if is_down:
                self._sent_key_targets.pop(key, None)
            elif is_alt:
                self._sent_alt_handles.discard(handle)
            raise
        if is_alt:
            if is_down:
                self._sent_alt_handles.add(handle)
            else:
                self._sent_alt_handles.discard(handle)
        return target

    def _deepest_child_at(
        self,
        handle: int,
        client_x: int,
        client_y: int,
    ) -> tuple[int, wintypes.POINT]:
        current = handle
        point = wintypes.POINT(client_x, client_y)
        skip_flags = CWP_SKIPINVISIBLE | CWP_SKIPDISABLED | CWP_SKIPTRANSPARENT
        for _ in range(32):
            child = int(
                self.user32.ChildWindowFromPointEx(
                    wintypes.HWND(current),
                    point,
                    skip_flags,
                )
                or 0
            )
            if not child or child == current:
                break
            mapped = wintypes.POINT(point.x, point.y)
            self.user32.MapWindowPoints(
                wintypes.HWND(current),
                wintypes.HWND(child),
                ctypes.byref(mapped),
                1,
            )
            current = child
            point = mapped
        return current, point

    def _keyboard_target(self, handle: int) -> int:
        thread_id = int(
            self.user32.GetWindowThreadProcessId(wintypes.HWND(handle), None) or 0
        )
        info = _GuiThreadInfo(cbSize=ctypes.sizeof(_GuiThreadInfo))
        if thread_id and self.user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
            focused = int(info.hwndFocus or 0)
            if focused and (
                focused == handle
                or self.user32.IsChild(wintypes.HWND(handle), wintypes.HWND(focused))
            ):
                return focused
        return handle

    def _post_message(self, handle: int, message: int, wparam: int, lparam: int) -> None:
        ctypes.set_last_error(0)
        if self.user32.PostMessageW(
            wintypes.HWND(handle),
            message,
            wparam,
            lparam,
        ):
            return
        error = ctypes.get_last_error()
        if error:
            raise ctypes.WinError(error)
        raise RuntimeError(f"Could not post Windows message 0x{message:04x} to {handle}")

    def _send_message(
        self,
        handle: int,
        message: int,
        wparam: int,
        lparam: int,
        timeout_ms: int,
    ) -> int:
        result = ctypes.c_size_t()
        ctypes.set_last_error(0)
        delivered = self.user32.SendMessageTimeoutW(
            wintypes.HWND(handle),
            message,
            wparam,
            lparam,
            SMTO_BLOCK | SMTO_ABORTIFHUNG,
            timeout_ms,
            ctypes.byref(result),
        )
        if delivered:
            return int(result.value)
        error = ctypes.get_last_error()
        if error:
            raise ctypes.WinError(error)
        raise TimeoutError(
            f"Window did not process message 0x{message:04x} within {timeout_ms}ms"
        )

    @staticmethod
    def _pack_client_point(x: int, y: int) -> int:
        return (x & 0xFFFF) | ((y & 0xFFFF) << 16)

    @staticmethod
    def _utf16_units(text: str) -> tuple[int, ...]:
        encoded = text.encode("utf-16-le")
        return tuple(
            int.from_bytes(encoded[index : index + 2], "little")
            for index in range(0, len(encoded), 2)
        )


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

    def focus(
        self,
        *,
        timeout_seconds: float = 0,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> WindowInfo:
        if timeout_seconds < 0:
            raise ValueError("Focus timeout cannot be negative")
        window = self._require_available(self.resolve())
        focus_requested = self.backend.focus_window(window.handle)
        deadline = time.monotonic() + timeout_seconds
        while self.backend.foreground_handle() != window.handle and time.monotonic() < deadline:
            sleeper(min(0.05, max(0, deadline - time.monotonic())))
        if self.backend.foreground_handle() != window.handle:
            reason = "Windows refused to focus" if not focus_requested else "Could not foreground"
            suffix = (
                f" within {timeout_seconds:g}s; switch to it manually while the command waits"
                if timeout_seconds
                else ""
            )
            raise WindowSafetyError(f"{reason} target window {window.handle}{suffix}")
        refreshed = self.backend.get_window(window.handle)
        return self._require_available(refreshed or window)

    def require_foreground(self) -> WindowInfo:
        window = self.require_available()
        if self.backend.foreground_handle() != window.handle:
            raise WindowSafetyError(
                f"Target window {window.handle} lost focus; input was not sent"
            )
        return window

    def require_available(self) -> WindowInfo:
        return self._require_available(self.resolve())

    def normalized_to_client(self, window: WindowInfo, x: float, y: float) -> tuple[int, int]:
        if not 0 <= x <= 1 or not 0 <= y <= 1:
            raise WindowSafetyError("Normalized mouse coordinates must stay between 0 and 1")
        if window.client_width <= 0 or window.client_height <= 0:
            raise WindowSafetyError("Target window has no usable client area")
        client_x = min(window.client_width - 1, int(x * window.client_width))
        client_y = min(window.client_height - 1, int(y * window.client_height))
        return client_x, client_y

    def normalized_to_screen(self, window: WindowInfo, x: float, y: float) -> tuple[int, int]:
        client_x, client_y = self.normalized_to_client(window, x, y)
        screen_x = window.client_left + client_x
        screen_y = window.client_top + client_y
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
    input_mode: str = "foreground"


class WindowsMotorExecutor:
    """Execute validated skills through foreground input or directed window messages."""

    def __init__(
        self,
        session: WindowSession,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        max_hold_ms: int = 5_000,
        max_drag_ms: int = 5_000,
        max_wait_ms: int = 10_000,
        background: bool = False,
    ) -> None:
        self.session = session
        self.sleeper = sleeper
        self.max_hold_ms = max_hold_ms
        self.max_drag_ms = max_drag_ms
        self.max_wait_ms = max_wait_ms
        self.background = background

    def execute(self, action: ActionCall | dict[str, Any]) -> MotorResult:
        call = action if isinstance(action, ActionCall) else ActionCall.from_payload(action)
        started = time.perf_counter()
        position: tuple[int, int] | None = None
        if call.skill == "focus_window" and not self.background:
            window = self.session.focus()
        else:
            window = (
                self.session.require_available()
                if self.background
                else self.session.require_foreground()
            )
            if call.skill == "focus_window":
                pass
            elif call.skill in {"click", "double_click"}:
                position = self.session.normalized_to_screen(
                    window,
                    call.args["x"],
                    call.args["y"],
                )
                if not self.background:
                    self.session.backend.set_cursor_position(*position)
                clicks = 2 if call.skill == "double_click" else 1
                for click_index in range(clicks):
                    self._click(
                        window,
                        call.args["x"],
                        call.args["y"],
                        call.args["button"],
                        double=call.skill == "double_click" and click_index == 1,
                    )
                    if click_index + 1 < clicks:
                        self.sleeper(0.08)
            elif call.skill == "drag":
                duration = call.args["duration_ms"]
                if duration > self.max_drag_ms:
                    raise WindowSafetyError(
                        f"Requested mouse drag {duration}ms exceeds executor limit "
                        f"{self.max_drag_ms}ms"
                    )
                position = self.session.normalized_to_screen(
                    window,
                    call.args["end_x"],
                    call.args["end_y"],
                )
                self._drag(window, call)
            elif call.skill == "type_text":
                if is_runtime_text_placeholder(call.args["text"]):
                    raise WindowSafetyError(
                        "A runtime-text demonstration marker must be resolved before execution"
                    )
                if self.background:
                    self.session.backend.post_window_text(window.handle, call.args["text"])
                else:
                    self.session.backend.send_text(call.args["text"])
            elif call.skill == "press_key":
                self._press(window, call.args["key"])
            elif call.skill == "hold_key":
                duration = call.args["duration_ms"]
                if duration > self.max_hold_ms:
                    raise WindowSafetyError(
                        f"Requested key hold {duration}ms exceeds executor limit {self.max_hold_ms}ms"
                    )
                self._hold(window, call.args["key"], duration)
            elif call.skill == "hotkey":
                self._hotkey(window, call.args["keys"])
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
            input_mode="background" if self.background else "foreground",
        )

    def _click(
        self,
        window: WindowInfo,
        x: float,
        y: float,
        button: str,
        *,
        double: bool = False,
    ) -> None:
        if self.background:
            client_x, client_y = self.session.normalized_to_client(window, x, y)
            send = lambda is_down: self.session.backend.post_window_mouse_button(
                window.handle,
                client_x,
                client_y,
                button,
                is_down,
                double=double and is_down,
            )
        else:
            send = lambda is_down: self.session.backend.send_mouse_button(button, is_down)
        send(True)
        try:
            self.sleeper(0.02)
        finally:
            send(False)

    def _drag(self, window: WindowInfo, call: ActionCall) -> None:
        start_x = call.args["start_x"]
        start_y = call.args["start_y"]
        end_x = call.args["end_x"]
        end_y = call.args["end_y"]
        duration_ms = call.args["duration_ms"]
        button = call.args["button"]
        steps = max(1, math.ceil(duration_ms / 16))
        step_seconds = duration_ms / steps / 1_000

        if self.background:
            client_x, client_y = self.session.normalized_to_client(window, start_x, start_y)
            self.session.backend.post_window_mouse_button(
                window.handle,
                client_x,
                client_y,
                button,
                True,
            )
        else:
            start_position = self.session.normalized_to_screen(window, start_x, start_y)
            self.session.backend.set_cursor_position(*start_position)
            self.session.backend.send_mouse_button(button, True)
        try:
            for step in range(1, steps + 1):
                self.sleeper(step_seconds)
                progress = step / steps
                x = start_x + (end_x - start_x) * progress
                y = start_y + (end_y - start_y) * progress
                current = (
                    self.session.require_available()
                    if self.background
                    else self.session.require_foreground()
                )
                if self.background:
                    client_x, client_y = self.session.normalized_to_client(current, x, y)
                    self.session.backend.post_window_mouse_move(
                        current.handle,
                        client_x,
                        client_y,
                        button,
                    )
                else:
                    position = self.session.normalized_to_screen(current, x, y)
                    self.session.backend.set_cursor_position(*position)
        finally:
            if self.background:
                client_x, client_y = self.session.normalized_to_client(window, end_x, end_y)
                self.session.backend.post_window_mouse_button(
                    window.handle,
                    client_x,
                    client_y,
                    button,
                    False,
                )
            else:
                self.session.backend.send_mouse_button(button, False)

    def _press(self, window: WindowInfo, key: str) -> None:
        virtual_key = virtual_key_for(key)
        send = self._key_sender(window)
        send(virtual_key, True)
        try:
            self.sleeper(0.02)
        finally:
            send(virtual_key, False)

    def _hold(self, window: WindowInfo, key: str, duration_ms: int) -> None:
        virtual_key = virtual_key_for(key)
        send = self._key_sender(window)
        send(virtual_key, True)
        try:
            self.sleeper(duration_ms / 1_000)
        finally:
            send(virtual_key, False)

    def _hotkey(self, window: WindowInfo, keys: list[str]) -> None:
        virtual_keys = [virtual_key_for(key) for key in keys]
        send = self._key_sender(window)
        pressed: list[int] = []
        try:
            for virtual_key in virtual_keys:
                send(virtual_key, True)
                pressed.append(virtual_key)
        finally:
            for virtual_key in reversed(pressed):
                send(virtual_key, False)

    def _key_sender(self, window: WindowInfo) -> Callable[[int, bool], None]:
        if self.background:
            return lambda virtual_key, is_down: self.session.backend.post_window_key(
                window.handle,
                virtual_key,
                is_down,
            )
        return self.session.backend.send_key


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


def probe_window_key(
    selector: WindowSelector,
    key: str,
    *,
    method: str = "send-message",
    hold_ms: int = 500,
    settle_seconds: float = 1.0,
    timeout_ms: int = 1_000,
    backend: WindowsBackend | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Test one keyboard delivery path without changing the Agent input path."""
    if method not in {"send-message", "send-input-vk"}:
        raise ValueError(f"Unsupported input probe method: {method}")
    if not 1 <= hold_ms <= 5_000:
        raise ValueError("Probe key hold must be between 1 and 5000ms")
    if not 0 <= settle_seconds <= 10:
        raise ValueError("Probe settle time must be between 0 and 10 seconds")
    active_backend = backend or Win32Backend()
    session = WindowSession(selector, active_backend)
    window = session.focus(timeout_seconds=10, sleeper=sleeper)
    if settle_seconds:
        sleeper(settle_seconds)
    window = session.require_foreground()
    virtual_key = virtual_key_for(key)
    if method == "send-message":
        input_target = active_backend.send_window_key(
            window.handle,
            virtual_key,
            True,
            timeout_ms=timeout_ms,
        )
        try:
            sleeper(hold_ms / 1_000)
        finally:
            active_backend.send_window_key(
                window.handle,
                virtual_key,
                False,
                timeout_ms=timeout_ms,
            )
        delivery_note = (
            "Delivered means Windows processed both messages; the app may still ignore them."
        )
    else:
        input_target = window.handle
        active_backend.send_virtual_key(virtual_key, True)
        try:
            sleeper(hold_ms / 1_000)
        finally:
            active_backend.send_virtual_key(virtual_key, False)
        delivery_note = (
            "SendInput accepted both transitions; the app may still ignore synthetic input."
        )
    return {
        "method": method,
        "key": normalize_key(key),
        "virtual_key": f"0x{virtual_key:02X}",
        "hold_ms": hold_ms,
        "message_timeout_ms": timeout_ms,
        "window_handle": window.handle,
        "input_target_handle": input_target,
        "foreground_verified": True,
        "delivered": True,
        "note": delivery_note,
    }


def probe_window_mouse_button(
    selector: WindowSelector,
    button: str,
    *,
    hold_ms: int = 120,
    settle_seconds: float = 1.0,
    backend: WindowsBackend | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Test foreground SendInput mouse-button delivery without moving the cursor."""
    if button not in MOUSE_FLAGS:
        raise ValueError(f"Unsupported mouse probe button: {button}")
    if not 1 <= hold_ms <= 5_000:
        raise ValueError("Probe mouse hold must be between 1 and 5000ms")
    if not 0 <= settle_seconds <= 10:
        raise ValueError("Probe settle time must be between 0 and 10 seconds")
    active_backend = backend or Win32Backend()
    session = WindowSession(selector, active_backend)
    window = session.focus(timeout_seconds=10, sleeper=sleeper)
    if settle_seconds:
        sleeper(settle_seconds)
    window = session.require_foreground()
    active_backend.send_mouse_button(button, True)
    try:
        sleeper(hold_ms / 1_000)
    finally:
        active_backend.send_mouse_button(button, False)
    return {
        "method": "send-input-mouse",
        "button": button,
        "hold_ms": hold_ms,
        "input_target_handle": window.handle,
        "foreground_verified": True,
        "delivered": True,
        "note": "SendInput accepted both transitions; verify the action inside the app.",
    }
