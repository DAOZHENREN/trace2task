from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pygame

from trace2task.agent import AgentDecision
from trace2task.codex_app_server import CodexAppServerSession
from trace2task.taskpack import TaskPack

BinaryResolver = Callable[[str], str]
SessionFactory = Callable[..., CodexAppServerSession]


def resolve_codex_binary(requested: str = "codex") -> str:
    """Resolve Codex CLI, including the versioned Windows desktop-app bundle."""

    explicit = Path(requested).expanduser()
    if explicit.is_file():
        return str(explicit.resolve())

    discovered = shutil.which(requested)
    if discovered:
        return discovered

    searched_roots: list[Path] = []
    if os.name == "nt" and requested.casefold() in {"codex", "codex.exe"}:
        for environment_variable in ("LOCALAPPDATA", "ProgramFiles"):
            base = os.environ.get(environment_variable)
            if not base:
                continue
            root = Path(base) / "OpenAI" / "Codex" / "bin"
            searched_roots.append(root)
            candidates = [root / "codex.exe", *root.glob("*/codex.exe")]
            existing = [candidate for candidate in candidates if candidate.is_file()]
            if existing:
                newest = max(existing, key=lambda candidate: candidate.stat().st_mtime_ns)
                return str(newest.resolve())

    searched = ", ".join(str(root) for root in searched_roots) or "the system PATH"
    raise RuntimeError(
        "Codex CLI was not found. Run 'codex login' if it is installed, restart the terminal "
        f"after a Codex app update, or pass --codex-bin with its full path. Searched: {searched}"
    )


class CodexMultimodalAgent:
    """Use one persistent Codex conversation as a multimodal route planner."""

    def __init__(
        self,
        task: TaskPack,
        *,
        model: str | None = "gpt-5.6-terra",
        codex_bin: str = "codex",
        plan_horizon: int = 12,
        timeout_seconds: float = 120,
        binary_resolver: BinaryResolver = resolve_codex_binary,
        session_factory: SessionFactory = CodexAppServerSession,
    ) -> None:
        if plan_horizon <= 0:
            raise ValueError("plan_horizon must be positive")
        self.task = task
        self.model = model
        self.codex_bin = codex_bin
        self.plan_horizon = plan_horizon
        self.timeout_seconds = timeout_seconds
        self.binary_resolver = binary_resolver
        self.session_factory = session_factory
        self.replans = 0
        self.goal_changes = 0
        self._pending_actions: list[str] = []
        self._pending_reason = ""
        self._pending_confidence = 0.0
        self._history: list[str] = []
        self._session: CodexAppServerSession | None = None
        self._turn_index = 0

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
                "provider": "codex_app_server",
                "model": self.model or "configured_default",
                "confidence": self._pending_confidence,
                "cached_actions_remaining": len(self._pending_actions),
                "plan_horizon": self.plan_horizon,
                "session_mode": "persistent",
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
            pygame.image.save(surface, frame_path)
            output = self._get_session().run_turn(
                prompt=self._prompt(),
                image_path=frame_path,
                output_schema=self._output_schema(),
            )
        self._turn_index += 1
        return self._parse_payload(output)

    def _get_session(self) -> CodexAppServerSession:
        if self._session is None:
            codex_executable = self.binary_resolver(self.codex_bin)
            self._session = self.session_factory(
                codex_executable,
                model=self.model,
                cwd=Path.cwd(),
                timeout_seconds=self.timeout_seconds,
            )
        return self._session

    def _prompt(self) -> str:
        history = ", ".join(self._history) if self._history else "none"
        actions = ", ".join(self.task.actions)
        session_context = (
            "This is the first observation in a new task run. "
            if self._turn_index == 0
            else "Continue the same task using this new authoritative screenshot. "
        )
        return (
            session_context
            + "You are the visual route planner of a constrained desktop agent. "
            "Inspect only the attached current screenshot. Do not run commands, read files, "
            "or use tools. The local motor controller will execute your returned actions quickly.\n\n"
            f"Task: {self.task.instruction}\n"
            f"Success condition: {self.task.expected_result}\n"
            f"Allowed actions: {actions}\n"
            f"Recent locally executed actions: {history}\n\n"
            f"Return 1 to {self.plan_horizon} actions. Plan a longer collision-free route when "
            "the board is clear. Prefer fewer actions near ambiguous obstacles. Use interact "
            "only when the target is within interaction range. The response must match the "
            "supplied JSON schema."
        )

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

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
