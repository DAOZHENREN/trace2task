from __future__ import annotations

import json
import math
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from trace2task import __version__
from trace2task.actions import ActionCall, runtime_text_placeholder
from trace2task.recording import make_run_dir
from trace2task.taskpack import load_taskpack

WINDOWS_ADAPTER = "trace2task.windows"
WINDOWS_INSTRUCTION = (
    "Repeat the reviewed workflow in the selected Windows application and reach the successful "
    "state shown in the final reference frame."
)
KEY_HOLD_THRESHOLD_MS = 300
WAIT_THRESHOLD_MS = 500
MAX_COMPILED_WAIT_MS = 10_000
MAX_KEY_HOLD_MS = 5_000
MAX_MOUSE_CLICK_MS = 1_000
MAX_MOUSE_HOLD_MS = 5_000
MAX_MOUSE_DRAG_MS = 5_000
DOUBLE_CLICK_GAP_MS = 500
POINTER_JITTER_TOLERANCE = 0.02
MODIFIER_KEYS = {"alt", "ctrl", "shift"}
COMMAND_MODIFIER_KEYS = {"alt", "ctrl"}
TEXT_ENTRY_CAPABILITY_PROFILES = {"messaging", "text_entry"}
MESSAGING_TEXT_KEYS = {
    *"abcdefghijklmnopqrstuvwxyz0123456789",
    "backspace",
    "space",
}


@dataclass(frozen=True)
class WindowsCompilation:
    task_id: str
    task_path: Path
    report_path: Path
    source_trace: Path
    demonstration_actions: int
    stages: int


@dataclass(frozen=True)
class _RawInputEvent:
    seq: int
    elapsed_ms: float
    frame: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class _InputInterval:
    device: str
    name: str
    start: _RawInputEvent
    end: _RawInputEvent


@dataclass(frozen=True)
class _TimedAction:
    action: ActionCall
    start_ms: float
    end_ms: float
    source_seqs: tuple[int, ...]
    evidence_frame: str
    inference: str


def _frame_paths(
    trace_path: Path,
    events: list[dict[str, Any]],
) -> list[Path]:
    source_dir = trace_path.parent.resolve()
    paths: list[Path] = []
    previous_elapsed = -1.0
    for expected_seq, event in enumerate(events):
        if event.get("seq") != expected_seq:
            raise ValueError("Trace sequence numbers must be contiguous and start at zero")
        elapsed = event.get("elapsed_ms")
        if not isinstance(elapsed, (int, float)) or isinstance(elapsed, bool) or elapsed < 0:
            raise ValueError(f"Trace event {expected_seq} has invalid elapsed_ms")
        if float(elapsed) < previous_elapsed:
            raise ValueError("Trace elapsed_ms values must be monotonic")
        previous_elapsed = float(elapsed)
        frame = event.get("frame")
        if not isinstance(frame, str) or not frame:
            raise ValueError(f"Trace event {expected_seq} has no frame reference")
        frame_path = (source_dir / frame).resolve()
        if not frame_path.is_relative_to(source_dir):
            raise ValueError(f"Trace event {expected_seq} references a frame outside its run")
        if not frame_path.is_file():
            raise FileNotFoundError(f"Trace frame does not exist: {frame_path}")
        paths.append(frame_path)
    return paths


def _validate_windows_trace(
    trace_path: Path,
    metadata: dict[str, Any],
    events: list[dict[str, Any]],
) -> tuple[list[Path], list[_RawInputEvent]]:
    if metadata.get("source") != "windows_human":
        raise ValueError("Windows compiler requires a windows_human recording")
    if metadata.get("success") is not True or metadata.get("stop_reason") != "success_marked":
        raise ValueError("Only a Windows trace completed with a human success marker can compile")
    if metadata.get("coordinate_space") != "physical_pixels":
        raise ValueError(
            "Windows trace does not use physical-pixel coordinates; re-record it with the "
            "DPI-safe v0.5.2 recorder"
        )
    task_id = metadata.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("Trace metadata must contain a non-empty task_id")
    if not events or events[0].get("type") != "start":
        raise ValueError("Windows trace must begin with a start event")
    if events[-1].get("type") != "success_marker":
        raise ValueError("Windows trace must end with a success_marker event")

    frames = _frame_paths(trace_path, events)
    raw_events: list[_RawInputEvent] = []
    previous_sampled_elapsed = -1.0
    for event in events:
        if event.get("type") != "windows_input":
            continue
        details = event.get("details")
        if not isinstance(details, dict) or not isinstance(details.get("raw_input"), dict):
            raise TypeError(f"Windows input event {event['seq']} has no raw_input object")
        raw_input = details["raw_input"]
        sampled_elapsed = raw_input.get("sampled_elapsed_ms", event["elapsed_ms"])
        if (
            not isinstance(sampled_elapsed, (int, float))
            or isinstance(sampled_elapsed, bool)
            or sampled_elapsed < 0
        ):
            raise ValueError(
                f"Windows input event {event['seq']} has invalid sampled_elapsed_ms"
            )
        if float(sampled_elapsed) < previous_sampled_elapsed:
            raise ValueError("Windows sampled input times must be monotonic")
        previous_sampled_elapsed = float(sampled_elapsed)
        raw_events.append(
            _RawInputEvent(
                seq=event["seq"],
                elapsed_ms=float(sampled_elapsed),
                frame=event["frame"],
                payload=raw_input,
            )
        )
    if not raw_events:
        raise ValueError("Windows trace contains no input events")
    if metadata.get("input_event_count") != len(raw_events):
        raise ValueError("Windows input event count does not match metadata")
    return frames, raw_events


def _pair_input_intervals(
    raw_events: list[_RawInputEvent],
) -> tuple[list[_InputInterval], list[_InputInterval], list[_RawInputEvent]]:
    active_keys: dict[str, _RawInputEvent] = {}
    active_buttons: dict[str, _RawInputEvent] = {}
    keys: list[_InputInterval] = []
    buttons: list[_InputInterval] = []
    ignored_releases: list[_RawInputEvent] = []

    for event in raw_events:
        device = event.payload.get("device")
        transition = event.payload.get("event")
        if transition not in {"down", "up"}:
            raise ValueError(f"Raw input event {event.seq} has an unsupported transition")
        if device == "keyboard":
            name = event.payload.get("key")
            active = active_keys
            destination = keys
        elif device == "mouse":
            name = event.payload.get("button")
            active = active_buttons
            destination = buttons
        else:
            raise ValueError(f"Raw input event {event.seq} has an unsupported device")
        if not isinstance(name, str) or not name:
            raise ValueError(f"Raw input event {event.seq} has no key/button name")

        if transition == "down":
            if name in active:
                raise ValueError(f"Repeated {device} down event without release: {name}")
            active[name] = event
            continue
        start = active.pop(name, None)
        if start is None:
            ignored_releases.append(event)
            continue
        destination.append(_InputInterval(device=device, name=name, start=start, end=event))

    if active_keys or active_buttons:
        held = sorted([*active_keys, *active_buttons])
        raise ValueError(f"Trace ended with unreleased input: {held}")
    return keys, buttons, ignored_releases


def _duration(interval: _InputInterval) -> int:
    return max(1, round(interval.end.elapsed_ms - interval.start.elapsed_ms))


def _compile_keyboard(intervals: list[_InputInterval]) -> list[_TimedAction]:
    ordered = sorted(intervals, key=lambda item: (item.start.elapsed_ms, item.start.seq))
    modifiers = [item for item in ordered if item.name in MODIFIER_KEYS]
    non_modifiers = [item for item in ordered if item.name not in MODIFIER_KEYS]
    used_modifiers: set[int] = set()
    actions: list[_TimedAction] = []

    for interval in non_modifiers:
        active_modifiers = [
            modifier
            for modifier in modifiers
            if modifier.start.elapsed_ms <= interval.start.elapsed_ms
            and modifier.end.elapsed_ms >= interval.end.elapsed_ms
        ]
        if active_modifiers:
            active_modifiers.sort(key=lambda item: (item.start.elapsed_ms, item.start.seq))
            if len(active_modifiers) > 3:
                raise ValueError("A compiled hotkey cannot contain more than three modifiers")
            keys = [modifier.name for modifier in active_modifiers] + [interval.name]
            action = ActionCall("hotkey", {"keys": keys})
            used_modifiers.update(id(modifier) for modifier in active_modifiers)
            source_seqs = tuple(
                sorted(
                    {
                        interval.start.seq,
                        interval.end.seq,
                        *(
                            seq
                            for modifier in active_modifiers
                            for seq in (modifier.start.seq, modifier.end.seq)
                        ),
                    }
                )
            )
            actions.append(
                _TimedAction(
                    action=action,
                    start_ms=interval.start.elapsed_ms,
                    end_ms=interval.end.elapsed_ms,
                    source_seqs=source_seqs,
                    evidence_frame=interval.end.frame,
                    inference="overlapping_modifier_chord",
                )
            )
            continue
        actions.append(_compile_single_key(interval))

    for modifier in modifiers:
        if id(modifier) not in used_modifiers:
            actions.append(_compile_single_key(modifier))
    return actions


def _compile_single_key(interval: _InputInterval) -> _TimedAction:
    duration_ms = _duration(interval)
    if duration_ms > MAX_KEY_HOLD_MS:
        raise ValueError(
            f"Key hold for {interval.name!r} exceeds the safe {MAX_KEY_HOLD_MS}ms limit"
        )
    if duration_ms >= KEY_HOLD_THRESHOLD_MS:
        action = ActionCall(
            "hold_key",
            {"key": interval.name, "duration_ms": duration_ms},
        )
        inference = "paired_key_duration_at_or_above_hold_threshold"
    else:
        action = ActionCall("press_key", {"key": interval.name})
        inference = "paired_key_duration_below_hold_threshold"
    return _TimedAction(
        action=action,
        start_ms=interval.start.elapsed_ms,
        end_ms=interval.end.elapsed_ms,
        source_seqs=(interval.start.seq, interval.end.seq),
        evidence_frame=interval.end.frame,
        inference=inference,
    )


def _normalized_position(event: _RawInputEvent) -> tuple[float, float]:
    if event.payload.get("inside_target") is not True:
        raise ValueError(f"Mouse event {event.seq} occurred outside the selected target")
    position = event.payload.get("normalized_position")
    if (
        not isinstance(position, list)
        or len(position) != 2
        or not all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and 0 <= float(value) <= 1
            for value in position
        )
    ):
        raise ValueError(f"Mouse event {event.seq} has invalid normalized coordinates")
    return float(position[0]), float(position[1])


def _distance(first: tuple[float, float], second: tuple[float, float]) -> float:
    return math.hypot(first[0] - second[0], first[1] - second[1])


def _overlap(first: _InputInterval, second: _InputInterval) -> bool:
    return max(first.start.elapsed_ms, second.start.elapsed_ms) < min(
        first.end.elapsed_ms,
        second.end.elapsed_ms,
    )


def _reject_unsupported_concurrency(
    keys: list[_InputInterval],
    buttons: list[_InputInterval],
    *,
    allowed_non_modifier_overlap_ids: set[int] | None = None,
) -> None:
    allowed_non_modifier_overlap_ids = allowed_non_modifier_overlap_ids or set()
    non_modifiers = [item for item in keys if item.name not in MODIFIER_KEYS]
    for index, first in enumerate(non_modifiers):
        for second in non_modifiers[index + 1 :]:
            if (
                _overlap(first, second)
                and not {
                    id(first),
                    id(second),
                }.issubset(allowed_non_modifier_overlap_ids)
            ):
                raise ValueError(
                    "Concurrent non-modifier keys require a parallel-hold motor skill"
                )
    for index, first in enumerate(buttons):
        for second in buttons[index + 1 :]:
            if _overlap(first, second):
                raise ValueError("Concurrent mouse buttons are not supported by v0.5.3")
    if any(_overlap(key, button) for key in keys for button in buttons):
        raise ValueError("Concurrent keyboard and mouse input is not supported by v0.5.3")


def _compile_mouse(intervals: list[_InputInterval]) -> list[_TimedAction]:
    actions: list[_TimedAction] = []
    for interval in intervals:
        duration_ms = _duration(interval)
        down_position = _normalized_position(interval.start)
        up_position = _normalized_position(interval.end)
        if _distance(down_position, up_position) > POINTER_JITTER_TOLERANCE:
            if duration_ms > MAX_MOUSE_DRAG_MS:
                raise ValueError(
                    f"Mouse drag exceeds the safe {MAX_MOUSE_DRAG_MS}ms limit"
                )
            action = ActionCall(
                "drag",
                {
                    "start_x": down_position[0],
                    "start_y": down_position[1],
                    "end_x": up_position[0],
                    "end_y": up_position[1],
                    "duration_ms": duration_ms,
                    "button": interval.name,
                },
            )
            inference = "paired_mouse_button_with_pointer_displacement"
        else:
            if duration_ms > MAX_MOUSE_HOLD_MS:
                raise ValueError(
                    f"Mouse button {interval.name!r} was held for {duration_ms}ms between "
                    f"events {interval.start.seq}-{interval.end.seq}; the safe hold limit is "
                    f"{MAX_MOUSE_HOLD_MS}ms"
                )
            if duration_ms > MAX_MOUSE_CLICK_MS:
                action = ActionCall(
                    "hold_mouse",
                    {
                        "x": down_position[0],
                        "y": down_position[1],
                        "duration_ms": duration_ms,
                        "button": interval.name,
                    },
                )
                inference = "paired_mouse_button_stationary_hold"
            else:
                action = ActionCall(
                    "click",
                    {"x": down_position[0], "y": down_position[1], "button": interval.name},
                )
                inference = "paired_mouse_button_without_drag"
        actions.append(
            _TimedAction(
                action=action,
                start_ms=interval.start.elapsed_ms,
                end_ms=interval.end.elapsed_ms,
                source_seqs=(interval.start.seq, interval.end.seq),
                evidence_frame=interval.end.frame,
                inference=inference,
            )
        )
    return actions


def _classify_messaging_keyboard(
    intervals: list[_InputInterval],
) -> tuple[list[_InputInterval], list[_InputInterval], list[_InputInterval]]:
    command_modifiers = [
        item for item in intervals if item.name in COMMAND_MODIFIER_KEYS
    ]
    text_intervals = [
        item
        for item in intervals
        if item.name in MESSAGING_TEXT_KEYS
        and not any(_overlap(item, modifier) for modifier in command_modifiers)
    ]
    text_ids = {id(item) for item in text_intervals}
    text_shifts = [
        item
        for item in intervals
        if item.name == "shift"
        and any(_overlap(item, text) for text in text_intervals)
    ]
    consumed_ids = text_ids | {id(item) for item in text_shifts}
    control_intervals = [item for item in intervals if id(item) not in consumed_ids]
    return text_intervals, text_shifts, control_intervals


def _compile_runtime_text_bursts(
    text_intervals: list[_InputInterval],
    text_shifts: list[_InputInterval],
    boundary_actions: list[_TimedAction],
) -> list[_TimedAction]:
    ordered = sorted(
        text_intervals,
        key=lambda item: (item.start.elapsed_ms, item.start.seq),
    )
    if not ordered:
        return []
    boundary_starts = sorted(action.start_ms for action in boundary_actions)
    groups: list[list[_InputInterval]] = []
    current = [ordered[0]]
    current_end = ordered[0].end.elapsed_ms
    for interval in ordered[1:]:
        has_boundary = any(
            current_end <= boundary_start <= interval.start.elapsed_ms
            for boundary_start in boundary_starts
        )
        if has_boundary:
            groups.append(current)
            current = [interval]
        else:
            current.append(interval)
        current_end = max(current_end, interval.end.elapsed_ms)
    groups.append(current)

    actions: list[_TimedAction] = []
    for index, group in enumerate(groups, start=1):
        related_shifts = [
            shift
            for shift in text_shifts
            if any(_overlap(shift, interval) for interval in group)
        ]
        evidence = max(
            group,
            key=lambda item: (item.end.elapsed_ms, item.end.seq),
        ).end
        source_seqs = tuple(
            sorted(
                {
                    seq
                    for interval in [*group, *related_shifts]
                    for seq in (interval.start.seq, interval.end.seq)
                }
            )
        )
        actions.append(
            _TimedAction(
                action=ActionCall(
                    "type_text",
                    {"text": runtime_text_placeholder(index)},
                ),
                start_ms=min(
                    interval.start.elapsed_ms for interval in [*group, *related_shifts]
                ),
                end_ms=max(
                    interval.end.elapsed_ms for interval in [*group, *related_shifts]
                ),
                source_seqs=source_seqs,
                evidence_frame=evidence.frame,
                inference=(
                    "keyboard_text_burst_with_runtime_instruction_value_"
                    f"{index}"
                ),
            )
        )
    return actions


def _merge_double_clicks(actions: list[_TimedAction]) -> list[_TimedAction]:
    merged: list[_TimedAction] = []
    index = 0
    while index < len(actions):
        first = actions[index]
        if index + 1 >= len(actions):
            merged.append(first)
            break
        second = actions[index + 1]
        if first.action.skill == second.action.skill == "click":
            first_args = first.action.args
            second_args = second.action.args
            gap_ms = second.start_ms - first.end_ms
            positions_match = _distance(
                (first_args["x"], first_args["y"]),
                (second_args["x"], second_args["y"]),
            ) <= POINTER_JITTER_TOLERANCE
            if (
                first_args["button"] == second_args["button"]
                and 0 <= gap_ms <= DOUBLE_CLICK_GAP_MS
                and positions_match
            ):
                merged.append(
                    _TimedAction(
                        action=ActionCall("double_click", dict(first_args)),
                        start_ms=first.start_ms,
                        end_ms=second.end_ms,
                        source_seqs=tuple(sorted({*first.source_seqs, *second.source_seqs})),
                        evidence_frame=second.evidence_frame,
                        inference="two_nearby_clicks_within_double_click_window",
                    )
                )
                index += 2
                continue
        merged.append(first)
        index += 1
    return merged


def _insert_waits(actions: list[_TimedAction]) -> list[_TimedAction]:
    if not actions:
        return []
    result: list[_TimedAction] = []
    previous: _TimedAction | None = None
    for action in actions:
        if previous is not None:
            observed_gap = round(action.start_ms - previous.end_ms)
            if observed_gap >= WAIT_THRESHOLD_MS:
                duration_ms = min(observed_gap, MAX_COMPILED_WAIT_MS)
                result.append(
                    _TimedAction(
                        action=ActionCall("wait", {"duration_ms": duration_ms}),
                        start_ms=previous.end_ms,
                        end_ms=action.start_ms,
                        source_seqs=action.source_seqs[:1],
                        evidence_frame=action.evidence_frame,
                        inference=f"idle_gap_{observed_gap}ms_capped_at_{duration_ms}ms",
                    )
                )
        result.append(action)
        previous = action
    return result


def _compile_actions(
    raw_events: list[_RawInputEvent],
    *,
    capability_profile: str | None = None,
) -> tuple[list[_TimedAction], list[_RawInputEvent]]:
    key_intervals, mouse_intervals, ignored_releases = _pair_input_intervals(raw_events)
    mouse_actions = _compile_mouse(mouse_intervals)
    if capability_profile in TEXT_ENTRY_CAPABILITY_PROFILES:
        text_intervals, text_shifts, control_intervals = _classify_messaging_keyboard(
            key_intervals
        )
        _reject_unsupported_concurrency(
            key_intervals,
            mouse_intervals,
            allowed_non_modifier_overlap_ids={id(item) for item in text_intervals},
        )
        control_actions = _compile_keyboard(control_intervals)
        text_actions = _compile_runtime_text_bursts(
            text_intervals,
            text_shifts,
            [*control_actions, *mouse_actions],
        )
        actions = [*control_actions, *text_actions, *mouse_actions]
    else:
        _reject_unsupported_concurrency(key_intervals, mouse_intervals)
        actions = [*_compile_keyboard(key_intervals), *mouse_actions]
    actions.sort(key=lambda item: (item.start_ms, item.source_seqs))
    return _insert_waits(_merge_double_clicks(actions)), ignored_releases


def _copy_reference_bundle(
    trace_path: Path,
    metadata_path: Path,
    frame_paths: list[Path],
    destination: Path,
) -> None:
    reference_dir = destination / "reference"
    frames_dir = reference_dir / "frames"
    frames_dir.mkdir(parents=True)
    shutil.copy2(trace_path, reference_dir / "trace.jsonl")
    shutil.copy2(metadata_path, reference_dir / "metadata.json")
    narration_manifest = trace_path.with_name("narration.json")
    if narration_manifest.is_file():
        try:
            narration_data = json.loads(narration_manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError("Narration manifest is not valid JSON") from error
        if not isinstance(narration_data, dict):
            raise TypeError("Narration manifest must be an object")
        audio_archived = isinstance(narration_data.get("audio"), dict)
        narration_data["audio"] = None
        narration_data["audio_archived_with_source_trace"] = audio_archived
        (reference_dir / narration_manifest.name).write_text(
            json.dumps(narration_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    for frame_path in dict.fromkeys(frame_paths):
        shutil.copy2(frame_path, frames_dir / frame_path.name)


def compile_windows_trace(
    source_trace: Path,
    metadata_path: Path,
    metadata: dict[str, Any],
    events: list[dict[str, Any]],
    output_root: Path,
    *,
    task_id_override: str | None = None,
) -> WindowsCompilation:
    """Compile one successful Windows demonstration into deterministic motor skills."""

    frame_paths, raw_events = _validate_windows_trace(source_trace, metadata, events)
    initial_window = metadata.get("initial_window")
    if not isinstance(initial_window, dict):
        raise TypeError("Windows trace metadata has no initial_window object")
    process_hint = initial_window.get("process_name")
    capability_profile = metadata.get("capability_profile")
    if (
        capability_profile is None
        and isinstance(process_hint, str)
        and process_hint.casefold() in {"weixin.exe", "wechat.exe"}
    ):
        capability_profile = "messaging"
    if capability_profile is None and (
        metadata.get("capture_method") == "waa_vm_desktop"
        or isinstance(metadata.get("waa_task_id"), str)
    ):
        capability_profile = "text_entry"
    if capability_profile not in {None, *TEXT_ENTRY_CAPABILITY_PROFILES}:
        raise ValueError(f"Unsupported Windows capability profile: {capability_profile!r}")
    inferred, ignored_releases = _compile_actions(
        raw_events,
        capability_profile=capability_profile,
    )
    if not inferred:
        raise ValueError("Windows trace did not produce any supported motor actions")
    task_id = str(task_id_override or metadata["task_id"]).strip()
    if not task_id:
        raise ValueError("Compiled Windows task id must not be empty")
    success_hotkey = str(metadata.get("success_hotkey") or "f8").upper()
    process_name = initial_window.get("process_name")
    title = initial_window.get("title")
    if not isinstance(process_name, str) or not process_name:
        raise ValueError("Windows trace has no target process name")
    if not isinstance(title, str) or not title:
        raise ValueError("Windows trace has no target window title")
    width = initial_window.get("client_width")
    height = initial_window.get("client_height")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise ValueError("Windows trace initial client dimensions are invalid")

    output_dir = make_run_dir(output_root, f"{task_id}-windows-taskpack")
    output_dir.mkdir(parents=True, exist_ok=False)
    _copy_reference_bundle(source_trace, metadata_path, frame_paths, output_dir)

    focus_action = {
        "index": 0,
        "action": ActionCall("focus_window", {}).to_payload(),
        "source": {
            "seqs": [events[0]["seq"]],
            "start_elapsed_ms": events[0]["elapsed_ms"],
            "end_elapsed_ms": events[0]["elapsed_ms"],
            "evidence_frame": f"reference/{events[0]['frame']}",
            "inference": "target_was_foreground_when_recording_started",
        },
    }
    demonstration = [focus_action]
    for index, action in enumerate(inferred, start=1):
        demonstration.append(
            {
                "index": index,
                "action": action.action.to_payload(),
                "source": {
                    "seqs": list(action.source_seqs),
                    "start_elapsed_ms": round(action.start_ms, 3),
                    "end_elapsed_ms": round(action.end_ms, 3),
                    "evidence_frame": f"reference/{action.evidence_frame}",
                    "inference": action.inference,
                },
            }
        )
    demonstration_path = output_dir / "demonstration.json"
    demonstration_path.write_text(
        json.dumps(
            {"schema_version": "0.1", "task_id": task_id, "actions": demonstration},
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    observed_skills = list(
        dict.fromkeys(item["action"]["skill"] for item in demonstration)
    )
    declared_skills = list(observed_skills)
    if capability_profile in TEXT_ENTRY_CAPABILITY_PROFILES or process_name.casefold() in {
        "weixin.exe",
        "wechat.exe",
    }:
        for skill in ("type_text", "press_key", "hotkey"):
            if skill not in declared_skills:
                declared_skills.append(skill)
    final_frame = f"reference/{events[-1]['frame']}"
    stages = [
        {
            "id": "execute",
            "goal": "Reach the demonstrated outcome using the compiled Windows motor skills.",
            "start_seq": min(inferred[0].source_seqs),
            "end_seq": max(inferred[-1].source_seqs),
            "action_count": len(demonstration),
            "observed_actions": observed_skills,
            "evidence_frames": [
                f"reference/{inferred[0].evidence_frame}",
                f"reference/{inferred[-1].evidence_frame}",
            ],
        },
        {
            "id": "verify",
            "goal": "Compare the result with the human-marked successful reference frame.",
            "start_seq": events[-1]["seq"],
            "end_seq": events[-1]["seq"],
            "action_count": 0,
            "observed_actions": [],
            "evidence_frames": [final_frame],
        },
    ]
    task_data: dict[str, Any] = {
        "schema_version": "0.3",
        "id": task_id,
        "instruction": WINDOWS_INSTRUCTION,
        "environment": {
            "adapter": WINDOWS_ADAPTER,
            "target": {
                "process_name": process_name,
                "title_contains": title,
                "recorded_handle": initial_window.get("handle"),
            },
            "reset": {"type": "external", "requires_user_setup": True},
        },
        "observation": {
            "type": "target_client_rgb",
            "width": width,
            "height": height,
            "coordinate_space": "physical_pixels",
        },
        "actions": declared_skills,
        "experience": {
            "intent": task_id,
            "examples": [task_id],
            "family_id": task_id,
            "source": "human_trace",
        },
        "demonstration": {"path": "demonstration.json", "action_count": len(demonstration)},
        "verifier": {
            "type": "reviewed_reference_frame",
            "expected": "Reach the human-marked successful target state.",
            "reference_frame": final_frame,
        },
        "limits": {"max_actions": max(25, len(demonstration) * 4)},
        "compiler": {
            "version": __version__,
            "source_trace": "reference/trace.jsonl",
            "source_metadata": "reference/metadata.json",
            "inference_scope": "trace2task.windows_deterministic_v0",
        },
        "review": {
            "status": "draft",
            "requires_confirmation": True,
            "checklist": [
                "Replace the generic instruction with the intended task wording.",
                "Confirm every compiled parameterized action and inferred wait.",
                "Confirm that the final reference frame proves success.",
                "Confirm that the process and title selector identify only the intended target.",
                (
                    "Resolve every <runtime-text-N> marker from the current run instruction; "
                    "never type the marker literally."
                ),
            ],
        },
    }
    task_path = output_dir / "task.yaml"
    task_path.write_text(
        yaml.safe_dump(task_data, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )

    counts: dict[str, int] = {}
    for skill in observed_skills:
        counts[skill] = sum(
            1 for item in demonstration if item["action"]["skill"] == skill
        )
    report = {
        "schema_version": "0.2",
        "compiler_version": __version__,
        "compiled_at": datetime.now(UTC).isoformat(),
        "source": {
            "trace": str(source_trace),
            "metadata": str(metadata_path),
            "source_type": metadata.get("source"),
            "success": metadata.get("success"),
            "stop_reason": metadata.get("stop_reason"),
            "coordinate_space": metadata.get("coordinate_space"),
        },
        "inference": {
            "task_id": task_id,
            "instruction": WINDOWS_INSTRUCTION,
            "instruction_source": "generic deterministic Windows template",
            "adapter": WINDOWS_ADAPTER,
            "target": task_data["environment"]["target"],
            "declared_actions": declared_skills,
            "observed_actions": observed_skills,
            "capability_profile": capability_profile,
            "runtime_text_bursts": sum(
                1
                for item in demonstration
                if item["action"]["skill"] == "type_text"
            ),
            "action_counts": counts,
            "demonstration": "demonstration.json",
            "thresholds_ms": {
                "key_hold": KEY_HOLD_THRESHOLD_MS,
                "wait": WAIT_THRESHOLD_MS,
                "double_click_gap": DOUBLE_CLICK_GAP_MS,
                "max_mouse_click": MAX_MOUSE_CLICK_MS,
                "max_mouse_hold": MAX_MOUSE_HOLD_MS,
                "max_mouse_drag": MAX_MOUSE_DRAG_MS,
            },
            "pointer_jitter_tolerance": POINTER_JITTER_TOLERANCE,
            "ignored_unmatched_releases": [
                {
                    "seq": event.seq,
                    "elapsed_ms": event.elapsed_ms,
                    "device": event.payload.get("device"),
                    "name": event.payload.get("key", event.payload.get("button")),
                    "reason": "release_without_recorded_press_at_focus_boundary",
                }
                for event in ignored_releases
            ],
            "verifier": {
                "type": "reviewed_reference_frame",
                "evidence_frame": final_frame,
                "success_source": f"human {success_hotkey} marker",
            },
            "stages": stages,
        },
        "review": task_data["review"],
        "assumptions": [
            "Short key intervals are presses and longer intervals are bounded holds.",
            "Ctrl, Alt, or Shift intervals containing another key interval form a hotkey.",
            "Two adjacent same-position clicks within the time window form a double click.",
            "Mouse intervals with pointer displacement compile to bounded drag actions.",
            "Stationary mouse intervals above the click threshold compile to bounded holds.",
            "Idle gaps above the threshold are waits capped at ten seconds.",
            f"The {success_hotkey} success marker is human evidence and still requires visual review.",
            "Unmatched release events are ignored and listed in the compiler report.",
            (
                    "Keyboard text bursts are structural text-entry evidence. Their literal output is "
                    "not treated as a fixed script, so reserved placeholders must be resolved from the "
                    "runtime instruction and current field context."
                )
            if capability_profile in TEXT_ENTRY_CAPABILITY_PROFILES
            else "No text-entry-specific inference was requested.",
            (
                "Unsupported concurrent input, outside-target mouse input, unreleased input, and "
                "excessive holds or drags fail closed."
            ),
        ],
    }
    report_path = output_dir / "compiler-report.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    load_taskpack(task_path)
    return WindowsCompilation(
        task_id=task_id,
        task_path=task_path,
        report_path=report_path,
        source_trace=source_trace,
        demonstration_actions=len(demonstration),
        stages=len(stages),
    )
