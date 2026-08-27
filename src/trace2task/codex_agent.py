from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pygame

from trace2task.agent import AgentDecision
from trace2task.taskpack import TaskPack

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


class CodexMultimodalAgent:
    """Use Codex CLI's saved ChatGPT login as a multimodal decision provider."""

    def __init__(
        self,
        task: TaskPack,
        *,
        model: str | None = "gpt-5.6-terra",
        codex_bin: str = "codex",
        plan_horizon: int = 4,
        timeout_seconds: float = 120,
        command_runner: CommandRunner = subprocess.run,
    ) -> None:
        if plan_horizon <= 0:
            raise ValueError("plan_horizon must be positive")
        self.task = task
        self.model = model
        self.codex_bin = codex_bin
        self.plan_horizon = plan_horizon
        self.timeout_seconds = timeout_seconds
        self.command_runner = command_runner
        self.replans = 0
        self.goal_changes = 0
        self._pending_actions: list[str] = []
        self._pending_reason = ""
        self._pending_confidence = 0.0
        self._history: list[str] = []

    def decide(self, surface: pygame.Surface) -> AgentDecision:
        if not self._pending_actions:
            payload = self._request_plan(surface)
            self._pending_actions = list(payload["actions"])
            self._pending_reason = payload["reason"]
            self._pending_confidence = float(payload["confidence"])
            self.replans += 1

        action = self._pending_actions.pop(0)
        return AgentDecision(
            action=action,
            reason=self._pending_reason,
            details={
                "provider": "codex_cli",
                "model": self.model or "configured_default",
                "confidence": self._pending_confidence,
                "cached_actions_remaining": len(self._pending_actions),
            },
        )

    def observe_transition(self, action: str, applied: bool) -> None:
        self._history.append(f"{action}: {'applied' if applied else 'blocked_or_failed'}")
        self._history = self._history[-6:]
        if not applied:
            self._pending_actions.clear()

    def invalidate_plan(self, reason: str) -> None:
        self._pending_actions.clear()
        self._history.append(f"environment_changed: {reason}")
        self._history = self._history[-6:]
        self.goal_changes += 1

    def _request_plan(self, surface: pygame.Surface) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="trace2task-codex-") as directory:
            temp_dir = Path(directory)
            frame_path = temp_dir / "observation.png"
            schema_path = temp_dir / "action.schema.json"
            pygame.image.save(surface, frame_path)
            schema_path.write_text(
                json.dumps(self._output_schema(), ensure_ascii=False),
                encoding="utf-8",
            )

            command = [
                self.codex_bin,
                "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "--output-schema",
                str(schema_path),
                "--image",
                str(frame_path),
            ]
            if self.model:
                command.extend(["--model", self.model])
            command.append(self._prompt())

            try:
                completed = self.command_runner(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except FileNotFoundError as error:
                raise RuntimeError(
                    "Codex CLI was not found. Install it and run 'codex login' first."
                ) from error
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(
                    f"Codex did not return a decision within {self.timeout_seconds:g} seconds"
                ) from error

        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(f"Codex decision failed: {message or 'unknown CLI error'}")
        return self._parse_payload(completed.stdout)

    def _prompt(self) -> str:
        history = ", ".join(self._history) if self._history else "none"
        actions = ", ".join(self.task.actions)
        return (
            "You are the visual decision component of a constrained desktop agent. "
            "Inspect only the attached current screenshot. Do not run commands, read files, "
            "or use tools. Choose a short safe action plan for the current visible state.\n\n"
            f"Task: {self.task.instruction}\n"
            f"Success condition: {self.task.expected_result}\n"
            f"Allowed actions: {actions}\n"
            f"Recent executed actions: {history}\n\n"
            f"Return 1 to {self.plan_horizon} actions. Prefer fewer actions when uncertain or "
            "near an obstacle, and use interact only when the screenshot indicates the target "
            "is within interaction range. The response must match the supplied JSON schema."
        )

    def _output_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "actions": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(self.task.actions)},
                    "minItems": 1,
                    "maxItems": self.plan_horizon,
                },
                "reason": {"type": "string", "minLength": 1},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["actions", "reason", "confidence"],
            "additionalProperties": False,
        }

    def _parse_payload(self, output: str) -> dict[str, Any]:
        try:
            payload = json.loads(output.strip())
        except json.JSONDecodeError as error:
            raise RuntimeError(f"Codex returned invalid JSON: {output.strip()}") from error
        if not isinstance(payload, dict):
            raise TypeError("Codex decision must be a JSON object")

        actions = payload.get("actions")
        reason = payload.get("reason")
        confidence = payload.get("confidence")
        if (
            not isinstance(actions, list)
            or not 1 <= len(actions) <= self.plan_horizon
            or not all(isinstance(action, str) and action in self.task.actions for action in actions)
        ):
            raise RuntimeError("Codex returned an invalid or disallowed action plan")
        if not isinstance(reason, str) or not reason.strip():
            raise RuntimeError("Codex returned an empty decision reason")
        if (
            not isinstance(confidence, (int, float))
            or isinstance(confidence, bool)
            or not 0 <= float(confidence) <= 1
        ):
            raise RuntimeError("Codex returned an invalid confidence value")
        return {
            "actions": actions,
            "reason": reason.strip(),
            "confidence": float(confidence),
        }
