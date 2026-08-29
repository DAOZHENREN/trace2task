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
    environment_adapter: str
    verifier_type: str
    expected_result: str
    completion_mode: str
    require_departure_from_reference: bool
    completion_reason: str
    max_actions: int
    review_status: str
    requires_confirmation: bool
    experience_intent: str
    experience_examples: tuple[str, ...]
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
    environment = _require_mapping(root.get("environment"), "environment")
    verifier = _require_mapping(root.get("verifier"), "verifier")
    limits = _require_mapping(root.get("limits"), "limits")
    environment_adapter = environment.get("adapter")
    verifier_type = verifier.get("type")
    expected_result = verifier.get("expected")
    completion = verifier.get("completion")
    if completion is None:
        completion_mode = "state"
        require_departure_from_reference = False
        completion_reason = "Legacy task completes when the reviewed reference state is reached."
    else:
        completion_mapping = _require_mapping(completion, "verifier.completion")
        completion_mode = completion_mapping.get("mode")
        require_departure_from_reference = completion_mapping.get(
            "require_departure_from_reference"
        )
        completion_reason = completion_mapping.get("reason")
    max_actions = limits.get("max_actions")
    review = root.get("review")
    if review is None:
        review_status = "confirmed"
        requires_confirmation = False
    else:
        review_mapping = _require_mapping(review, "review")
        review_status = review_mapping.get("status")
        requires_confirmation = review_mapping.get("requires_confirmation")
    experience = root.get("experience")
    if experience is None:
        experience_intent = task_id
        experience_examples = [task_id]
    else:
        experience_mapping = _require_mapping(experience, "experience")
        experience_intent = experience_mapping.get("intent", task_id)
        experience_examples = experience_mapping.get("examples", [task_id])

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
    if not isinstance(environment_adapter, str) or not environment_adapter.strip():
        raise ValueError("Task pack environment must define a non-empty string 'adapter'")
    if not isinstance(verifier_type, str) or not verifier_type.strip():
        raise ValueError("Task pack verifier must define a non-empty string 'type'")
    if not isinstance(expected_result, str) or not expected_result.strip():
        raise ValueError("Task pack verifier must define a non-empty string 'expected'")
    if completion_mode not in {"state", "cycle"}:
        raise ValueError("Task pack verifier completion mode must be 'state' or 'cycle'")
    if not isinstance(require_departure_from_reference, bool):
        raise TypeError("Task pack verifier departure policy must be a boolean")
    if completion_mode == "state" and require_departure_from_reference:
        raise ValueError("State completion cannot require departure from the reference")
    if not isinstance(completion_reason, str) or not completion_reason.strip():
        raise ValueError("Task pack verifier completion reason must be a non-empty string")
    if not isinstance(max_actions, int) or isinstance(max_actions, bool) or max_actions <= 0:
        raise ValueError("Task pack limits.max_actions must be a positive integer")
    if review_status not in {"draft", "confirmed"}:
        raise ValueError("Task pack review.status must be 'draft' or 'confirmed'")
    if not isinstance(requires_confirmation, bool):
        raise TypeError("Task pack review.requires_confirmation must be a boolean")
    if (review_status == "draft") != requires_confirmation:
        raise ValueError("Draft task packs must require confirmation, and confirmed packs must not")
    if not isinstance(experience_intent, str) or not experience_intent.strip():
        raise ValueError("Task pack experience.intent must be a non-empty string")
    if (
        not isinstance(experience_examples, list)
        or not all(isinstance(example, str) and example.strip() for example in experience_examples)
    ):
        raise ValueError("Task pack experience.examples must be a string list")

    return TaskPack(
        task_id=task_id.strip(),
        instruction=" ".join(instruction.split()),
        actions=tuple(actions),
        environment_adapter=environment_adapter.strip(),
        verifier_type=verifier_type.strip(),
        expected_result=expected_result.strip(),
        completion_mode=completion_mode,
        require_departure_from_reference=require_departure_from_reference,
        completion_reason=" ".join(completion_reason.split()),
        max_actions=max_actions,
        review_status=review_status,
        requires_confirmation=requires_confirmation,
        experience_intent=experience_intent.strip(),
        experience_examples=tuple(" ".join(example.split()) for example in experience_examples),
        source_path=source_path,
    )
