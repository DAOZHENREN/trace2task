from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class TaskPack:
    task_id: str
    instruction: str
    actions: tuple[str, ...]
    expected_result: str
    max_actions: int
    source_path: Path


def _require_mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"Task pack field '{field}' must be a mapping")
    return value


def load_taskpack(path: Path) -> TaskPack:
    """Load the small task contract consumed by any AgentAdapter."""

    source_path = path.resolve()
    data = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    root = _require_mapping(data, "root")

    task_id = root.get("id")
    instruction = root.get("instruction")
    actions = root.get("actions")
    verifier = _require_mapping(root.get("verifier"), "verifier")
    limits = _require_mapping(root.get("limits"), "limits")
    expected_result = verifier.get("expected")
    max_actions = limits.get("max_actions")

    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("Task pack must define a non-empty string 'id'")
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValueError("Task pack must define a non-empty string 'instruction'")
    if (
        not isinstance(actions, list)
        or not actions
        or not all(isinstance(action, str) and action for action in actions)
    ):
        raise ValueError("Task pack must define a non-empty string list 'actions'")
    if len(set(actions)) != len(actions):
        raise ValueError("Task pack actions must be unique")
    if not isinstance(expected_result, str) or not expected_result.strip():
        raise ValueError("Task pack verifier must define a non-empty string 'expected'")
    if not isinstance(max_actions, int) or isinstance(max_actions, bool) or max_actions <= 0:
        raise ValueError("Task pack limits.max_actions must be a positive integer")

    return TaskPack(
        task_id=task_id.strip(),
        instruction=" ".join(instruction.split()),
        actions=tuple(actions),
        expected_result=expected_result.strip(),
        max_actions=max_actions,
        source_path=source_path,
    )
