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


def _required_after_setup_paths(example: dict[str, Any]) -> list[str]:
    spec_value = os.environ.get(RESET_SPEC_ENV, "").strip()
    if not spec_value:
        return []
    payload = json.loads(Path(spec_value).read_text(encoding="utf-8"))
    tasks = payload.get("tasks") if isinstance(payload, dict) else None
    task_spec = tasks.get(example.get("id")) if isinstance(tasks, dict) else None
    if not isinstance(task_spec, dict):
        return []
    paths = task_spec.get("must_exist_after_setup", [])
    if not isinstance(paths, list) or not all(
        isinstance(path, str) and path.strip() for path in paths
    ):
        raise TypeError(
            "Trace2Task WAA reset must_exist_after_setup must be a list of "
            "non-empty strings"
        )
    return paths


def _verify_required_after_setup(env, example: dict[str, Any]) -> list[str]:
    paths = _required_after_setup_paths(example)
    if not paths:
        return []
    code = (
        "from pathlib import Path; import json; paths = "
        + json.dumps(paths)
        + "; print(json.dumps([path for path in paths if not Path(path).exists()]))"
    )
    response = requests.post(
        f"http://{env.vm_ip}:5000/execute",
        json={"command": ["python", "-c", code], "shell": False},
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(
            "Trace2Task WAA setup readiness check failed "
            f"({response.status_code}): {response.text}"
        )
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("returncode") != 0:
        raise RuntimeError("Trace2Task WAA setup readiness check returned an error")
    try:
        missing = json.loads(str(payload.get("output", "")).strip() or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Trace2Task WAA setup readiness check returned invalid output"
        ) from exc
    if missing:
        raise RuntimeError(
            "Trace2Task WAA setup did not create required files: "
            + ", ".join(str(path) for path in missing)
        )
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
    receipt = _request_reset(env, example, "verify")
    required = _verify_required_after_setup(env, example)
    if receipt is not None and required:
        receipt["verified_present"] = required
    return receipt
