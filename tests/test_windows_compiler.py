from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pygame
import pytest
import yaml

from trace2task.compiler import compile_trace
from trace2task.taskpack import load_taskpack


def _keyboard(event: str, key: str) -> dict[str, Any]:
    return {"device": "keyboard", "event": event, "key": key}


def _mouse(
    event: str,
    *,
    position: tuple[float, float] = (0.4, 0.5),
) -> dict[str, Any]:
    return {
        "device": "mouse",
        "event": event,
        "button": "left",
        "screen_position": [140, 250],
        "normalized_position": list(position),
        "inside_target": True,
    }


def _write_windows_trace(tmp_path: Path) -> tuple[Path, list[dict[str, Any]], dict[str, Any]]:
    run_dir = tmp_path / "windows-source"
    frames_dir = run_dir / "frames"
    frames_dir.mkdir(parents=True)
    timeline: list[tuple[float, str, dict[str, Any] | None]] = [
        (0, "start", None),
        (100, "windows_input", _keyboard("down", "ctrl")),
        (120, "windows_input", _keyboard("down", "c")),
        (180, "windows_input", _keyboard("up", "c")),
        (200, "windows_input", _keyboard("up", "ctrl")),
        (500, "windows_input", _keyboard("down", "w")),
        (1200, "windows_input", _keyboard("up", "w")),
        (2000, "windows_input", _mouse("down")),
        (2050, "windows_input", _mouse("up")),
        (2200, "windows_input", _mouse("down", position=(0.402, 0.501))),
        (2250, "windows_input", _mouse("up", position=(0.402, 0.501))),
        (4000, "windows_input", _keyboard("down", "enter")),
        (4070, "windows_input", _keyboard("up", "enter")),
        (4500, "success_marker", None),
    ]
    events: list[dict[str, Any]] = []
    for seq, (elapsed_ms, event_type, raw_input) in enumerate(timeline):
        frame = f"frames/{seq:04d}.png"
        surface = pygame.Surface((100, 50))
        surface.fill((20 + seq, 50, 90))
        pygame.image.save(surface, run_dir / frame)
        event: dict[str, Any] = {
            "seq": seq,
            "elapsed_ms": elapsed_ms,
            "type": event_type,
            "frame": frame,
        }
        if raw_input is not None:
            event["details"] = {"raw_input": raw_input}
        events.append(event)

    metadata: dict[str, Any] = {
        "schema_version": "0.1",
        "task_id": "copy-and-open",
        "seed": 0,
        "source": "windows_human",
        "success": True,
        "event_count": len(events),
        "action_count": 0,
        "input_event_count": 12,
        "focus_losses": 0,
        "stop_reason": "success_marked",
        "coordinate_space": "physical_pixels",
        "window_selector": {"handle": 7, "title_contains": None, "process_name": None},
        "initial_window": {
            "handle": 7,
            "title": "Trace2Task External Target",
            "process_id": 207,
            "process_name": "target.exe",
            "client_left": 100,
            "client_top": 200,
            "client_width": 100,
            "client_height": 50,
            "dpi": 120,
            "is_visible": True,
            "is_minimized": False,
            "is_foreground": True,
        },
    }
    trace_path = run_dir / "trace.jsonl"
    _rewrite_bundle(trace_path, events, metadata)
    return trace_path, events, metadata


def _rewrite_bundle(
    trace_path: Path,
    events: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> None:
    trace_path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    (trace_path.parent / "metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="utf-8",
    )


def test_windows_compiler_infers_parameterized_actions_and_review_bundle(
    tmp_path: Path,
) -> None:
    trace_path, _, _ = _write_windows_trace(tmp_path)
    (trace_path.parent / "narration.json").write_text(
        json.dumps(
            {
                "transcript": "这是人工讲解",
                "segments": [],
                "audio": {
                    "path": "narration.webm",
                    "mime_type": "audio/webm",
                    "bytes": 5,
                },
            }
        ),
        encoding="utf-8",
    )
    (trace_path.parent / "narration.webm").write_bytes(b"audio")

    result = compile_trace(trace_path, tmp_path / "compiled")
    task_path = Path(result.task_path)
    task = load_taskpack(task_path)
    task_yaml = yaml.safe_load(task_path.read_text(encoding="utf-8"))
    demonstration = json.loads(
        (task_path.parent / "demonstration.json").read_text(encoding="utf-8")
    )
    report = json.loads(Path(result.report_path).read_text(encoding="utf-8"))
    actions = [item["action"] for item in demonstration["actions"]]

    assert task.environment_adapter == "trace2task.windows"
    assert task.actions == (
        "focus_window",
        "hotkey",
        "hold_key",
        "wait",
        "double_click",
        "press_key",
    )
    assert task.review_status == "draft"
    assert result.demonstration_actions == 7
    assert result.stages == 2
    assert actions == [
        {"skill": "focus_window", "args": {}},
        {"skill": "hotkey", "args": {"keys": ["ctrl", "c"]}},
        {"skill": "hold_key", "args": {"key": "w", "duration_ms": 700}},
        {"skill": "wait", "args": {"duration_ms": 800}},
        {
            "skill": "double_click",
            "args": {"x": 0.4, "y": 0.5, "button": "left"},
        },
        {"skill": "wait", "args": {"duration_ms": 1750}},
        {"skill": "press_key", "args": {"key": "enter"}},
    ]
    assert task_yaml["environment"]["target"] == {
        "process_name": "target.exe",
        "title_contains": "Trace2Task External Target",
        "recorded_handle": 7,
    }
    assert task_yaml["observation"]["coordinate_space"] == "physical_pixels"
    assert task_yaml["experience"] == {
        "intent": task.task_id,
        "examples": [task.task_id],
        "family_id": task.task_id,
        "source": "human_trace",
    }
    assert report["inference"]["action_counts"]["wait"] == 2
    assert report["inference"]["verifier"]["success_source"] == "human F8 marker"
    assert (task_path.parent / "reference" / "trace.jsonl").is_file()
    assert (task_path.parent / "reference" / "narration.json").is_file()
    compiled_narration = json.loads(
        (task_path.parent / "reference" / "narration.json").read_text(encoding="utf-8")
    )
    assert compiled_narration["audio"] is None
    assert compiled_narration["audio_archived_with_source_trace"] is True
    assert not (task_path.parent / "reference" / "narration.webm").exists()
    assert len(list((task_path.parent / "reference" / "frames").glob("*.png"))) == 14


def test_windows_compiler_declares_messaging_generalization_capabilities(
    tmp_path: Path,
) -> None:
    trace_path, events, metadata = _write_windows_trace(tmp_path)
    metadata["capability_profile"] = "messaging"
    events[11]["elapsed_ms"] = 700
    events[11]["details"]["raw_input"]["key"] = "e"
    events[12]["elapsed_ms"] = 800
    events[12]["details"]["raw_input"]["key"] = "e"
    start, success = events[0], events[-1]
    inputs = sorted(events[1:-1], key=lambda event: event["elapsed_ms"])
    events = [start, *inputs, success]
    for seq, event in enumerate(events):
        event["seq"] = seq
    _rewrite_bundle(trace_path, events, metadata)

    result = compile_trace(trace_path, tmp_path / "compiled")
    task = load_taskpack(Path(result.task_path))
    report = json.loads(Path(result.report_path).read_text(encoding="utf-8"))
    demonstration = json.loads(
        (Path(result.task_path).parent / "demonstration.json").read_text(
            encoding="utf-8"
        )
    )
    text_actions = [
        item["action"]
        for item in demonstration["actions"]
        if item["action"]["skill"] == "type_text"
    ]

    assert {"type_text", "press_key", "hotkey"}.issubset(task.actions)
    assert {"type_text", "press_key", "hotkey"}.issubset(
        report["inference"]["declared_actions"]
    )
    assert report["inference"]["capability_profile"] == "messaging"
    assert "type_text" in report["inference"]["observed_actions"]
    assert report["inference"]["runtime_text_bursts"] == 1
    assert text_actions == [
        {"skill": "type_text", "args": {"text": "<runtime-text-1>"}}
    ]


def test_windows_compiler_rejects_pre_dpi_fix_recording(tmp_path: Path) -> None:
    trace_path, events, metadata = _write_windows_trace(tmp_path)
    metadata.pop("coordinate_space")
    _rewrite_bundle(trace_path, events, metadata)

    with pytest.raises(ValueError, match="physical-pixel"):
        compile_trace(trace_path, tmp_path / "compiled")


def test_windows_compiler_rejects_unreleased_key(tmp_path: Path) -> None:
    trace_path, events, metadata = _write_windows_trace(tmp_path)
    events.pop(12)  # Remove Enter key-up while retaining the final success marker.
    for seq, event in enumerate(events):
        event["seq"] = seq
    metadata["event_count"] = len(events)
    metadata["input_event_count"] = 11
    _rewrite_bundle(trace_path, events, metadata)

    with pytest.raises(ValueError, match="unreleased input"):
        compile_trace(trace_path, tmp_path / "compiled")


def test_windows_compiler_ignores_unmatched_release_at_focus_boundary(
    tmp_path: Path,
) -> None:
    trace_path, events, metadata = _write_windows_trace(tmp_path)
    orphan = {
        **events[8],
        "seq": 1,
        "elapsed_ms": 50,
        "details": {"raw_input": _mouse("up", position=(0.8, 0.4))},
    }
    events.insert(1, orphan)
    for seq, event in enumerate(events):
        event["seq"] = seq
    metadata["event_count"] = len(events)
    metadata["input_event_count"] = 13
    _rewrite_bundle(trace_path, events, metadata)

    result = compile_trace(trace_path, tmp_path / "compiled")
    report = json.loads(Path(result.report_path).read_text(encoding="utf-8"))

    assert result.demonstration_actions == 7
    assert report["inference"]["ignored_unmatched_releases"] == [
        {
            "seq": 1,
            "elapsed_ms": 50.0,
            "device": "mouse",
            "name": "left",
            "reason": "release_without_recorded_press_at_focus_boundary",
        }
    ]


def test_windows_compiler_infers_bounded_drag_motor_skill(tmp_path: Path) -> None:
    trace_path, events, metadata = _write_windows_trace(tmp_path)
    events[8]["details"]["raw_input"]["normalized_position"] = [0.8, 0.8]
    _rewrite_bundle(trace_path, events, metadata)

    result = compile_trace(trace_path, tmp_path / "compiled")
    demonstration = json.loads(
        (Path(result.task_path).parent / "demonstration.json").read_text(encoding="utf-8")
    )
    actions = [item["action"] for item in demonstration["actions"]]

    assert {
        "skill": "drag",
        "args": {
            "start_x": 0.4,
            "start_y": 0.5,
            "end_x": 0.8,
            "end_y": 0.8,
            "duration_ms": 50,
            "button": "left",
        },
    } in actions


def test_windows_compiler_preserves_stationary_mouse_hold(tmp_path: Path) -> None:
    trace_path, events, metadata = _write_windows_trace(tmp_path)
    events[8]["elapsed_ms"] = 3150
    events[9]["elapsed_ms"] = 3300
    events[10]["elapsed_ms"] = 3350
    _rewrite_bundle(trace_path, events, metadata)

    result = compile_trace(trace_path, tmp_path / "compiled")
    demonstration = json.loads(
        (Path(result.task_path).parent / "demonstration.json").read_text(encoding="utf-8")
    )
    actions = [item["action"] for item in demonstration["actions"]]

    assert {
        "skill": "hold_mouse",
        "args": {
            "x": 0.4,
            "y": 0.5,
            "duration_ms": 1_150,
            "button": "left",
        },
    } in actions


def test_windows_compiler_rejects_concurrent_non_modifier_holds(tmp_path: Path) -> None:
    trace_path, events, metadata = _write_windows_trace(tmp_path)
    events[6]["elapsed_ms"] = 900
    events[11]["elapsed_ms"] = 700
    events[12]["elapsed_ms"] = 800
    start, success = events[0], events[-1]
    inputs = sorted(events[1:-1], key=lambda event: event["elapsed_ms"])
    events = [start, *inputs, success]
    for seq, event in enumerate(events):
        event["seq"] = seq
    _rewrite_bundle(trace_path, events, metadata)

    with pytest.raises(ValueError, match="Concurrent non-modifier keys"):
        compile_trace(trace_path, tmp_path / "compiled")
