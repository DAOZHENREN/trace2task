from __future__ import annotations

from pathlib import Path

import pygame
import pytest

from trace2task.actions import ActionCall
from trace2task.codex_app_server import CodexTurnTimeoutError
from trace2task.waa_bridge import WAA_MOTOR_SKILLS, CodexWaaAgent, WaaBridge, action_to_waa
from trace2task.windows_agent import WindowsAgentPlan


def test_action_to_waa_uses_screenshot_pixel_space() -> None:
    action = ActionCall("click", {"x": 0.5, "y": 1.0, "button": "left"})

    assert action_to_waa(action, width=1920, height=1080) == (
        "pyautogui.click(x=960, y=1079, button='left')"
    )


def test_action_to_waa_preserves_unicode_via_clipboard() -> None:
    action = ActionCall("type_text", {"text": "你好 WAA"})

    command = action_to_waa(action, width=100, height=50)

    assert "pyperclip.copy('你好 WAA')" in command
    assert "pyautogui.hotkey('ctrl', 'v')" in command


def test_action_to_waa_normalizes_special_key_names() -> None:
    action = ActionCall("press_key", {"key": "page_down"})

    assert action_to_waa(action, width=100, height=50) == "pyautogui.press('pagedown')"


def test_action_to_waa_rejects_host_only_focus() -> None:
    with pytest.raises(ValueError, match="focus_window"):
        action_to_waa(ActionCall("focus_window", {}), width=100, height=50)


def test_waa_bridge_rejects_unknown_experiment_condition() -> None:
    with pytest.raises(ValueError, match="experience_mode"):
        WaaBridge(
            Path("missing-task.yaml"),
            experience_mode="everything",
            model="gpt-5.6-terra",
            reasoning_effort="low",
        )


def test_waa_motor_policy_is_condition_independent() -> None:
    agent = object.__new__(CodexWaaAgent)

    assert agent._allowed_skills() == WAA_MOTOR_SKILLS
    assert "focus_window" not in WAA_MOTOR_SKILLS


def test_waa_completion_uses_current_benchmark_instruction_only() -> None:
    agent = object.__new__(CodexWaaAgent)

    policy = agent._completion_context()

    assert "current WAA instruction" in policy
    assert "not additional goals" in policy
    assert "Do not close applications" in policy


def test_waa_bridge_retries_one_transient_planner_failure_without_losing_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeContract:
        def with_instruction(self, instruction: str) -> FakeContract:
            return self

    class RetryAgent:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.plan_calls = 0
            self.reset_calls = 0

        def observe_transition(self, action: ActionCall, applied: bool) -> None:
            return None

        def plan(self, surface: pygame.Surface) -> WindowsAgentPlan:
            self.plan_calls += 1
            if self.plan_calls == 1:
                raise CodexTurnTimeoutError(
                    "first_token_timeout",
                    "Codex did not begin its model response",
                )
            return WindowsAgentPlan(
                task_complete=False,
                actions=(ActionCall("click", {"x": 0.5, "y": 0.5}),),
                reason="Recovered from a transport-only failure.",
                confidence=0.9,
            )

        def reset_planner_session(self) -> None:
            self.reset_calls += 1

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "trace2task.waa_bridge.load_windows_task",
        lambda path: FakeContract(),
    )
    bridge = WaaBridge(
        tmp_path / "task.yaml",
        experience_mode="baseline",
        model="gpt-5.6-terra",
        reasoning_effort="low",
        agent_type=RetryAgent,  # type: ignore[arg-type]
    )
    frame_path = tmp_path / "frame.png"
    pygame.image.save(pygame.Surface((100, 50)), frame_path)

    result = bridge.plan("complete the task", frame_path.read_bytes())

    assert result.planner_retries == 1
    assert result.planner_retry_categories == ("first_token_timeout",)
    assert result.actions == ("pyautogui.click(x=50, y=24, button='left')",)
    assert bridge._agent is not None
    assert bridge._agent.reset_calls == 1


def test_waa_bridge_converts_repeated_planner_timeout_to_a_scored_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeContract:
        def with_instruction(self, instruction: str) -> FakeContract:
            return self

    class TimeoutAgent:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.reset_calls = 0

        def observe_transition(self, action: ActionCall, applied: bool) -> None:
            return None

        def plan(self, surface: pygame.Surface) -> WindowsAgentPlan:
            raise CodexTurnTimeoutError(
                "response_in_progress_timeout",
                "Codex response stopped before completion",
            )

        def reset_planner_session(self) -> None:
            self.reset_calls += 1

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "trace2task.waa_bridge.load_windows_task",
        lambda path: FakeContract(),
    )
    bridge = WaaBridge(
        tmp_path / "task.yaml",
        experience_mode="baseline",
        model="gpt-5.6-terra",
        reasoning_effort="low",
        agent_type=TimeoutAgent,  # type: ignore[arg-type]
    )
    frame_path = tmp_path / "frame.png"
    pygame.image.save(pygame.Surface((100, 50)), frame_path)

    result = bridge.plan("complete the task", frame_path.read_bytes())

    assert result.actions == ("FAIL",)
    assert result.planner_retries == 1
    assert result.planner_failure_category == "response_in_progress_timeout"
    assert "stopped before completion" in (result.planner_failure_message or "")
    assert bridge._agent is not None
    assert bridge._agent.reset_calls == 1
