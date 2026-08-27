from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pygame

from trace2task.game import (
    CELL_SIZE,
    GRID_COLS,
    GRID_ROWS,
    OBSTACLE_COLOR,
    PLAYER_COLOR,
    STATUS_HEIGHT,
    SUCCESS_BG,
    TARGET_COLOR,
    Cell,
)


@dataclass(frozen=True)
class VisualObservation:
    player: Cell | None
    target: Cell | None
    obstacles: set[Cell]
    completed: bool


def _color_mask(pixels: np.ndarray, color: tuple[int, int, int], tolerance: int = 3) -> np.ndarray:
    delta = np.abs(pixels.astype(np.int16) - np.asarray(color, dtype=np.int16))
    return np.max(delta, axis=2) <= tolerance


class VisualObserver:
    """Extracts task state from rendered pixels, without reading GameState."""

    def observe(self, surface: pygame.Surface) -> VisualObservation:
        pixels = pygame.surfarray.array3d(surface)
        player = self._find_entity(pixels, PLAYER_COLOR)
        target = self._find_entity(pixels, TARGET_COLOR)
        obstacles = self._find_obstacles(pixels)
        completed = self._is_completed(pixels)
        return VisualObservation(
            player=player,
            target=target,
            obstacles=obstacles,
            completed=completed,
        )

    def _find_entity(self, pixels: np.ndarray, color: tuple[int, int, int]) -> Cell | None:
        board_pixels = pixels[:, STATUS_HEIGHT:, :]
        mask = _color_mask(board_pixels, color)
        xs, ys = np.where(mask)
        if len(xs) < 8:
            return None
        x = int(np.median(xs)) // CELL_SIZE
        y = int(np.median(ys)) // CELL_SIZE
        if 0 <= x < GRID_COLS and 0 <= y < GRID_ROWS:
            return (x, y)
        return None

    def _find_obstacles(self, pixels: np.ndarray) -> set[Cell]:
        board_pixels = pixels[:, STATUS_HEIGHT:, :]
        mask = _color_mask(board_pixels, OBSTACLE_COLOR)
        obstacles: set[Cell] = set()
        for y in range(GRID_ROWS):
            for x in range(GRID_COLS):
                cell = mask[
                    x * CELL_SIZE + 4 : (x + 1) * CELL_SIZE - 4,
                    y * CELL_SIZE + 4 : (y + 1) * CELL_SIZE - 4,
                ]
                if cell.size and float(cell.mean()) > 0.55:
                    obstacles.add((x, y))
        return obstacles

    def _is_completed(self, pixels: np.ndarray) -> bool:
        status = pixels[:, :STATUS_HEIGHT, :]
        return float(_color_mask(status, SUCCESS_BG).mean()) > 0.5


class VisualVerifier:
    def __init__(self) -> None:
        self.observer = VisualObserver()

    def completed(self, surface: pygame.Surface) -> bool:
        return self.observer.observe(surface).completed
