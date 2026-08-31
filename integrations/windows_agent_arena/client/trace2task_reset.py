from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests

RESET_SPEC_ENV = "TRACE2TASK_WAA_RESET_SPEC"


def _reset_paths(example: dict[str, Any]) -> list[str]:
    spec_value = os.environ.get(RESET_SPEC_ENV, "").strip()
    if not spec_value:
        return []
    spec_path = Path(spec_value)
    payload = json.loads(spec_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("Trace2Task WAA reset spec must be a JSON object")
    tasks = payload.get("tasks")
    if not isinstance(tasks, dict):
        raise TypeError("Trace2Task WAA reset spec tasks must be an object")
    task_id = example.get("id")
    task_spec = tasks.get(task_id)
    if not isinstance(task_spec, dict):
        raise TypeError(f"Trace2Task WAA reset spec has no entry for task {task_id!r}")
    paths = task_spec.get("must_not_exist")
    if not isinstance(paths, list) or not paths:
        raise TypeError("Trace2Task WAA reset must_not_exist must be a non-empty list")
    if not all(isinstance(path, str) and path.strip() for path in paths):
        raise TypeError("Trace2Task WAA reset paths must be non-empty strings")
    return paths


def _request_reset(
    env,
    example: dict[str, Any],
    action: str,
) -> dict[str, Any] | None:
    paths = _reset_paths(example)
    if not paths:
        return None
    response = requests.post(
        f"http://{env.vm_ip}:5000/trace2task/reset",
        json={"action": action, "must_not_exist": paths},
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(
            f"Trace2Task WAA reset {action} failed ({response.status_code}): "
            f"{response.text}"
        )
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise RuntimeError(f"Trace2Task WAA reset {action} returned an invalid receipt")
    return payload


def apply_trace2task_reset(
    env,
    example: dict[str, Any],
) -> dict[str, Any] | None:
    if not os.environ.get(RESET_SPEC_ENV, "").strip():
        return None
    env.setup_controller._close_all_setup()
    return _request_reset(env, example, "apply")


def verify_trace2task_reset(
    env,
    example: dict[str, Any],
) -> dict[str, Any] | None:
    return _request_reset(env, example, "verify")
