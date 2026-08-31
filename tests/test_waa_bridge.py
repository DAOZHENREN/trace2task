from __future__ import annotations

from pathlib import Path

import pytest

from trace2task.actions import ActionCall
from trace2task.waa_bridge import WAA_MOTOR_SKILLS, CodexWaaAgent, WaaBridge, action_to_waa


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
