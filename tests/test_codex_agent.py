from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pygame
import pytest

from trace2task import codex_agent
from trace2task.codex_agent import CodexMultimodalAgent, resolve_codex_binary
from trace2task.game import WINDOW_SIZE, GameRenderer, GameState
from trace2task.taskpack import load_taskpack

TASK_PATH = Path("taskpacks/daily-reward/task.yaml")


def rendered_state() -> pygame.Surface:
    pygame.font.init()
    surface = pygame.Surface(WINDOW_SIZE)
    GameRenderer().render(surface, GameState.reset(19), mode="agent")
    return surface


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
        self.codex_executable = codex_executable
        self.responses = responses
        self.calls = calls
        self.model = model
        self.cwd = cwd
        self.timeout_seconds = timeout_seconds
        self.closed = False

    def run_turn(
        self,
        *,
        prompt: str,
        image_path: Path,
        output_schema: dict[str, Any],
    ) -> str:
        assert image_path.is_file()
        self.calls.append(
            {
                "prompt": prompt,
                "schema": output_schema,
                "image_path": image_path,
            }
        )
        return json.dumps(next(self.responses))

    def close(self) -> None:
        self.closed = True


def fake_session_factory(
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


def test_taskpack_loads_agent_contract() -> None:
    task = load_taskpack(TASK_PATH)

    assert task.task_id == "daily-reward"
    assert task.actions == (
        "move_up",
        "move_down",
        "move_left",
        "move_right",
        "interact",
    )
    assert task.expected_result == "DAILY TASK COMPLETE"
    assert task.max_actions == 300


def test_codex_agent_reuses_session_and_caches_local_action_batch() -> None:
    calls: list[dict[str, Any]] = []
    sessions: list[FakeSession] = []
    agent = CodexMultimodalAgent(
        load_taskpack(TASK_PATH),
        binary_resolver=lambda requested: requested,
        session_factory=fake_session_factory(
            [
                {
                    "actions": ["move_right", "move_down"],
                    "reason": "Move around the visible obstacle.",
                    "confidence": 0.82,
                }
            ],
            calls,
            sessions,
        ),
    )

    first = agent.decide(rendered_state())
    agent.observe_transition(first.action, True)
    second = agent.decide(rendered_state())

    assert first.action == "move_right"
    assert second.action == "move_down"
    assert first.details["provider"] == "codex_app_server"
    assert first.details["session_mode"] == "persistent"
    assert first.details["plan_horizon"] == 12
    assert agent.replans == 1
    assert len(sessions) == 1
    assert len(calls) == 1
    assert calls[0]["schema"]["properties"]["actions"]["maxItems"] == 12
    assert "local motor controller" in calls[0]["prompt"]
    assert sessions[0].codex_executable == "codex"
    assert sessions[0].model == "gpt-5.6-terra"
    assert sessions[0].timeout_seconds == 120

    agent.close()
    assert sessions[0].closed


def test_codex_agent_rejects_actions_outside_taskpack() -> None:
    agent = CodexMultimodalAgent(
        load_taskpack(TASK_PATH),
        binary_resolver=lambda requested: requested,
        session_factory=fake_session_factory(
            [
                {
                    "actions": ["open_inventory"],
                    "reason": "Not allowed",
                    "confidence": 0.5,
                }
            ],
            [],
            [],
        ),
    )

    with pytest.raises(RuntimeError, match="disallowed action"):
        agent.decide(rendered_state())


def test_failed_action_discards_batch_and_replans_in_same_session() -> None:
    calls: list[dict[str, Any]] = []
    sessions: list[FakeSession] = []
    agent = CodexMultimodalAgent(
        load_taskpack(TASK_PATH),
        binary_resolver=lambda requested: requested,
        session_factory=fake_session_factory(
            [
                {
                    "actions": ["move_left", "move_left"],
                    "reason": "Try the left route.",
                    "confidence": 0.7,
                },
                {
                    "actions": ["move_up"],
                    "reason": "The previous route was blocked.",
                    "confidence": 0.75,
                },
            ],
            calls,
            sessions,
        ),
    )
    first = agent.decide(rendered_state())
    agent.observe_transition(first.action, False)
    second = agent.decide(rendered_state())

    assert first.action == "move_left"
    assert second.action == "move_up"
    assert agent.replans == 2
    assert len(sessions) == 1
    assert len(calls) == 2
    assert "Continue the same task" in calls[1]["prompt"]
    assert "blocked_or_failed" in calls[1]["prompt"]


@pytest.mark.skipif(os.name != "nt", reason="Windows desktop bundle discovery")
def test_windows_desktop_codex_is_found_without_path(tmp_path, monkeypatch) -> None:
    bundled = tmp_path / "OpenAI" / "Codex" / "bin" / "version-hash" / "codex.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"test executable placeholder")
    monkeypatch.setattr(codex_agent.shutil, "which", lambda command: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("ProgramFiles", raising=False)

    assert resolve_codex_binary() == str(bundled.resolve())
