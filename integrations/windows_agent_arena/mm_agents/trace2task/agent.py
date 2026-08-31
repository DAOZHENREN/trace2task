from __future__ import annotations

import base64
import io
import json
import os
from typing import Any

import requests


class Trace2TaskAgent:
    """Thin WAA client; planning and subscription auth remain on the Windows host."""

    action_space = "pyautogui"

    def __init__(self) -> None:
        self.bridge_url = os.environ.get(
            "TRACE2TASK_WAA_BRIDGE_URL",
            "http://host.docker.internal:8876",
        ).rstrip("/")
        self.token = os.environ.get("TRACE2TASK_WAA_TOKEN", "")
        if not self.token:
            raise RuntimeError("TRACE2TASK_WAA_TOKEN is required")
        self.timeout_seconds = float(os.environ.get("TRACE2TASK_WAA_TIMEOUT", "330"))

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-Trace2Task-Token": self.token}

    def reset(self) -> None:
        response = requests.post(
            f"{self.bridge_url}/v1/reset",
            headers=self._headers,
            json={},
            timeout=15,
        )
        response.raise_for_status()

    @staticmethod
    def _screenshot_bytes(value: Any) -> bytes:
        if isinstance(value, bytes):
            return value
        if hasattr(value, "save"):
            stream = io.BytesIO()
            value.save(stream, format="PNG")
            return stream.getvalue()
        raise TypeError(f"Unsupported WAA screenshot type: {type(value).__name__}")

    def predict(self, instruction: str, obs: dict[str, Any]):
        screenshot = self._screenshot_bytes(obs.get("screenshot"))
        response = requests.post(
            f"{self.bridge_url}/v1/plan",
            headers=self._headers,
            json={
                "instruction": instruction,
                "screenshot_base64": base64.b64encode(screenshot).decode("ascii"),
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        actions = payload["actions"]
        logs = {
            "user_question": instruction,
            "plan_result": json.dumps(payload, ensure_ascii=False, indent=2),
        }
        return payload, actions, logs, None
