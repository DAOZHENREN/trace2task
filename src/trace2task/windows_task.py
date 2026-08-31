from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from trace2task.actions import WINDOWS_MOTOR_SKILLS, ActionCall
from trace2task.taskpack import TaskPack, load_taskpack
from trace2task.windows_control import WindowSelector
from trace2task.windows_experience import SemanticExperience, load_semantic_experience
from trace2task.windows_guidance import HumanGuidance, load_human_guidance
from trace2task.windows_verification import EffectVerifierSpec

WINDOWS_ADAPTER = "trace2task.windows"


@dataclass(frozen=True)
class WindowsTaskContract:
    task: TaskPack
    selector: WindowSelector
    demonstration: tuple[ActionCall, ...]
    reference_frame: Path
    effect_verifier: EffectVerifierSpec
    semantic_experience: SemanticExperience | None = None
    human_guidance: HumanGuidance | None = None
    runtime_instruction: str | None = None

    @property
    def instruction(self) -> str:
        return self.runtime_instruction or self.task.instruction

    def with_instruction(self, instruction: str) -> WindowsTaskContract:
        normalized = " ".join(instruction.split())
        if not normalized:
            raise ValueError("Runtime instruction must not be empty")
        if len(normalized) > 2_000:
            raise ValueError("Runtime instruction must not exceed 2000 characters")
        return WindowsTaskContract(
            task=self.task,
            selector=self.selector,
            demonstration=self.demonstration,
            reference_frame=self.reference_frame,
            effect_verifier=self.effect_verifier,
            semantic_experience=self.semantic_experience,
            human_guidance=self.human_guidance,
            runtime_instruction=normalized,
        )


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"Windows task field '{field}' must be a mapping")
    return value


def _contained_file(task_path: Path, relative: object, field: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError(f"Windows task field '{field}' must be a relative file path")
    root = task_path.parent.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"Windows task field '{field}' points outside the task pack")
    if not path.is_file():
        raise FileNotFoundError(f"Windows task file does not exist: {path}")
    return path


def load_windows_task(path: Path) -> WindowsTaskContract:
    """Load the Windows-only fields alongside the shared task-pack contract."""

    source_path = path.expanduser().resolve()
    task = load_taskpack(source_path)
    if task.environment_adapter != WINDOWS_ADAPTER:
        raise ValueError(f"Expected Windows adapter {WINDOWS_ADAPTER!r}")
    unknown_skills = set(task.actions) - set(WINDOWS_MOTOR_SKILLS)
    if unknown_skills:
        raise ValueError(f"Windows task declares unsupported skills: {sorted(unknown_skills)}")

    root = _mapping(yaml.safe_load(source_path.read_text(encoding="utf-8")), "root")
    environment = _mapping(root.get("environment"), "environment")
    target = _mapping(environment.get("target"), "environment.target")
    title = target.get("title_contains")
    process = target.get("process_name")
    if title is not None and (not isinstance(title, str) or not title.strip()):
        raise ValueError("Windows target title_contains must be a non-empty string when provided")
    if process is not None and (not isinstance(process, str) or not process.strip()):
        raise ValueError("Windows target process_name must be a non-empty string when provided")
    selector = WindowSelector(
        title_contains=title.strip() if isinstance(title, str) else None,
        process_name=process.strip() if isinstance(process, str) else None,
    )

    demonstration_config = _mapping(root.get("demonstration"), "demonstration")
    demonstration_path = _contained_file(
        source_path,
        demonstration_config.get("path"),
        "demonstration.path",
    )
    demonstration_data = _mapping(
        json.loads(demonstration_path.read_text(encoding="utf-8")),
        "demonstration file",
    )
    raw_actions = demonstration_data.get("actions")
    if not isinstance(raw_actions, list) or not raw_actions:
        raise ValueError("Windows demonstration must contain a non-empty actions list")
    actions: list[ActionCall] = []
    for index, entry in enumerate(raw_actions):
        entry_mapping = _mapping(entry, f"demonstration.actions[{index}]")
        call = ActionCall.from_payload(entry_mapping.get("action"))
        if call.skill not in task.actions:
            raise ValueError(
                f"Demonstration action {index} uses undeclared skill {call.skill!r}"
            )
        actions.append(call)
    declared_count = demonstration_config.get("action_count")
    if declared_count != len(actions):
        raise ValueError("Windows demonstration action_count does not match its action list")

    verifier = _mapping(root.get("verifier"), "verifier")
    reference_frame = _contained_file(
        source_path,
        verifier.get("reference_frame"),
        "verifier.reference_frame",
    )
    verifier_type = verifier.get("type")
    expected = verifier.get("expected")
    if not isinstance(verifier_type, str) or not verifier_type.strip():
        raise ValueError("Windows verifier.type must be a non-empty string")
    if not isinstance(expected, str) or not expected.strip():
        raise ValueError("Windows verifier.expected must be a non-empty string")
    effect_verifier = EffectVerifierSpec(
        verifier_type=verifier_type.strip(),
        expected=" ".join(expected.split()),
        reference_frame=reference_frame,
        options={
            key: value
            for key, value in verifier.items()
            if key not in {"type", "expected", "reference_frame", "completion"}
        },
    )
    semantic_experience: SemanticExperience | None = None
    semantic_config = root.get("semantic_experience")
    if semantic_config is not None:
        semantic_mapping = _mapping(semantic_config, "semantic_experience")
        semantic_path = _contained_file(
            source_path,
            semantic_mapping.get("path"),
            "semantic_experience.path",
        )
        semantic_experience = load_semantic_experience(
            semantic_path,
            task_id=task.task_id,
            action_count=len(actions),
        )
        declared_stages = semantic_mapping.get("stage_count")
        if declared_stages != len(semantic_experience.stages):
            raise ValueError(
                "Windows semantic_experience.stage_count does not match experience.yaml"
            )
        declared_states = semantic_mapping.get("state_count")
        if declared_states is not None and declared_states != len(
            semantic_experience.states
        ):
            raise ValueError(
                "Windows semantic_experience.state_count does not match experience.yaml"
            )
        declared_transitions = semantic_mapping.get("transition_count")
        if declared_transitions is not None and declared_transitions != len(
            semantic_experience.transitions
        ):
            raise ValueError(
                "Windows semantic_experience.transition_count does not match experience.yaml"
            )
        if task.requires_confirmation != semantic_experience.requires_confirmation:
            raise ValueError(
                "Task pack and semantic experience confirmation states do not match"
            )
    human_guidance: HumanGuidance | None = None
    guidance_config = root.get("human_guidance")
    if guidance_config is not None:
        if semantic_experience is None:
            raise ValueError("Human guidance requires a semantic experience")
        guidance_mapping = _mapping(guidance_config, "human_guidance")
        guidance_path = _contained_file(
            source_path,
            guidance_mapping.get("path"),
            "human_guidance.path",
        )
        stage_ids = set(semantic_experience.state_ids)
        human_guidance = load_human_guidance(
            guidance_path,
            task_id=task.task_id,
            stage_ids=stage_ids,
            transition_ids={
                transition.transition_id
                for transition in semantic_experience.transitions
            },
            terminal_ids=set(semantic_experience.terminal_ids),
        )
        if guidance_mapping.get("revision") != human_guidance.revision:
            raise ValueError("Windows human_guidance.revision does not match guidance.yaml")
        if guidance_mapping.get("rule_count") != len(human_guidance.rules):
            raise ValueError("Windows human_guidance.rule_count does not match guidance.yaml")
    return WindowsTaskContract(
        task=task,
        selector=selector,
        demonstration=tuple(actions),
        reference_frame=reference_frame,
        effect_verifier=effect_verifier,
        semantic_experience=semantic_experience,
        human_guidance=human_guidance,
    )
