from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pygame

from trace2task.actions import (
    ActionCall,
    is_runtime_text_placeholder,
    parameterized_action_schema,
)
from trace2task.codex_agent import resolve_codex_binary
from trace2task.codex_app_server import (
    DEFAULT_CODEX_MODEL,
    DEFAULT_CODEX_REASONING_EFFORT,
    CodexAppServerSession,
)
from trace2task.windows_task import WindowsTaskContract

BinaryResolver = Callable[[str], str]
SessionFactory = Callable[..., CodexAppServerSession]


@dataclass(frozen=True)
class WindowsAgentPlan:
    task_complete: bool
    actions: tuple[ActionCall, ...]
    reason: str
    confidence: float


class CodexWindowsAgent:
    """Plan bounded parameterized actions from current and successful reference frames."""

    def __init__(
        self,
        contract: WindowsTaskContract,
        *,
        model: str | None = DEFAULT_CODEX_MODEL,
        reasoning_effort: str = DEFAULT_CODEX_REASONING_EFFORT,
        codex_bin: str = "codex",
        plan_horizon: int = 4,
        timeout_seconds: float = 120,
        background: bool = False,
        binary_resolver: BinaryResolver = resolve_codex_binary,
        session_factory: SessionFactory = CodexAppServerSession,
    ) -> None:
        if plan_horizon <= 0 or plan_horizon > 4:
            raise ValueError("Windows plan_horizon must be between 1 and 4")
        self.contract = contract
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.codex_bin = codex_bin
        self.plan_horizon = plan_horizon
        self.timeout_seconds = timeout_seconds
        self.background = background
        self.binary_resolver = binary_resolver
        self.session_factory = session_factory
        self.replans = 0
        self._turn_index = 0
        self._history: list[str] = []
        self._session: CodexAppServerSession | None = None

    def plan(self, surface: pygame.Surface) -> WindowsAgentPlan:
        with tempfile.TemporaryDirectory(prefix="trace2task-windows-agent-") as directory:
            current_frame = Path(directory) / "current.png"
            pygame.image.save(surface, current_frame)
            reference_paths = (
                (self.contract.reference_frame,) if self._turn_index == 0 else ()
            )
            output = self._get_session().run_turn(
                prompt=self._prompt(),
                image_path=current_frame,
                additional_image_paths=reference_paths,
                output_schema=self._output_schema(),
            )
        self._turn_index += 1
        self.replans += 1
        return self._parse_payload(output)

    def observe_transition(self, action: ActionCall, applied: bool) -> None:
        payload = json.dumps(action.to_payload(), ensure_ascii=False, separators=(",", ":"))
        outcome = "applied" if applied else "blocked_or_failed"
        self._history.append(f"{payload}: {outcome}")
        self._history = self._history[-8:]

    def _get_session(self) -> CodexAppServerSession:
        if self._session is None:
            executable = self.binary_resolver(self.codex_bin)
            self._session = self.session_factory(
                executable,
                model=self.model,
                reasoning_effort=self.reasoning_effort,
                cwd=Path.cwd(),
                timeout_seconds=self.timeout_seconds,
            )
        return self._session

    def _prompt(self) -> str:
        task = self.contract.task
        history = "\n".join(self._history) if self._history else "none"
        if self._turn_index > 0:
            return (
                "Continue the same Windows task using this new authoritative screenshot as "
                "Image 1. The reviewed reference frame and recorded demonstration from the first "
                "turn remain the success template; do not ask for them again. Follow every prior "
                "motor, coordinate, and safety constraint.\n"
                f"Current run instruction: {self.contract.instruction}\n"
                f"Recent execution history:\n{history}\n\n"
                "For an external submission, verify both destination and content before returning "
                "the send/submit action. If Image 1 now satisfies the analogous success condition, "
                "return task_complete=true and no actions. Otherwise return a safe next batch of "
                f"1 to {self.plan_horizon} actions and stop before an uncertain visual branch. "
                "The response must match the supplied JSON schema."
            )

        demonstration = [action.to_payload() for action in self.contract.demonstration]
        allowed_skills = self._allowed_skills()
        execution_context = (
            "Execution mode: background window messages. The target stays behind the user's "
            "foreground app. Never return focus_window.\n"
            if self.background
            else "Execution mode: guarded foreground input.\n"
        )
        if self.contract.runtime_instruction is None:
            task_context = (
                f"Task: {task.instruction}\n"
                f"Success condition: {task.expected_result}\n"
            )
        else:
            task_context = (
                f"Task instruction for this run: {self.contract.instruction}\n"
                f"Original demonstration intent: {task.instruction}\n"
                "The demonstration and reference frame are structural examples, not literal "
                "values to copy. Names, message text, coordinates, and visible content may differ. "
                "Infer the concrete goal from the run instruction and verify an analogous successful "
                "result in the current UI.\n"
                f"Template success condition: {task.expected_result}\n"
            )
        return (
            "This is the first observation in a new Windows task run.\n"
            "You are the visual planner of a constrained Windows agent. Do not run commands, "
            "read files, or use tools. Image 1 is the current target client area. Image 2 is the "
            "human-reviewed successful reference frame. Compare them visually. The local motor "
            "controller alone will execute your structured actions.\n\n"
            f"{task_context}"
            f"Allowed motor skills: {', '.join(allowed_skills)}\n"
            f"{execution_context}"
            "Mouse x/y coordinates are normalized within Image 1: top-left is (0,0), "
            "bottom-right is (1,1).\n"
            "Use type_text for literal Unicode text, including Chinese and emoji; it never presses "
            "Enter and cannot contain newlines. For any message or other external submission, first "
            "verify the destination, then type the content, then stop the batch so the next screenshot "
            "can verify both before a later send/submit action.\n"
            "A recorded <runtime-text-N> value is a reserved semantic marker: resolve it from the "
            "current run instruction and the visibly focused field. Never return or type that marker "
            "literally.\n"
            f"Recorded demonstration (a hint, not a fixed script): "
            f"{json.dumps(demonstration, ensure_ascii=False, separators=(',', ':'))}\n"
            f"Recent execution history:\n{history}\n\n"
            "If Image 1 already satisfies the success condition, return task_complete=true and "
            "no actions. Otherwise return task_complete=false and a safe next action batch "
            f"between 1 and {self.plan_horizon} actions. Include multiple adjacent reviewed "
            "actions when no intermediate visual choice is required, but stop before a loading "
            "screen, uncertain branch, or state-dependent target. Replan from current pixels rather "
            "than blindly copying recorded coordinates. Never interact outside Image 1. The "
            "response must match the supplied JSON schema."
        )

    def _output_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_complete": {"type": "boolean"},
                "actions": {
                    "type": "array",
                    "items": parameterized_action_schema(self._allowed_skills()),
                    "minItems": 0,
                    "maxItems": self.plan_horizon,
                },
                "reason": {"type": "string", "minLength": 1},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["task_complete", "actions", "reason", "confidence"],
            "additionalProperties": False,
        }

    def _allowed_skills(self) -> tuple[str, ...]:
        if not self.background:
            return self.contract.task.actions
        skills = tuple(
            skill for skill in self.contract.task.actions if skill != "focus_window"
        )
        if not skills:
            raise RuntimeError("A background Windows task requires a non-focus motor skill")
        return skills

    def _parse_payload(self, output: str) -> WindowsAgentPlan:
        try:
            payload = json.loads(output.strip())
        except json.JSONDecodeError as error:
            raise RuntimeError(f"Codex returned invalid JSON: {output.strip()}") from error
        if not isinstance(payload, dict):
            raise TypeError("Codex Windows decision must be a JSON object")
        task_complete = payload.get("task_complete")
        raw_actions = payload.get("actions")
        reason = payload.get("reason")
        confidence = payload.get("confidence")
        if not isinstance(task_complete, bool):
            raise TypeError("Codex returned an invalid task_complete value")
        if not isinstance(raw_actions, list) or len(raw_actions) > self.plan_horizon:
            raise RuntimeError("Codex returned an invalid Windows action batch")
        actions = tuple(ActionCall.from_payload(raw_action) for raw_action in raw_actions)
        if any(
            action.skill == "type_text"
            and is_runtime_text_placeholder(action.args["text"])
            for action in actions
        ):
            raise RuntimeError(
                "Codex returned a reserved runtime-text demonstration marker literally"
            )
        if any(action.skill not in self._allowed_skills() for action in actions):
            raise RuntimeError(
                "Codex returned an action outside the Windows task pack or active execution mode"
            )
        if task_complete and actions:
            raise RuntimeError("Codex marked the task complete while still returning actions")
        if not task_complete and not actions:
            raise RuntimeError("Codex returned no action for an incomplete task")
        if not isinstance(reason, str) or not reason.strip():
            raise RuntimeError("Codex returned an empty Windows decision reason")
        if (
            not isinstance(confidence, (int, float))
            or isinstance(confidence, bool)
            or not 0 <= float(confidence) <= 1
        ):
            raise RuntimeError("Codex returned an invalid Windows confidence value")
        return WindowsAgentPlan(
            task_complete=task_complete,
            actions=actions,
            reason=reason.strip(),
            confidence=float(confidence),
        )

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None
