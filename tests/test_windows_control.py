from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from trace2task.actions import (
    ActionCall,
    ActionValidationError,
    parameterized_action_schema,
    runtime_text_placeholder,
)
from trace2task.windows_control import (
    KEYEVENTF_KEYUP,
    KEYEVENTF_SCANCODE,
    KEYEVENTF_UNICODE,
    MAPVK_VK_TO_VSC,
    Win32Backend,
    WindowInfo,
    WindowLookupError,
    WindowSafetyError,
    WindowSelector,
    WindowSession,
    WindowsMotorExecutor,
    list_window_records,
    physical_dpi_context,
    probe_window_key,
    probe_window_mouse_button,
)


def window(
    handle: int = 7,
    *,
    title: str = "Trace2Task Target",
    process_name: str = "target.exe",
    minimized: bool = False,
    visible: bool = True,
) -> WindowInfo:
    return WindowInfo(
        handle=handle,
        title=title,
        process_id=100 + handle,
        process_name=process_name,
        client_left=100,
        client_top=200,
        client_width=400,
        client_height=200,
        dpi=144,
        is_visible=visible,
        is_minimized=minimized,
        is_foreground=False,
    )


class FakeWindowsBackend:
    def __init__(self, windows: list[WindowInfo], *, foreground: int = 0) -> None:
        self.windows = {item.handle: item for item in windows}
        self.foreground = foreground
        self.events: list[tuple[Any, ...]] = []
        self.focus_succeeds = True

    def list_windows(self) -> list[WindowInfo]:
        return list(self.windows.values())

    def get_window(self, handle: int) -> WindowInfo | None:
        return self.windows.get(handle)

    def foreground_handle(self) -> int:
        return self.foreground

    def focus_window(self, handle: int) -> bool:
        self.events.append(("focus", handle))
        if self.focus_succeeds:
            self.foreground = handle
        return self.focus_succeeds

    def set_cursor_position(self, x: int, y: int) -> None:
        self.events.append(("cursor", x, y))

    def send_mouse_button(self, button: str, is_down: bool) -> None:
        self.events.append(("mouse", button, is_down))

    def send_key(self, virtual_key: int, is_down: bool) -> None:
        self.events.append(("key", virtual_key, is_down))

    def send_virtual_key(self, virtual_key: int, is_down: bool) -> None:
        self.events.append(("virtual_key", virtual_key, is_down))

    def send_text(self, text: str) -> None:
        self.events.append(("text", text))

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
        self.events.append(
            ("window_mouse", handle, client_x, client_y, button, is_down, double)
        )

    def post_window_mouse_move(
        self,
        handle: int,
        client_x: int,
        client_y: int,
        button: str,
    ) -> None:
        self.events.append(("window_mouse_move", handle, client_x, client_y, button))

    def post_window_key(self, handle: int, virtual_key: int, is_down: bool) -> None:
        self.events.append(("window_key", handle, virtual_key, is_down))

    def post_window_text(self, handle: int, text: str) -> None:
        self.events.append(("window_text", handle, text))

    def send_window_key(
        self,
        handle: int,
        virtual_key: int,
        is_down: bool,
        *,
        timeout_ms: int = 1_000,
    ) -> int:
        self.events.append(("sent_window_key", handle, virtual_key, is_down, timeout_ms))
        return handle + 1


class FakeDpiSetter:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def __call__(self, context: Any) -> object:
        self.calls.append(context)
        return "previous-context"


class FakeDpiApi:
    def __init__(self) -> None:
        self.SetThreadDpiAwarenessContext = FakeDpiSetter()


class FakeScanCodeApi:
    def MapVirtualKeyW(self, virtual_key: int, mode: int) -> int:
        assert mode == MAPVK_VK_TO_VSC
        return {0x46: 0x21}[virtual_key]


def test_physical_dpi_context_switches_and_restores_thread_coordinates() -> None:
    user32 = FakeDpiApi()

    with physical_dpi_context(user32):
        assert len(user32.SetThreadDpiAwarenessContext.calls) == 1

    calls = user32.SetThreadDpiAwarenessContext.calls
    assert calls[0].value is not None
    assert calls[1] == "previous-context"


def test_foreground_keyboard_input_uses_scan_codes_for_game_compatibility() -> None:
    backend = Win32Backend.__new__(Win32Backend)
    backend.user32 = FakeScanCodeApi()
    sent: list[Any] = []
    backend._send_input = sent.append

    backend.send_key(0x46, True)
    backend.send_key(0x46, False)

    assert [(item.ki.wVk, item.ki.wScan, item.ki.dwFlags) for item in sent] == [
        (0, 0x21, KEYEVENTF_SCANCODE),
        (0, 0x21, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP),
    ]


def test_unicode_text_input_emits_utf16_key_pairs() -> None:
    backend = Win32Backend.__new__(Win32Backend)
    sent: list[Any] = []
    backend._send_input = sent.append

    backend.send_text("中😊")

    assert [(item.ki.wVk, item.ki.wScan, item.ki.dwFlags) for item in sent] == [
        (0, 0x4E2D, KEYEVENTF_UNICODE),
        (0, 0x4E2D, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP),
        (0, 0xD83D, KEYEVENTF_UNICODE),
        (0, 0xD83D, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP),
        (0, 0xDE0A, KEYEVENTF_UNICODE),
        (0, 0xDE0A, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP),
    ]


def test_synchronous_input_probe_focuses_holds_and_releases_key() -> None:
    backend = FakeWindowsBackend([window()], foreground=99)
    sleeps: list[float] = []

    result = probe_window_key(
        WindowSelector(handle=7),
        "F",
        method="send-message",
        hold_ms=650,
        settle_seconds=0.25,
        timeout_ms=750,
        backend=backend,
        sleeper=sleeps.append,
    )

    assert result == {
        "method": "send-message",
        "key": "f",
        "virtual_key": "0x46",
        "hold_ms": 650,
        "message_timeout_ms": 750,
        "window_handle": 7,
        "input_target_handle": 8,
        "foreground_verified": True,
        "delivered": True,
        "note": "Delivered means Windows processed both messages; the app may still ignore them.",
    }
    assert backend.events == [
        ("focus", 7),
        ("sent_window_key", 7, 0x46, True, 750),
        ("sent_window_key", 7, 0x46, False, 750),
    ]
    assert sleeps == [0.25, 0.65]


def test_virtual_key_input_probe_uses_foreground_send_input_path() -> None:
    backend = FakeWindowsBackend([window()], foreground=7)
    sleeps: list[float] = []

    result = probe_window_key(
        WindowSelector(handle=7),
        "f",
        method="send-input-vk",
        hold_ms=400,
        settle_seconds=0,
        backend=backend,
        sleeper=sleeps.append,
    )

    assert result["method"] == "send-input-vk"
    assert result["input_target_handle"] == 7
    assert result["delivered"] is True
    assert backend.events == [
        ("focus", 7),
        ("virtual_key", 0x46, True),
        ("virtual_key", 0x46, False),
    ]
    assert sleeps == [0.4]


def test_mouse_input_probe_focuses_and_releases_button() -> None:
    backend = FakeWindowsBackend([window()], foreground=99)
    sleeps: list[float] = []

    result = probe_window_mouse_button(
        WindowSelector(handle=7),
        "middle",
        hold_ms=150,
        settle_seconds=0.5,
        backend=backend,
        sleeper=sleeps.append,
    )

    assert result == {
        "method": "send-input-mouse",
        "button": "middle",
        "hold_ms": 150,
        "input_target_handle": 7,
        "foreground_verified": True,
        "delivered": True,
        "note": "SendInput accepted both transitions; verify the action inside the app.",
    }
    assert backend.events == [
        ("focus", 7),
        ("mouse", "middle", True),
        ("mouse", "middle", False),
    ]
    assert sleeps == [0.5, 0.15]


def test_parameterized_actions_are_normalized_and_strict() -> None:
    hold = ActionCall.from_payload(
        {"skill": "hold_key", "args": {"key": "W", "duration_ms": 420}}
    )
    click = ActionCall("click", {"x": 1, "y": 0.25})
    drag = ActionCall(
        "drag",
        {
            "start_x": 0.1,
            "start_y": 0.2,
            "end_x": 0.8,
            "end_y": 0.9,
            "duration_ms": 650,
        },
    )
    text = ActionCall("type_text", {"text": "给文件传输助手发送：测试😊"})

    assert hold.to_payload() == {
        "skill": "hold_key",
        "args": {"key": "w", "duration_ms": 420},
    }
    assert click.args == {"x": 1.0, "y": 0.25, "button": "left"}
    assert drag.args["button"] == "left"
    assert text.args == {"text": "给文件传输助手发送：测试😊"}

    with pytest.raises(ActionValidationError, match="exactly"):
        ActionCall.from_payload({"skill": "wait", "args": {}, "reason": "extra"})
    with pytest.raises(ActionValidationError, match="between 0 and 1"):
        ActionCall("click", {"x": 1.1, "y": 0.5})
    with pytest.raises(ActionValidationError, match="between 1 and 5000"):
        ActionCall("hold_key", {"key": "w", "duration_ms": 5_001})
    with pytest.raises(ActionValidationError, match="must be unique"):
        ActionCall("hotkey", {"keys": ["ctrl", "CTRL"]})
    with pytest.raises(ActionValidationError, match="Unsupported keyboard key"):
        ActionCall("press_key", {"key": "volume_up"})
    with pytest.raises(ActionValidationError, match="between 1 and 5000"):
        ActionCall(
            "drag",
            {
                "start_x": 0.1,
                "start_y": 0.2,
                "end_x": 0.8,
                "end_y": 0.9,
                "duration_ms": 5_001,
            },
        )
    with pytest.raises(ActionValidationError, match="control characters"):
        ActionCall("type_text", {"text": "第一行\n第二行"})


def test_action_schema_can_be_limited_to_task_allowed_skills() -> None:
    schema = parameterized_action_schema(("click", "wait"))

    assert [entry["properties"]["skill"]["enum"][0] for entry in schema["anyOf"]] == [
        "click",
        "wait",
    ]
    click_schema = schema["anyOf"][0]["properties"]["args"]
    assert click_schema["properties"]["x"] == {
        "type": "number",
        "minimum": 0,
        "maximum": 1,
    }
    assert click_schema["required"] == ["x", "y", "button"]
    assert click_schema["additionalProperties"] is False


def test_selector_requires_one_unambiguous_window() -> None:
    backend = FakeWindowsBackend(
        [
            window(7, title="Game - One"),
            window(8, title="Game - Two"),
        ]
    )

    with pytest.raises(WindowLookupError, match="ambiguous"):
        WindowSession(WindowSelector(process_name="target.exe"), backend).resolve()
    with pytest.raises(WindowLookupError, match="No window"):
        WindowSession(WindowSelector(title_contains="Missing"), backend).resolve()

    selected = WindowSession(WindowSelector(title_contains="One"), backend).resolve()
    assert selected.handle == 7


def test_session_re_resolves_window_after_handle_changes() -> None:
    backend = FakeWindowsBackend([window(7)], foreground=7)
    session = WindowSession(WindowSelector(title_contains="Trace2Task"), backend)
    assert session.resolve().handle == 7

    del backend.windows[7]
    backend.windows[9] = window(9)

    assert session.resolve().handle == 9


def test_executor_focuses_then_maps_normalized_click_inside_client_area() -> None:
    backend = FakeWindowsBackend([window()], foreground=99)
    sleeps: list[float] = []
    executor = WindowsMotorExecutor(
        WindowSession(WindowSelector(handle=7), backend),
        sleeper=sleeps.append,
    )

    focus_result = executor.execute(ActionCall("focus_window", {}))
    click_result = executor.execute(ActionCall("click", {"x": 0.5, "y": 0.25}))

    assert focus_result.window_handle == 7
    assert click_result.screen_position == (300, 250)
    assert backend.events == [
        ("focus", 7),
        ("cursor", 300, 250),
        ("mouse", "left", True),
        ("mouse", "left", False),
    ]
    assert sleeps == [0.02]


def test_session_allows_manual_focus_during_bounded_wait() -> None:
    backend = FakeWindowsBackend([window()], foreground=99)
    backend.focus_succeeds = False
    sleeps: list[float] = []

    def switch_manually(seconds: float) -> None:
        sleeps.append(seconds)
        backend.foreground = 7

    focused = WindowSession(WindowSelector(handle=7), backend).focus(
        timeout_seconds=1,
        sleeper=switch_manually,
    )

    assert focused.handle == 7
    assert sleeps and sleeps[0] <= 0.05
    assert backend.events == [("focus", 7)]


def test_lost_focus_or_minimized_window_blocks_input() -> None:
    backend = FakeWindowsBackend([window()], foreground=99)
    executor = WindowsMotorExecutor(WindowSession(WindowSelector(handle=7), backend))

    with pytest.raises(WindowSafetyError, match="lost focus"):
        executor.execute(ActionCall("press_key", {"key": "w"}))
    assert backend.events == []

    backend.windows[7] = replace(window(), is_minimized=True)
    with pytest.raises(WindowSafetyError, match="minimized"):
        executor.execute(ActionCall("focus_window", {}))
    assert backend.events == []


def test_background_executor_targets_window_without_focus_or_cursor_movement() -> None:
    backend = FakeWindowsBackend([window()], foreground=99)
    sleeps: list[float] = []
    executor = WindowsMotorExecutor(
        WindowSession(WindowSelector(handle=7), backend),
        sleeper=sleeps.append,
        background=True,
    )

    focus_result = executor.execute(ActionCall("focus_window", {}))
    click_result = executor.execute(ActionCall("click", {"x": 0.5, "y": 0.25}))
    executor.execute(ActionCall("press_key", {"key": "w"}))

    assert focus_result.input_mode == "background"
    assert click_result.screen_position == (300, 250)
    assert click_result.input_mode == "background"
    assert backend.foreground == 99
    assert backend.events == [
        ("window_mouse", 7, 200, 50, "left", True, False),
        ("window_mouse", 7, 200, 50, "left", False, False),
        ("window_key", 7, 0x57, True),
        ("window_key", 7, 0x57, False),
    ]
    assert sleeps == [0.02, 0.02]


def test_background_executor_still_rejects_minimized_target() -> None:
    backend = FakeWindowsBackend([window(minimized=True)], foreground=99)
    executor = WindowsMotorExecutor(
        WindowSession(WindowSelector(handle=7), backend),
        background=True,
    )

    with pytest.raises(WindowSafetyError, match="minimized"):
        executor.execute(ActionCall("press_key", {"key": "w"}))
    assert backend.events == []


def test_keyboard_skills_release_every_pressed_key() -> None:
    backend = FakeWindowsBackend([window()], foreground=7)
    sleeps: list[float] = []
    executor = WindowsMotorExecutor(
        WindowSession(WindowSelector(handle=7), backend),
        sleeper=sleeps.append,
    )

    executor.execute(ActionCall("press_key", {"key": "w"}))
    executor.execute(ActionCall("hold_key", {"key": "s", "duration_ms": 250}))
    executor.execute(ActionCall("hotkey", {"keys": ["ctrl", "shift", "a"]}))
    executor.execute(ActionCall("type_text", {"text": "微信测试"}))

    assert backend.events == [
        ("key", 0x57, True),
        ("key", 0x57, False),
        ("key", 0x53, True),
        ("key", 0x53, False),
        ("key", 0x11, True),
        ("key", 0x10, True),
        ("key", 0x41, True),
        ("key", 0x41, False),
        ("key", 0x10, False),
        ("key", 0x11, False),
        ("text", "微信测试"),
    ]
    assert sleeps == [0.02, 0.25]


def test_double_click_and_wait_use_bounded_local_timing() -> None:
    backend = FakeWindowsBackend([window()], foreground=7)
    sleeps: list[float] = []
    executor = WindowsMotorExecutor(
        WindowSession(WindowSelector(handle=7), backend),
        sleeper=sleeps.append,
    )

    executor.execute(ActionCall("double_click", {"x": 1, "y": 1, "button": "right"}))
    executor.execute(ActionCall("wait", {"duration_ms": 300}))

    assert backend.events == [
        ("cursor", 499, 399),
        ("mouse", "right", True),
        ("mouse", "right", False),
        ("mouse", "right", True),
        ("mouse", "right", False),
    ]
    assert sleeps == [0.02, 0.08, 0.02, 0.3]


def test_drag_interpolates_pointer_and_always_releases_button() -> None:
    backend = FakeWindowsBackend([window()], foreground=7)
    sleeps: list[float] = []
    executor = WindowsMotorExecutor(
        WindowSession(WindowSelector(handle=7), backend),
        sleeper=sleeps.append,
    )

    result = executor.execute(
        ActionCall(
            "drag",
            {
                "start_x": 0.25,
                "start_y": 0.25,
                "end_x": 0.75,
                "end_y": 0.75,
                "duration_ms": 48,
                "button": "left",
            },
        )
    )

    assert result.screen_position == (400, 350)
    assert backend.events[0:2] == [("cursor", 200, 250), ("mouse", "left", True)]
    assert backend.events[-2:] == [("cursor", 400, 350), ("mouse", "left", False)]
    assert len([event for event in backend.events if event[0] == "cursor"]) == 4
    assert sleeps == [0.016, 0.016, 0.016]


def test_background_drag_posts_held_mouse_moves_to_target() -> None:
    backend = FakeWindowsBackend([window()], foreground=99)
    executor = WindowsMotorExecutor(
        WindowSession(WindowSelector(handle=7), backend),
        sleeper=lambda _: None,
        background=True,
    )

    executor.execute(
        ActionCall(
            "drag",
            {
                "start_x": 0.25,
                "start_y": 0.25,
                "end_x": 0.75,
                "end_y": 0.75,
                "duration_ms": 16,
            },
        )
    )

    assert backend.events == [
        ("window_mouse", 7, 100, 50, "left", True, False),
        ("window_mouse_move", 7, 300, 150, "left"),
        ("window_mouse", 7, 300, 150, "left", False, False),
    ]


def test_background_text_targets_window_without_clipboard() -> None:
    backend = FakeWindowsBackend([window()], foreground=99)
    executor = WindowsMotorExecutor(
        WindowSession(WindowSelector(handle=7), backend),
        background=True,
    )

    executor.execute(ActionCall("type_text", {"text": "后台文本"}))

    assert backend.events == [("window_text", 7, "后台文本")]


def test_executor_refuses_unresolved_runtime_text_marker() -> None:
    backend = FakeWindowsBackend([window()], foreground=7)
    executor = WindowsMotorExecutor(WindowSession(WindowSelector(handle=7), backend))

    with pytest.raises(WindowSafetyError, match="must be resolved"):
        executor.execute(
            ActionCall("type_text", {"text": runtime_text_placeholder(1)})
        )

    assert backend.events == []


def test_interrupted_drag_still_releases_mouse_button() -> None:
    backend = FakeWindowsBackend([window()], foreground=7)

    def interrupt(_: float) -> None:
        raise RuntimeError("interrupted")

    executor = WindowsMotorExecutor(
        WindowSession(WindowSelector(handle=7), backend),
        sleeper=interrupt,
    )

    with pytest.raises(RuntimeError, match="interrupted"):
        executor.execute(
            ActionCall(
                "drag",
                {
                    "start_x": 0.25,
                    "start_y": 0.25,
                    "end_x": 0.75,
                    "end_y": 0.75,
                    "duration_ms": 100,
                },
            )
        )

    assert backend.events == [
        ("cursor", 200, 250),
        ("mouse", "left", True),
        ("mouse", "left", False),
    ]


def test_interrupted_hold_still_releases_key() -> None:
    backend = FakeWindowsBackend([window()], foreground=7)

    def interrupt(_: float) -> None:
        raise RuntimeError("interrupted")

    executor = WindowsMotorExecutor(
        WindowSession(WindowSelector(handle=7), backend),
        sleeper=interrupt,
    )

    with pytest.raises(RuntimeError, match="interrupted"):
        executor.execute(ActionCall("hold_key", {"key": "w", "duration_ms": 200}))
    assert backend.events == [
        ("key", 0x57, True),
        ("key", 0x57, False),
    ]


def test_executor_specific_duration_limit_blocks_before_key_down() -> None:
    backend = FakeWindowsBackend([window()], foreground=7)
    executor = WindowsMotorExecutor(
        WindowSession(WindowSelector(handle=7), backend),
        max_hold_ms=300,
    )

    with pytest.raises(WindowSafetyError, match="exceeds executor limit"):
        executor.execute(ActionCall("hold_key", {"key": "w", "duration_ms": 500}))
    assert backend.events == []


def test_window_listing_filters_without_sending_input() -> None:
    backend = FakeWindowsBackend(
        [
            window(7, title="Trace2Task Target", process_name="target.exe"),
            window(8, title="Notes", process_name="notepad.exe"),
        ],
        foreground=7,
    )

    result = list_window_records(
        title_contains="trace2task",
        process_name="TARGET",
        backend=backend,
    )

    assert result["count"] == 1
    assert result["windows"][0]["handle"] == 7
    assert result["windows"][0]["dpi"] == 144
    assert backend.events == []
