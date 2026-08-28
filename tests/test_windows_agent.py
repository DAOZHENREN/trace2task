from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pygame
import pytest
import yaml

from trace2task.actions import ActionCall
from trace2task.windows_agent import CodexWindowsAgent, WindowsAgentPlan
from trace2task.windows_control import WindowInfo
from trace2task.windows_runner import (
    EmergencyStopRequested,
    run_windows_agent,
)
from trace2task.windows_task import load_windows_task


def _write_taskpack(tmp_path: Path, *, confirmed: bool = False) -> Path:
    task_dir = tmp_path / ("confirmed-task" if confirmed else "draft-task")
    reference_dir = task_dir / "reference"
    reference_dir.mkdir(parents=True)
    reference = pygame.Surface((100, 50))
    reference.fill((30, 120, 70))
    pygame.image.save(reference, reference_dir / "final.png")
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
    task_path = task_dir / "task.yaml"
    task_path.write_text(yaml.safe_dump(task, sort_keys=False), encoding="utf-8")
    return task_path


class FakeSession:
    def __init__(
        self,
        codex_executable: str,
        *,
        responses: Iterator[dict[str, Any]],
        calls: list[dict[str, Any]],
        model: str | None,
        cwd: Path,
        timeout_seconds: float,
    ) -> None:
        self.responses = responses
        self.calls = calls
        self.closed = False

    def run_turn(
        self,
        *,
        prompt: str,
        image_path: Path,
        output_schema: dict[str, Any],
        additional_image_paths: tuple[Path, ...] = (),
    ) -> str:
        assert image_path.is_file()
        assert all(path.is_file() for path in additional_image_paths)
        self.calls.append(
            {
                "prompt": prompt,
                "schema": output_schema,
                "reference_paths": additional_image_paths,
            }
        )
        return json.dumps(next(self.responses))

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


class FakeCapture:
    def __init__(self) -> None:
        self.calls = 0

    def capture(self, window: WindowInfo) -> pygame.Surface:
        self.calls += 1
        surface = pygame.Surface((window.client_width, window.client_height))
        surface.fill((20 + self.calls, 70, 110))
        return surface


class ScriptedAgent:
    def __init__(self, plans: list[WindowsAgentPlan]) -> None:
        self.plans = iter(plans)
        self.replans = 0
        self.observations: list[tuple[ActionCall, bool]] = []
        self.closed = False

    def plan(self, surface: pygame.Surface) -> WindowsAgentPlan:
        self.replans += 1
        return next(self.plans)

    def observe_transition(self, action: ActionCall, applied: bool) -> None:
        self.observations.append((action, applied))

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


def _plan(*actions: ActionCall, complete: bool = False) -> WindowsAgentPlan:
    return WindowsAgentPlan(
        task_complete=complete,
        actions=actions,
        reason="Target comparison decision.",
        confidence=0.8,
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
                }
            ],
            calls,
            sessions,
        ),
    )

    plan = agent.plan(pygame.Surface((100, 50)))

    assert plan.actions == (ActionCall("click", {"x": 0.25, "y": 0.75}),)
    assert calls[0]["reference_paths"] == (contract.reference_frame,)
    assert calls[0]["schema"]["properties"]["actions"]["items"]["oneOf"]
    assert "Image 1" in calls[0]["prompt"] and "Image 2" in calls[0]["prompt"]
    assert "Recorded demonstration" in calls[0]["prompt"]
    agent.close()
    assert sessions[0].closed


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
    agent = ScriptedAgent([_plan(click), _plan(complete=True)])

    result = run_windows_agent(
        _write_taskpack(tmp_path, confirmed=True),
        execute=True,
        output_root=tmp_path / "runs",
        backend=backend,
        capture=capture,
        emergency_stop=emergency,
        agent_factory=lambda contract: agent,
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
