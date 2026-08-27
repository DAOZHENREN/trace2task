from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from trace2task.actions import (
    ActionCall,
    ActionValidationError,
    parameterized_action_schema,
)
from trace2task.windows_control import (
    WindowInfo,
    WindowLookupError,
    WindowSafetyError,
    WindowSelector,
    WindowSession,
    WindowsMotorExecutor,
    list_window_records,
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


def test_parameterized_actions_are_normalized_and_strict() -> None:
    hold = ActionCall.from_payload(
        {"skill": "hold_key", "args": {"key": "W", "duration_ms": 420}}
    )
    click = ActionCall("click", {"x": 1, "y": 0.25})

    assert hold.to_payload() == {
        "skill": "hold_key",
        "args": {"key": "w", "duration_ms": 420},
    }
    assert click.args == {"x": 1.0, "y": 0.25, "button": "left"}

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


def test_action_schema_can_be_limited_to_task_allowed_skills() -> None:
    schema = parameterized_action_schema(("click", "wait"))

    assert [entry["properties"]["skill"]["const"] for entry in schema["oneOf"]] == [
        "click",
        "wait",
    ]
    click_schema = schema["oneOf"][0]["properties"]["args"]
    assert click_schema["properties"]["x"] == {
        "type": "number",
        "minimum": 0,
        "maximum": 1,
    }
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
