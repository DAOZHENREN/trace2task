from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import pygame

from trace2task.game import Cell
from trace2task.planner import a_star, step_to_action
from trace2task.vision import VisualObservation, VisualObserver


@dataclass(frozen=True)
class AgentDecision:
    action: str
    reason: str
    path: list[Cell] = field(default_factory=list)
    observation: VisualObservation | None = None
    details: dict[str, Any] = field(default_factory=dict)


class AgentAdapter(Protocol):
    """Small interface shared by deterministic and model-backed agents."""

    replans: int
    goal_changes: int

    def decide(self, surface: pygame.Surface) -> AgentDecision | None: ...

    def observe_transition(self, action: str, applied: bool) -> None: ...

    def invalidate_plan(self, reason: str) -> None: ...


class VisualReplanningAgent:
    """A small hybrid agent: pixel perception plus deterministic motor planning."""

    def __init__(self, observer: VisualObserver | None = None) -> None:
        self.observer = observer or VisualObserver()
        self.goal_memory: Cell | None = None
        self.replans = 0
        self.goal_changes = 0

    def decide(self, surface: pygame.Surface) -> AgentDecision | None:
        observation = self.observer.observe(surface)
        if observation.completed:
            return None
        if observation.player is None:
            raise RuntimeError("The visual agent cannot find the player in the current frame")

        reason = "state_refresh"
        if observation.target is not None and observation.target != self.goal_memory:
            if self.goal_memory is not None:
                self.goal_changes += 1
                reason = "target_changed"
            else:
                reason = "target_acquired"
            self.goal_memory = observation.target

        if self.goal_memory is None:
            raise RuntimeError("The visual agent cannot find the task target in the current frame")
        if observation.player == self.goal_memory:
            return AgentDecision(
                action="interact",
                reason="at_goal",
                path=[observation.player],
                observation=observation,
            )

        path = a_star(observation.player, self.goal_memory, observation.obstacles)
        self.replans += 1
        if len(path) < 2:
            raise RuntimeError("The visual agent could not find a path to the target")
        return AgentDecision(
            action=step_to_action(path[0], path[1]),
            reason=reason,
            path=path,
            observation=observation,
        )

    def observe_transition(self, action: str, applied: bool) -> None:
        """The deterministic observer replans from pixels on every step."""

    def invalidate_plan(self, reason: str) -> None:
        """No cache to clear: the deterministic agent observes every frame."""
