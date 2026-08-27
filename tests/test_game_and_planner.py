import pygame

from trace2task.game import ACTION_DELTAS, WINDOW_SIZE, GameRenderer, GameState
from trace2task.planner import a_star, path_to_actions
from trace2task.vision import VisualObserver


def test_seeded_games_are_solvable() -> None:
    for seed in range(25):
        state = GameState.reset(seed)
        path = a_star(state.player, state.target, state.obstacles)
        assert path[0] == state.player
        assert path[-1] == state.target


def test_initial_spawn_accepts_every_wasd_direction() -> None:
    for seed in range(25):
        state = GameState.reset(seed)
        for dx, dy in ACTION_DELTAS.values():
            candidate = (state.player[0] + dx, state.player[1] + dy)
            assert candidate not in state.obstacles
            assert 0 <= candidate[0] < 20
            assert 0 <= candidate[1] < 15


def test_visual_observer_reads_pixels_instead_of_state() -> None:
    pygame.init()
    state = GameState.reset(7)
    surface = pygame.Surface(WINDOW_SIZE)
    GameRenderer().render(surface, state)

    observation = VisualObserver().observe(surface)

    assert observation.player == state.player
    assert observation.target == state.target
    assert observation.obstacles == state.obstacles
    assert not observation.completed


def test_shortest_path_actions_complete_task() -> None:
    state = GameState.reset(11)
    path = a_star(state.player, state.target, state.obstacles)
    for action in path_to_actions(path):
        state.apply(action)
    assert state.completed
