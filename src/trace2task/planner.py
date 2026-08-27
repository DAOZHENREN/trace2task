from __future__ import annotations

import heapq
from dataclasses import dataclass
from itertools import pairwise

from trace2task.game import ACTION_DELTAS, GRID_COLS, GRID_ROWS, Cell, neighbors


def manhattan(a: Cell, b: Cell) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def a_star(
    start: Cell,
    goal: Cell,
    obstacles: set[Cell],
    cols: int = GRID_COLS,
    rows: int = GRID_ROWS,
) -> list[Cell]:
    if start == goal:
        return [start]

    frontier: list[tuple[int, int, Cell]] = [(manhattan(start, goal), 0, start)]
    came_from: dict[Cell, Cell] = {}
    best_cost: dict[Cell, int] = {start: 0}

    while frontier:
        _, cost, current = heapq.heappop(frontier)
        if current == goal:
            path = [goal]
            while path[-1] != start:
                path.append(came_from[path[-1]])
            path.reverse()
            return path

        if cost != best_cost[current]:
            continue
        for candidate in neighbors(current, cols=cols, rows=rows):
            if candidate in obstacles:
                continue
            new_cost = cost + 1
            if new_cost < best_cost.get(candidate, 1_000_000):
                best_cost[candidate] = new_cost
                came_from[candidate] = current
                priority = new_cost + manhattan(candidate, goal)
                heapq.heappush(frontier, (priority, new_cost, candidate))

    return []


def step_to_action(current: Cell, next_cell: Cell) -> str:
    delta = (next_cell[0] - current[0], next_cell[1] - current[1])
    for action, action_delta in ACTION_DELTAS.items():
        if delta == action_delta:
            return action
    raise ValueError(f"Cells are not adjacent: {current!r} -> {next_cell!r}")


def path_to_actions(path: list[Cell], include_interact: bool = True) -> list[str]:
    actions = [step_to_action(current, nxt) for current, nxt in pairwise(path)]
    if include_interact:
        actions.append("interact")
    return actions


@dataclass(frozen=True)
class Plan:
    cells: list[Cell]
    action: str
    reason: str
