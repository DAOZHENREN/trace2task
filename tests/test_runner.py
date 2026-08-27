import pygame

from trace2task.runner import (
    HUMAN_KEY_ACTIONS,
    create_reference_trace,
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
