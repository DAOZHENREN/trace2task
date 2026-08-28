from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pygame

from trace2task.windows_capture import capture_window_once
from trace2task.windows_control import WindowInfo, WindowSelector, WindowSession
from trace2task.windows_recording import InputSnapshot, WindowRecorder


def window(handle: int = 7) -> WindowInfo:
    return WindowInfo(
        handle=handle,
        title="Trace2Task External Target",
        process_id=207,
        process_name="target.exe",
        client_left=100,
        client_top=200,
        client_width=400,
        client_height=200,
        dpi=144,
        is_visible=True,
        is_minimized=False,
        is_foreground=True,
    )


class FakeBackend:
    def __init__(self, target: WindowInfo) -> None:
        self.target = target
        self.foreground = 0
        self.events: list[tuple[Any, ...]] = []
        self.focus_succeeds = True

    def list_windows(self) -> list[WindowInfo]:
        return [self.target]

    def get_window(self, handle: int) -> WindowInfo | None:
        return self.target if handle == self.target.handle else None

    def foreground_handle(self) -> int:
        return self.foreground

    def focus_window(self, handle: int) -> bool:
        self.events.append(("focus", handle))
        if self.focus_succeeds:
            self.foreground = handle
        return self.focus_succeeds

    def set_cursor_position(self, x: int, y: int) -> None:
        raise AssertionError("recorder must not move the cursor")

    def send_mouse_button(self, button: str, is_down: bool) -> None:
        raise AssertionError("recorder must not send mouse input")

    def send_key(self, virtual_key: int, is_down: bool) -> None:
        raise AssertionError("recorder must not send keyboard input")


class FakeCapture:
    def __init__(self) -> None:
        self.windows: list[WindowInfo] = []

    def capture(self, target: WindowInfo) -> pygame.Surface:
        self.windows.append(target)
        surface = pygame.Surface((target.client_width, target.client_height))
        surface.fill((30, 80, 140))
        return surface


class SequenceMonitor:
    def __init__(
        self,
        snapshots: list[InputSnapshot],
        *,
        on_poll: dict[int, Callable[[], None]] | None = None,
    ) -> None:
        self.snapshots = iter(snapshots)
        self.on_poll = on_poll or {}
        self.poll_count = 0
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def poll(self) -> InputSnapshot:
        callback = self.on_poll.get(self.poll_count)
        if callback:
            callback()
        self.poll_count += 1
        return next(self.snapshots)

    def close(self) -> None:
        self.closed = True


class StepClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        self.value += 0.001
        return self.value


def snapshot(
    *,
    keys: set[str] | None = None,
    buttons: set[str] | None = None,
    cursor: tuple[int, int] = (300, 250),
    success: bool = False,
    cancel: bool = False,
) -> InputSnapshot:
    return InputSnapshot(
        keys_down=frozenset(keys or set()),
        buttons_down=frozenset(buttons or set()),
        cursor_position=cursor,
        success_requested=success,
        cancel_requested=cancel,
    )


def trace_events(trace_path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_window_recorder_saves_raw_input_transitions_and_target_frames(tmp_path: Path) -> None:
    backend = FakeBackend(window())
    capture = FakeCapture()
    monitor = SequenceMonitor(
        [
            snapshot(),
            snapshot(keys={"w"}),
            snapshot(),
            snapshot(buttons={"left"}),
            snapshot(),
            snapshot(success=True),
        ]
    )
    statuses: list[str] = []
    recorder = WindowRecorder(
        WindowSession(WindowSelector(handle=7), backend),
        capture,
        monitor,
        clock=StepClock(),
        sleeper=lambda _: None,
        status_callback=statuses.append,
    )

    result = recorder.record(task_id="external-daily", output_root=tmp_path)
    events = trace_events(Path(result.trace_path))
    metadata = json.loads(
        (Path(result.trace_path).parent / "metadata.json").read_text(encoding="utf-8")
    )

    assert result.success
    assert result.stop_reason == "success_marked"
    assert result.input_events == 4
    assert [event["type"] for event in events] == [
        "start",
        "windows_input",
        "windows_input",
        "windows_input",
        "windows_input",
        "success_marker",
    ]
    assert [event["details"]["raw_input"] for event in events[1:5]] == [
        {"device": "keyboard", "event": "down", "key": "w"},
        {"device": "keyboard", "event": "up", "key": "w"},
        {
            "device": "mouse",
            "event": "down",
            "button": "left",
            "screen_position": [300, 250],
            "normalized_position": [0.5, 0.25],
            "inside_target": True,
        },
        {
            "device": "mouse",
            "event": "up",
            "button": "left",
            "screen_position": [300, 250],
            "normalized_position": [0.5, 0.25],
            "inside_target": True,
        },
    ]
    assert metadata["success"] is True
    assert metadata["input_event_count"] == 4
    assert metadata["capture_method"] == "target_client_area"
    assert metadata["coordinate_space"] == "physical_pixels"
    assert metadata["success_hotkey"] == "f8"
    assert events[0]["details"]["coordinate_space"] == "physical_pixels"
    assert len(list((Path(result.trace_path).parent / "frames").glob("*.png"))) == 6
    assert len(capture.windows) == 6
    assert monitor.started and monitor.closed
    assert backend.events == [("focus", 7)]
    assert "Press F8" in statuses[0]


def test_recorder_pauses_on_focus_loss_and_does_not_capture_other_app_input(
    tmp_path: Path,
) -> None:
    backend = FakeBackend(window())
    capture = FakeCapture()

    def lose_focus() -> None:
        backend.foreground = 99

    def restore_focus() -> None:
        backend.foreground = 7

    monitor = SequenceMonitor(
        [
            snapshot(),
            snapshot(keys={"x"}),
            snapshot(),
            snapshot(),
            snapshot(success=True),
        ],
        on_poll={1: lose_focus, 3: restore_focus},
    )
    statuses: list[str] = []
    recorder = WindowRecorder(
        WindowSession(WindowSelector(handle=7), backend),
        capture,
        monitor,
        clock=StepClock(),
        sleeper=lambda _: None,
        status_callback=statuses.append,
    )

    result = recorder.record(task_id="focus-test", output_root=tmp_path)
    events = trace_events(Path(result.trace_path))

    assert result.success
    assert result.focus_losses == 1
    assert result.input_events == 0
    assert [event["type"] for event in events] == ["start", "success_marker"]
    assert any("paused" in status for status in statuses)
    assert any("resumed" in status for status in statuses)


def test_cancel_hotkey_finishes_unsuccessful_recording(tmp_path: Path) -> None:
    backend = FakeBackend(window())
    monitor = SequenceMonitor([snapshot(), snapshot(cancel=True)])
    recorder = WindowRecorder(
        WindowSession(WindowSelector(handle=7), backend),
        FakeCapture(),
        monitor,
        clock=StepClock(),
        sleeper=lambda _: None,
        status_callback=lambda _: None,
    )

    result = recorder.record(task_id="cancel-test", output_root=tmp_path)
    metadata = json.loads(
        (Path(result.trace_path).parent / "metadata.json").read_text(encoding="utf-8")
    )

    assert not result.success
    assert result.stop_reason == "cancelled"
    assert metadata["success"] is False
    assert monitor.closed


def test_cancel_while_waiting_for_manual_focus_releases_hotkeys(tmp_path: Path) -> None:
    backend = FakeBackend(window())
    backend.focus_succeeds = False
    monitor = SequenceMonitor([snapshot(cancel=True)])
    statuses: list[str] = []
    recorder = WindowRecorder(
        WindowSession(WindowSelector(handle=7), backend),
        FakeCapture(),
        monitor,
        clock=StepClock(),
        sleeper=lambda _: None,
        status_callback=statuses.append,
    )

    try:
        recorder.record(task_id="waiting-test", output_root=tmp_path)
    except RuntimeError as error:
        assert "cancelled before" in str(error)
    else:
        raise AssertionError("recorder should stop before the target becomes active")

    assert monitor.closed
    assert backend.events == [("focus", 7)]
    assert "Switch to" in statuses[0]
    assert not list(tmp_path.glob("*-windows-human"))


def test_capture_once_saves_only_fake_target_surface(tmp_path: Path) -> None:
    backend = FakeBackend(window())
    capture = FakeCapture()
    output = tmp_path / "captures" / "target.png"

    result = capture_window_once(
        WindowSelector(handle=7),
        output,
        backend=backend,
        capture=capture,
    )

    loaded = pygame.image.load(output)
    assert result.output_path == str(output.resolve())
    assert result.size == (400, 200)
    assert loaded.get_size() == (400, 200)
    assert backend.events == []


def test_capture_once_can_focus_target_before_capture(tmp_path: Path) -> None:
    backend = FakeBackend(window())
    capture = FakeCapture()
    output = tmp_path / "captures" / "focused-target.png"

    capture_window_once(
        WindowSelector(handle=7),
        output,
        backend=backend,
        capture=capture,
        focus=True,
    )

    assert output.is_file()
    assert backend.events == [("focus", 7)]


def test_mouse_event_outside_target_is_marked_without_normalized_coordinates() -> None:
    raw_event = WindowRecorder._mouse_event("down", "left", (10, 20), window())

    assert raw_event["inside_target"] is False
    assert raw_event["normalized_position"] is None
