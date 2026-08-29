from __future__ import annotations

import json
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
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
MAX_STAGE_SESSION_TURNS = 4
MAX_DECISION_REPAIR_ATTEMPTS = 1
WINDOWS_DECISION_TIMEOUT_SECONDS = 300
MODEL_CURRENT_IMAGE_MAX_EDGE = 1_440
MODEL_REFERENCE_IMAGE_MAX_EDGE = 1_280


class _IncompletePlanWithoutActionsError(RuntimeError):
    """A model decision that is safe to reject and repair before execution."""

    def __init__(self, message: str, *, payload: dict[str, Any]) -> None:
        super().__init__(message)
        self.payload = payload


@dataclass(frozen=True)
class WindowsPlanTiming:
    total_ms: float = 0.0
    frame_encode_ms: float = 0.0
    prompt_build_ms: float = 0.0
    model_roundtrip_ms: float = 0.0
    request_ack_ms: float = 0.0
    model_completion_wait_ms: float = 0.0
    thread_start_ms: float = 0.0
    parse_ms: float = 0.0
    prompt_chars: int = 0
    image_count: int = 0
    session_generation: int = 0
    session_reused: bool = False
    session_reset_reason: str = "initial"
    decision_repair_attempts: int = 0
    decision_repair_stage_id: str | None = None


@dataclass(frozen=True)
class WindowsAgentPlan:
    task_complete: bool
    actions: tuple[ActionCall, ...]
    reason: str
    confidence: float
    stage_id: str = "unknown"
    stage_goal: str = ""
    expected_end_state: str = ""
    abort_conditions: tuple[str, ...] = ()
    model: str | None = None
    reasoning_effort: str = DEFAULT_CODEX_REASONING_EFFORT
    timing: WindowsPlanTiming = field(default_factory=WindowsPlanTiming)


class CodexWindowsAgent:
    """Plan bounded parameterized actions from current and successful reference frames."""

    def __init__(
        self,
        contract: WindowsTaskContract,
        *,
        model: str | None = DEFAULT_CODEX_MODEL,
        reasoning_effort: str = DEFAULT_CODEX_REASONING_EFFORT,
        codex_bin: str = "codex",
        plan_horizon: int = 12,
        timeout_seconds: float = WINDOWS_DECISION_TIMEOUT_SECONDS,
        background: bool = False,
        adaptive_reasoning: bool = True,
        binary_resolver: BinaryResolver = resolve_codex_binary,
        session_factory: SessionFactory = CodexAppServerSession,
    ) -> None:
        if plan_horizon <= 0 or plan_horizon > 12:
            raise ValueError("Windows plan_horizon must be between 1 and 12")
        self.contract = contract
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.codex_bin = codex_bin
        self.plan_horizon = plan_horizon
        self.timeout_seconds = timeout_seconds
        self.background = background
        self.adaptive_reasoning = adaptive_reasoning
        self.binary_resolver = binary_resolver
        self.session_factory = session_factory
        self.replans = 0
        self._turn_index = 0
        self._history: list[str] = []
        self._session: CodexAppServerSession | None = None
        self._session_context_key: str | None = None
        self._session_turns = 0
        self._active_stage_id: str | None = None
        self._escalation_level = 0
        self.session_resets = 0

    def plan(self, surface: pygame.Surface) -> WindowsAgentPlan:
        total_started = time.perf_counter()
        session, reset_reason, fresh_context = self._prepare_session()
        model_roundtrip_ms = 0.0
        prompt_build_ms = 0.0
        parse_ms = 0.0
        prompt_chars = 0
        repair_attempts = 0
        repair_stage_id: str | None = None
        server_metrics: list[Any] = []
        with tempfile.TemporaryDirectory(prefix="trace2task-windows-agent-") as directory:
            model_directory = Path(directory)
            current_frame = model_directory / "current.png"
            encode_started = time.perf_counter()
            self._save_model_surface(surface, current_frame)
            reference_paths = self._reference_paths(fresh_context=fresh_context)
            model_reference_paths = self._prepare_model_references(
                reference_paths,
                model_directory,
            )
            frame_encode_ms = (time.perf_counter() - encode_started) * 1000
            active_model, active_effort = self._planning_profile()
            prompt_started = time.perf_counter()
            prompt = self._prompt(
                fresh_context=fresh_context,
                reference_paths=reference_paths,
            )
            output_schema = self._output_schema()
            prompt_build_ms += (time.perf_counter() - prompt_started) * 1000
            prompt_chars += len(prompt)
            additional_image_paths = model_reference_paths
            while True:
                model_started = time.perf_counter()
                output = session.run_turn(
                    prompt=prompt,
                    image_path=current_frame,
                    additional_image_paths=additional_image_paths,
                    output_schema=output_schema,
                    model=active_model,
                    reasoning_effort=active_effort,
                )
                model_roundtrip_ms += (time.perf_counter() - model_started) * 1000
                server_metrics.append(getattr(session, "last_turn_metrics", None))
                parse_started = time.perf_counter()
                try:
                    plan = self._parse_payload(
                        output,
                        model=active_model,
                        reasoning_effort=active_effort,
                    )
                except _IncompletePlanWithoutActionsError as error:
                    parse_ms += (time.perf_counter() - parse_started) * 1000
                    if repair_attempts >= MAX_DECISION_REPAIR_ATTEMPTS:
                        target = repair_stage_id or "unknown"
                        raise RuntimeError(
                            "Codex repeated an empty incomplete decision after "
                            f"same-session repair toward stage {target}"
                        ) from error
                    repair_attempts += 1
                    repair_stage_id = self._repair_stage_id(error.payload)
                    repair_prompt_started = time.perf_counter()
                    prompt = self._decision_repair_prompt(
                        error.payload,
                        repair_stage_id=repair_stage_id,
                    )
                    output_schema = self._repair_output_schema()
                    prompt_build_ms += (
                        time.perf_counter() - repair_prompt_started
                    ) * 1000
                    prompt_chars += len(prompt)
                    # Keep the same model thread and the exact same captured pixels. The
                    # repair turn is validation feedback, not a fresh observation.
                    additional_image_paths = ()
                    continue
                parse_ms += (time.perf_counter() - parse_started) * 1000
                break
        self._turn_index += 1
        self._session_turns += 1 + repair_attempts
        self.replans += 1
        final_server_metrics = server_metrics[-1] if server_metrics else None
        timing = WindowsPlanTiming(
            total_ms=(time.perf_counter() - total_started) * 1000,
            frame_encode_ms=frame_encode_ms,
            prompt_build_ms=prompt_build_ms,
            model_roundtrip_ms=model_roundtrip_ms,
            request_ack_ms=sum(
                float(getattr(metrics, "request_ack_ms", 0.0))
                for metrics in server_metrics
            ),
            model_completion_wait_ms=sum(
                float(getattr(metrics, "completion_wait_ms", 0.0))
                for metrics in server_metrics
            ),
            thread_start_ms=sum(
                float(getattr(metrics, "thread_start_ms", 0.0))
                for metrics in server_metrics
            ),
            parse_ms=parse_ms,
            prompt_chars=prompt_chars,
            image_count=1 + len(model_reference_paths) + repair_attempts,
            session_generation=int(
                getattr(final_server_metrics, "thread_generation", 0)
            ),
            session_reused=bool(
                getattr(final_server_metrics, "thread_reused", not fresh_context)
            ),
            session_reset_reason=reset_reason,
            decision_repair_attempts=repair_attempts,
            decision_repair_stage_id=repair_stage_id,
        )
        plan = replace(plan, timing=timing)
        if plan.stage_id != "unknown":
            self._active_stage_id = plan.stage_id
        self._update_escalation(plan)
        return plan

    @staticmethod
    def _model_size(
        size: tuple[int, int],
        *,
        max_edge: int,
    ) -> tuple[int, int]:
        width, height = size
        longest = max(width, height)
        if longest <= max_edge:
            return size
        scale = max_edge / longest
        return max(1, round(width * scale)), max(1, round(height * scale))

    @classmethod
    def _save_model_surface(
        cls,
        surface: pygame.Surface,
        path: Path,
        *,
        max_edge: int = MODEL_CURRENT_IMAGE_MAX_EDGE,
    ) -> None:
        target_size = cls._model_size(surface.get_size(), max_edge=max_edge)
        prepared = (
            pygame.transform.smoothscale(surface, target_size)
            if target_size != surface.get_size()
            else surface
        )
        pygame.image.save(prepared, path)

    @classmethod
    def _prepare_model_references(
        cls,
        reference_paths: tuple[Path, ...],
        directory: Path,
    ) -> tuple[Path, ...]:
        prepared: list[Path] = []
        for index, source_path in enumerate(reference_paths):
            surface = pygame.image.load(source_path)
            if (
                cls._model_size(
                    surface.get_size(),
                    max_edge=MODEL_REFERENCE_IMAGE_MAX_EDGE,
                )
                == surface.get_size()
            ):
                prepared.append(source_path)
                continue
            model_path = directory / f"reference-{index:02d}.png"
            cls._save_model_surface(
                surface,
                model_path,
                max_edge=MODEL_REFERENCE_IMAGE_MAX_EDGE,
            )
            prepared.append(model_path)
        return tuple(prepared)

    def _reference_paths(self, *, fresh_context: bool) -> tuple[Path, ...]:
        if not fresh_context:
            return ()
        paths = [self.contract.reference_frame]
        experience = self.contract.semantic_experience
        if experience is not None:
            if self._turn_index == 0:
                evidence = experience.evidence_paths(experience.source_path.parent)
                if len(evidence) > 4:
                    indexes = {
                        round(index * (len(evidence) - 1) / 3)
                        for index in range(4)
                    }
                    evidence = tuple(evidence[index] for index in sorted(indexes))
            else:
                active = next(
                    (
                        stage
                        for stage in experience.stages
                        if stage.stage_id == self._active_stage_id
                    ),
                    None,
                )
                evidence = (
                    (
                        (
                            experience.source_path.parent
                            / active.state_before.evidence_frame
                        ).resolve(),
                        (
                            experience.source_path.parent
                            / active.state_after.evidence_frame
                        ).resolve(),
                    )
                    if active is not None
                    else ()
                )
            for path in evidence:
                if path not in paths:
                    paths.append(path)
        return tuple(paths)

    def observe_transition(self, action: ActionCall, applied: bool) -> None:
        payload = json.dumps(action.to_payload(), ensure_ascii=False, separators=(",", ":"))
        outcome = "applied" if applied else "blocked_or_failed"
        self._history.append(f"{payload}: {outcome}")
        self._history = self._history[-8:]
        if not applied and self.adaptive_reasoning:
            self._escalation_level = 2

    def observe_completion_rejected(self, reason: str) -> None:
        self._history.append(f"task_complete rejected by local verifier: {reason}")
        self._history = self._history[-8:]
        if self.adaptive_reasoning:
            self._escalation_level = max(self._escalation_level, 1)

    def _planning_profile(self) -> tuple[str | None, str]:
        if not self.adaptive_reasoning or self._escalation_level <= 0:
            return self.model, self.reasoning_effort
        model_order = ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"]
        effort_order = ["low", "medium", "high", "xhigh", "max"]
        active_model = self.model
        if active_model in model_order:
            model_index = min(
                model_order.index(active_model) + self._escalation_level,
                len(model_order) - 1,
            )
            active_model = model_order[model_index]
        effort_index = min(
            effort_order.index(self.reasoning_effort) + self._escalation_level,
            len(effort_order) - 1,
        )
        return active_model, effort_order[effort_index]

    def _update_escalation(self, plan: WindowsAgentPlan) -> None:
        if not self.adaptive_reasoning:
            self._escalation_level = 0
        elif (
            self.contract.semantic_experience is not None
            and plan.stage_id == "unknown"
        ) or plan.confidence < 0.7:
            self._escalation_level = 2
        elif plan.confidence < 0.85:
            self._escalation_level = 1
        else:
            self._escalation_level = 0

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

    def _prepare_session(self) -> tuple[CodexAppServerSession, str, bool]:
        context_key = self._active_stage_id or "bootstrap"
        reset_reason = "none"
        fresh_context = self._session is None
        if self._session is not None:
            if self._session_context_key != context_key:
                reset_reason = "stage_change"
            elif self._session_turns >= MAX_STAGE_SESSION_TURNS:
                reset_reason = "context_limit"
            if reset_reason != "none":
                reset_thread = getattr(self._session, "reset_thread", None)
                if callable(reset_thread):
                    reset_thread()
                else:
                    self._session.close()
                    self._session = None
                self._session_turns = 0
                self.session_resets += 1
                fresh_context = True
        if self._session_context_key is None or fresh_context:
            self._session_context_key = context_key
        if reset_reason == "none" and fresh_context:
            reset_reason = "initial"
        return self._get_session(), reset_reason, fresh_context

    def _prompt(
        self,
        *,
        fresh_context: bool = False,
        reference_paths: tuple[Path, ...] = (),
    ) -> str:
        task = self.contract.task
        history = "\n".join(self._history) if self._history else "none"
        completion_context = self._completion_context()
        if self._turn_index > 0:
            stage_context = self._active_stage_context(reference_paths)
            session_intro = (
                "This is a fresh bounded model session for the current semantic stage. No prior "
                "messages are available; the compact authoritative stage context and exact "
                "stage-boundary images below are self-contained. Image 1 is the current target "
                "client area and Image 2 is the final success reference.\n"
                if fresh_context
                else "Continue the current semantic stage using Image 1 as the new authoritative "
                "target screenshot.\n"
            )
            return (
                f"{session_intro}"
                "The local motor controller alone executes structured actions. Follow the motor, "
                "coordinate, and safety constraints included below.\n"
                f"Current run instruction: {self.contract.instruction}\n"
                f"{completion_context}"
                f"{stage_context}"
                f"Recent execution history:\n{history}\n\n"
                "For an external submission, verify both destination and content before returning "
                "the send/submit action. If the completion policy permits completion and Image 1 "
                "now satisfies the analogous success condition, "
                "return task_complete=true and no actions. Otherwise return one complete stage "
                f"program of 1 to {self.plan_horizon} actions. When the reviewed Trace makes the "
                "continuation predictable, target 5 to 8 adjacent actions rather than stopping "
                "after every click. A wait action is an adaptive local visual checkpoint: after its "
                "requested duration the runner keeps sampling until pixels settle, "
                "and interrupts the remaining batch if the preceding pointer action produced no "
                "visible response. Never return a one-action wait-only program for a routine "
                "animation. Keep the next predictable reviewed interactions after the wait in the "
                "same program. If the current stage's state_after is already visible, select the "
                "next stage whose state_before matches Image 1, set stage_id to that stage, and "
                "return its first safe actions in this response. A stage boundary is not a reason "
                "to return an empty incomplete decision. Stop only before a choice whose correct "
                "target cannot be known from the current screenshot and reviewed Trace. State the "
                "expected end state and observable abort "
                "conditions. A blocked_or_failed history item means the previous batch was "
                "discarded at that action; recover from the current pixels instead of continuing it. "
                "The response must match the supplied JSON schema."
            )

        semantic_context = self._semantic_context(reference_paths)
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
            "Evidence priority is: current pixels and local safety, confirmed human guidance, "
            "reviewed semantic stages, then raw Trace motor evidence. The Trace proves observed "
            "state transitions; it is not a coordinate script. Preserve reviewed phase intent when "
            "it matches the current pixels and explain deviations in the response reason.\n"
            f"{task_context}"
            f"{completion_context}"
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
            "A wait action is an adaptive local visual checkpoint: its duration is the minimum "
            "wait, then the runner samples locally until the window stabilizes. "
            "If the preceding pointer action causes no visible change near its target, the runner "
            "discards the rest of this batch and replans. Use interaction, wait, then the next "
            "predictable interaction in one batch; do not spend a model turn only checking whether "
            "a routine animation ended.\n"
            "Raw demonstration coordinates, drag paths, hold durations, and fixed waits are "
            "intentionally withheld. Locate every target from Image 1 and use the simplest motor "
            "primitive justified by the visible control.\n"
            f"{semantic_context}"
            f"Recent execution history:\n{history}\n\n"
            "If the completion policy permits completion and Image 1 already satisfies the success "
            "condition, return task_complete=true and "
            "no actions. Otherwise return task_complete=false and one complete stage program "
            f"between 1 and {self.plan_horizon} actions. Target 5 to 8 adjacent reviewed actions "
            "whenever their continuation is predictable, and include waits between visual "
            "transitions. Never return a one-action wait-only program for a routine animation. "
            "Routine animations are local checkpoints, not reasons to end the batch. Stop before "
            "a loading screen, a "
            "choice whose correct target cannot be known yet. If a semantic stage has already "
            "reached its state_after, transition to the next matching stage and include that "
            "stage's first safe actions in the same response; never use an empty incomplete "
            "decision merely to signal a stage boundary. Describe the "
            "stage goal, expected end state, and observable abort conditions. The local runner will "
            "discard the unexecuted suffix and request a new plan immediately if a motor action "
            "fails. Replan from current pixels rather "
            "than blindly copying recorded coordinates. Never interact outside Image 1. The "
            "response must match the supplied JSON schema."
        )

    def _completion_context(self) -> str:
        task = self.contract.task
        if task.completion_mode == "cycle":
            return (
                "Completion policy: cycle. The reviewed reference is both a possible start anchor "
                "and the required end anchor. If the run starts on that visual state, do not return "
                "task_complete until actions have visibly left it, completed one cycle, and returned. "
                f"Observable success condition: {task.expected_result}\n"
            )
        return (
            "Completion policy: terminal state. Return task_complete only when the current pixels "
            f"satisfy: {task.expected_result}\n"
        )

    def _semantic_context(self, references: tuple[Path, ...]) -> str:
        experience = self.contract.semantic_experience
        if experience is None:
            return "Semantic stage interpretation: unavailable; rely on raw demonstration evidence.\n"
        image_map = [
            {
                "image": index,
                "path": path.resolve().relative_to(experience.source_path.parent).as_posix(),
            }
            for index, path in enumerate(references[1:], start=3)
        ]
        guidance = self.contract.human_guidance
        guidance_payload = guidance.prompt_payload() if guidance is not None else None
        return (
            "Compiler Agent semantic stage index (derived and reviewable from the immutable human "
            "Trace). Identify the current stage and return its id: "
            f"{json.dumps(experience.stage_index_payload(), ensure_ascii=False, separators=(',', ':'))}\n"
            "Confirmed human guidance has higher priority than the derived interpretation: "
            f"{json.dumps(guidance_payload, ensure_ascii=False, separators=(',', ':'))}\n"
            "Semantic evidence images after the final success reference: "
            f"{json.dumps(image_map, ensure_ascii=False, separators=(',', ':'))}\n"
            "Use stage preconditions and expected effects to interpret the current state. Choices "
            "marked runtime_agent_decides or unknown must be decided from the current screenshot, "
            "not copied from the single demonstration.\n"
        )

    def _active_stage_context(self, references: tuple[Path, ...]) -> str:
        experience = self.contract.semantic_experience
        if experience is None:
            return "Active semantic stage: unavailable.\n"
        payload = experience.active_stage_payload(self._active_stage_id)
        guidance = self.contract.human_guidance
        guidance_payload = (
            guidance.prompt_payload(self._active_stage_id)
            if guidance is not None
            else None
        )
        trace_summary = self._stage_trace_summary(self._active_stage_id)
        image_map = [
            {
                "image": index,
                "path": path.resolve().relative_to(
                    experience.source_path.parent
                ).as_posix(),
            }
            for index, path in enumerate(references[1:], start=3)
        ]
        return (
            "Compact semantic stage index for boundary recognition and forward transition: "
            f"{json.dumps(experience.stage_index_payload(), ensure_ascii=False, separators=(',', ':'))}\n"
            "Locally retrieved active-stage experience: "
            f"{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n"
            "Sanitized Trace evidence for this stage contains action categories only, with all "
            "recorded coordinates, drag paths, hold durations, and fixed wait values removed. It "
            "describes what the human physically did, not what must be replayed: "
            f"{json.dumps(trace_summary, ensure_ascii=False, separators=(',', ':'))}\n"
            "Exact human Trace images for this stage after the final success reference: "
            f"{json.dumps(image_map, ensure_ascii=False, separators=(',', ':'))}\n"
            "Confirmed human tricks for this stage: "
            f"{json.dumps(guidance_payload, ensure_ascii=False, separators=(',', ':'))}\n"
        )

    def _repair_stage_id(self, rejected: dict[str, Any]) -> str | None:
        experience = self.contract.semantic_experience
        if experience is None:
            return None
        stage_ids = [stage.stage_id for stage in experience.stages]
        rejected_stage = rejected.get("stage_id")
        if rejected_stage in stage_ids and rejected_stage != self._active_stage_id:
            return str(rejected_stage)
        if self._active_stage_id in stage_ids:
            current_index = stage_ids.index(self._active_stage_id)
            if current_index + 1 < len(stage_ids):
                return stage_ids[current_index + 1]
            return self._active_stage_id
        if rejected_stage in stage_ids:
            return str(rejected_stage)
        return stage_ids[0] if stage_ids else None

    def _decision_repair_prompt(
        self,
        rejected: dict[str, Any],
        *,
        repair_stage_id: str | None,
    ) -> str:
        experience = self.contract.semantic_experience
        candidate_payload = (
            experience.active_stage_payload(repair_stage_id)
            if experience is not None
            else None
        )
        guidance = self.contract.human_guidance
        guidance_payload = (
            guidance.prompt_payload(repair_stage_id)
            if guidance is not None
            else None
        )
        trace_summary = self._stage_trace_summary(repair_stage_id)
        history = self._history[-8:]
        return (
            "The immediately preceding decision failed local validation: it declared the task "
            "incomplete but returned no actions. No action from that invalid decision was executed, "
            "no new screenshot was captured, and this model thread was not reset. Treat this like a "
            "coding-agent test failure: keep the valid context, inspect the validation failure, and "
            "return a corrected decision for the same authoritative Image 1.\n"
            "Rejected decision summary: "
            f"{json.dumps(rejected, ensure_ascii=False, separators=(',', ':'))}\n"
            f"Previously active stage: {self._active_stage_id or 'unknown'}\n"
            f"Candidate recovery stage: {repair_stage_id or 'unknown'}\n"
            "Full local context for that candidate stage (a hypothesis that must still match "
            "Image 1): "
            f"{json.dumps(candidate_payload, ensure_ascii=False, separators=(',', ':'))}\n"
            "Confirmed human guidance for the candidate stage: "
            f"{json.dumps(guidance_payload, ensure_ascii=False, separators=(',', ':'))}\n"
            "Sanitized human Trace categories for the candidate stage; these express intent, not "
            "coordinates or a replay script: "
            f"{json.dumps(trace_summary, ensure_ascii=False, separators=(',', ':'))}\n"
            "Recent locally verified execution outcomes: "
            f"{json.dumps(history, ensure_ascii=False, separators=(',', ':'))}\n\n"
            "Forbidden recovery behavior:\n"
            "- Do not return task_complete=false with an empty actions array again.\n"
            "- Do not repeat an already applied interaction when its expected visible effect is "
            "already present, including clicking a visibly cooling-down or disabled control.\n"
            "- Do not replay a blocked_or_failed action unchanged unless current pixels visibly "
            "show that its precondition has changed.\n"
            "- Do not skip an unresolved choice, invent a target, or mark the task complete unless "
            "the declared success condition is visibly satisfied.\n\n"
            "First verify the candidate stage preconditions against Image 1. If they match, set "
            "stage_id to that stage and return its first safe action sequence. If they do not match, "
            "continue the stage whose preconditions do match and return a different safe sequence. "
            "When no input target is visually justified and wait is an allowed skill, return a "
            "bounded wait rather than guessing a click. Return the complete corrected object matching "
            "the stricter supplied JSON schema."
        )

    def _stage_trace_summary(self, stage_id: str | None) -> dict[str, Any]:
        experience = self.contract.semantic_experience
        if experience is None or stage_id is None:
            return {}
        active = next(
            (
                stage
                for stage in experience.stages
                if stage.stage_id == stage_id
            ),
            None,
        )
        if active is None:
            return {}
        categories: dict[str, int] = {}
        for action in self.contract.demonstration[
            active.start_action_index : active.end_action_index + 1
        ]:
            category = {
                "drag": "unverified_pointer_gesture",
                "hold_mouse": "unverified_pointer_gesture",
                "wait": "observed_transition_wait",
                "click": "pointer_activation",
            }.get(action.skill, action.skill)
            categories[category] = categories.get(category, 0) + 1
        return {
            "stage_id": active.stage_id,
            "action_range": [active.start_action_index, active.end_action_index],
            "observed_categories": categories,
            "motor_policy": "semantic_intent_only_no_recorded_coordinates",
        }

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
                "stage_id": {
                    "type": "string",
                    "enum": self._stage_ids(),
                },
                "stage_goal": {"type": "string", "minLength": 1},
                "expected_end_state": {"type": "string", "minLength": 1},
                "abort_conditions": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "maxItems": 6,
                },
            },
            "required": [
                "task_complete",
                "actions",
                "reason",
                "confidence",
                "stage_id",
                "stage_goal",
                "expected_end_state",
                "abort_conditions",
            ],
            "additionalProperties": False,
        }

    def _repair_output_schema(self) -> dict[str, Any]:
        schema = self._output_schema()
        schema["properties"]["task_complete"] = {
            "type": "boolean",
            "enum": [False],
        }
        schema["properties"]["actions"]["minItems"] = 1
        return schema

    def _allowed_skills(self) -> tuple[str, ...]:
        if not self.background:
            return self.contract.task.actions
        skills = tuple(
            skill for skill in self.contract.task.actions if skill != "focus_window"
        )
        if not skills:
            raise RuntimeError("A background Windows task requires a non-focus motor skill")
        return skills

    def _stage_ids(self) -> list[str]:
        experience = self.contract.semantic_experience
        if experience is None:
            return ["unknown"]
        return [stage.stage_id for stage in experience.stages] + ["unknown"]

    def _parse_payload(
        self,
        output: str,
        *,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> WindowsAgentPlan:
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
        stage_id = payload.get("stage_id", "unknown")
        stage_goal = payload.get("stage_goal", reason)
        expected_end_state = payload.get(
            "expected_end_state",
            "Observe the resulting state before the next plan.",
        )
        abort_conditions = payload.get("abort_conditions", [])
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
            raise _IncompletePlanWithoutActionsError(
                "Codex returned no action for an incomplete task",
                payload={
                    "task_complete": task_complete,
                    "action_count": len(raw_actions),
                    "reason": reason if isinstance(reason, str) else None,
                    "confidence": confidence
                    if isinstance(confidence, (int, float))
                    and not isinstance(confidence, bool)
                    else None,
                    "stage_id": stage_id if isinstance(stage_id, str) else "unknown",
                    "stage_goal": stage_goal
                    if isinstance(stage_goal, str)
                    else None,
                    "expected_end_state": expected_end_state
                    if isinstance(expected_end_state, str)
                    else None,
                },
            )
        if not isinstance(reason, str) or not reason.strip():
            raise RuntimeError("Codex returned an empty Windows decision reason")
        if (
            not isinstance(confidence, (int, float))
            or isinstance(confidence, bool)
            or not 0 <= float(confidence) <= 1
        ):
            raise RuntimeError("Codex returned an invalid Windows confidence value")
        if not isinstance(stage_id, str) or stage_id not in self._stage_ids():
            raise RuntimeError("Codex returned an invalid semantic stage id")
        if not isinstance(stage_goal, str) or not stage_goal.strip():
            raise RuntimeError("Codex returned an empty stage goal")
        if not isinstance(expected_end_state, str) or not expected_end_state.strip():
            raise RuntimeError("Codex returned an empty expected stage end state")
        if (
            not isinstance(abort_conditions, list)
            or len(abort_conditions) > 6
            or any(not isinstance(item, str) or not item.strip() for item in abort_conditions)
        ):
            raise RuntimeError("Codex returned invalid stage abort conditions")
        return WindowsAgentPlan(
            task_complete=task_complete,
            actions=actions,
            reason=reason.strip(),
            confidence=float(confidence),
            stage_id=stage_id,
            stage_goal=stage_goal.strip(),
            expected_end_state=expected_end_state.strip(),
            abort_conditions=tuple(item.strip() for item in abort_conditions),
            model=model,
            reasoning_effort=reasoning_effort or self.reasoning_effort,
        )

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None
