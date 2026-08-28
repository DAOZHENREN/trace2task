from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pygame
import yaml

from trace2task import __version__
from trace2task.game import ACTION_DELTAS, SUCCESS_TEXT, WINDOW_SIZE
from trace2task.recording import make_run_dir
from trace2task.taskpack import load_taskpack
from trace2task.vision import VisualVerifier
from trace2task.windows_compiler import compile_windows_trace

MINI_GAME_ADAPTER = "trace2task.mini_game"
MINI_GAME_ACTIONS = (*ACTION_DELTAS.keys(), "interact")
DEFAULT_INSTRUCTION = (
    "Move the controlled player onto or next to the visible task target and interact with it."
)


@dataclass(frozen=True)
class CompileResult:
    task_id: str
    task_path: str
    report_path: str
    source_trace: str
    demonstration_actions: int
    stages: int
    review_status: str
    requires_confirmation: bool


@dataclass(frozen=True)
class ConfirmResult:
    task_id: str
    task_path: str
    review_status: str
    requires_confirmation: bool


def _load_mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{label} is not valid JSON: {path}") from error
    if not isinstance(value, dict):
        raise TypeError(f"{label} must contain a JSON object: {path}")
    return value


def _load_trace_events(trace_path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        trace_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"Trace line {line_number} is not valid JSON") from error
        if not isinstance(event, dict):
            raise TypeError(f"Trace line {line_number} must contain a JSON object")
        events.append(event)
    if not events:
        raise ValueError("Cannot compile an empty trace")
    return events


def _validate_trace_bundle(
    trace_path: Path,
    metadata: dict[str, Any],
    events: list[dict[str, Any]],
) -> list[Path]:
    if metadata.get("success") is not True:
        raise ValueError("Only a trace whose metadata reports success can be compiled")
    task_id = metadata.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("Trace metadata must contain a non-empty task_id")

    source_dir = trace_path.parent.resolve()
    frame_paths: list[Path] = []
    actions: list[str] = []
    for expected_sequence, event in enumerate(events):
        if event.get("seq") != expected_sequence:
            raise ValueError("Trace sequence numbers must be contiguous and start at zero")
        frame = event.get("frame")
        if not isinstance(frame, str) or not frame:
            raise ValueError(f"Trace event {expected_sequence} has no frame reference")
        frame_path = (source_dir / frame).resolve()
        if not frame_path.is_relative_to(source_dir):
            raise ValueError(f"Trace event {expected_sequence} references a frame outside its run")
        if not frame_path.is_file():
            raise FileNotFoundError(f"Trace frame does not exist: {frame_path}")
        frame_paths.append(frame_path)

        action = event.get("action")
        if action is not None:
            if not isinstance(action, str) or action not in MINI_GAME_ACTIONS:
                raise ValueError(f"Trace contains an unsupported mini-game action: {action!r}")
            actions.append(action)

    if metadata.get("action_count") != len(actions):
        raise ValueError("Trace action count does not match metadata")
    if not actions or "interact" not in actions:
        raise ValueError("A successful mini-game demonstration must contain an interact action")

    final_surface = pygame.image.load(frame_paths[-1])
    if final_surface.get_size() != WINDOW_SIZE:
        raise ValueError(
            f"Trace frame size {final_surface.get_size()} does not match {WINDOW_SIZE}"
        )
    if not VisualVerifier().completed(final_surface):
        raise ValueError("The final trace frame does not contain the visual success signal")
    return frame_paths


def _infer_stages(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def reference_frame(event: dict[str, Any]) -> str:
        return f"reference/{event['frame']}"

    movement = [event for event in events if str(event.get("action", "")).startswith("move_")]
    interactions = [event for event in events if event.get("action") == "interact"]
    stages: list[dict[str, Any]] = []
    if movement:
        stages.append(
            {
                "id": "navigate",
                "goal": "Approach the visible task target while avoiding obstacles.",
                "start_seq": movement[0]["seq"],
                "end_seq": movement[-1]["seq"],
                "action_count": len(movement),
                "observed_actions": list(dict.fromkeys(event["action"] for event in movement)),
                "evidence_frames": [reference_frame(movement[0]), reference_frame(movement[-1])],
            }
        )
    stages.append(
        {
            "id": "interact",
            "goal": "Trigger the target interaction from within interaction range.",
            "start_seq": interactions[0]["seq"],
            "end_seq": interactions[-1]["seq"],
            "action_count": len(interactions),
            "observed_actions": ["interact"],
            "evidence_frames": [reference_frame(interactions[-1])],
        }
    )
    stages.append(
        {
            "id": "verify",
            "goal": f"Confirm the visual result '{SUCCESS_TEXT}'.",
            "start_seq": events[-1]["seq"],
            "end_seq": events[-1]["seq"],
            "action_count": 0,
            "observed_actions": [],
            "evidence_frames": [reference_frame(events[-1])],
        }
    )
    return stages


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
    for frame_path in dict.fromkeys(frame_paths):
        shutil.copy2(frame_path, frames_dir / frame_path.name)


def compile_trace(trace_path: Path, output_root: Path) -> CompileResult:
    """Compile one verified mini-game or Windows demonstration into a task pack."""

    source_trace = trace_path.expanduser().resolve()
    if not source_trace.is_file():
        raise FileNotFoundError(f"Trace does not exist: {source_trace}")
    metadata_path = source_trace.parent / "metadata.json"
    if not metadata_path.is_file():
        raise FileNotFoundError(f"Trace metadata does not exist: {metadata_path}")

    metadata = _load_mapping(metadata_path, "Trace metadata")
    events = _load_trace_events(source_trace)
    if metadata.get("source") == "windows_human":
        windows_result = compile_windows_trace(
            source_trace,
            metadata_path.resolve(),
            metadata,
            events,
            output_root,
        )
        compiled_task = load_taskpack(windows_result.task_path)
        return CompileResult(
            task_id=compiled_task.task_id,
            task_path=str(windows_result.task_path),
            report_path=str(windows_result.report_path),
            source_trace=str(windows_result.source_trace),
            demonstration_actions=windows_result.demonstration_actions,
            stages=windows_result.stages,
            review_status=compiled_task.review_status,
            requires_confirmation=compiled_task.requires_confirmation,
        )
    frame_paths = _validate_trace_bundle(source_trace, metadata, events)
    actions = [event["action"] for event in events if isinstance(event.get("action"), str)]
    stages = _infer_stages(events)
    task_id = str(metadata["task_id"]).strip()

    output_dir = make_run_dir(output_root, f"{task_id}-taskpack")
    output_dir.mkdir(parents=True, exist_ok=False)
    _copy_reference_bundle(source_trace, metadata_path, frame_paths, output_dir)

    max_actions = max(100, len(actions) * 4)
    task_data: dict[str, Any] = {
        "schema_version": "0.2",
        "id": task_id,
        "instruction": DEFAULT_INSTRUCTION,
        "environment": {
            "adapter": MINI_GAME_ADAPTER,
            "reset": {"type": "seeded", "default_seed": metadata.get("seed", 19)},
        },
        "observation": {"type": "rgb_frame", "width": 800, "height": 656},
        "actions": list(MINI_GAME_ACTIONS),
        "verifier": {
            "type": "visual_status",
            "expected": SUCCESS_TEXT,
            "reference_frame": f"reference/{events[-1]['frame']}",
        },
        "limits": {"max_actions": max_actions},
        "compiler": {
            "version": __version__,
            "source_trace": "reference/trace.jsonl",
            "source_metadata": "reference/metadata.json",
            "inference_scope": "trace2task.mini_game_v0",
        },
        "review": {
            "status": "draft",
            "requires_confirmation": True,
            "checklist": [
                "Confirm that the instruction describes the intended task.",
                "Confirm that the final reference frame proves success.",
                "Confirm that the declared action vocabulary is safe for this task.",
            ],
        },
    }
    task_path = output_dir / "task.yaml"
    task_path.write_text(
        yaml.safe_dump(task_data, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )

    report = {
        "schema_version": "0.1",
        "compiler_version": __version__,
        "compiled_at": datetime.now(UTC).isoformat(),
        "source": {
            "trace": str(source_trace),
            "metadata": str(metadata_path.resolve()),
            "source_type": metadata.get("source"),
            "seed": metadata.get("seed"),
            "success": metadata.get("success"),
        },
        "inference": {
            "task_id": task_id,
            "instruction": DEFAULT_INSTRUCTION,
            "instruction_source": "known mini-game adapter profile",
            "adapter": MINI_GAME_ADAPTER,
            "declared_actions": list(MINI_GAME_ACTIONS),
            "observed_actions": list(dict.fromkeys(actions)),
            "action_vocabulary_source": "adapter profile plus demonstration validation",
            "verifier": {
                "type": "visual_status",
                "expected": SUCCESS_TEXT,
                "evidence_frame": f"reference/{events[-1]['frame']}",
            },
            "stages": stages,
        },
        "review": task_data["review"],
        "assumptions": [
            "This compiler version supports only the built-in mini-game adapter.",
            "Semantic task wording comes from the adapter profile, not unrestricted model inference.",
            "The reference verifier was checked against the final recorded RGB frame.",
        ],
    }
    report_path = output_dir / "compiler-report.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # Validate the artifact through the same loader used at runtime.
    compiled_task = load_taskpack(task_path)
    return CompileResult(
        task_id=compiled_task.task_id,
        task_path=str(task_path),
        report_path=str(report_path),
        source_trace=str(source_trace),
        demonstration_actions=len(actions),
        stages=len(stages),
        review_status=compiled_task.review_status,
        requires_confirmation=compiled_task.requires_confirmation,
    )


def confirm_taskpack(task_path: Path) -> ConfirmResult:
    """Mark a reviewed compiler-generated task pack as ready to execute."""

    source_path = task_path.expanduser().resolve()
    task = load_taskpack(source_path)
    data = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("Task pack root must be a mapping")
    review = data.get("review")
    if not isinstance(review, dict):
        raise TypeError("Only a task pack with a compiler review block can be confirmed")
    confirmed_at = datetime.now(UTC).isoformat()
    review["status"] = "confirmed"
    review["requires_confirmation"] = False
    review["confirmed_at"] = confirmed_at

    semantic_path: Path | None = None
    semantic_data: dict[str, Any] | None = None
    semantic_config = data.get("semantic_experience")
    if semantic_config is not None:
        if not isinstance(semantic_config, dict):
            raise TypeError("Task semantic_experience block must be a mapping")
        relative = semantic_config.get("path")
        if not isinstance(relative, str) or not relative:
            raise ValueError("Task semantic_experience.path must be a relative file path")
        semantic_path = (source_path.parent / relative).resolve()
        if not semantic_path.is_relative_to(source_path.parent.resolve()):
            raise ValueError("Task semantic_experience.path points outside the task pack")
        semantic_data = yaml.safe_load(semantic_path.read_text(encoding="utf-8"))
        if not isinstance(semantic_data, dict):
            raise TypeError("Semantic experience root must be a mapping")
        semantic_review = semantic_data.get("review")
        if not isinstance(semantic_review, dict):
            raise TypeError("Semantic experience review block must be a mapping")
        semantic_review["status"] = "confirmed"
        semantic_review["requires_confirmation"] = False
        semantic_review["confirmed_at"] = confirmed_at

    report_path = source_path.parent / "compiler-report.json"
    report: dict[str, Any] | None = None
    if report_path.is_file():
        report = _load_mapping(report_path, "Compiler report")
        report_review = report.get("review")
        if not isinstance(report_review, dict):
            raise TypeError("Compiler report review block must be a mapping")
        report_review["status"] = "confirmed"
        report_review["requires_confirmation"] = False
        report_review["confirmed_at"] = confirmed_at

    source_path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )
    if report is not None:
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    if semantic_path is not None and semantic_data is not None:
        semantic_path.write_text(
            yaml.safe_dump(
                semantic_data,
                sort_keys=False,
                allow_unicode=True,
                width=100,
            ),
            encoding="utf-8",
        )
    confirmed = load_taskpack(source_path)
    return ConfirmResult(
        task_id=task.task_id,
        task_path=str(source_path),
        review_status=confirmed.review_status,
        requires_confirmation=confirmed.requires_confirmation,
    )
