from __future__ import annotations

import json
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from typing import Any

import pygame
import pytest
import yaml

from trace2task.actions import ActionCall
from trace2task.windows_agent import (
    MODEL_CURRENT_IMAGE_MAX_EDGE,
    MODEL_REFERENCE_IMAGE_MAX_EDGE,
    WINDOWS_DECISION_TIMEOUT_SECONDS,
    CodexWindowsAgent,
    WindowsAgentPlan,
    WindowsPlanTiming,
)
from trace2task.windows_control import WindowInfo
from trace2task.windows_runner import (
    LOCAL_WAIT_UNTIL_MAX_EXTRA_MS,
    EmergencyStopRequested,
    WindowsAgentRunFailed,
    run_windows_agent,
)
from trace2task.windows_task import load_windows_task


def _write_taskpack(
    tmp_path: Path,
    *,
    confirmed: bool = False,
    semantic: bool = False,
    guidance: bool = False,
    cycle: bool = False,
) -> Path:
    if guidance and not semantic:
        raise ValueError("guidance test fixture requires semantic experience")
    task_dir = tmp_path / ("confirmed-task" if confirmed else "draft-task")
    reference_dir = task_dir / "reference"
    reference_dir.mkdir(parents=True)
    reference = pygame.Surface((100, 50))
    reference.fill((30, 120, 70))
    pygame.image.save(reference, reference_dir / "final.png")
    if semantic:
        frames_dir = reference_dir / "frames"
        frames_dir.mkdir()
        pygame.image.save(reference, frames_dir / "before.png")
        after = reference.copy()
        after.fill((60, 150, 90))
        pygame.image.save(after, frames_dir / "after.png")
    demonstration = {
        "schema_version": "0.1",
        "task_id": "windows-smoke",
        "actions": [
            {"index": 0, "action": {"skill": "focus_window", "args": {}}},
            {
                "index": 1,
                "action": {
                    "skill": "click",
                    "args": {"x": 0.5, "y": 0.5, "button": "left"},
                },
            },
        ],
    }
    (task_dir / "demonstration.json").write_text(
        json.dumps(demonstration),
        encoding="utf-8",
    )
    task = {
        "schema_version": "0.3",
        "id": "windows-smoke",
        "instruction": "Click the visible target and reach the reference state.",
        "environment": {
            "adapter": "trace2task.windows",
            "target": {
                "process_name": "target.exe",
                "title_contains": "Trace2Task Target",
            },
        },
        "actions": ["focus_window", "click"],
        "demonstration": {"path": "demonstration.json", "action_count": 2},
        "verifier": {
            "type": "reviewed_reference_frame",
            "expected": "The target is visibly complete.",
            "reference_frame": "reference/final.png",
        },
        "limits": {"max_actions": 5},
        "review": {
            "status": "confirmed" if confirmed else "draft",
            "requires_confirmation": not confirmed,
        },
    }
    if cycle:
        task["verifier"]["completion"] = {
            "mode": "cycle",
            "require_departure_from_reference": True,
            "reason": "The workflow must leave and return to the same anchor.",
        }
    if semantic:
        task["semantic_experience"] = {
            "path": "experience.yaml",
            "stage_count": 1,
            "source": "human_trace",
        }
        experience = {
            "schema_version": "0.1",
            "task_id": "windows-smoke",
            "source": {
                "type": "human_trace",
                "trace": "reference/trace.jsonl",
                "demonstration": "demonstration.json",
                "policy": "immutable_strong_evidence",
            },
            "compiler": {
                "type": "multimodal_agent",
                "version": "0.7.0",
                "model": "gpt-5.6-terra",
                "reasoning_effort": "low",
                "policy": "replaceable_derived_interpretation",
            },
            "goal": "Open the visible target.",
            "summary": "Focus the window and click the target.",
            "stages": [
                {
                    "id": "open_target",
                    "name": "Open target",
                    "start_action_index": 0,
                    "end_action_index": 1,
                    "state_before": {
                        "description": "Target is visible.",
                        "evidence_frame": "reference/frames/before.png",
                        "visual_anchors": ["Visible target"],
                    },
                    "action_intents": [
                        {
                            "start_action_index": 0,
                            "end_action_index": 1,
                            "description": "Focus and open the target.",
                            "target": "Visible target",
                            "provenance": "inferred",
                            "confidence": 0.8,
                        }
                    ],
                    "preconditions": ["Target is visible"],
                    "expected_effects": ["Target opens"],
                    "state_after": {
                        "description": "Target is open.",
                        "evidence_frame": "reference/frames/after.png",
                        "visual_anchors": ["Open target content"],
                    },
                    "dynamic_decisions": [
                        {
                            "description": "Choose current visible content.",
                            "generalization": "runtime_agent_decides",
                            "confidence": 0.4,
                        }
                    ],
                    "confidence": 0.75,
                }
            ],
            "review": {
                "status": "confirmed" if confirmed else "draft",
                "requires_confirmation": not confirmed,
            },
        }
        (task_dir / "experience.yaml").write_text(
            yaml.safe_dump(experience, sort_keys=False), encoding="utf-8"
        )
    if guidance:
        task["human_guidance"] = {
            "path": "guidance.yaml",
            "revision": 1,
            "rule_count": 1,
        }
        (task_dir / "guidance.yaml").write_text(
            yaml.safe_dump(
                {
                    "schema_version": "0.1",
                    "task_id": "windows-smoke",
                    "status": "confirmed",
                    "revision": 1,
                    "summary": "Avoid redundant replanning after a stable click.",
                    "rules": [
                        {
                            "id": "trick-01",
                            "stage_id": "open_target",
                            "when": "The target is stable.",
                            "prefer": "Click once and wait for the expected content.",
                            "avoid": ["Replanning immediately after the click"],
                            "replan_when": ["Expected content does not appear"],
                            "expected_effect": "The target opens efficiently.",
                            "priority": "high",
                        }
                    ],
                    "revision_agent": {
                        "model": "gpt-5.6-sol",
                        "reasoning_effort": "high",
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
    task_path = task_dir / "task.yaml"
    task_path.write_text(yaml.safe_dump(task, sort_keys=False), encoding="utf-8")
    return task_path


def _add_followup_semantic_stage(task_path: Path) -> None:
    experience_path = task_path.with_name("experience.yaml")
    experience = yaml.safe_load(experience_path.read_text(encoding="utf-8"))
    first = experience["stages"][0]
    first["end_action_index"] = 0
    first["action_intents"] = [
        {
            **first["action_intents"][0],
            "start_action_index": 0,
            "end_action_index": 0,
        }
    ]
    second = deepcopy(first)
    second.update(
        {
            "id": "complete_target",
            "name": "Complete target",
            "start_action_index": 1,
            "end_action_index": 1,
            "state_before": deepcopy(first["state_after"]),
            "state_after": {
                "description": "The target workflow is complete.",
                "evidence_frame": "reference/frames/after.png",
                "visual_anchors": ["Completed target content"],
            },
            "action_intents": [
                {
                    "start_action_index": 1,
                    "end_action_index": 1,
                    "description": "Complete the visible target.",
                    "target": "Visible completion control",
                    "provenance": "inferred",
                    "confidence": 0.8,
                }
            ],
            "preconditions": ["The first stage is visibly complete"],
            "expected_effects": ["The target workflow completes"],
        }
    )
    experience["stages"] = [first, second]
    experience_path.write_text(
        yaml.safe_dump(experience, sort_keys=False),
        encoding="utf-8",
    )
    task = yaml.safe_load(task_path.read_text(encoding="utf-8"))
    task["semantic_experience"]["stage_count"] = 2
    task_path.write_text(yaml.safe_dump(task, sort_keys=False), encoding="utf-8")


def _replace_linear_stage_with_directed_state_graph(task_path: Path) -> None:
    experience_path = task_path.with_name("experience.yaml")
    experience = yaml.safe_load(experience_path.read_text(encoding="utf-8"))
    experience["schema_version"] = "0.4"
    experience["state_graph"] = {
        "entry_state_id": "ready",
        "states": [
            {
                "id": "ready",
                "name": "Ready",
                "description": "The target is ready for an interaction.",
                "preconditions": ["The visible target is ready"],
                "visual_anchors": ["Visible target"],
                "evidence_stage_ids": ["open_target"],
                "confidence": 0.9,
            },
            {
                "id": "retry",
                "name": "Retry",
                "description": "The target remains closed after an attempt.",
                "preconditions": ["The target is still closed"],
                "visual_anchors": ["Visible target"],
                "evidence_stage_ids": ["open_target"],
                "confidence": 0.75,
            },
        ],
        "transitions": [
            {
                "id": "ready_to_retry",
                "source_state_id": "ready",
                "target_type": "state",
                "target_id": "retry",
                "condition": "The first attempt has no visible effect.",
                "action_goal": "Check and retry the target.",
                "expected_effects": ["The target reacts"],
                "evidence_stage_ids": ["open_target"],
                "confidence": 0.75,
            },
            {
                "id": "retry_to_ready",
                "source_state_id": "retry",
                "target_type": "state",
                "target_id": "ready",
                "condition": "The ready target is visible again.",
                "action_goal": "Return to the ready state.",
                "expected_effects": ["The ready target is recognized"],
                "evidence_stage_ids": ["open_target"],
                "confidence": 0.75,
            },
            {
                "id": "retry_to_success",
                "source_state_id": "retry",
                "target_type": "terminal",
                "target_id": "success",
                "condition": "The target is visibly open.",
                "action_goal": "Stop after verification.",
                "expected_effects": ["The task is complete"],
                "evidence_stage_ids": ["open_target"],
                "confidence": 0.9,
            },
        ],
        "terminals": [
            {
                "id": "success",
                "kind": "success",
                "name": "Complete",
                "condition": "The target is visibly open.",
                "visual_anchors": ["Open target content"],
                "evidence_frame": "reference/frames/after.png",
                "confidence": 0.9,
            }
        ],
    }
    experience_path.write_text(
        yaml.safe_dump(experience, sort_keys=False),
        encoding="utf-8",
    )
    task = yaml.safe_load(task_path.read_text(encoding="utf-8"))
    task["semantic_experience"].update(
        {"state_count": 2, "transition_count": 3, "terminal_count": 1}
    )
    task_path.write_text(yaml.safe_dump(task, sort_keys=False), encoding="utf-8")


class FakeSession:
    def __init__(
        self,
        codex_executable: str,
        *,
        responses: Iterator[dict[str, Any]],
        calls: list[dict[str, Any]],
        model: str | None,
        reasoning_effort: str,
        cwd: Path,
        timeout_seconds: float,
    ) -> None:
        self.responses = responses
        self.calls = calls
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds
        self.closed = False
        self.reset_count = 0

    def run_turn(
        self,
        *,
        prompt: str,
        image_path: Path,
        output_schema: dict[str, Any],
        additional_image_paths: tuple[Path, ...] = (),
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        assert image_path.is_file()
        assert all(path.is_file() for path in additional_image_paths)
        self.calls.append(
            {
                "prompt": prompt,
                "schema": output_schema,
                "reference_paths": additional_image_paths,
                "image_sizes": [
                    pygame.image.load(path).get_size()
                    for path in (image_path, *additional_image_paths)
                ],
                "model": model,
                "reasoning_effort": reasoning_effort,
            }
        )
        return json.dumps(next(self.responses))

    def reset_thread(self) -> None:
        self.reset_count += 1

    def close(self) -> None:
        self.closed = True


def _session_factory(
    responses: list[dict[str, Any]],
    calls: list[dict[str, Any]],
    sessions: list[FakeSession],
):
    response_iterator = iter(responses)

    def create(codex_executable: str, **kwargs: Any) -> FakeSession:
        session = FakeSession(
            codex_executable,
            responses=response_iterator,
            calls=calls,
            **kwargs,
        )
        sessions.append(session)
        return session

    return create


def _window() -> WindowInfo:
    return WindowInfo(
        handle=7,
        title="Trace2Task Target",
        process_id=207,
        process_name="target.exe",
        client_left=100,
        client_top=200,
        client_width=100,
        client_height=50,
        dpi=120,
        is_visible=True,
        is_minimized=False,
        is_foreground=False,
    )


class FakeBackend:
    def __init__(self) -> None:
        self.target = _window()
        self.foreground = 0
        self.events: list[tuple[Any, ...]] = []

    def list_windows(self) -> list[WindowInfo]:
        return [self.target]

    def get_window(self, handle: int) -> WindowInfo | None:
        return self.target if handle == 7 else None

    def foreground_handle(self) -> int:
        return self.foreground

    def focus_window(self, handle: int) -> bool:
        self.events.append(("focus", handle))
        self.foreground = handle
        return True

    def set_cursor_position(self, x: int, y: int) -> None:
        self.events.append(("cursor", x, y))

    def send_mouse_button(self, button: str, is_down: bool) -> None:
        self.events.append(("mouse", button, is_down))

    def send_key(self, virtual_key: int, is_down: bool) -> None:
        self.events.append(("key", virtual_key, is_down))

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


class FailSecondCursorBackend(FakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self.cursor_calls = 0

    def set_cursor_position(self, x: int, y: int) -> None:
        self.cursor_calls += 1
        if self.cursor_calls == 2:
            raise RuntimeError("simulated motor anomaly")
        super().set_cursor_position(x, y)


class AlwaysFailCursorBackend(FakeBackend):
    def set_cursor_position(self, x: int, y: int) -> None:
        raise RuntimeError("persistent motor anomaly")


class FakeCapture:
    def __init__(self) -> None:
        self.calls = 0

    def capture(self, window: WindowInfo) -> pygame.Surface:
        self.calls += 1
        surface = pygame.Surface((window.client_width, window.client_height))
        surface.fill((20 + self.calls, 70, 110))
        return surface


class ColorSequenceCapture:
    def __init__(self, colors: list[tuple[int, int, int]]) -> None:
        self.colors = colors
        self.calls = 0

    def capture(self, window: WindowInfo) -> pygame.Surface:
        color = self.colors[min(self.calls, len(self.colors) - 1)]
        self.calls += 1
        surface = pygame.Surface((window.client_width, window.client_height))
        surface.fill(color)
        return surface


class CyclingColorCapture:
    def __init__(self, colors: list[tuple[int, int, int]]) -> None:
        self.colors = colors
        self.calls = 0

    def capture(self, window: WindowInfo) -> pygame.Surface:
        color = self.colors[self.calls % len(self.colors)]
        self.calls += 1
        surface = pygame.Surface((window.client_width, window.client_height))
        surface.fill(color)
        return surface


class ScriptedAgent:
    def __init__(self, plans: list[WindowsAgentPlan]) -> None:
        self.plans = iter(plans)
        self.replans = 0
        self.observations: list[tuple[ActionCall, bool]] = []
        self.completion_rejections: list[str] = []
        self.closed = False

    def plan(self, surface: pygame.Surface) -> WindowsAgentPlan:
        self.replans += 1
        return next(self.plans)

    def observe_transition(self, action: ActionCall, applied: bool) -> None:
        self.observations.append((action, applied))

    def observe_completion_rejected(self, reason: str) -> None:
        self.completion_rejections.append(reason)

    def close(self) -> None:
        self.closed = True


class FakeEmergencyStop:
    def __init__(self, *, stop_on_check: int | None = None) -> None:
        self.stop_on_check = stop_on_check
        self.checks = 0
        self.sleeps: list[float] = []
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def raise_if_requested(self) -> None:
        self.checks += 1
        if self.stop_on_check == self.checks:
            raise EmergencyStopRequested

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.raise_if_requested()

    def close(self) -> None:
        self.closed = True


def _plan(
    *actions: ActionCall,
    complete: bool = False,
    timing: WindowsPlanTiming | None = None,
) -> WindowsAgentPlan:
    return WindowsAgentPlan(
        task_complete=complete,
        actions=actions,
        reason="Target comparison decision.",
        confidence=0.8,
        timing=timing or WindowsPlanTiming(),
    )


def test_windows_task_loader_validates_target_demo_and_reference(tmp_path: Path) -> None:
    contract = load_windows_task(_write_taskpack(tmp_path))

    assert contract.task.environment_adapter == "trace2task.windows"
    assert contract.selector.title_contains == "Trace2Task Target"
    assert contract.selector.process_name == "target.exe"
    assert [action.skill for action in contract.demonstration] == ["focus_window", "click"]
    assert contract.reference_frame.name == "final.png"


def test_codex_windows_agent_uses_current_and_reference_with_strict_actions(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []
    sessions: list[FakeSession] = []
    contract = load_windows_task(_write_taskpack(tmp_path))
    agent = CodexWindowsAgent(
        contract,
        model="gpt-5.6-sol",
        reasoning_effort="high",
        plan_horizon=1,
        binary_resolver=lambda requested: requested,
        session_factory=_session_factory(
            [
                {
                    "task_complete": False,
                    "actions": [
                        {
                            "skill": "click",
                            "args": {"x": 0.25, "y": 0.75, "button": "left"},
                        }
                    ],
                    "reason": "Click the visible target.",
                    "confidence": 0.9,
                },
                {
                    "task_complete": False,
                    "actions": [
                        {
                            "skill": "click",
                            "args": {"x": 0.5, "y": 0.5, "button": "left"},
                        }
                    ],
                    "reason": "Continue from the new screenshot.",
                    "confidence": 0.91,
                },
            ],
            calls,
            sessions,
        ),
    )

    plan = agent.plan(pygame.Surface((100, 50)))
    agent.observe_transition(plan.actions[0], True)
    agent.plan(pygame.Surface((100, 50)))

    assert plan.actions == (ActionCall("click", {"x": 0.25, "y": 0.75}),)
    assert calls[0]["reference_paths"] == (contract.reference_frame,)
    assert calls[1]["reference_paths"] == ()
    assert "Continue the current semantic stage" in calls[1]["prompt"]
    assert len(calls[1]["prompt"]) < len(calls[0]["prompt"])
    assert calls[0]["schema"]["properties"]["actions"]["items"]["anyOf"]
    assert "Image 1" in calls[0]["prompt"] and "Image 2" in calls[0]["prompt"]
    assert "Recorded demonstration" not in calls[0]["prompt"]
    assert '"x":0.5' not in calls[0]["prompt"]
    assert "Raw demonstration coordinates" in calls[0]["prompt"]
    assert "The Trace proves observed state transitions" in calls[0]["prompt"]
    assert sessions[0].model == "gpt-5.6-sol"
    assert sessions[0].reasoning_effort == "high"
    assert sessions[0].timeout_seconds == WINDOWS_DECISION_TIMEOUT_SECONDS
    agent.close()
    assert sessions[0].closed


def test_codex_windows_agent_downscales_large_model_images(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []
    sessions: list[FakeSession] = []
    task_path = _write_taskpack(tmp_path, semantic=True)
    task_dir = task_path.parent
    large_reference = pygame.Surface((1_900, 1_100))
    large_reference.fill((30, 120, 70))
    for path in (
        task_dir / "reference" / "final.png",
        task_dir / "reference" / "frames" / "before.png",
        task_dir / "reference" / "frames" / "after.png",
    ):
        pygame.image.save(large_reference, path)
    contract = load_windows_task(task_path)
    agent = CodexWindowsAgent(
        contract,
        binary_resolver=lambda requested: requested,
        session_factory=_session_factory(
            [
                {
                    "task_complete": False,
                    "actions": [{"skill": "click", "args": {"x": 0.5, "y": 0.5}}],
                    "reason": "Continue.",
                    "confidence": 0.9,
                    "stage_id": "open_target",
                }
            ],
            calls,
            sessions,
        ),
    )

    agent.plan(pygame.Surface((1_800, 1_000)))

    assert len(calls[0]["image_sizes"]) == 4
    assert max(calls[0]["image_sizes"][0]) <= MODEL_CURRENT_IMAGE_MAX_EDGE
    assert all(
        max(size) <= MODEL_REFERENCE_IMAGE_MAX_EDGE
        for size in calls[0]["image_sizes"][1:]
    )
    assert calls[0]["image_sizes"][0] == (1_440, 800)
    assert calls[0]["image_sizes"][1:] == [(1_280, 741)] * 3


def test_codex_windows_agent_uses_semantic_stages_and_their_evidence(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []
    contract = load_windows_task(_write_taskpack(tmp_path, semantic=True))
    agent = CodexWindowsAgent(
        contract,
        plan_horizon=1,
        binary_resolver=lambda requested: requested,
        session_factory=_session_factory(
            [
                {
                    "task_complete": False,
                    "actions": [
                        {
                            "skill": "click",
                            "args": {"x": 0.25, "y": 0.75, "button": "left"},
                        }
                    ],
                    "reason": "Follow the grounded stage.",
                    "confidence": 0.9,
                    "stage_id": "open_target",
                },
                {
                    "task_complete": False,
                    "actions": [
                        {
                            "skill": "click",
                            "args": {"x": 0.5, "y": 0.5, "button": "left"},
                        }
                    ],
                    "reason": "Continue the active stage.",
                    "confidence": 0.92,
                    "stage_id": "open_target",
                }
            ],
            calls,
            [],
        ),
    )

    first = agent.plan(pygame.Surface((100, 50)))
    agent.plan(pygame.Surface((100, 50)))

    assert contract.semantic_experience is not None
    assert first.stage_id == "open_target"
    assert len(calls[0]["reference_paths"]) == 3
    assert "semantic stage index" in calls[0]["prompt"]
    assert "action_intents" not in calls[0]["prompt"]
    assert len(calls[1]["reference_paths"]) == 3
    assert "Locally retrieved active-stage experience" in calls[1]["prompt"]
    assert "Compact semantic stage index" in calls[1]["prompt"]
    assert "Sanitized Trace evidence" in calls[1]["prompt"]
    assert '"action_range":[0,1]' in calls[1]["prompt"]
    assert '"pointer_activation":1' in calls[1]["prompt"]
    assert '"x":0.5' not in calls[1]["prompt"]
    assert "runtime_agent_decides" in calls[1]["prompt"]
    assert "derived and reviewable from the immutable human Trace" in calls[0]["prompt"]
    assert "fresh bounded model session" in calls[1]["prompt"]
    assert "exact stage-boundary images" in calls[1]["prompt"]
    assert "Exact human Trace images for this stage" in calls[1]["prompt"]
    agent.close()


def test_codex_windows_agent_never_exposes_recorded_drag_coordinates(
    tmp_path: Path,
) -> None:
    task_path = _write_taskpack(tmp_path, semantic=True)
    task_root = yaml.safe_load(task_path.read_text(encoding="utf-8"))
    task_root["actions"].append("drag")
    task_path.write_text(yaml.safe_dump(task_root, sort_keys=False), encoding="utf-8")
    demonstration_path = task_path.with_name("demonstration.json")
    demonstration = json.loads(demonstration_path.read_text(encoding="utf-8"))
    demonstration["actions"][1]["action"] = {
        "skill": "drag",
        "args": {
            "start_x": 0.897476,
            "start_y": 0.744479,
            "end_x": 0.531546,
            "end_y": 0.442692,
            "duration_ms": 602,
            "button": "left",
        },
    }
    demonstration_path.write_text(json.dumps(demonstration), encoding="utf-8")
    calls: list[dict[str, Any]] = []
    agent = CodexWindowsAgent(
        load_windows_task(task_path),
        binary_resolver=lambda requested: requested,
        session_factory=_session_factory(
            [
                {
                    "task_complete": False,
                    "actions": [{"skill": "click", "args": {"x": 0.4, "y": 0.5}}],
                    "reason": "Identify the stage.",
                    "confidence": 0.9,
                    "stage_id": "open_target",
                },
                {
                    "task_complete": False,
                    "actions": [{"skill": "click", "args": {"x": 0.45, "y": 0.5}}],
                    "reason": "Continue from current pixels.",
                    "confidence": 0.9,
                    "stage_id": "open_target",
                },
            ],
            calls,
            [],
        ),
    )

    agent.plan(pygame.Surface((100, 50)))
    agent.plan(pygame.Surface((100, 50)))

    combined = "\n".join(call["prompt"] for call in calls)
    assert "0.897476" not in combined
    assert "0.531546" not in combined
    assert "duration_ms" not in combined
    assert '"unverified_pointer_gesture":1' in calls[1]["prompt"]
    agent.close()


def test_codex_windows_agent_returns_a_complete_stage_program_by_default(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []
    contract = load_windows_task(_write_taskpack(tmp_path, semantic=True))
    actions = [
        {"skill": "click", "args": {"x": 0.1 * index, "y": 0.5}}
        for index in range(1, 6)
    ]
    agent = CodexWindowsAgent(
        contract,
        binary_resolver=lambda requested: requested,
        session_factory=_session_factory(
            [
                {
                    "task_complete": False,
                    "actions": actions,
                    "reason": "Execute the stable stage.",
                    "confidence": 0.94,
                    "stage_id": "open_target",
                    "stage_goal": "Open the target through the stable sequence.",
                    "expected_end_state": "The target content is open.",
                    "abort_conditions": ["The target disappears"],
                }
            ],
            calls,
            [],
        ),
    )

    plan = agent.plan(pygame.Surface((100, 50)))

    assert len(plan.actions) == 5
    assert plan.stage_goal == "Open the target through the stable sequence."
    assert plan.expected_end_state == "The target content is open."
    assert plan.abort_conditions == ("The target disappears",)
    assert calls[0]["schema"]["properties"]["actions"]["maxItems"] == 12
    assert "adaptive local visual checkpoint" in calls[0]["prompt"]
    assert "System multi-action planning policy" in calls[0]["prompt"]
    assert "executor policy, not task-specific Trace" in calls[0]["prompt"]
    assert "target 5 to 8 adjacent actions" in calls[0]["prompt"]
    assert "one-action wait-only program" in calls[0]["prompt"]
    agent.close()


def test_codex_windows_agent_repairs_empty_stage_boundary_in_same_session(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []
    sessions: list[FakeSession] = []
    task_path = _write_taskpack(tmp_path, semantic=True)
    _add_followup_semantic_stage(task_path)
    agent = CodexWindowsAgent(
        load_windows_task(task_path),
        binary_resolver=lambda requested: requested,
        session_factory=_session_factory(
            [
                {
                    "task_complete": False,
                    "actions": [
                        {"skill": "click", "args": {"x": 0.25, "y": 0.75}}
                    ],
                    "reason": "Finish the first stage.",
                    "confidence": 0.9,
                    "stage_id": "open_target",
                },
                {
                    "task_complete": False,
                    "actions": [],
                    "reason": "The first stage ended, but I omitted the transition actions.",
                    "confidence": 0.8,
                    "stage_id": "open_target",
                },
                {
                    "task_complete": False,
                    "actions": [
                        {"skill": "click", "args": {"x": 0.75, "y": 0.75}}
                    ],
                    "reason": "Transition to the next stage safely.",
                    "confidence": 0.92,
                    "stage_id": "complete_target",
                },
            ],
            calls,
            sessions,
        ),
    )

    agent.plan(pygame.Surface((100, 50)))
    recovered = agent.plan(pygame.Surface((100, 50)))

    assert recovered.stage_id == "complete_target"
    assert len(recovered.actions) == 1
    assert len(calls) == 3
    assert len(sessions) == 1
    assert sessions[0].reset_count == 1
    assert "Compact semantic stage index" in calls[1]["prompt"]
    assert "A stage boundary is not a reason" in calls[1]["prompt"]
    assert "System multi-action planning policy" in calls[1]["prompt"]
    assert "coding-agent test failure" in calls[2]["prompt"]
    assert "System multi-action planning policy" in calls[2]["prompt"]
    assert "No action from that invalid decision was executed" in calls[2]["prompt"]
    assert "Do not repeat an already applied interaction" in calls[2]["prompt"]
    assert "cooling-down or disabled control" in calls[2]["prompt"]
    assert "Candidate recovery stage: complete_target" in calls[2]["prompt"]
    assert "Complete the visible target" in calls[2]["prompt"]
    assert "The first stage is visibly complete" in calls[2]["prompt"]
    assert "I omitted the transition actions" in calls[2]["prompt"]
    assert calls[2]["schema"]["properties"]["actions"]["minItems"] == 1
    assert calls[2]["schema"]["properties"]["task_complete"]["enum"] == [False]
    assert calls[2]["reference_paths"] == ()
    assert len(calls[2]["image_sizes"]) == 1
    assert recovered.timing.decision_repair_attempts == 1
    assert recovered.timing.decision_repair_stage_id == "complete_target"
    agent.close()


def test_codex_windows_agent_stops_after_bounded_same_session_repair(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []
    sessions: list[FakeSession] = []
    empty = {
        "task_complete": False,
        "actions": [],
        "reason": "The task still needs work.",
        "confidence": 0.7,
        "stage_id": "open_target",
    }
    agent = CodexWindowsAgent(
        load_windows_task(_write_taskpack(tmp_path, semantic=True)),
        binary_resolver=lambda requested: requested,
        session_factory=_session_factory([empty, dict(empty)], calls, sessions),
    )

    with pytest.raises(
        RuntimeError,
        match=(
            "repeated an empty incomplete decision after same-session repair toward "
            "stage open_target"
        ),
    ):
        agent.plan(pygame.Surface((100, 50)))

    assert len(calls) == 2
    assert len(sessions) == 1
    assert sessions[0].reset_count == 0
    assert "coding-agent test failure" in calls[1]["prompt"]
    assert calls[1]["schema"]["properties"]["actions"]["minItems"] == 1
    assert calls[1]["schema"]["properties"]["task_complete"]["enum"] == [False]
    agent.close()


def test_codex_windows_agent_bounds_context_with_stage_thread_resets(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []
    sessions: list[FakeSession] = []
    contract = load_windows_task(_write_taskpack(tmp_path, semantic=True))
    response = {
        "task_complete": False,
        "actions": [{"skill": "click", "args": {"x": 0.5, "y": 0.5}}],
        "reason": "Continue the active stage.",
        "confidence": 0.95,
        "stage_id": "open_target",
    }
    agent = CodexWindowsAgent(
        contract,
        plan_horizon=1,
        binary_resolver=lambda requested: requested,
        session_factory=_session_factory([dict(response) for _ in range(6)], calls, sessions),
    )

    plans = [agent.plan(pygame.Surface((100, 50))) for _ in range(6)]

    assert len(plans) == 6
    assert len(sessions) == 1
    assert sessions[0].reset_count == 2
    assert agent.session_resets == 2
    assert plans[1].timing.session_reset_reason == "stage_change"
    assert plans[5].timing.session_reset_reason == "context_limit"
    assert "fresh bounded model session" in calls[5]["prompt"]
    assert len(calls[5]["reference_paths"]) == 3
    agent.close()


def test_codex_windows_agent_temporarily_escalates_unknown_low_confidence_stage(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []
    contract = load_windows_task(_write_taskpack(tmp_path, semantic=True))
    agent = CodexWindowsAgent(
        contract,
        model="gpt-5.6-luna",
        reasoning_effort="low",
        plan_horizon=1,
        binary_resolver=lambda requested: requested,
        session_factory=_session_factory(
            [
                {
                    "task_complete": False,
                    "actions": [{"skill": "click", "args": {"x": 0.2, "y": 0.2}}],
                    "reason": "The current stage is unclear.",
                    "confidence": 0.6,
                    "stage_id": "unknown",
                },
                {
                    "task_complete": False,
                    "actions": [{"skill": "click", "args": {"x": 0.3, "y": 0.3}}],
                    "reason": "Recovered the active stage.",
                    "confidence": 0.95,
                    "stage_id": "open_target",
                },
                {
                    "task_complete": False,
                    "actions": [{"skill": "click", "args": {"x": 0.4, "y": 0.4}}],
                    "reason": "Continue cheaply.",
                    "confidence": 0.95,
                    "stage_id": "open_target",
                },
            ],
            calls,
            [],
        ),
    )

    agent.plan(pygame.Surface((100, 50)))
    agent.plan(pygame.Surface((100, 50)))
    agent.plan(pygame.Surface((100, 50)))

    assert [(call["model"], call["reasoning_effort"]) for call in calls] == [
        ("gpt-5.6-luna", "low"),
        ("gpt-5.6-sol", "high"),
        ("gpt-5.6-luna", "low"),
    ]
    agent.close()


def test_codex_windows_agent_injects_confirmed_human_tricks(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []
    contract = load_windows_task(
        _write_taskpack(tmp_path, semantic=True, guidance=True)
    )
    agent = CodexWindowsAgent(
        contract,
        binary_resolver=lambda requested: requested,
        session_factory=_session_factory(
            [
                {
                    "task_complete": False,
                    "actions": [{"skill": "click", "args": {"x": 0.5, "y": 0.5}}],
                    "reason": "Follow the confirmed trick.",
                    "confidence": 0.95,
                    "stage_id": "open_target",
                }
            ],
            calls,
            [],
        ),
    )

    agent.plan(pygame.Surface((100, 50)))

    assert contract.human_guidance is not None
    assert "Confirmed human guidance has higher priority" in calls[0]["prompt"]
    assert "Click once and wait for the expected content" in calls[0]["prompt"]
    agent.close()


def test_runtime_instruction_treats_trace_as_structural_example(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []
    contract = load_windows_task(_write_taskpack(tmp_path)).with_instruction(
        "给文件传输助手发送：Trace2Task 测试"
    )
    agent = CodexWindowsAgent(
        contract,
        plan_horizon=1,
        binary_resolver=lambda requested: requested,
        session_factory=_session_factory(
            [
                {
                    "task_complete": False,
                    "actions": [
                        {
                            "skill": "click",
                            "args": {"x": 0.25, "y": 0.75, "button": "left"},
                        }
                    ],
                    "reason": "Follow the runtime instruction.",
                    "confidence": 0.9,
                }
            ],
            calls,
            [],
        ),
    )

    agent.plan(pygame.Surface((100, 50)))

    prompt = calls[0]["prompt"]
    assert "Task instruction for this run: 给文件传输助手发送：Trace2Task 测试" in prompt
    assert "Original demonstration intent:" in prompt
    assert "structural examples, not literal values to copy" in prompt
    assert "<runtime-text-N> value is a reserved semantic marker" in prompt
    agent.close()


def test_codex_windows_agent_rejects_literal_runtime_text_marker(tmp_path: Path) -> None:
    task_path = _write_taskpack(tmp_path)
    root = yaml.safe_load(task_path.read_text(encoding="utf-8"))
    root["actions"].append("type_text")
    root["demonstration"]["action_count"] = 3
    task_path.write_text(
        yaml.safe_dump(root, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    demonstration_path = task_path.parent / "demonstration.json"
    demonstration = json.loads(demonstration_path.read_text(encoding="utf-8"))
    demonstration["actions"].append(
        {
            "index": 2,
            "action": {
                "skill": "type_text",
                "args": {"text": "<runtime-text-1>"},
            },
        }
    )
    demonstration_path.write_text(json.dumps(demonstration), encoding="utf-8")
    agent = CodexWindowsAgent(
        load_windows_task(task_path).with_instruction("给张三发送：测试"),
        binary_resolver=lambda requested: requested,
        session_factory=_session_factory(
            [
                {
                    "task_complete": False,
                    "actions": [
                        {
                            "skill": "type_text",
                            "args": {"text": "<runtime-text-1>"},
                        }
                    ],
                    "reason": "Copied the marker.",
                    "confidence": 0.5,
                }
            ],
            [],
            [],
        ),
    )

    with pytest.raises(RuntimeError, match="reserved runtime-text"):
        agent.plan(pygame.Surface((100, 50)))
    agent.close()


def test_runtime_instruction_is_single_normalized_parameter(tmp_path: Path) -> None:
    contract = load_windows_task(_write_taskpack(tmp_path))

    overridden = contract.with_instruction("  给 张三   发送 测试消息  ")

    assert overridden.instruction == "给 张三 发送 测试消息"
    assert overridden.task.instruction == contract.task.instruction
    with pytest.raises(ValueError, match="must not be empty"):
        contract.with_instruction("   ")


def test_codex_windows_agent_rejects_skill_outside_taskpack(tmp_path: Path) -> None:
    contract = load_windows_task(_write_taskpack(tmp_path))
    agent = CodexWindowsAgent(
        contract,
        binary_resolver=lambda requested: requested,
        session_factory=_session_factory(
            [
                {
                    "task_complete": False,
                    "actions": [{"skill": "press_key", "args": {"key": "enter"}}],
                    "reason": "Disallowed action.",
                    "confidence": 0.5,
                }
            ],
            [],
            [],
        ),
    )

    with pytest.raises(RuntimeError, match="outside the Windows task pack"):
        agent.plan(pygame.Surface((100, 50)))
    agent.close()


def test_codex_windows_agent_excludes_focus_action_in_background(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []
    contract = load_windows_task(_write_taskpack(tmp_path))
    agent = CodexWindowsAgent(
        contract,
        background=True,
        binary_resolver=lambda requested: requested,
        session_factory=_session_factory(
            [
                {
                    "task_complete": False,
                    "actions": [
                        {
                            "skill": "click",
                            "args": {"x": 0.25, "y": 0.75, "button": "left"},
                        }
                    ],
                    "reason": "Click without focusing.",
                    "confidence": 0.9,
                }
            ],
            calls,
            [],
        ),
    )

    agent.plan(pygame.Surface((100, 50)))

    variants = calls[0]["schema"]["properties"]["actions"]["items"]["anyOf"]
    skills = [variant["properties"]["skill"]["enum"][0] for variant in variants]
    assert skills == ["click"]
    assert "Never return focus_window" in calls[0]["prompt"]
    agent.close()


def test_windows_agent_dry_run_accepts_draft_and_sends_no_input(tmp_path: Path) -> None:
    task_path = _write_taskpack(tmp_path)
    backend = FakeBackend()
    agent = ScriptedAgent([_plan(ActionCall("click", {"x": 0.5, "y": 0.5}))])

    result = run_windows_agent(
        task_path,
        backend=backend,
        capture=FakeCapture(),
        agent_factory=lambda contract: agent,
    )

    assert result.mode == "windows_agent_dry_run"
    assert result.executed_actions == 0
    assert result.proposed_actions == [
        {"skill": "click", "args": {"x": 0.5, "y": 0.5, "button": "left"}}
    ]
    assert backend.events == []
    assert agent.closed
    assert result.input_mode == "foreground"
    assert result.model == "gpt-5.6-terra"
    assert result.reasoning_effort == "low"
    assert result.planning_ms >= 0


def test_windows_agent_dry_run_can_focus_for_gpu_capture(tmp_path: Path) -> None:
    backend = FakeBackend()
    agent = ScriptedAgent([_plan(ActionCall("click", {"x": 0.5, "y": 0.5}))])

    result = run_windows_agent(
        _write_taskpack(tmp_path),
        focus=True,
        backend=backend,
        capture=FakeCapture(),
        agent_factory=lambda contract: agent,
    )

    assert result.mode == "windows_agent_dry_run"
    assert backend.events == [("focus", 7)]


def test_windows_agent_rejects_conflicting_focus_and_background(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot be combined"):
        run_windows_agent(
            _write_taskpack(tmp_path),
            focus=True,
            background=True,
            backend=FakeBackend(),
            capture=FakeCapture(),
            agent_factory=lambda contract: pytest.fail("agent must not be created"),
        )


def test_windows_agent_refuses_to_execute_draft_before_model_or_input(tmp_path: Path) -> None:
    backend = FakeBackend()

    with pytest.raises(RuntimeError, match="still a draft"):
        run_windows_agent(
            _write_taskpack(tmp_path),
            execute=True,
            backend=backend,
            capture=FakeCapture(),
            agent_factory=lambda contract: pytest.fail("agent must not be created"),
        )

    assert backend.events == []


def test_confirmed_windows_agent_executes_guarded_action_then_verifies(
    tmp_path: Path,
) -> None:
    backend = FakeBackend()
    capture = FakeCapture()
    emergency = FakeEmergencyStop()
    click = ActionCall("click", {"x": 0.5, "y": 0.5})
    agent = ScriptedAgent(
        [
            _plan(
                click,
                timing=WindowsPlanTiming(
                    decision_repair_attempts=1,
                    decision_repair_stage_id="open_target",
                ),
            ),
            _plan(complete=True),
        ]
    )
    statuses: list[str] = []

    result = run_windows_agent(
        _write_taskpack(tmp_path, confirmed=True),
        execute=True,
        output_root=tmp_path / "runs",
        backend=backend,
        capture=capture,
        emergency_stop=emergency,
        agent_factory=lambda contract: agent,
        status_callback=statuses.append,
    )
    metadata = json.loads(
        (Path(result.trace_path).parent / "metadata.json").read_text(encoding="utf-8")
    )

    assert result.task_complete
    assert result.executed_actions == 1
    assert result.replans == 2
    assert result.stop_reason == "model_complete"
    assert backend.events == [
        ("focus", 7),
        ("cursor", 150, 225),
        ("mouse", "left", True),
        ("mouse", "left", False),
    ]
    assert emergency.started and emergency.closed
    assert emergency.sleeps == [0.02]
    assert agent.observations == [(click, True)]
    assert metadata["success"] is True
    assert metadata["parameterized_action_count"] == 1
    assert metadata["planning_ms"] >= 0
    assert any("Requesting a multimodal decision" in status for status in statuses)
    assert any(
        "Same-session recovery succeeded" in status
        and "candidate stage open_target" in status
        for status in statuses
    )
    assert any("Received in" in status for status in statuses)
    assert any("click completed" in status for status in statuses)


def test_cycle_task_must_leave_initial_reference_before_completion(
    tmp_path: Path,
) -> None:
    reference_color = (30, 120, 70)
    departed_color = (180, 30, 40)
    click = ActionCall("click", {"x": 0.5, "y": 0.5})
    agent = ScriptedAgent(
        [_plan(complete=True), _plan(click), _plan(complete=True)]
    )
    capture = ColorSequenceCapture(
        [reference_color, reference_color, departed_color, reference_color]
    )

    result = run_windows_agent(
        _write_taskpack(tmp_path, confirmed=True, cycle=True),
        execute=True,
        output_root=tmp_path / "runs",
        backend=FakeBackend(),
        capture=capture,
        emergency_stop=FakeEmergencyStop(),
        agent_factory=lambda contract: agent,
    )
    trace_events = [
        json.loads(line)
        for line in Path(result.trace_path).read_text(encoding="utf-8").splitlines()
    ]

    assert result.task_complete is True
    assert result.executed_actions == 1
    assert agent.completion_rejections == [
        "cycle run started at the reference anchor and has not visibly left it"
    ]
    assert any(event["type"] == "completion_rejected" for event in trace_events)


def test_confirmed_windows_agent_can_execute_in_background_without_focus(
    tmp_path: Path,
) -> None:
    backend = FakeBackend()
    capture = FakeCapture()
    emergency = FakeEmergencyStop()
    click = ActionCall("click", {"x": 0.5, "y": 0.5})
    agent = ScriptedAgent([_plan(click), _plan(complete=True)])

    result = run_windows_agent(
        _write_taskpack(tmp_path, confirmed=True),
        execute=True,
        background=True,
        output_root=tmp_path / "runs",
        backend=backend,
        capture=capture,
        emergency_stop=emergency,
        agent_factory=lambda contract: agent,
    )

    assert result.task_complete
    assert result.input_mode == "background"
    assert backend.foreground == 0
    assert backend.events == [
        ("window_mouse", 7, 50, 25, "left", True, False),
        ("window_mouse", 7, 50, 25, "left", False, False),
    ]


def test_motor_anomaly_discards_batch_suffix_and_replans_immediately(
    tmp_path: Path,
) -> None:
    backend = FailSecondCursorBackend()
    first = ActionCall("click", {"x": 0.1, "y": 0.5})
    failed = ActionCall("click", {"x": 0.2, "y": 0.5})
    discarded = ActionCall("click", {"x": 0.3, "y": 0.5})
    recovery = ActionCall("click", {"x": 0.8, "y": 0.5})
    agent = ScriptedAgent(
        [_plan(first, failed, discarded), _plan(recovery), _plan(complete=True)]
    )
    statuses: list[str] = []

    result = run_windows_agent(
        _write_taskpack(tmp_path, confirmed=True),
        execute=True,
        output_root=tmp_path / "runs",
        backend=backend,
        capture=FakeCapture(),
        emergency_stop=FakeEmergencyStop(),
        agent_factory=lambda contract: agent,
        status_callback=statuses.append,
    )

    assert result.task_complete
    assert result.executed_actions == 2
    assert result.batch_count == 2
    assert result.planned_actions == 4
    assert result.interrupted_batches == 1
    assert result.average_batch_size == 2.0
    assert result.max_batch_size == 3
    assert agent.observations == [
        (first, True),
        (failed, False),
        (recovery, True),
    ]
    assert all(event != ("cursor", 130, 225) for event in backend.events)
    assert any("replanning immediately" in status for status in statuses)


def test_local_visual_checkpoints_keep_a_long_batch_running(tmp_path: Path) -> None:
    backend = FakeBackend()
    capture = ColorSequenceCapture(
        [
            (10, 10, 10),
            (10, 10, 10),
            (10, 10, 10),
            (80, 80, 80),
            (80, 80, 80),
            (80, 80, 80),
            (150, 150, 150),
            (150, 150, 150),
            (150, 150, 150),
            (150, 150, 150),
        ]
    )
    first_click = ActionCall("click", {"x": 0.25, "y": 0.5})
    first_wait = ActionCall("wait", {"duration_ms": 100})
    second_click = ActionCall("click", {"x": 0.75, "y": 0.5})
    second_wait = ActionCall("wait", {"duration_ms": 100})
    agent = ScriptedAgent(
        [
            _plan(first_click, first_wait, second_click, second_wait),
            _plan(complete=True),
        ]
    )

    result = run_windows_agent(
        _write_taskpack(tmp_path, confirmed=True),
        execute=True,
        output_root=tmp_path / "runs",
        backend=backend,
        capture=capture,
        emergency_stop=FakeEmergencyStop(),
        agent_factory=lambda contract: agent,
    )

    assert result.task_complete
    assert result.executed_actions == 4
    assert result.batch_count == 1
    assert result.average_batch_size == 4.0
    assert result.visual_checkpoints == 2
    assert result.visual_checkpoint_failures == 0
    assert result.visual_stability_wait_ms >= 400
    assert agent.observations == [
        (first_click, True),
        (first_wait, True),
        (second_click, True),
        (second_wait, True),
    ]


def test_trailing_wait_uses_local_wait_until_before_replanning(tmp_path: Path) -> None:
    wait = ActionCall("wait", {"duration_ms": 100})
    agent = ScriptedAgent([_plan(wait), _plan(complete=True)])
    emergency = FakeEmergencyStop()
    capture = ColorSequenceCapture([(40, 40, 40)] * 20)
    statuses: list[str] = []

    result = run_windows_agent(
        _write_taskpack(tmp_path, confirmed=True),
        execute=True,
        output_root=tmp_path / "runs",
        backend=FakeBackend(),
        capture=capture,
        emergency_stop=emergency,
        agent_factory=lambda contract: agent,
        status_callback=statuses.append,
    )

    assert result.task_complete
    assert result.local_wait_until_count == 1
    assert result.local_wait_until_ms >= 1_400
    assert result.wait_only_plans == 1
    assert result.short_batch_count == 1
    assert result.performance["local_wait_until_ms"] == result.local_wait_until_ms
    assert result.stage_timings[0]["local_wait_until_ms"] == result.local_wait_until_ms
    assert any("local decision-ready" in status for status in statuses)


def test_trailing_wait_tolerates_small_persistent_animation(tmp_path: Path) -> None:
    wait = ActionCall("wait", {"duration_ms": 100})
    agent = ScriptedAgent([_plan(wait), _plan(complete=True)])
    capture = CyclingColorCapture(
        [
            (40, 40, 40),
            (42, 42, 42),
            (44, 44, 44),
            (46, 46, 46),
            (48, 48, 48),
            (50, 50, 50),
            (52, 52, 52),
            (54, 54, 54),
            (62, 62, 62),
        ]
    )
    statuses: list[str] = []

    result = run_windows_agent(
        _write_taskpack(tmp_path, confirmed=True),
        execute=True,
        output_root=tmp_path / "runs",
        backend=FakeBackend(),
        capture=capture,
        emergency_stop=FakeEmergencyStop(),
        agent_factory=lambda contract: agent,
        status_callback=statuses.append,
    )

    assert result.task_complete
    assert result.local_wait_until_ms == 1_400
    assert result.interrupted_batches == 0
    assert result.visual_checkpoint_failures == 0
    assert any("ready=True" in status for status in statuses)


def test_trailing_wait_motion_timeout_replans_without_failure(tmp_path: Path) -> None:
    wait = ActionCall("wait", {"duration_ms": 100})
    agent = ScriptedAgent([_plan(wait), _plan(complete=True)])
    statuses: list[str] = []

    result = run_windows_agent(
        _write_taskpack(tmp_path, confirmed=True),
        execute=True,
        output_root=tmp_path / "runs",
        backend=FakeBackend(),
        capture=CyclingColorCapture([(0, 0, 0), (255, 255, 255)]),
        emergency_stop=FakeEmergencyStop(),
        agent_factory=lambda contract: agent,
        status_callback=statuses.append,
    )
    trace_events = [
        json.loads(line)
        for line in Path(result.trace_path).read_text(encoding="utf-8").splitlines()
    ]

    assert result.task_complete
    assert result.local_wait_until_ms == LOCAL_WAIT_UNTIL_MAX_EXTRA_MS
    assert result.interrupted_batches == 0
    assert result.visual_checkpoint_failures == 0
    assert agent.observations == [(wait, True)]
    assert not any(
        event["type"] == "windows_visual_checkpoint_failed"
        for event in trace_events
    )
    assert any("without marking the completed action as failed" in status for status in statuses)


def test_trailing_wait_still_rejects_a_click_with_no_visual_response(
    tmp_path: Path,
) -> None:
    click = ActionCall("click", {"x": 0.5, "y": 0.5})
    wait = ActionCall("wait", {"duration_ms": 100})
    agent = ScriptedAgent([_plan(click, wait), _plan(complete=True)])

    result = run_windows_agent(
        _write_taskpack(tmp_path, confirmed=True),
        execute=True,
        output_root=tmp_path / "runs",
        backend=FakeBackend(),
        capture=ColorSequenceCapture([(20, 20, 20)] * 30),
        emergency_stop=FakeEmergencyStop(),
        agent_factory=lambda contract: agent,
    )

    assert result.task_complete
    assert result.local_wait_until_count == 0
    assert result.interrupted_batches == 1
    assert result.visual_checkpoint_failures == 1
    assert agent.observations == [(click, True), (wait, True), (click, False)]


def test_no_visual_response_discards_the_rest_of_the_batch(tmp_path: Path) -> None:
    backend = FakeBackend()
    capture = ColorSequenceCapture([(20, 20, 20)] * 8)
    click = ActionCall("click", {"x": 0.5, "y": 0.5})
    wait = ActionCall("wait", {"duration_ms": 100})
    discarded = ActionCall("click", {"x": 0.8, "y": 0.5})
    agent = ScriptedAgent(
        [_plan(click, wait, discarded), _plan(complete=True)]
    )
    statuses: list[str] = []

    result = run_windows_agent(
        _write_taskpack(tmp_path, confirmed=True),
        execute=True,
        output_root=tmp_path / "runs",
        backend=backend,
        capture=capture,
        emergency_stop=FakeEmergencyStop(),
        agent_factory=lambda contract: agent,
        status_callback=statuses.append,
    )
    trace_events = [
        json.loads(line)
        for line in Path(result.trace_path).read_text(encoding="utf-8").splitlines()
    ]

    assert result.task_complete
    assert result.executed_actions == 2
    assert result.interrupted_batches == 1
    assert result.visual_checkpoints == 1
    assert result.visual_checkpoint_failures == 1
    assert agent.observations == [(click, True), (wait, True), (click, False)]
    assert backend.events.count(("cursor", 180, 225)) == 0
    assert any(event["type"] == "windows_visual_checkpoint_failed" for event in trace_events)
    assert any("Visual checkpoint failed" in status for status in statuses)


def test_three_consecutive_motor_anomalies_stop_recovery(tmp_path: Path) -> None:
    click = ActionCall("click", {"x": 0.5, "y": 0.5})
    agent = ScriptedAgent([_plan(click), _plan(click), _plan(click)])

    with pytest.raises(WindowsAgentRunFailed, match="recovery limit") as failure:
        run_windows_agent(
            _write_taskpack(tmp_path, confirmed=True),
            execute=True,
            output_root=tmp_path / "runs",
            backend=AlwaysFailCursorBackend(),
            capture=FakeCapture(),
            emergency_stop=FakeEmergencyStop(),
            agent_factory=lambda contract: agent,
        )

    result = failure.value.result
    metadata = json.loads(
        Path(result.trace_path).with_name("metadata.json").read_text(encoding="utf-8")
    )
    assert not result.task_complete
    assert result.stop_reason == "failed:RuntimeError"
    assert result.failure_message == (
        "Stage batch recovery limit reached after 3 consecutive motor failures"
    )
    assert Path(result.trace_path).is_file()
    assert metadata["failure_message"] == result.failure_message
    assert agent.observations == [(click, False), (click, False), (click, False)]
    assert agent.closed


def test_emergency_stop_before_action_prevents_injection(tmp_path: Path) -> None:
    backend = FakeBackend()
    emergency = FakeEmergencyStop(stop_on_check=3)
    agent = ScriptedAgent([_plan(ActionCall("click", {"x": 0.5, "y": 0.5}))])

    result = run_windows_agent(
        _write_taskpack(tmp_path, confirmed=True),
        execute=True,
        output_root=tmp_path / "runs",
        backend=backend,
        capture=FakeCapture(),
        emergency_stop=emergency,
        agent_factory=lambda contract: agent,
    )

    assert not result.task_complete
    assert result.stop_reason == "emergency_stop"
    assert result.executed_actions == 0
    assert backend.events == [("focus", 7)]
    assert emergency.closed and agent.closed


def test_immediate_emergency_stop_returns_before_focus_or_trace(tmp_path: Path) -> None:
    backend = FakeBackend()
    emergency = FakeEmergencyStop(stop_on_check=1)
    agent = ScriptedAgent([])

    result = run_windows_agent(
        _write_taskpack(tmp_path, confirmed=True),
        execute=True,
        output_root=tmp_path / "runs",
        backend=backend,
        capture=FakeCapture(),
        emergency_stop=emergency,
        agent_factory=lambda contract: agent,
    )

    assert result.stop_reason == "emergency_stop"
    assert result.trace_path is None
    assert backend.events == []
    assert emergency.closed and agent.closed


def test_runtime_planner_uses_graph_state_ids_instead_of_trace_episode_order(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []
    task_path = _write_taskpack(tmp_path, semantic=True)
    _replace_linear_stage_with_directed_state_graph(task_path)
    contract = load_windows_task(task_path)
    agent = CodexWindowsAgent(
        contract,
        plan_horizon=1,
        binary_resolver=lambda requested: requested,
        session_factory=_session_factory(
            [
                {
                    "task_complete": False,
                    "actions": [
                        {"skill": "click", "args": {"x": 0.5, "y": 0.5}}
                    ],
                    "reason": "Use the legal ready-state transition.",
                    "confidence": 0.9,
                    "stage_id": "ready",
                }
            ],
            calls,
            [],
        ),
    )

    plan = agent.plan(pygame.Surface((100, 50)))

    assert plan.stage_id == "ready"
    assert calls[0]["schema"]["properties"]["stage_id"]["enum"] == [
        "ready",
        "retry",
        "unknown",
    ]
    assert "open_target" not in calls[0]["schema"]["properties"]["stage_id"]["enum"]
    assert "ready_to_retry" in calls[0]["prompt"]
    assert "loops and backward transitions" in calls[0]["prompt"]
