from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field

import pygame

Cell = tuple[int, int]

CELL_SIZE = 40
GRID_COLS = 20
GRID_ROWS = 15
STATUS_HEIGHT = 56
BOARD_WIDTH = GRID_COLS * CELL_SIZE
BOARD_HEIGHT = GRID_ROWS * CELL_SIZE
WINDOW_SIZE = (BOARD_WIDTH, BOARD_HEIGHT + STATUS_HEIGHT)

BACKGROUND = (246, 248, 252)
GRID_LINE = (222, 226, 234)
STATUS_BG = (31, 36, 48)
SUCCESS_BG = (35, 134, 88)
PLAYER_COLOR = (40, 117, 230)
TARGET_COLOR = (244, 180, 0)
OBSTACLE_COLOR = (74, 82, 98)
TEXT_COLOR = (250, 251, 252)

ACTION_DELTAS: dict[str, Cell] = {
    "move_up": (0, -1),
    "move_down": (0, 1),
    "move_left": (-1, 0),
    "move_right": (1, 0),
}


def neighbors(cell: Cell, cols: int = GRID_COLS, rows: int = GRID_ROWS) -> list[Cell]:
    x, y = cell
    result: list[Cell] = []
    for dx, dy in ACTION_DELTAS.values():
        candidate = (x + dx, y + dy)
        if 0 <= candidate[0] < cols and 0 <= candidate[1] < rows:
            result.append(candidate)
    return result


def path_exists(start: Cell, goal: Cell, obstacles: set[Cell]) -> bool:
    queue = deque([start])
    visited = {start}
    while queue:
        current = queue.popleft()
        if current == goal:
            return True
        for candidate in neighbors(current):
            if candidate not in obstacles and candidate not in visited:
                visited.add(candidate)
                queue.append(candidate)
    return False


@dataclass
class GameState:
    seed: int
    player: Cell
    target: Cell
    obstacles: set[Cell] = field(default_factory=set)
    completed: bool = False
    move_attempts: int = 0
    successful_moves: int = 0
    interactions: int = 0
    relocations: int = 0

    @classmethod
    def reset(cls, seed: int, obstacle_ratio: float = 0.12) -> GameState:
        rng = random.Random(seed)
        cells = [(x, y) for y in range(GRID_ROWS) for x in range(GRID_COLS)]
        player = rng.choice(cells)

        distant = [
            cell
            for cell in cells
            if cell != player and abs(cell[0] - player[0]) + abs(cell[1] - player[1]) >= 9
        ]
        target = rng.choice(distant)

        candidates = [cell for cell in cells if cell not in {player, target}]
        rng.shuffle(candidates)
        desired = int(len(cells) * obstacle_ratio)
        obstacles: set[Cell] = set()
        for candidate in candidates:
            if len(obstacles) >= desired:
                break
            obstacles.add(candidate)
            if not path_exists(player, target, obstacles):
                obstacles.remove(candidate)

        return cls(seed=seed, player=player, target=target, obstacles=obstacles)

    def apply(self, action: str) -> bool:
        if self.completed:
            return False
        if action == "interact":
            self.interactions += 1
            if self.player == self.target:
                self.completed = True
                return True
            return False
        if action not in ACTION_DELTAS:
            return False

        self.move_attempts += 1
        dx, dy = ACTION_DELTAS[action]
        candidate = (self.player[0] + dx, self.player[1] + dy)
        if (
            0 <= candidate[0] < GRID_COLS
            and 0 <= candidate[1] < GRID_ROWS
            and candidate not in self.obstacles
        ):
            self.player = candidate
            self.successful_moves += 1
            return True
        return False

    def relocate_target(self) -> Cell:
        rng = random.Random(self.seed * 1009 + self.relocations * 7919 + self.successful_moves)
        candidates = [
            (x, y)
            for y in range(GRID_ROWS)
            for x in range(GRID_COLS)
            if (x, y) not in self.obstacles
            and (x, y) != self.player
            and abs(x - self.player[0]) + abs(y - self.player[1]) >= 7
        ]
        rng.shuffle(candidates)
        for candidate in candidates:
            if path_exists(self.player, candidate, self.obstacles):
                self.target = candidate
                self.relocations += 1
                return candidate
        raise RuntimeError("Unable to relocate the target to a reachable cell")


class GameRenderer:
    def __init__(self) -> None:
        if not pygame.font.get_init():
            pygame.font.init()
        self.font = pygame.font.Font(None, 27)
        self.small_font = pygame.font.Font(None, 20)

    def render(self, surface: pygame.Surface, state: GameState) -> None:
        surface.fill(BACKGROUND)
        status_color = SUCCESS_BG if state.completed else STATUS_BG
        pygame.draw.rect(surface, status_color, (0, 0, BOARD_WIDTH, STATUS_HEIGHT))
        status = (
            "DAILY TASK COMPLETE"
            if state.completed
            else "Reach the gold marker, then press E   |   Move: WASD / arrows"
        )
        surface.blit(self.font.render(status, True, TEXT_COLOR), (18, 17))

        board = pygame.Rect(0, STATUS_HEIGHT, BOARD_WIDTH, BOARD_HEIGHT)
        pygame.draw.rect(surface, BACKGROUND, board)

        for x in range(GRID_COLS + 1):
            px = x * CELL_SIZE
            pygame.draw.line(surface, GRID_LINE, (px, STATUS_HEIGHT), (px, WINDOW_SIZE[1]))
        for y in range(GRID_ROWS + 1):
            py = STATUS_HEIGHT + y * CELL_SIZE
            pygame.draw.line(surface, GRID_LINE, (0, py), (BOARD_WIDTH, py))

        for x, y in state.obstacles:
            rect = pygame.Rect(
                x * CELL_SIZE + 4,
                STATUS_HEIGHT + y * CELL_SIZE + 4,
                CELL_SIZE - 8,
                CELL_SIZE - 8,
            )
            pygame.draw.rect(surface, OBSTACLE_COLOR, rect, border_radius=5)

        target_center = (
            state.target[0] * CELL_SIZE + CELL_SIZE // 2,
            STATUS_HEIGHT + state.target[1] * CELL_SIZE + CELL_SIZE // 2,
        )
        pygame.draw.circle(surface, TARGET_COLOR, target_center, CELL_SIZE // 3)
        target_text = self.small_font.render("E", True, (72, 52, 0))
        surface.blit(target_text, target_text.get_rect(center=target_center))

        player_rect = pygame.Rect(
            state.player[0] * CELL_SIZE + 8,
            STATUS_HEIGHT + state.player[1] * CELL_SIZE + 8,
            CELL_SIZE - 16,
            CELL_SIZE - 16,
        )
        pygame.draw.rect(surface, PLAYER_COLOR, player_rect, border_radius=6)
