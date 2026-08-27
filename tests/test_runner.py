import pygame

from trace2task import runner
from trace2task.runner import (
    HUMAN_KEY_ACTIONS,
    create_reference_trace,
    human_action_for_event,
    interaction_key_is_pressed,
    replay_trace,
    run_visual_agent,
)


def test_wasd_and_arrow_keys_map_to_movement() -> None:
    assert HUMAN_KEY_ACTIONS[pygame.K_w] == "move_up"
    assert HUMAN_KEY_ACTIONS[pygame.K_a] == "move_left"
    assert HUMAN_KEY_ACTIONS[pygame.K_s] == "move_down"
    assert HUMAN_KEY_ACTIONS[pygame.K_d] == "move_right"
    assert HUMAN_KEY_ACTIONS[pygame.K_UP] == "move_up"
    assert HUMAN_KEY_ACTIONS[pygame.K_e] == "interact"
    assert HUMAN_KEY_ACTIONS[pygame.K_SPACE] == "interact"


def test_interact_accepts_physical_text_and_ime_events() -> None:
    physical = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_e, unicode="e")
    unicode_fallback = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_UNKNOWN, unicode="E")
    text_input = pygame.event.Event(pygame.TEXTINPUT, text="e")

    assert human_action_for_event(physical) == "interact"
    assert human_action_for_event(unicode_fallback) == "interact"
    assert human_action_for_event(text_input) == "interact"


def test_win32_polling_detects_e_when_pygame_event_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(runner, "_pygame_interaction_key_is_pressed", lambda: False)
    monkeypatch.setattr(runner, "_win32_key_was_pressed", lambda key: key == 0x45)

    assert interaction_key_is_pressed()


def test_visual_agent_completes_after_target_relocation(tmp_path) -> None:
    result = run_visual_agent(
        19,
        relocate_after=4,
        max_actions=200,
        output_root=tmp_path,
    )

    assert result.success
    assert result.relocations == 1
    assert result.goal_changes == 1
    assert result.replans >= 4


def test_fixed_replay_is_layout_specific(tmp_path) -> None:
    trace = create_reference_trace(7, tmp_path / "reference")
    same_layout = replay_trace(trace.trace_path, seed=7)
    changed_layout = replay_trace(trace.trace_path, seed=19)

    assert same_layout.success
    assert not changed_layout.success
