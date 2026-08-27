from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pygame
import pytest

from trace2task.codex_agent import CodexMultimodalAgent
from trace2task.game import WINDOW_SIZE, GameRenderer, GameState
from trace2task.taskpack import load_taskpack

TASK_PATH = Path("taskpacks/daily-reward/task.yaml")


def rendered_state() -> pygame.Surface:
    pygame.font.init()
    surface = pygame.Surface(WINDOW_SIZE)
    GameRenderer().render(surface, GameState.reset(19), mode="agent")
    return surface


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


def test_codex_agent_uses_saved_cli_bridge_and_caches_short_plan() -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        image_path = Path(command[command.index("--image") + 1])
        schema_path = Path(command[command.index("--output-schema") + 1])
        assert image_path.is_file()
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        assert schema["properties"]["actions"]["maxItems"] == 4
        assert kwargs["timeout"] == 120
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "actions": ["move_right", "move_down"],
                    "reason": "Move around the visible obstacle.",
                    "confidence": 0.82,
                }
            ),
            stderr="",
        )

    agent = CodexMultimodalAgent(
        load_taskpack(TASK_PATH),
        command_runner=fake_run,
    )
    first = agent.decide(rendered_state())
    agent.observe_transition(first.action, True)
    second = agent.decide(rendered_state())

    assert first.action == "move_right"
    assert second.action == "move_down"
    assert first.details["provider"] == "codex_cli"
    assert agent.replans == 1
    assert len(calls) == 1
    assert calls[0][:2] == ["codex", "exec"]
    assert "--ephemeral" in calls[0]
    assert "--ignore-user-config" in calls[0]
    assert "--ignore-rules" in calls[0]
    assert ["--model", "gpt-5.6-terra"] == calls[0][
        calls[0].index("--model") : calls[0].index("--model") + 2
    ]


def test_codex_agent_rejects_actions_outside_taskpack() -> None:
    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "actions": ["open_inventory"],
                    "reason": "Not allowed",
                    "confidence": 0.5,
                }
            ),
            stderr="",
        )

    agent = CodexMultimodalAgent(load_taskpack(TASK_PATH), command_runner=fake_run)

    with pytest.raises(RuntimeError, match="disallowed action"):
        agent.decide(rendered_state())


def test_failed_action_discards_cached_plan() -> None:
    responses = iter(
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
        ]
    )

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(next(responses)),
            stderr="",
        )

    agent = CodexMultimodalAgent(load_taskpack(TASK_PATH), command_runner=fake_run)
    first = agent.decide(rendered_state())
    agent.observe_transition(first.action, False)
    second = agent.decide(rendered_state())

    assert first.action == "move_left"
    assert second.action == "move_up"
    assert agent.replans == 2
