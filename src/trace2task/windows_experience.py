from __future__ import annotations

import json
import re
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pygame
import yaml

from trace2task import __version__
from trace2task.codex_agent import resolve_codex_binary
from trace2task.codex_app_server import (
    CodexAppServerSession,
)
from trace2task.narration import load_narration

MAX_EVIDENCE_IMAGES = 12
CONTACT_SHEET_COLUMNS = 3
CONTACT_SHEET_ROWS = 2
CONTACT_SHEET_THUMBNAIL = (320, 190)
CONTACT_SHEET_LABEL_HEIGHT = 24
MAX_SEMANTIC_STAGES = 12
MAX_TASK_STATES = 24
MAX_TASK_TRANSITIONS = 64
MAX_TERMINAL_STATES = 8
SCREEN_CHANGE_THRESHOLD = 0.12
TEMPORAL_BOUNDARY_MS = 1_500
PROVENANCE_VALUES = {"observed", "inferred", "unknown"}
GENERALIZATION_VALUES = {"demonstrated_only", "runtime_agent_decides", "unknown"}
COMPLETION_MODES = {"state", "cycle"}
NARRATION_CLAIM_TYPES = {"goal", "strategy", "observation", "recovery", "example_only"}
NARRATION_CLAIM_VERDICTS = {"supported", "advisory", "rejected"}
DEFAULT_COMPILER_MODEL = "gpt-5.6-sol"
DEFAULT_COMPILER_REASONING_EFFORT = "high"
COMPILER_PREFLIGHT_MODEL = "gpt-5.6-luna"
COMPILER_PREFLIGHT_TIMEOUT_SECONDS = 45
COMPILER_RESPONSE_IDLE_TIMEOUT_SECONDS = 90
COMPILER_HARD_TIMEOUT_SECONDS = 600

BinaryResolver = Callable[[str], str]
SessionFactory = Callable[..., CodexAppServerSession]


def probe_codex_compiler_connection(
    *,
    model: str = COMPILER_PREFLIGHT_MODEL,
    codex_bin: str = "codex",
    timeout_seconds: float = COMPILER_PREFLIGHT_TIMEOUT_SECONDS,
    binary_resolver: BinaryResolver = resolve_codex_binary,
    session_factory: SessionFactory = CodexAppServerSession,
) -> dict[str, Any]:
    """Run a small text-only turn before spending time building compiler prompts."""

    executable = binary_resolver(codex_bin)
    session = session_factory(
        executable,
        model=model,
        reasoning_effort="low",
        cwd=Path.cwd(),
        timeout_seconds=timeout_seconds,
    )
    started = time.perf_counter()
    try:
        output = session.run_turn(
            prompt=(
                "This is a Trace2Task compiler connectivity preflight. "
                'Return exactly {"status":"ok"}.'
            ),
            image_path=None,
            output_schema={
                "type": "object",
                "properties": {"status": {"type": "string", "enum": ["ok"]}},
                "required": ["status"],
                "additionalProperties": False,
            },
        )
        payload = json.loads(output.strip())
        if payload != {"status": "ok"}:
            raise RuntimeError("Codex compiler connectivity preflight returned an invalid result")
    finally:
        session.close()
    return {
        "status": "completed",
        "model": model,
        "reasoning_effort": "low",
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }


@dataclass(frozen=True)
class ExperienceState:
    description: str
    evidence_frame: str
    visual_anchors: tuple[str, ...]


@dataclass(frozen=True)
class ActionIntent:
    start_action_index: int
    end_action_index: int
    description: str
    target: str
    provenance: str
    confidence: float


@dataclass(frozen=True)
class DynamicDecision:
    description: str
    generalization: str
    confidence: float


@dataclass(frozen=True)
class NarrationClaim:
    text: str
    claim_type: str
    start_action_index: int
    end_action_index: int
    confidence: float
    verdict: str
    reason: str


@dataclass(frozen=True)
class ExperienceStage:
    stage_id: str
    name: str
    start_action_index: int
    end_action_index: int
    state_before: ExperienceState
    action_intents: tuple[ActionIntent, ...]
    preconditions: tuple[str, ...]
    expected_effects: tuple[str, ...]
    state_after: ExperienceState
    dynamic_decisions: tuple[DynamicDecision, ...]
    confidence: float


@dataclass(frozen=True)
class TaskState:
    state_id: str
    name: str
    description: str
    preconditions: tuple[str, ...]
    visual_anchors: tuple[str, ...]
    evidence_stage_ids: tuple[str, ...]
    confidence: float


@dataclass(frozen=True)
class TaskTransition:
    transition_id: str
    source_state_id: str
    target_type: str
    target_id: str
    condition: str
    action_goal: str
    expected_effects: tuple[str, ...]
    evidence_stage_ids: tuple[str, ...]
    confidence: float


@dataclass(frozen=True)
class TerminalState:
    terminal_id: str
    kind: str
    name: str
    condition: str
    visual_anchors: tuple[str, ...]
    evidence_frame: str
    confidence: float


@dataclass(frozen=True)
class SemanticExperience:
    canonical_instruction: str
    goal: str
    summary: str
    completion_mode: str
    completion_success_condition: str
    completion_reason: str
    stages: tuple[ExperienceStage, ...]
    entry_state_id: str
    states: tuple[TaskState, ...]
    transitions: tuple[TaskTransition, ...]
    terminal_states: tuple[TerminalState, ...]
    narration_claims: tuple[NarrationClaim, ...]
    source_type: str
    narration_available: bool
    narration_kind: str
    model: str
    reasoning_effort: str
    review_status: str
    requires_confirmation: bool
    source_path: Path

    @property
    def state_ids(self) -> tuple[str, ...]:
        return tuple(state.state_id for state in self.states)

    @property
    def terminal_ids(self) -> tuple[str, ...]:
        return tuple(terminal.terminal_id for terminal in self.terminal_states)

    def state(self, state_id: str | None) -> TaskState | None:
        return next(
            (state for state in self.states if state.state_id == state_id),
            None,
        )

    def outgoing_transitions(self, state_id: str | None) -> tuple[TaskTransition, ...]:
        return tuple(
            transition
            for transition in self.transitions
            if transition.source_state_id == state_id
        )

    def evidence_stages_for_state(self, state_id: str | None) -> tuple[ExperienceStage, ...]:
        state = self.state(state_id)
        if state is None:
            return ()
        evidence_ids = set(state.evidence_stage_ids)
        return tuple(stage for stage in self.stages if stage.stage_id in evidence_ids)

    @staticmethod
    def _stage_payload(stage: ExperienceStage) -> dict[str, Any]:
        return {
            "id": stage.stage_id,
            "name": stage.name,
            "action_range": [stage.start_action_index, stage.end_action_index],
            "state_before": {
                "description": stage.state_before.description,
                "evidence_frame": stage.state_before.evidence_frame,
                "visual_anchors": list(stage.state_before.visual_anchors),
            },
            "action_intents": [
                {
                    "action_range": [
                        intent.start_action_index,
                        intent.end_action_index,
                    ],
                    "description": intent.description,
                    "target": intent.target,
                    "provenance": intent.provenance,
                    "confidence": intent.confidence,
                }
                for intent in stage.action_intents
            ],
            "preconditions": list(stage.preconditions),
            "expected_effects": list(stage.expected_effects),
            "state_after": {
                "description": stage.state_after.description,
                "evidence_frame": stage.state_after.evidence_frame,
                "visual_anchors": list(stage.state_after.visual_anchors),
            },
            "dynamic_decisions": [
                {
                    "description": decision.description,
                    "generalization": decision.generalization,
                    "confidence": decision.confidence,
                }
                for decision in stage.dynamic_decisions
            ],
            "confidence": stage.confidence,
        }

    def prompt_payload(self) -> dict[str, Any]:
        return {
            "canonical_instruction": self.canonical_instruction,
            "goal": self.goal,
            "summary": self.summary,
            "completion": {
                "mode": self.completion_mode,
                "success_condition": self.completion_success_condition,
                "reason": self.completion_reason,
            },
            "trace_episodes": [self._stage_payload(stage) for stage in self.stages],
            "state_graph": self.state_graph_payload(),
        }

    def state_graph_payload(self) -> dict[str, Any]:
        return {
            "entry_state_id": self.entry_state_id,
            "states": [
                {
                    "id": state.state_id,
                    "name": state.name,
                    "description": state.description,
                    "preconditions": list(state.preconditions),
                    "visual_anchors": list(state.visual_anchors),
                    "evidence_stage_ids": list(state.evidence_stage_ids),
                    "confidence": state.confidence,
                }
                for state in self.states
            ],
            "transitions": [
                {
                    "id": transition.transition_id,
                    "source_state_id": transition.source_state_id,
                    "target_type": transition.target_type,
                    "target_id": transition.target_id,
                    "condition": transition.condition,
                    "action_goal": transition.action_goal,
                    "expected_effects": list(transition.expected_effects),
                    "evidence_stage_ids": list(transition.evidence_stage_ids),
                    "confidence": transition.confidence,
                }
                for transition in self.transitions
            ],
            "terminals": [
                {
                    "id": terminal.terminal_id,
                    "kind": terminal.kind,
                    "name": terminal.name,
                    "condition": terminal.condition,
                    "visual_anchors": list(terminal.visual_anchors),
                    "evidence_frame": terminal.evidence_frame,
                    "confidence": terminal.confidence,
                }
                for terminal in self.terminal_states
            ],
        }

    def narration_audit_payload(self) -> list[dict[str, Any]]:
        return [
            {
                "text": claim.text,
                "type": claim.claim_type,
                "action_range": [claim.start_action_index, claim.end_action_index],
                "confidence": claim.confidence,
                "verdict": claim.verdict,
                "reason": claim.reason,
                "runtime_policy": "compiler_evidence_only",
            }
            for claim in self.narration_claims
        ]

    def stage_index_payload(self) -> dict[str, Any]:
        return {
            "canonical_instruction": self.canonical_instruction,
            "goal": self.goal,
            "summary": self.summary,
            "completion": {
                "mode": self.completion_mode,
                "success_condition": self.completion_success_condition,
                "reason": self.completion_reason,
            },
            "state_graph": self.state_graph_payload(),
        }

    def active_stage_payload(self, stage_id: str | None) -> dict[str, Any] | None:
        state = self.state(stage_id)
        if state is None:
            return None
        evidence = self.evidence_stages_for_state(stage_id)
        return {
            "active_state": {
                "id": state.state_id,
                "name": state.name,
                "description": state.description,
                "preconditions": list(state.preconditions),
                "visual_anchors": list(state.visual_anchors),
                "confidence": state.confidence,
            },
            "legal_outgoing_transitions": [
                {
                    "id": transition.transition_id,
                    "target_type": transition.target_type,
                    "target_id": transition.target_id,
                    "condition": transition.condition,
                    "action_goal": transition.action_goal,
                    "expected_effects": list(transition.expected_effects),
                    "confidence": transition.confidence,
                }
                for transition in self.outgoing_transitions(stage_id)
            ],
            "trace_episodes": [self._stage_payload(stage) for stage in evidence],
        }

    def evidence_paths_for_state(
        self,
        state_id: str | None,
        task_root: Path,
    ) -> tuple[Path, ...]:
        relative_paths = [
            relative
            for stage in self.evidence_stages_for_state(state_id)
            for relative in (
                stage.state_before.evidence_frame,
                stage.state_after.evidence_frame,
            )
        ]
        return self._resolve_evidence_paths(relative_paths, task_root)

    def evidence_paths(self, task_root: Path) -> tuple[Path, ...]:
        relative_paths = [
            relative
            for stage in self.stages
            for relative in (
                stage.state_before.evidence_frame,
                stage.state_after.evidence_frame,
            )
        ]
        return self._resolve_evidence_paths(relative_paths, task_root)

    @staticmethod
    def _resolve_evidence_paths(
        relative_paths: Sequence[str],
        task_root: Path,
    ) -> tuple[Path, ...]:
        paths: list[Path] = []
        resolved_root = task_root.resolve()
        for relative in dict.fromkeys(relative_paths):
            path = (resolved_root / relative).resolve()
            if path.is_relative_to(resolved_root) and path.is_file():
                paths.append(path)
        return tuple(paths)


@dataclass(frozen=True)
class SemanticCompilation:
    experience_path: str
    stage_count: int
    model: str
    reasoning_effort: str
    review_status: str
    narration_available: bool = False
    narration_kind: str = "none"


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be a mapping")
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return " ".join(value.split())


def _confidence(value: object, label: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not 0 <= float(value) <= 1
    ):
        raise ValueError(f"{label} must be between zero and one")
    return round(float(value), 3)


def _string_list(value: object, label: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError(f"{label} must be a {'possibly empty ' if allow_empty else 'non-empty '}list")
    return tuple(_string(item, f"{label} item") for item in value)


def _experience_state(
    value: object,
    label: str,
    *,
    allowed_frames: set[str],
) -> ExperienceState:
    data = _mapping(value, label)
    evidence_frame = _string(data.get("evidence_frame"), f"{label}.evidence_frame")
    if evidence_frame not in allowed_frames:
        raise ValueError(f"{label}.evidence_frame is not part of the preserved human evidence")
    return ExperienceState(
        description=_string(data.get("description"), f"{label}.description"),
        evidence_frame=evidence_frame,
        visual_anchors=_string_list(data.get("visual_anchors"), f"{label}.visual_anchors"),
    )


def _legacy_state_graph(
    stages: Sequence[ExperienceStage],
    *,
    completion_success_condition: str,
) -> tuple[str, tuple[TaskState, ...], tuple[TaskTransition, ...], tuple[TerminalState, ...]]:
    states = tuple(
        TaskState(
            state_id=stage.stage_id,
            name=stage.name,
            description=stage.state_before.description,
            preconditions=stage.preconditions,
            visual_anchors=stage.state_before.visual_anchors,
            evidence_stage_ids=(stage.stage_id,),
            confidence=stage.confidence,
        )
        for stage in stages
    )
    transitions: list[TaskTransition] = []
    for index, stage in enumerate(stages):
        is_last = index == len(stages) - 1
        transitions.append(
            TaskTransition(
                transition_id=f"legacy_transition_{index + 1}",
                source_state_id=stage.stage_id,
                target_type="terminal" if is_last else "state",
                target_id="success" if is_last else stages[index + 1].stage_id,
                condition=(
                    completion_success_condition
                    if is_last
                    else stage.state_after.description
                ),
                action_goal="；".join(
                    intent.description for intent in stage.action_intents
                ),
                expected_effects=stage.expected_effects,
                evidence_stage_ids=(stage.stage_id,),
                confidence=stage.confidence,
            )
        )
    last_stage = stages[-1]
    terminals = (
        TerminalState(
            terminal_id="success",
            kind="success",
            name="任务成功",
            condition=completion_success_condition,
            visual_anchors=last_stage.state_after.visual_anchors,
            evidence_frame=last_stage.state_after.evidence_frame,
            confidence=last_stage.confidence,
        ),
    )
    return stages[0].stage_id, states, tuple(transitions), terminals


def validate_state_graph(
    value: object,
    *,
    stages: Sequence[ExperienceStage],
    completion_success_condition: str,
    allowed_frames: set[str],
) -> tuple[str, tuple[TaskState, ...], tuple[TaskTransition, ...], tuple[TerminalState, ...]]:
    if value is None:
        return _legacy_state_graph(
            stages,
            completion_success_condition=completion_success_condition,
        )
    graph = _mapping(value, "state_graph")
    raw_states = graph.get("states")
    if not isinstance(raw_states, list) or not 1 <= len(raw_states) <= MAX_TASK_STATES:
        raise ValueError(f"state_graph.states must contain 1-{MAX_TASK_STATES} states")
    stage_ids = {stage.stage_id for stage in stages}
    states: list[TaskState] = []
    state_ids: set[str] = set()
    for index, raw_state in enumerate(raw_states):
        label = f"state_graph.states[{index}]"
        data = _mapping(raw_state, label)
        state_id = _string(data.get("id"), f"{label}.id")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,47}", state_id):
            raise ValueError(f"{label}.id must be a short lowercase identifier")
        if state_id in state_ids:
            raise ValueError(f"state_graph contains duplicate state id {state_id!r}")
        state_ids.add(state_id)
        evidence_stage_ids = _string_list(
            data.get("evidence_stage_ids"),
            f"{label}.evidence_stage_ids",
        )
        if not evidence_stage_ids or not set(evidence_stage_ids) <= stage_ids:
            raise ValueError(f"{label}.evidence_stage_ids must reference Trace episodes")
        states.append(
            TaskState(
                state_id=state_id,
                name=_string(data.get("name"), f"{label}.name"),
                description=_string(data.get("description"), f"{label}.description"),
                preconditions=_string_list(
                    data.get("preconditions"), f"{label}.preconditions"
                ),
                visual_anchors=_string_list(
                    data.get("visual_anchors"), f"{label}.visual_anchors"
                ),
                evidence_stage_ids=evidence_stage_ids,
                confidence=_confidence(data.get("confidence"), f"{label}.confidence"),
            )
        )
    entry_state_id = _string(graph.get("entry_state_id"), "state_graph.entry_state_id")
    if entry_state_id not in state_ids:
        raise ValueError("state_graph.entry_state_id must reference a state")

    raw_terminals = graph.get("terminals")
    if not isinstance(raw_terminals, list) or not 1 <= len(raw_terminals) <= MAX_TERMINAL_STATES:
        raise ValueError(
            f"state_graph.terminals must contain 1-{MAX_TERMINAL_STATES} terminal states"
        )
    terminals: list[TerminalState] = []
    terminal_ids: set[str] = set()
    for index, raw_terminal in enumerate(raw_terminals):
        label = f"state_graph.terminals[{index}]"
        data = _mapping(raw_terminal, label)
        terminal_id = _string(data.get("id"), f"{label}.id")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,47}", terminal_id):
            raise ValueError(f"{label}.id must be a short lowercase identifier")
        if terminal_id in terminal_ids or terminal_id in state_ids:
            raise ValueError(f"state_graph contains duplicate terminal id {terminal_id!r}")
        terminal_ids.add(terminal_id)
        kind = _string(data.get("kind"), f"{label}.kind")
        if kind not in {"success", "failure"}:
            raise ValueError(f"{label}.kind must be success or failure")
        evidence_frame = _string(data.get("evidence_frame"), f"{label}.evidence_frame")
        if evidence_frame not in allowed_frames:
            raise ValueError(f"{label}.evidence_frame must reference preserved evidence")
        terminals.append(
            TerminalState(
                terminal_id=terminal_id,
                kind=kind,
                name=_string(data.get("name"), f"{label}.name"),
                condition=_string(data.get("condition"), f"{label}.condition"),
                visual_anchors=_string_list(
                    data.get("visual_anchors"), f"{label}.visual_anchors"
                ),
                evidence_frame=evidence_frame,
                confidence=_confidence(data.get("confidence"), f"{label}.confidence"),
            )
        )
    if not any(terminal.kind == "success" for terminal in terminals):
        raise ValueError("state_graph must define at least one success terminal")

    raw_transitions = graph.get("transitions")
    if not isinstance(raw_transitions, list) or len(raw_transitions) > MAX_TASK_TRANSITIONS:
        raise ValueError(
            f"state_graph.transitions must contain at most {MAX_TASK_TRANSITIONS} transitions"
        )
    transitions: list[TaskTransition] = []
    transition_ids: set[str] = set()
    for index, raw_transition in enumerate(raw_transitions):
        label = f"state_graph.transitions[{index}]"
        data = _mapping(raw_transition, label)
        transition_id = _string(data.get("id"), f"{label}.id")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", transition_id):
            raise ValueError(f"{label}.id must be a short lowercase identifier")
        if transition_id in transition_ids:
            raise ValueError(f"state_graph contains duplicate transition id {transition_id!r}")
        transition_ids.add(transition_id)
        source_state_id = _string(
            data.get("source_state_id"), f"{label}.source_state_id"
        )
        if source_state_id not in state_ids:
            raise ValueError(f"{label}.source_state_id must reference a state")
        target_type = _string(data.get("target_type"), f"{label}.target_type")
        target_id = _string(data.get("target_id"), f"{label}.target_id")
        if target_type == "state":
            valid_target = target_id in state_ids
        elif target_type == "terminal":
            valid_target = target_id in terminal_ids
        else:
            raise ValueError(f"{label}.target_type must be state or terminal")
        if not valid_target:
            raise ValueError(f"{label}.target_id does not match its target_type")
        evidence_stage_ids = _string_list(
            data.get("evidence_stage_ids"), f"{label}.evidence_stage_ids"
        )
        if not evidence_stage_ids or not set(evidence_stage_ids) <= stage_ids:
            raise ValueError(f"{label}.evidence_stage_ids must reference Trace episodes")
        transitions.append(
            TaskTransition(
                transition_id=transition_id,
                source_state_id=source_state_id,
                target_type=target_type,
                target_id=target_id,
                condition=_string(data.get("condition"), f"{label}.condition"),
                action_goal=_string(data.get("action_goal"), f"{label}.action_goal"),
                expected_effects=_string_list(
                    data.get("expected_effects"), f"{label}.expected_effects"
                ),
                evidence_stage_ids=evidence_stage_ids,
                confidence=_confidence(
                    data.get("confidence"), f"{label}.confidence"
                ),
            )
        )
    if not transitions:
        raise ValueError("state_graph must define at least one legal transition")
    reachable = {entry_state_id}
    changed = True
    while changed:
        changed = False
        for transition in transitions:
            if (
                transition.source_state_id in reachable
                and transition.target_type == "state"
                and transition.target_id not in reachable
            ):
                reachable.add(transition.target_id)
                changed = True
    if reachable != state_ids:
        unreachable = sorted(state_ids - reachable)
        raise ValueError(f"state_graph has unreachable states: {unreachable}")
    sources_with_outgoing = {transition.source_state_id for transition in transitions}
    dead_ends = sorted(state_ids - sources_with_outgoing)
    if dead_ends:
        raise ValueError(f"state_graph has non-terminal states with no outgoing edge: {dead_ends}")
    reachable_terminals = {
        transition.target_id
        for transition in transitions
        if transition.source_state_id in reachable and transition.target_type == "terminal"
    }
    unreachable_terminals = sorted(terminal_ids - reachable_terminals)
    if unreachable_terminals:
        raise ValueError(
            f"state_graph has unreachable terminal states: {unreachable_terminals}"
        )
    success_ids = {
        terminal.terminal_id for terminal in terminals if terminal.kind == "success"
    }
    if not success_ids <= reachable_terminals:
        raise ValueError("state_graph success terminals must be reachable from the entry state")
    return entry_state_id, tuple(states), tuple(transitions), tuple(terminals)


def _validate_semantic_payload(
    payload: object,
    *,
    task_id: str,
    action_count: int,
    allowed_frames: set[str],
) -> tuple[
    str,
    str,
    str,
    str,
    str,
    str,
    tuple[ExperienceStage, ...],
    str,
    tuple[TaskState, ...],
    tuple[TaskTransition, ...],
    tuple[TerminalState, ...],
    tuple[NarrationClaim, ...],
]:
    root = _mapping(payload, "Compiler Agent output")
    goal = _string(root.get("goal"), "goal")
    summary = _string(root.get("summary"), "summary")
    canonical_instruction = _string(
        root.get("canonical_instruction", goal), "canonical_instruction"
    )
    raw_completion = root.get("completion")
    if raw_completion is None:
        completion_mode = "state"
        completion_success_condition = goal
        completion_reason = "Legacy semantic experience uses a terminal state verifier."
    else:
        completion = _mapping(raw_completion, "completion")
        completion_mode = _string(completion.get("mode"), "completion.mode")
        if completion_mode not in COMPLETION_MODES:
            raise ValueError("completion.mode must be state or cycle")
        completion_success_condition = _string(
            completion.get("success_condition"), "completion.success_condition"
        )
        completion_reason = _string(completion.get("reason"), "completion.reason")
    raw_stages = root.get("stages")
    if not isinstance(raw_stages, list) or not 1 <= len(raw_stages) <= MAX_SEMANTIC_STAGES:
        raise ValueError(f"Compiler Agent must return 1-{MAX_SEMANTIC_STAGES} stages")

    stages: list[ExperienceStage] = []
    expected_stage_start = 0
    stage_ids: set[str] = set()
    for stage_index, raw_stage in enumerate(raw_stages):
        label = f"stages[{stage_index}]"
        data = _mapping(raw_stage, label)
        stage_id = _string(data.get("id"), f"{label}.id")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,47}", stage_id):
            raise ValueError(f"{label}.id must be a short lowercase identifier")
        if stage_id in stage_ids:
            raise ValueError(f"Compiler Agent returned duplicate stage id {stage_id!r}")
        stage_ids.add(stage_id)
        start = data.get("start_action_index")
        end = data.get("end_action_index")
        if not isinstance(start, int) or isinstance(start, bool):
            raise TypeError(f"{label}.start_action_index must be an integer")
        if not isinstance(end, int) or isinstance(end, bool):
            raise TypeError(f"{label}.end_action_index must be an integer")
        if start != expected_stage_start or not start <= end < action_count:
            raise ValueError("Semantic stages must form one contiguous partition of all actions")

        raw_intents = data.get("action_intents")
        if not isinstance(raw_intents, list) or not raw_intents:
            raise ValueError(f"{label}.action_intents must be a non-empty list")
        intents: list[ActionIntent] = []
        expected_intent_start = start
        for intent_index, raw_intent in enumerate(raw_intents):
            intent_label = f"{label}.action_intents[{intent_index}]"
            intent_data = _mapping(raw_intent, intent_label)
            intent_start = intent_data.get("start_action_index")
            intent_end = intent_data.get("end_action_index")
            if (
                not isinstance(intent_start, int)
                or isinstance(intent_start, bool)
                or not isinstance(intent_end, int)
                or isinstance(intent_end, bool)
                or intent_start != expected_intent_start
                or not intent_start <= intent_end <= end
            ):
                raise ValueError(
                    f"{label}.action_intents must partition the stage action range"
                )
            provenance = _string(
                intent_data.get("provenance"), f"{intent_label}.provenance"
            )
            if provenance not in PROVENANCE_VALUES:
                raise ValueError(f"{intent_label}.provenance is unsupported")
            intents.append(
                ActionIntent(
                    start_action_index=intent_start,
                    end_action_index=intent_end,
                    description=_string(
                        intent_data.get("description"), f"{intent_label}.description"
                    ),
                    target=_string(intent_data.get("target"), f"{intent_label}.target"),
                    provenance=provenance,
                    confidence=_confidence(
                        intent_data.get("confidence"), f"{intent_label}.confidence"
                    ),
                )
            )
            expected_intent_start = intent_end + 1
        if expected_intent_start != end + 1:
            raise ValueError(f"{label}.action_intents do not cover the complete stage")

        raw_decisions = data.get("dynamic_decisions")
        if not isinstance(raw_decisions, list):
            raise TypeError(f"{label}.dynamic_decisions must be a list")
        decisions: list[DynamicDecision] = []
        for decision_index, raw_decision in enumerate(raw_decisions):
            decision_label = f"{label}.dynamic_decisions[{decision_index}]"
            decision_data = _mapping(raw_decision, decision_label)
            generalization = _string(
                decision_data.get("generalization"),
                f"{decision_label}.generalization",
            )
            if generalization not in GENERALIZATION_VALUES:
                raise ValueError(f"{decision_label}.generalization is unsupported")
            decisions.append(
                DynamicDecision(
                    description=_string(
                        decision_data.get("description"),
                        f"{decision_label}.description",
                    ),
                    generalization=generalization,
                    confidence=_confidence(
                        decision_data.get("confidence"),
                        f"{decision_label}.confidence",
                    ),
                )
            )

        stages.append(
            ExperienceStage(
                stage_id=stage_id,
                name=_string(data.get("name"), f"{label}.name"),
                start_action_index=start,
                end_action_index=end,
                state_before=_experience_state(
                    data.get("state_before"),
                    f"{label}.state_before",
                    allowed_frames=allowed_frames,
                ),
                action_intents=tuple(intents),
                preconditions=_string_list(
                    data.get("preconditions"), f"{label}.preconditions"
                ),
                expected_effects=_string_list(
                    data.get("expected_effects"), f"{label}.expected_effects"
                ),
                state_after=_experience_state(
                    data.get("state_after"),
                    f"{label}.state_after",
                    allowed_frames=allowed_frames,
                ),
                dynamic_decisions=tuple(decisions),
                confidence=_confidence(data.get("confidence"), f"{label}.confidence"),
            )
        )
        expected_stage_start = end + 1
    if expected_stage_start != action_count:
        raise ValueError(
            f"Semantic stages cover {expected_stage_start} of {action_count} actions for {task_id}"
        )

    entry_state_id, states, transitions, terminal_states = validate_state_graph(
        root.get("state_graph"),
        stages=stages,
        completion_success_condition=completion_success_condition,
        allowed_frames=allowed_frames,
    )

    raw_claims = root.get("narration_claims", [])
    if not isinstance(raw_claims, list) or len(raw_claims) > 24:
        raise ValueError("narration_claims must contain at most 24 items")
    narration_claims: list[NarrationClaim] = []
    for claim_index, raw_claim in enumerate(raw_claims):
        label = f"narration_claims[{claim_index}]"
        data = _mapping(raw_claim, label)
        claim_type = _string(data.get("type"), f"{label}.type")
        if claim_type not in NARRATION_CLAIM_TYPES:
            raise ValueError(f"{label}.type is unsupported")
        verdict = _string(data.get("verdict"), f"{label}.verdict")
        if verdict not in NARRATION_CLAIM_VERDICTS:
            raise ValueError(f"{label}.verdict is unsupported")
        start = data.get("start_action_index")
        end = data.get("end_action_index")
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or not 0 <= start <= end < action_count
        ):
            raise ValueError(f"{label} has an invalid aligned action range")
        narration_claims.append(
            NarrationClaim(
                text=_string(data.get("text"), f"{label}.text"),
                claim_type=claim_type,
                start_action_index=start,
                end_action_index=end,
                confidence=_confidence(data.get("confidence"), f"{label}.confidence"),
                verdict=verdict,
                reason=_string(data.get("reason"), f"{label}.reason"),
            )
        )
    return (
        canonical_instruction,
        goal,
        summary,
        completion_mode,
        completion_success_condition,
        completion_reason,
        tuple(stages),
        entry_state_id,
        states,
        transitions,
        terminal_states,
        tuple(narration_claims),
    )


def _state_payload(state: ExperienceState) -> dict[str, Any]:
    return {
        "description": state.description,
        "evidence_frame": state.evidence_frame,
        "visual_anchors": list(state.visual_anchors),
    }


def _experience_document(
    *,
    task_id: str,
    canonical_instruction: str,
    goal: str,
    summary: str,
    completion_mode: str,
    completion_success_condition: str,
    completion_reason: str,
    stages: Sequence[ExperienceStage],
    entry_state_id: str,
    states: Sequence[TaskState],
    transitions: Sequence[TaskTransition],
    terminal_states: Sequence[TerminalState],
    narration_claims: Sequence[NarrationClaim],
    model: str,
    reasoning_effort: str,
    narration_kind: str,
) -> dict[str, Any]:
    narration_available = narration_kind != "none"
    return {
        "schema_version": "0.4",
        "task_id": task_id,
        "source": {
            "type": "human_trace",
            "trace": "reference/trace.jsonl",
            "demonstration": "demonstration.json",
            "narration": "reference/narration.json" if narration_available else None,
            "narration_kind": narration_kind,
            "policy": "immutable_observation_evidence",
            "runtime_motor_policy": "semantic_intent_only_no_recorded_coordinates",
        },
        "compiler": {
            "type": "multimodal_agent",
            "version": __version__,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "compiled_at": datetime.now(UTC).isoformat(),
            "policy": "replaceable_derived_interpretation",
        },
        "canonical_instruction": canonical_instruction,
        "goal": goal,
        "summary": summary,
        "completion": {
            "mode": completion_mode,
            "success_condition": completion_success_condition,
            "reason": completion_reason,
        },
        "narration_claims": [
            {
                "text": claim.text,
                "type": claim.claim_type,
                "start_action_index": claim.start_action_index,
                "end_action_index": claim.end_action_index,
                "confidence": claim.confidence,
                "verdict": claim.verdict,
                "reason": claim.reason,
                "runtime_policy": "compiler_evidence_only",
            }
            for claim in narration_claims
        ],
        "stages": [
            {
                "id": stage.stage_id,
                "name": stage.name,
                "start_action_index": stage.start_action_index,
                "end_action_index": stage.end_action_index,
                "state_before": _state_payload(stage.state_before),
                "action_intents": [
                    {
                        "start_action_index": intent.start_action_index,
                        "end_action_index": intent.end_action_index,
                        "description": intent.description,
                        "target": intent.target,
                        "provenance": intent.provenance,
                        "confidence": intent.confidence,
                    }
                    for intent in stage.action_intents
                ],
                "preconditions": list(stage.preconditions),
                "expected_effects": list(stage.expected_effects),
                "state_after": _state_payload(stage.state_after),
                "dynamic_decisions": [
                    {
                        "description": decision.description,
                        "generalization": decision.generalization,
                        "confidence": decision.confidence,
                    }
                    for decision in stage.dynamic_decisions
                ],
                "confidence": stage.confidence,
            }
            for stage in stages
        ],
        "state_graph": {
            "entry_state_id": entry_state_id,
            "states": [
                {
                    "id": state.state_id,
                    "name": state.name,
                    "description": state.description,
                    "preconditions": list(state.preconditions),
                    "visual_anchors": list(state.visual_anchors),
                    "evidence_stage_ids": list(state.evidence_stage_ids),
                    "confidence": state.confidence,
                }
                for state in states
            ],
            "transitions": [
                {
                    "id": transition.transition_id,
                    "source_state_id": transition.source_state_id,
                    "target_type": transition.target_type,
                    "target_id": transition.target_id,
                    "condition": transition.condition,
                    "action_goal": transition.action_goal,
                    "expected_effects": list(transition.expected_effects),
                    "evidence_stage_ids": list(transition.evidence_stage_ids),
                    "confidence": transition.confidence,
                }
                for transition in transitions
            ],
            "terminals": [
                {
                    "id": terminal.terminal_id,
                    "kind": terminal.kind,
                    "name": terminal.name,
                    "condition": terminal.condition,
                    "visual_anchors": list(terminal.visual_anchors),
                    "evidence_frame": terminal.evidence_frame,
                    "confidence": terminal.confidence,
                }
                for terminal in terminal_states
            ],
        },
        "review": {
            "status": "draft",
            "requires_confirmation": True,
            "note": "Review inferred semantics; preserved human Trace evidence is unchanged.",
        },
    }


def load_semantic_experience(
    path: Path,
    *,
    task_id: str,
    action_count: int,
) -> SemanticExperience:
    source_path = path.resolve()
    root = _mapping(yaml.safe_load(source_path.read_text(encoding="utf-8")), "experience")
    if _string(root.get("task_id"), "experience.task_id") != task_id:
        raise ValueError("Semantic experience task_id does not match the task pack")
    source = _mapping(root.get("source"), "experience.source")
    source_type = _string(source.get("type"), "experience.source.type")
    if source_type != "human_trace":
        raise ValueError("V0.7 semantic experience must derive from a human Trace")
    narration_source = source.get("narration")
    if narration_source is not None and (
        not isinstance(narration_source, str) or not narration_source.strip()
    ):
        raise ValueError("experience.source.narration must be null or a non-empty path")
    narration_kind = source.get("narration_kind")
    if narration_kind is None:
        narration_kind = "none"
        if isinstance(narration_source, str):
            narration_path = (source_path.parent / narration_source).resolve()
            if narration_path.is_relative_to(source_path.parent.resolve()) and narration_path.is_file():
                narration_payload = _load_json(narration_path, "experience narration")
                narration_kind = (
                    "task_instruction"
                    if narration_payload.get("transcription_engine") == "waa_task_instruction"
                    else "human"
                )
    if narration_kind not in {"none", "task_instruction", "human"}:
        raise ValueError("experience.source.narration_kind is invalid")
    if (narration_source is None) != (narration_kind == "none"):
        raise ValueError("experience narration path and narration_kind disagree")
    task_root = source_path.parent.resolve()
    allowed_frames = {
        path.resolve().relative_to(task_root).as_posix()
        for path in (task_root / "reference" / "frames").glob("*.png")
        if path.is_file()
    }
    (
        canonical_instruction,
        goal,
        summary,
        completion_mode,
        completion_success_condition,
        completion_reason,
        stages,
        entry_state_id,
        states,
        transitions,
        terminal_states,
        narration_claims,
    ) = _validate_semantic_payload(
        root,
        task_id=task_id,
        action_count=action_count,
        allowed_frames=allowed_frames,
    )
    compiler = _mapping(root.get("compiler"), "experience.compiler")
    review = _mapping(root.get("review"), "experience.review")
    review_status = _string(review.get("status"), "experience.review.status")
    requires_confirmation = review.get("requires_confirmation")
    if review_status not in {"draft", "confirmed"} or not isinstance(
        requires_confirmation, bool
    ):
        raise ValueError("Semantic experience review state is invalid")
    if (review_status == "draft") != requires_confirmation:
        raise ValueError("Semantic experience draft and confirmation state disagree")
    return SemanticExperience(
        canonical_instruction=canonical_instruction,
        goal=goal,
        summary=summary,
        completion_mode=completion_mode,
        completion_success_condition=completion_success_condition,
        completion_reason=completion_reason,
        stages=stages,
        entry_state_id=entry_state_id,
        states=states,
        transitions=transitions,
        terminal_states=terminal_states,
        narration_claims=narration_claims,
        source_type=source_type,
        narration_available=narration_source is not None,
        narration_kind=narration_kind,
        model=_string(compiler.get("model"), "experience.compiler.model"),
        reasoning_effort=_string(
            compiler.get("reasoning_effort"), "experience.compiler.reasoning_effort"
        ),
        review_status=review_status,
        requires_confirmation=requires_confirmation,
        source_path=source_path,
    )


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{label} is not valid JSON: {path}") from error
    return _mapping(value, label)


def _reference_frame_for_event(event: Mapping[str, Any]) -> str:
    frame = event.get("frame")
    if not isinstance(frame, str) or not frame:
        raise ValueError("Human Trace event has no frame")
    return f"reference/{frame}"


def _screen_change(first: Path, second: Path) -> float:
    if first == second:
        return 0.0
    first_surface = pygame.transform.smoothscale(pygame.image.load(first), (64, 36))
    second_surface = pygame.transform.smoothscale(pygame.image.load(second), (64, 36))
    first_rgb = pygame.surfarray.array3d(first_surface).astype(np.float32)
    second_rgb = pygame.surfarray.array3d(second_surface).astype(np.float32)
    return round(float(np.mean(np.abs(first_rgb - second_rgb)) / 255.0), 4)


def _prepare_timeline(task_root: Path) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    demonstration = _load_json(task_root / "demonstration.json", "demonstration")
    raw_actions = demonstration.get("actions")
    if not isinstance(raw_actions, list) or not raw_actions:
        raise ValueError("Demonstration must contain actions")
    trace = _load_json_lines(task_root / "reference" / "trace.jsonl")
    by_seq = {event["seq"]: event for event in trace}
    timeline: list[dict[str, Any]] = []
    boundary_hints: list[dict[str, Any]] = []
    all_frames: list[str] = [_reference_frame_for_event(trace[0])]
    previous_end_ms = 0.0
    for index, raw_entry in enumerate(raw_actions):
        entry = _mapping(raw_entry, f"demonstration.actions[{index}]")
        action = _mapping(entry.get("action"), f"demonstration.actions[{index}].action")
        source = _mapping(entry.get("source"), f"demonstration.actions[{index}].source")
        seqs = source.get("seqs")
        if not isinstance(seqs, list) or not seqs or not all(isinstance(seq, int) for seq in seqs):
            raise ValueError(f"Demonstration action {index} has invalid source sequences")
        start_seq = min(seqs)
        before_event = by_seq.get(max(0, start_seq - 1), by_seq[start_seq])
        before_frame = _reference_frame_for_event(before_event)
        after_frame = _string(
            source.get("evidence_frame"),
            f"demonstration.actions[{index}].source.evidence_frame",
        )
        start_ms = float(source.get("start_elapsed_ms", 0.0))
        end_ms = float(source.get("end_elapsed_ms", start_ms))
        before_path = (task_root / before_frame).resolve()
        after_path = (task_root / after_frame).resolve()
        change = _screen_change(before_path, after_path)
        reasons: list[str] = []
        if start_ms - previous_end_ms >= TEMPORAL_BOUNDARY_MS:
            reasons.append("long_gap_before")
        if action.get("skill") == "wait" and int(
            _mapping(action.get("args"), "action.args").get("duration_ms", 0)
        ) >= TEMPORAL_BOUNDARY_MS:
            reasons.append("long_wait")
        if change >= SCREEN_CHANGE_THRESHOLD:
            reasons.append("large_screen_change")
        if reasons:
            boundary_hints.append(
                {
                    "after_action_index": index,
                    "reasons": reasons,
                    "screen_change": change,
                }
            )
        timeline.append(
            {
                "index": index,
                "action": action,
                "source_seqs": seqs,
                "start_elapsed_ms": start_ms,
                "end_elapsed_ms": end_ms,
                "before_frame": before_frame,
                "after_frame": after_frame,
                "screen_change": change,
                "compiler_inference": source.get("inference"),
            }
        )
        all_frames.extend((before_frame, after_frame))
        previous_end_ms = max(previous_end_ms, end_ms)
    all_frames.append(_reference_frame_for_event(trace[-1]))
    return timeline, list(dict.fromkeys(all_frames)), boundary_hints


def _load_json_lines(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"Human Trace line {line_number} is invalid JSON") from error
        events.append(_mapping(event, f"Human Trace line {line_number}"))
    if not events:
        raise ValueError("Human Trace is empty")
    return events


def _sample_frames(frames: Sequence[str]) -> list[str]:
    if len(frames) <= MAX_EVIDENCE_IMAGES:
        return list(frames)
    indexes = {
        round(index * (len(frames) - 1) / (MAX_EVIDENCE_IMAGES - 1))
        for index in range(MAX_EVIDENCE_IMAGES)
    }
    return [frames[index] for index in sorted(indexes)]


def _create_contact_sheets(
    task_root: Path,
    evidence_frames: Sequence[str],
    output_dir: Path,
) -> tuple[tuple[Path, ...], str]:
    pygame.font.init()
    font = pygame.font.Font(None, 20)
    per_sheet = CONTACT_SHEET_COLUMNS * CONTACT_SHEET_ROWS
    cell_width, thumbnail_height = CONTACT_SHEET_THUMBNAIL
    cell_height = CONTACT_SHEET_LABEL_HEIGHT + thumbnail_height
    paths: list[Path] = []
    labels: list[str] = []
    for sheet_index, start in enumerate(range(0, len(evidence_frames), per_sheet), 1):
        frames = evidence_frames[start : start + per_sheet]
        rows = (len(frames) + CONTACT_SHEET_COLUMNS - 1) // CONTACT_SHEET_COLUMNS
        sheet = pygame.Surface((cell_width * CONTACT_SHEET_COLUMNS, cell_height * rows))
        sheet.fill((24, 28, 32))
        for local_index, relative in enumerate(frames):
            global_index = start + local_index + 1
            column = local_index % CONTACT_SHEET_COLUMNS
            row = local_index // CONTACT_SHEET_COLUMNS
            left = column * cell_width
            top = row * cell_height
            label = font.render(f"Frame {global_index}", True, (245, 248, 250))
            sheet.blit(label, (left + 8, top + 3))
            source = pygame.image.load(task_root / relative)
            thumbnail = pygame.transform.smoothscale(source, CONTACT_SHEET_THUMBNAIL)
            sheet.blit(thumbnail, (left, top + CONTACT_SHEET_LABEL_HEIGHT))
            labels.append(
                f"Contact sheet Image {sheet_index}, cell Frame {global_index}: {relative}"
            )
        path = output_dir / f"contact-sheet-{sheet_index:02d}.png"
        pygame.image.save(sheet, path)
        paths.append(path)
    return tuple(paths), "\n".join(labels)


def _output_schema(evidence_frames: Sequence[str], action_count: int) -> dict[str, Any]:
    state = {
        "type": "object",
        "properties": {
            "description": {"type": "string", "minLength": 1},
            "evidence_frame": {"type": "string", "enum": list(evidence_frames)},
            "visual_anchors": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "minItems": 1,
                "maxItems": 6,
            },
        },
        "required": ["description", "evidence_frame", "visual_anchors"],
        "additionalProperties": False,
    }
    graph_state = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "minLength": 2, "maxLength": 48},
            "name": {"type": "string", "minLength": 1},
            "description": {"type": "string", "minLength": 1},
            "preconditions": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "minItems": 1,
                "maxItems": 8,
            },
            "visual_anchors": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "minItems": 1,
                "maxItems": 8,
            },
            "evidence_stage_ids": {
                "type": "array",
                "items": {"type": "string", "minLength": 2, "maxLength": 48},
                "minItems": 1,
                "maxItems": 6,
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": [
            "id",
            "name",
            "description",
            "preconditions",
            "visual_anchors",
            "evidence_stage_ids",
            "confidence",
        ],
        "additionalProperties": False,
    }
    graph_transition = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "minLength": 2, "maxLength": 64},
            "source_state_id": {"type": "string", "minLength": 2, "maxLength": 48},
            "target_type": {"type": "string", "enum": ["state", "terminal"]},
            "target_id": {"type": "string", "minLength": 2, "maxLength": 48},
            "condition": {"type": "string", "minLength": 1},
            "action_goal": {"type": "string", "minLength": 1},
            "expected_effects": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "minItems": 1,
                "maxItems": 8,
            },
            "evidence_stage_ids": {
                "type": "array",
                "items": {"type": "string", "minLength": 2, "maxLength": 48},
                "minItems": 1,
                "maxItems": 6,
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": [
            "id",
            "source_state_id",
            "target_type",
            "target_id",
            "condition",
            "action_goal",
            "expected_effects",
            "evidence_stage_ids",
            "confidence",
        ],
        "additionalProperties": False,
    }
    terminal_state = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "minLength": 2, "maxLength": 48},
            "kind": {"type": "string", "enum": ["success", "failure"]},
            "name": {"type": "string", "minLength": 1},
            "condition": {"type": "string", "minLength": 1},
            "visual_anchors": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "minItems": 1,
                "maxItems": 8,
            },
            "evidence_frame": {"type": "string", "enum": list(evidence_frames)},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": [
            "id",
            "kind",
            "name",
            "condition",
            "visual_anchors",
            "evidence_frame",
            "confidence",
        ],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "canonical_instruction": {"type": "string", "minLength": 1},
            "goal": {"type": "string", "minLength": 1},
            "summary": {"type": "string", "minLength": 1},
            "completion": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": sorted(COMPLETION_MODES)},
                    "success_condition": {"type": "string", "minLength": 1},
                    "reason": {"type": "string", "minLength": 1},
                },
                "required": ["mode", "success_condition", "reason"],
                "additionalProperties": False,
            },
            "narration_claims": {
                "type": "array",
                "maxItems": 24,
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "minLength": 1, "maxLength": 1000},
                        "type": {
                            "type": "string",
                            "enum": sorted(NARRATION_CLAIM_TYPES),
                        },
                        "start_action_index": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": action_count - 1,
                        },
                        "end_action_index": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": action_count - 1,
                        },
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "verdict": {
                            "type": "string",
                            "enum": sorted(NARRATION_CLAIM_VERDICTS),
                        },
                        "reason": {"type": "string", "minLength": 1, "maxLength": 1000},
                    },
                    "required": [
                        "text",
                        "type",
                        "start_action_index",
                        "end_action_index",
                        "confidence",
                        "verdict",
                        "reason",
                    ],
                    "additionalProperties": False,
                },
            },
            "stages": {
                "type": "array",
                "minItems": 1,
                "maxItems": min(MAX_SEMANTIC_STAGES, action_count),
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "minLength": 2, "maxLength": 48},
                        "name": {"type": "string", "minLength": 1},
                        "start_action_index": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": action_count - 1,
                        },
                        "end_action_index": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": action_count - 1,
                        },
                        "state_before": state,
                        "action_intents": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": action_count,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "start_action_index": {
                                        "type": "integer",
                                        "minimum": 0,
                                        "maximum": action_count - 1,
                                    },
                                    "end_action_index": {
                                        "type": "integer",
                                        "minimum": 0,
                                        "maximum": action_count - 1,
                                    },
                                    "description": {"type": "string", "minLength": 1},
                                    "target": {"type": "string", "minLength": 1},
                                    "provenance": {
                                        "type": "string",
                                        "enum": sorted(PROVENANCE_VALUES),
                                    },
                                    "confidence": {
                                        "type": "number",
                                        "minimum": 0,
                                        "maximum": 1,
                                    },
                                },
                                "required": [
                                    "start_action_index",
                                    "end_action_index",
                                    "description",
                                    "target",
                                    "provenance",
                                    "confidence",
                                ],
                                "additionalProperties": False,
                            },
                        },
                        "preconditions": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                            "minItems": 1,
                            "maxItems": 8,
                        },
                        "expected_effects": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                            "minItems": 1,
                            "maxItems": 8,
                        },
                        "state_after": state,
                        "dynamic_decisions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "description": {"type": "string", "minLength": 1},
                                    "generalization": {
                                        "type": "string",
                                        "enum": sorted(GENERALIZATION_VALUES),
                                    },
                                    "confidence": {
                                        "type": "number",
                                        "minimum": 0,
                                        "maximum": 1,
                                    },
                                },
                                "required": ["description", "generalization", "confidence"],
                                "additionalProperties": False,
                            },
                            "maxItems": 8,
                        },
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": [
                        "id",
                        "name",
                        "start_action_index",
                        "end_action_index",
                        "state_before",
                        "action_intents",
                        "preconditions",
                        "expected_effects",
                        "state_after",
                        "dynamic_decisions",
                        "confidence",
                    ],
                    "additionalProperties": False,
                },
            },
            "state_graph": {
                "type": "object",
                "properties": {
                    "entry_state_id": {
                        "type": "string",
                        "minLength": 2,
                        "maxLength": 48,
                    },
                    "states": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": MAX_TASK_STATES,
                        "items": graph_state,
                    },
                    "transitions": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": MAX_TASK_TRANSITIONS,
                        "items": graph_transition,
                    },
                    "terminals": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": MAX_TERMINAL_STATES,
                        "items": terminal_state,
                    },
                },
                "required": ["entry_state_id", "states", "transitions", "terminals"],
                "additionalProperties": False,
            },
        },
        "required": [
            "canonical_instruction",
            "goal",
            "summary",
            "completion",
            "narration_claims",
            "stages",
            "state_graph",
        ],
        "additionalProperties": False,
    }


def _deduplicate_narration_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    clauses = [
        " ".join(clause.split())
        for clause in re.split(r"[，。！？,!?；;]+", value)
        if clause.strip()
    ]
    compact: list[str] = []
    for clause in clauses:
        if not compact or clause.casefold() != compact[-1].casefold():
            compact.append(clause)
    return "；".join(compact)


def _aligned_narration_context(
    narration: Mapping[str, Any] | None,
    timeline: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if narration is None or not timeline:
        return []
    raw_segments = narration.get("segments")
    segments = raw_segments if isinstance(raw_segments, list) else []
    raw_audio_offset = narration.get("audio_start_trace_elapsed_ms")
    audio_offset = (
        float(raw_audio_offset)
        if isinstance(raw_audio_offset, (int, float))
        and not isinstance(raw_audio_offset, bool)
        else 0.0
    )
    if not segments:
        transcript = _deduplicate_narration_text(narration.get("transcript"))
        return (
            [
                {
                    "segment": 1,
                    "text": transcript,
                    "aligned_action_range": [0, len(timeline) - 1],
                    "alignment": "task_level_without_timestamps",
                }
            ]
            if transcript
            else []
        )

    aligned: list[dict[str, Any]] = []
    for segment_index, raw_segment in enumerate(segments, start=1):
        if not isinstance(raw_segment, dict):
            continue
        text = _deduplicate_narration_text(raw_segment.get("text"))
        start_ms = raw_segment.get("start_ms")
        end_ms = raw_segment.get("end_ms")
        if (
            not text
            or not isinstance(start_ms, (int, float))
            or isinstance(start_ms, bool)
            or not isinstance(end_ms, (int, float))
            or isinstance(end_ms, bool)
            or end_ms < start_ms
        ):
            continue
        trace_start_ms = float(start_ms) + audio_offset
        trace_end_ms = float(end_ms) + audio_offset
        overlapping = [
            index
            for index, action in enumerate(timeline)
            if float(action.get("end_elapsed_ms", 0.0)) >= trace_start_ms
            and float(action.get("start_elapsed_ms", 0.0)) <= trace_end_ms
        ]
        alignment = "timestamp_overlap"
        if not overlapping:
            spoken_before = [
                index
                for index, action in enumerate(timeline)
                if trace_end_ms
                <= float(action.get("start_elapsed_ms", 0.0))
                <= trace_end_ms + 4_000
            ]
            if spoken_before:
                overlapping = [spoken_before[0]]
                alignment = "spoken_before_action"
            else:
                midpoint = (trace_start_ms + trace_end_ms) / 2
                nearest = min(
                    range(len(timeline)),
                    key=lambda index: abs(
                        (
                            float(timeline[index].get("start_elapsed_ms", 0.0))
                            + float(timeline[index].get("end_elapsed_ms", 0.0))
                        )
                        / 2
                        - midpoint
                    ),
                )
                overlapping = [nearest]
                alignment = "nearest_action"
        aligned.append(
            {
                "segment": segment_index,
                "audio_start_ms": round(float(start_ms), 1),
                "audio_end_ms": round(float(end_ms), 1),
                "start_ms": round(trace_start_ms, 1),
                "end_ms": round(trace_end_ms, 1),
                "trace_start_ms": round(trace_start_ms, 1),
                "trace_end_ms": round(trace_end_ms, 1),
                "text": text,
                "aligned_action_range": [min(overlapping), max(overlapping)],
                "alignment": alignment,
            }
        )
    return aligned


def _prompt(
    *,
    task_id: str,
    task_instruction: str,
    timeline: Sequence[Mapping[str, Any]],
    evidence_map: str,
    boundary_hints: Sequence[Mapping[str, Any]],
    narration: Mapping[str, Any] | None,
) -> str:
    aligned_narration = _aligned_narration_context(narration, timeline)
    narration_context = json.dumps(
        {
            "transcription_engine": (
                narration.get("transcription_engine", "unknown")
                if narration is not None
                else "unavailable"
            ),
            "segments": aligned_narration,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        "You are the offline multimodal Trace Compiler Agent for Trace2Task. You do not "
        "execute the task. Convert one successful human demonstration into a grounded, "
        "replaceable semantic interpretation. The preserved raw Trace is immutable evidence of "
        "what happened, not an executable program for future runs.\n\n"
        f"Human task name: {task_id}\n"
        f"Current generic task instruction: {task_instruction}\n"
        f"Evidence contact-sheet map:\n{evidence_map}\n\n"
        "Optional human narration is advisory evidence. Its timestamped segments have been aligned "
        "to action ranges, but casual phrases such as '随便', '都用掉', repetition, and speech-recognition "
        "errors are not universal strategy. Extract every useful statement into narration_claims. "
        "Classify a one-off convenient choice as example_only, use verdict=advisory when pixels do not "
        "fully ground it, and verdict=rejected when it conflicts with observed state changes. A claim "
        "may help explain intent during compilation, but narration claims are never injected directly "
        "as runtime motor commands:\n"
        f"{narration_context}\n\n"
        "Return a concise canonical_instruction suitable for the runtime Agent. It must state the "
        "goal and major phase order, not turn low-confidence narration or one demonstrated choice "
        "into a universal tactic. Multi-action batching, plan horizon, reducing model calls, and "
        "adaptive wait behavior are task-independent executor policies; do not encode them in the "
        "canonical instruction, summary, state graph, stages, or dynamic decisions. Preserve only "
        "task-specific sequence facts, such as a UI that requires exactly three selections or a "
        "predictable target-selection branch after a particular control. Also classify "
        "completion as state or cycle. A cycle starts and ends at the same visual anchor; if the "
        "initial screen already matches that anchor, it is not complete until the run visibly "
        "leaves it and later returns.\n\n"
        "First partition every action index exactly once into contiguous Trace episodes. These "
        "episodes describe the one recorded trajectory and remain evidence, not the runtime control "
        "flow. Within every episode, "
        "partition every action index exactly once into contiguous action_intents. Describe only "
        "visually grounded states, observable preconditions, action intent, and expected visible "
        "effects. Then derive a separate state_graph for runtime reasoning. A state is a reusable "
        "visually recognizable situation, not an action interval. Transitions are directed legal "
        "moves and may branch, loop, or return from a later state to an earlier state when the "
        "visible task permits it; never infer next_state from episode order alone. Success and "
        "failure are terminal nodes outside the numbered/reusable states. Every state and transition "
        "must cite one or more Trace episode IDs as grounding evidence. Coordinates, drag paths, "
        "hold durations, and input skills are physical evidence, "
        "not semantic intent. A recorded drag or hold does not prove that the control requires that "
        "gesture; describe the visible target and intended state transition instead.\n\n"
        "A single trajectory does not prove a general strategy. If a choice could depend on "
        "runtime content (for example which game card, contact, file, item, or route to choose), "
        "record it in dynamic_decisions as runtime_agent_decides or unknown. Never turn a "
        "demonstrated choice into a universal rule without evidence. Use provenance=unknown and "
        "low confidence when intent cannot be recovered. Do not invent application internals or "
        "text that is not visible. Use concise Simplified Chinese descriptions when the task name "
        "or visible application is primarily Chinese; otherwise follow the task language.\n\n"
        "For every stage, choose state_before and state_after evidence only from contact-sheet frames "
        "you can actually see. Prefer stable pre-interaction and post-transition frames; do not use an "
        "animation frame merely because it is mechanically adjacent to an action boundary. The chosen "
        "frames remain reviewable and will not be overwritten by raw boundary frames.\n\n"
        "Local candidate boundaries are hints, not truth:\n"
        f"{json.dumps(list(boundary_hints), ensure_ascii=False, separators=(',', ':'))}\n\n"
        "Deterministically compiled action timeline:\n"
        f"{json.dumps(list(timeline), ensure_ascii=False, separators=(',', ':'))}\n\n"
        "Return the full result matching the supplied JSON schema."
    )


def _attach_experience(task_path: Path, document: Mapping[str, Any]) -> Path:
    task_root = task_path.parent.resolve()
    experience_path = task_root / "experience.yaml"
    temporary = task_root / "experience.yaml.tmp"
    temporary.write_text(
        yaml.safe_dump(dict(document), sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )
    temporary.replace(experience_path)

    task_data = _mapping(yaml.safe_load(task_path.read_text(encoding="utf-8")), "task")
    task_data["instruction"] = document["canonical_instruction"]
    completion = _mapping(document.get("completion"), "experience completion")
    verifier = _mapping(task_data.get("verifier"), "task.verifier")
    verifier["expected"] = completion["success_condition"]
    verifier["completion"] = {
        "mode": completion["mode"],
        "require_departure_from_reference": completion["mode"] == "cycle",
        "reason": completion["reason"],
    }
    task_data["semantic_experience"] = {
        "path": "experience.yaml",
        "stage_count": len(document["stages"]),
        "state_count": len(document["state_graph"]["states"]),
        "transition_count": len(document["state_graph"]["transitions"]),
        "terminal_count": len(document["state_graph"]["terminals"]),
        "revision": 0,
        "source": "human_trace",
    }
    previous_guidance = task_data.get("human_guidance")
    guidance_compatible = False
    if isinstance(previous_guidance, dict):
        guidance_relative = previous_guidance.get("path")
        guidance_path = (
            (task_root / guidance_relative).resolve()
            if isinstance(guidance_relative, str) and guidance_relative
            else None
        )
        if (
            guidance_path is not None
            and guidance_path.is_relative_to(task_root)
            and guidance_path.is_file()
        ):
            guidance_data = yaml.safe_load(guidance_path.read_text(encoding="utf-8"))
            raw_rules = guidance_data.get("rules", []) if isinstance(guidance_data, dict) else []
            graph = document["state_graph"]
            scope_ids = {
                "state": {str(state["id"]) for state in graph["states"]},
                "transition": {
                    str(transition["id"]) for transition in graph["transitions"]
                },
                "terminal": {str(terminal["id"]) for terminal in graph["terminals"]},
            }
            guidance_compatible = True
            for rule in raw_rules:
                if not isinstance(rule, dict):
                    guidance_compatible = False
                    break
                raw_scope = rule.get("scope")
                if isinstance(raw_scope, dict):
                    scope_type = str(raw_scope.get("type") or "")
                    scope_id = str(raw_scope.get("id") or "")
                else:
                    legacy_stage = str(rule.get("stage_id") or "")
                    scope_type = "global" if legacy_stage == "global" else "state"
                    scope_id = "global" if legacy_stage == "global" else legacy_stage
                if scope_type == "global" and scope_id == "global":
                    continue
                if scope_id not in scope_ids.get(scope_type, set()):
                    guidance_compatible = False
                    break
    if previous_guidance is not None and not guidance_compatible:
        task_data.pop("human_guidance", None)
    review = _mapping(task_data.get("review"), "task.review")
    review["status"] = "draft"
    review["requires_confirmation"] = True
    review.pop("confirmed_at", None)
    checklist = review.setdefault("checklist", [])
    semantic_check = (
        "Review the Compiler Agent Trace episodes, directed runtime states, legal transitions, "
        "terminal outcomes, intents, preconditions, effects, and unknown dynamic decisions."
    )
    if isinstance(checklist, list) and semantic_check not in checklist:
        checklist.append(semantic_check)
    guidance_check = (
        "The semantic task-state structure changed, so previously confirmed human guidance was "
        "deactivated. Generate and review a new guidance revision before relying on it."
    )
    if (
        previous_guidance is not None
        and not guidance_compatible
        and isinstance(checklist, list)
        and guidance_check not in checklist
    ):
        checklist.append(guidance_check)
    task_path.write_text(
        yaml.safe_dump(task_data, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )
    return experience_path


def compile_windows_semantic_experience(
    task_path: Path,
    *,
    model: str = DEFAULT_COMPILER_MODEL,
    reasoning_effort: str = DEFAULT_COMPILER_REASONING_EFFORT,
    codex_bin: str = "codex",
    timeout_seconds: float = 300,
    use_narration: bool = True,
    binary_resolver: BinaryResolver = resolve_codex_binary,
    session_factory: SessionFactory = CodexAppServerSession,
) -> SemanticCompilation:
    """Interpret a preserved human Windows Trace without modifying its evidence."""

    source_path = task_path.expanduser().resolve()
    task_data = _mapping(yaml.safe_load(source_path.read_text(encoding="utf-8")), "task")
    environment = _mapping(task_data.get("environment"), "task.environment")
    if environment.get("adapter") != "trace2task.windows":
        raise ValueError("Semantic Windows compilation requires a Windows task pack")
    task_id = _string(task_data.get("id"), "task.id")
    task_instruction = _string(task_data.get("instruction"), "task.instruction")
    timeline, all_frames, boundary_hints = _prepare_timeline(source_path.parent)
    evidence_frames = _sample_frames(all_frames)
    narration = (
        load_narration(source_path.parent / "reference" / "narration.json")
        if use_narration
        else None
    )
    narration_kind = (
        "none"
        if narration is None
        else (
            "task_instruction"
            if narration.get("transcription_engine") == "waa_task_instruction"
            else "human"
        )
    )
    original_evidence_paths = tuple(
        (source_path.parent / frame).resolve() for frame in evidence_frames
    )
    if not original_evidence_paths or not all(path.is_file() for path in original_evidence_paths):
        raise FileNotFoundError("Semantic compiler evidence frames are incomplete")
    schema = _output_schema(evidence_frames, len(timeline))
    last_error: Exception | None = None
    with tempfile.TemporaryDirectory(prefix="trace2task-compiler-evidence-") as directory:
        evidence_paths, evidence_map = _create_contact_sheets(
            source_path.parent,
            evidence_frames,
            Path(directory),
        )
        prompt = _prompt(
            task_id=task_id,
            task_instruction=task_instruction,
            timeline=timeline,
            evidence_map=evidence_map,
            boundary_hints=boundary_hints,
            narration=narration,
        )
        executable = binary_resolver(codex_bin)
        session = session_factory(
            executable,
            model=model,
            reasoning_effort=reasoning_effort,
            cwd=Path.cwd(),
            timeout_seconds=timeout_seconds,
            progress_timeout_seconds=COMPILER_RESPONSE_IDLE_TIMEOUT_SECONDS,
            hard_timeout_seconds=max(COMPILER_HARD_TIMEOUT_SECONDS, timeout_seconds),
        )
        try:
            for attempt in range(2):
                active_prompt = prompt
                if attempt and last_error is not None:
                    active_prompt = (
                        "Your previous semantic compilation failed deterministic validation: "
                        f"{last_error}. Return a complete corrected result. Preserve exact "
                        "contiguous coverage of every action index and use only supplied evidence "
                        "frame paths. Keep Trace episodes separate from the validated directed "
                        "state graph and keep terminal outcomes outside reusable states."
                    )
                output = session.run_turn(
                    prompt=active_prompt,
                    image_path=evidence_paths[0],
                    additional_image_paths=evidence_paths[1:],
                    output_schema=schema,
                )
                try:
                    payload = json.loads(output.strip())
                    (
                        canonical_instruction,
                        goal,
                        summary,
                        completion_mode,
                        completion_success_condition,
                        completion_reason,
                        stages,
                        entry_state_id,
                        states,
                        transitions,
                        terminal_states,
                        narration_claims,
                    ) = _validate_semantic_payload(
                        payload,
                        task_id=task_id,
                        action_count=len(timeline),
                        allowed_frames=set(evidence_frames),
                    )
                except (json.JSONDecodeError, TypeError, ValueError) as error:
                    last_error = error
                    continue
                document = _experience_document(
                    task_id=task_id,
                    canonical_instruction=canonical_instruction,
                    goal=goal,
                    summary=summary,
                    completion_mode=completion_mode,
                    completion_success_condition=completion_success_condition,
                    completion_reason=completion_reason,
                    stages=stages,
                    entry_state_id=entry_state_id,
                    states=states,
                    transitions=transitions,
                    terminal_states=terminal_states,
                    narration_claims=narration_claims,
                    model=model,
                    reasoning_effort=reasoning_effort,
                    narration_kind=narration_kind,
                )
                experience_path = _attach_experience(source_path, document)
                loaded = load_semantic_experience(
                    experience_path,
                    task_id=task_id,
                    action_count=len(timeline),
                )
                return SemanticCompilation(
                    experience_path=str(experience_path),
                    stage_count=len(loaded.stages),
                    model=model,
                    reasoning_effort=reasoning_effort,
                    review_status=loaded.review_status,
                    narration_available=loaded.narration_available,
                    narration_kind=loaded.narration_kind,
                )
        finally:
            session.close()
    raise RuntimeError(f"Compiler Agent returned an invalid semantic experience: {last_error}")
