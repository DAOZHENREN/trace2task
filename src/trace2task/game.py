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
WARNING_BG = (178, 63, 68)
PLAYER_COLOR = (40, 117, 230)
TARGET_COLOR = (244, 180, 0)
OBSTACLE_COLOR = (74, 82, 98)
TEXT_COLOR = (250, 251, 252)
PANEL_BG = (255, 255, 255)
PANEL_TEXT = (31, 36, 48)

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
    feedback: str | None = None

    @classmethod
    def reset(cls, seed: int, obstacle_ratio: float = 0.12) -> GameState:
        rng = random.Random(seed)
        cells = [(x, y) for y in range(GRID_ROWS) for x in range(GRID_COLS)]
        interior_cells = [(x, y) for y in range(1, GRID_ROWS - 1) for x in range(1, GRID_COLS - 1)]
        player = rng.choice(interior_cells)

        distant = [
            cell
            for cell in cells
            if cell != player and abs(cell[0] - player[0]) + abs(cell[1] - player[1]) >= 9
        ]
        target = rng.choice(distant)

        # Keep the four cells around the initial spawn clear. A fresh manual
        # recording should always provide immediate visual feedback for WASD.
        protected_spawn = {player, *neighbors(player)}
        candidates = [cell for cell in cells if cell not in protected_spawn | {target}]
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
            distance = abs(self.player[0] - self.target[0]) + abs(self.player[1] - self.target[1])
            if distance <= 1:
                self.completed = True
                self.feedback = None
                return True
            self.feedback = "TOO FAR   |   Move onto or next to the gold marker, then press E"
            return False
        if action not in ACTION_DELTAS:
            return False

        self.feedback = None
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
        self.title_font = pygame.font.Font(None, 44)

    def render(self, surface: pygame.Surface, state: GameState, *, mode: str = "record") -> None:
        surface.fill(BACKGROUND)
        if state.completed:
            status_color = SUCCESS_BG
        elif state.feedback:
            status_color = WARNING_BG
        else:
            status_color = STATUS_BG
        pygame.draw.rect(surface, status_color, (0, 0, BOARD_WIDTH, STATUS_HEIGHT))
        if state.completed:
            status = "DAILY TASK COMPLETE"
        elif state.feedback:
            status = state.feedback
        elif mode == "replay":
            status = "FIXED REPLAY   |   Input is automatic"
        elif mode == "agent":
            status = "AGENT MODE   |   Observing and replanning automatically"
        else:
            status = "RECORD MODE   |   WASD / arrows   |   Press E when touching gold"
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

        if state.completed:
            self._render_completion(surface, mode)

    def _render_completion(self, surface: pygame.Surface, mode: str) -> None:
        dimmer = pygame.Surface((BOARD_WIDTH, BOARD_HEIGHT), pygame.SRCALPHA)
        dimmer.fill((18, 24, 34, 105))
        surface.blit(dimmer, (0, STATUS_HEIGHT))

        panel = pygame.Rect(170, STATUS_HEIGHT + 175, 460, 210)
        pygame.draw.rect(surface, PANEL_BG, panel, border_radius=14)
        pygame.draw.rect(surface, SUCCESS_BG, panel, width=4, border_radius=14)

        if mode == "agent":
            subtitle = "The visual agent reached and verified the goal."
        elif mode == "replay":
            subtitle = "The recorded actions reached the goal."
        else:
            subtitle = "Your demonstration and final frame were saved."

        title = self.title_font.render("TASK COMPLETE", True, SUCCESS_BG)
        detail = self.small_font.render(subtitle, True, PANEL_TEXT)
        surface.blit(title, title.get_rect(center=(panel.centerx, panel.top + 58)))
        surface.blit(detail, detail.get_rect(center=(panel.centerx, panel.top + 112)))
        if mode == "record":
            dismiss = self.small_font.render("Press ENTER or ESC to close", True, PANEL_TEXT)
            surface.blit(dismiss, dismiss.get_rect(center=(panel.centerx, panel.top + 157)))
