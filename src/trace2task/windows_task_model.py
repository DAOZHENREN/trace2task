from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from trace2task import __version__
from trace2task.codex_agent import resolve_codex_binary
from trace2task.codex_app_server import CodexAppServerSession
from trace2task.windows_experience import (
    COMPLETION_MODES,
    MAX_TASK_STATES,
    MAX_TASK_TRANSITIONS,
    MAX_TERMINAL_STATES,
    SemanticExperience,
    load_semantic_experience,
    validate_state_graph,
)
from trace2task.windows_guidance import (
    GuidanceScopeCatalog,
    _execution_evidence,
    guidance_scope_catalog,
    guidance_scope_payload,
)

DEFAULT_TASK_MODEL_REVISION_MODEL = "gpt-5.6-sol"
DEFAULT_TASK_MODEL_REVISION_REASONING_EFFORT = "high"


@dataclass(frozen=True)
class TaskModelProposalResult:
    proposal_path: str
    task_path: str
    candidate_path: str
    base_revision: int
    proposed_revision: int
    operation_count: int
    blocking_issue_count: int
    model: str
    reasoning_effort: str


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be an object")
    return value


def _string(value: object, label: str, *, limit: int = 4_000) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    normalized = " ".join(value.split())
    if len(normalized) > limit:
        raise ValueError(f"{label} is too long")
    return normalized


def _atomic_yaml(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        yaml.safe_dump(value, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )
    temporary.replace(path)


def _resolve_project_file(project_root: Path, value: object, label: str) -> Path:
    relative = _string(value, label)
    path = (project_root / relative).resolve()
    if not path.is_relative_to(project_root) or not path.is_file():
        raise ValueError(f"{label} does not point to a project file")
    return path


def _demonstration_action_count(task_path: Path) -> int:
    demonstration = _mapping(
        json.loads(task_path.with_name("demonstration.json").read_text(encoding="utf-8")),
        "demonstration",
    )
    actions = demonstration.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError("Demonstration must contain actions")
    return len(actions)


def _graph_schema(stage_ids: list[str], evidence_frames: list[str]) -> dict[str, Any]:
    state = {
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
                "items": {"type": "string", "enum": stage_ids},
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
    transition = {
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
                "items": {"type": "string", "enum": stage_ids},
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
    terminal = {
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
            "evidence_frame": {"type": "string", "enum": evidence_frames},
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
                        "items": state,
                    },
                    "transitions": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": MAX_TASK_TRANSITIONS,
                        "items": transition,
                    },
                    "terminals": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": MAX_TERMINAL_STATES,
                        "items": terminal,
                    },
                },
                "required": ["entry_state_id", "states", "transitions", "terminals"],
                "additionalProperties": False,
            },
            "guidance_scope_mappings": {
                "type": "array",
                "maxItems": MAX_TASK_STATES + MAX_TASK_TRANSITIONS + MAX_TERMINAL_STATES,
                "items": {
                    "type": "object",
                    "properties": {
                        "from_type": {
                            "type": "string",
                            "enum": ["state", "transition", "terminal"],
                        },
                        "from_id": {"type": "string", "minLength": 1},
                        "to_type": {
                            "type": "string",
                            "enum": ["state", "transition", "terminal"],
                        },
                        "to_id": {"type": "string", "minLength": 1},
                        "reason": {"type": "string", "minLength": 1},
                    },
                    "required": ["from_type", "from_id", "to_type", "to_id", "reason"],
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "canonical_instruction",
            "goal",
            "summary",
            "completion",
            "state_graph",
            "guidance_scope_mappings",
        ],
        "additionalProperties": False,
    }


def _indexed(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item["id"]): item for item in items}


def _diff_collection(
    kind: str,
    current: list[dict[str, Any]],
    proposed: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    before = _indexed(current)
    after = _indexed(proposed)
    operations: list[dict[str, Any]] = []
    for item_id in sorted(before.keys() - after.keys()):
        operations.append(
            {"operation": f"deprecate_{kind}", "target_id": item_id, "before": before[item_id]}
        )
    for item_id in sorted(after.keys() - before.keys()):
        operations.append(
            {"operation": f"add_{kind}", "target_id": item_id, "after": after[item_id]}
        )
    for item_id in sorted(before.keys() & after.keys()):
        if before[item_id] != after[item_id]:
            operations.append(
                {
                    "operation": f"update_{kind}",
                    "target_id": item_id,
                    "before": before[item_id],
                    "after": after[item_id],
                }
            )
    return operations


def _structural_diff(
    current: dict[str, Any],
    proposed: dict[str, Any],
    *,
    current_completion: dict[str, Any],
    proposed_completion: dict[str, Any],
) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    if current["entry_state_id"] != proposed["entry_state_id"]:
        operations.append(
            {
                "operation": "update_entry",
                "before": current["entry_state_id"],
                "after": proposed["entry_state_id"],
            }
        )
    if current_completion != proposed_completion:
        operations.append(
            {
                "operation": "update_completion",
                "before": current_completion,
                "after": proposed_completion,
            }
        )
    operations.extend(_diff_collection("state", current["states"], proposed["states"]))
    operations.extend(
        _diff_collection("transition", current["transitions"], proposed["transitions"])
    )
    operations.extend(
        _diff_collection("terminal", current["terminals"], proposed["terminals"])
    )
    return operations


def _guidance_analysis(
    task_path: Path,
    *,
    old_catalog: GuidanceScopeCatalog,
    new_catalog: GuidanceScopeCatalog,
    raw_mappings: object,
) -> tuple[list[dict[str, str]], list[str]]:
    mappings_value = raw_mappings if isinstance(raw_mappings, list) else []
    mappings: list[dict[str, str]] = []
    mapping_by_source: dict[tuple[str, str], tuple[str, str]] = {}
    issues: list[str] = []
    for index, raw_mapping in enumerate(mappings_value):
        data = _mapping(raw_mapping, f"guidance_scope_mappings[{index}]")
        if "from_type" in data:
            source_type = _string(data.get("from_type"), "from_type")
            source_id = _string(data.get("from_id"), "from_id")
            target_type = _string(data.get("to_type"), "to_type")
            target_id = _string(data.get("to_id"), "to_id")
        else:
            # V0.14.0 proposals used state-only mappings. They remain confirmable.
            source_type = "state"
            source_id = _string(data.get("from_state_id"), "from_state_id")
            target_type = "state"
            target_id = _string(data.get("to_state_id"), "to_state_id")
        reason = _string(data.get("reason"), "guidance mapping reason")
        source = (source_type, source_id)
        target = (target_type, target_id)
        if not old_catalog.contains(*source):
            issues.append(
                f"Guidance mapping source {source_type}:{source_id!r} does not exist"
            )
        if not new_catalog.contains(*target):
            issues.append(
                f"Guidance mapping target {target_type}:{target_id!r} does not exist"
            )
        if source in mapping_by_source and mapping_by_source[source] != target:
            issues.append(f"Guidance mapping for {source_type}:{source_id!r} is ambiguous")
        mapping_by_source[source] = target
        mappings.append(
            {
                "from_type": source_type,
                "from_id": source_id,
                "to_type": target_type,
                "to_id": target_id,
                "reason": reason,
            }
        )

    guidance_path = task_path.with_name("guidance.yaml")
    if not guidance_path.is_file():
        return mappings, issues
    guidance = _mapping(
        yaml.safe_load(guidance_path.read_text(encoding="utf-8")),
        "guidance",
    )
    referenced: set[tuple[str, str]] = set()
    for rule in guidance.get("rules", []):
        if not isinstance(rule, dict):
            continue
        scope = guidance_scope_payload(rule)
        if scope["type"] != "global":
            referenced.add((scope["type"], scope["id"]))
    for removed in sorted(referenced):
        if new_catalog.contains(*removed):
            continue
        if removed not in mapping_by_source:
            scope_type, scope_id = removed
            issues.append(
                "Confirmed guidance still references removed "
                f"{scope_type} {scope_id!r}; add a scope mapping"
            )
    return mappings, issues


def compile_task_model_revision(
    project_root: Path,
    candidate_path: Path,
    task_path: Path,
    *,
    experience: SemanticExperience,
    reference_frame: Path,
    feedback: str,
    model: str = DEFAULT_TASK_MODEL_REVISION_MODEL,
    reasoning_effort: str = DEFAULT_TASK_MODEL_REVISION_REASONING_EFFORT,
    codex_bin: str = "codex",
    timeout_seconds: float = 300,
    binary_resolver=resolve_codex_binary,
    session_factory=CodexAppServerSession,
) -> TaskModelProposalResult:
    normalized_feedback = _string(feedback, "human structural feedback", limit=2_000)
    project_root = project_root.resolve()
    candidate_path = candidate_path.resolve()
    task_path = task_path.resolve()
    candidate = _mapping(
        yaml.safe_load(candidate_path.read_text(encoding="utf-8")),
        "candidate",
    )
    declared_task = _resolve_project_file(
        project_root,
        candidate.get("source_task"),
        "candidate.source_task",
    )
    if declared_task != task_path:
        raise ValueError("Candidate experience does not belong to the selected task pack")
    task_data = _mapping(yaml.safe_load(task_path.read_text(encoding="utf-8")), "task")
    semantic_pointer = _mapping(task_data.get("semantic_experience"), "semantic_experience")
    base_revision = int(semantic_pointer.get("revision") or 0)
    events, run_frames = _execution_evidence(project_root, candidate)
    stage_ids = [stage.stage_id for stage in experience.stages]
    evidence_frames = sorted(
        {
            stage.state_before.evidence_frame
            for stage in experience.stages
        }
        | {stage.state_after.evidence_frame for stage in experience.stages}
    )
    prompt = (
        "You are the Task Model Revision Agent for Trace2Task V0.14. Improve the derived task "
        "model from authoritative human structural feedback and one reviewed Agent run. Raw Trace "
        "episodes are immutable evidence and are not being rewritten. Return a complete proposed "
        "directed state graph: reusable visual states, legal transitions that may branch, loop, or "
        "move backward, and separate success/failure terminal nodes. Do not preserve a wrong linear "
        "order merely because the human demonstration was linear. Every state and transition must "
        "cite one or more existing Trace episode IDs. Never add fixed coordinates or motor replay. "
        "Keep unchanged state and transition IDs stable. Guidance may be attached to a state, a "
        "specific transition, or a terminal outcome. If any referenced graph location is removed "
        "or renamed, provide exactly one guidance_scope_mappings entry to a valid proposed graph "
        "location; otherwise omit mappings. Human feedback is authoritative, current pixels and run "
        "evidence are observations, and the existing Compiler output is replaceable derived context. "
        "Image 1 is the reviewed human success reference; later images sample this Agent run. Return "
        "only the supplied JSON schema.\n\n"
        f"Human structural feedback: {normalized_feedback}\n"
        f"Current task model revision: {base_revision}\n"
        "Current semantic experience: "
        f"{json.dumps(experience.prompt_payload(), ensure_ascii=False, separators=(',', ':'))}\n"
        "Observed Agent actions and reasons: "
        f"{json.dumps(events, ensure_ascii=False, separators=(',', ':'))}"
    )
    session = session_factory(
        binary_resolver(codex_bin),
        model=model,
        reasoning_effort=reasoning_effort,
        cwd=project_root,
        timeout_seconds=timeout_seconds,
    )
    try:
        output = session.run_turn(
            prompt=prompt,
            image_path=reference_frame,
            additional_image_paths=run_frames,
            output_schema=_graph_schema(stage_ids, evidence_frames),
        )
    finally:
        session.close()
    try:
        payload = _mapping(json.loads(output.strip()), "Task Model Revision Agent output")
    except json.JSONDecodeError as error:
        raise RuntimeError("Task Model Revision Agent returned invalid JSON") from error

    canonical_instruction = _string(
        payload.get("canonical_instruction"), "canonical_instruction"
    )
    goal = _string(payload.get("goal"), "goal")
    summary = _string(payload.get("summary"), "summary")
    completion = _mapping(payload.get("completion"), "completion")
    completion_mode = _string(completion.get("mode"), "completion.mode")
    if completion_mode not in COMPLETION_MODES:
        raise ValueError("completion.mode must be state or cycle")
    normalized_completion = {
        "mode": completion_mode,
        "success_condition": _string(
            completion.get("success_condition"), "completion.success_condition"
        ),
        "reason": _string(completion.get("reason"), "completion.reason"),
    }
    graph = _mapping(payload.get("state_graph"), "state_graph")
    allowed_frames = set(evidence_frames)
    validate_state_graph(
        graph,
        stages=experience.stages,
        completion_success_condition=normalized_completion["success_condition"],
        allowed_frames=allowed_frames,
    )
    current_graph = experience.state_graph_payload()
    operations = _structural_diff(
        current_graph,
        graph,
        current_completion={
            "mode": experience.completion_mode,
            "success_condition": experience.completion_success_condition,
            "reason": experience.completion_reason,
        },
        proposed_completion=normalized_completion,
    )
    old_catalog = guidance_scope_catalog(
        stage_ids=set(experience.state_ids),
        transition_ids={transition.transition_id for transition in experience.transitions},
        terminal_ids=set(experience.terminal_ids),
    )
    new_catalog = guidance_scope_catalog(
        stage_ids={str(state["id"]) for state in graph["states"]},
        transition_ids={str(transition["id"]) for transition in graph["transitions"]},
        terminal_ids={str(terminal["id"]) for terminal in graph["terminals"]},
    )
    mappings, blocking_issues = _guidance_analysis(
        task_path,
        old_catalog=old_catalog,
        new_catalog=new_catalog,
        raw_mappings=(
            payload.get("guidance_scope_mappings")
            if payload.get("guidance_scope_mappings") is not None
            else payload.get("guidance_mappings")
        ),
    )
    proposed_revision = base_revision + 1
    created_at = datetime.now(UTC).isoformat()
    proposal = {
        "schema_version": "0.1",
        "task_id": task_data.get("id"),
        "status": "draft",
        "base_revision": base_revision,
        "proposed_revision": proposed_revision,
        "canonical_instruction": canonical_instruction,
        "goal": goal,
        "summary": summary,
        "completion": normalized_completion,
        "state_graph": graph,
        "operations": operations,
        "guidance_scope_mappings": mappings,
        "blocking_issues": blocking_issues,
        "source": {
            "type": "human_structural_feedback",
            "candidate": candidate_path.parent.relative_to(project_root).as_posix(),
            "execution_trace": candidate.get("execution_trace"),
            "feedback": normalized_feedback,
        },
        "revision_agent": {
            "version": __version__,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "created_at": created_at,
        },
    }
    proposal_path = candidate_path.with_name("task-model-revision-proposal.yaml")
    _atomic_yaml(proposal_path, proposal)
    candidate["task_model_revision"] = {
        "status": "draft",
        "path": proposal_path.name,
        "base_revision": base_revision,
        "proposed_revision": proposed_revision,
        "summary": summary,
        "operation_count": len(operations),
        "blocking_issue_count": len(blocking_issues),
        "operations": [
            {"operation": operation["operation"], "target_id": operation.get("target_id")}
            for operation in operations
        ],
        "blocking_issues": blocking_issues,
        "model": model,
        "reasoning_effort": reasoning_effort,
    }
    _atomic_yaml(candidate_path, candidate)
    return TaskModelProposalResult(
        proposal_path=str(proposal_path),
        task_path=str(task_path),
        candidate_path=str(candidate_path),
        base_revision=base_revision,
        proposed_revision=proposed_revision,
        operation_count=len(operations),
        blocking_issue_count=len(blocking_issues),
        model=model,
        reasoning_effort=reasoning_effort,
    )


def _migrate_guidance(
    task_path: Path,
    *,
    mappings: list[dict[str, str]],
    new_catalog: GuidanceScopeCatalog,
) -> int | None:
    guidance_path = task_path.with_name("guidance.yaml")
    if not guidance_path.is_file():
        return None
    guidance = _mapping(
        yaml.safe_load(guidance_path.read_text(encoding="utf-8")),
        "guidance",
    )
    mapping_by_source = {
        (mapping["from_type"], mapping["from_id"]): (
            mapping["to_type"],
            mapping["to_id"],
        )
        for mapping in mappings
    }
    changed = False
    for rule in guidance.get("rules", []):
        if not isinstance(rule, dict):
            continue
        scope = guidance_scope_payload(rule)
        current = (scope["type"], scope["id"])
        if current in mapping_by_source:
            current = mapping_by_source[current]
            changed = True
        if not new_catalog.contains(*current):
            raise ValueError(
                "Guidance rule still references missing graph scope "
                f"{current[0]}:{current[1]!r}"
            )
        canonical_scope = {"type": current[0], "id": current[1]}
        if rule.get("scope") != canonical_scope or "stage_id" in rule:
            rule["scope"] = canonical_scope
            rule.pop("stage_id", None)
            changed = True
    if not changed:
        return int(guidance.get("revision") or 0)
    current_revision = int(guidance.get("revision") or 0)
    revision = current_revision + 1
    guidance["revision"] = revision
    guidance["parent_revision"] = current_revision
    guidance["source"] = {
        "type": "task_model_scope_migration",
        "mappings": mappings,
        "migrated_at": datetime.now(UTC).isoformat(),
    }
    revision_path = task_path.parent / "guidance-revisions" / f"revision-{revision:04d}.yaml"
    _atomic_yaml(revision_path, guidance)
    _atomic_yaml(guidance_path, guidance)
    return revision


def activate_task_model_revision(
    project_root: Path,
    candidate_path: Path,
    task_path: Path,
) -> dict[str, Any]:
    project_root = project_root.resolve()
    candidate_path = candidate_path.resolve()
    task_path = task_path.resolve()
    candidate = _mapping(
        yaml.safe_load(candidate_path.read_text(encoding="utf-8")),
        "candidate",
    )
    proposal_path = candidate_path.with_name("task-model-revision-proposal.yaml")
    proposal = _mapping(
        yaml.safe_load(proposal_path.read_text(encoding="utf-8")),
        "task model proposal",
    )
    if proposal.get("status") != "draft":
        raise ValueError("Only a draft task model proposal can be confirmed")
    blocking = proposal.get("blocking_issues")
    if isinstance(blocking, list) and blocking:
        raise ValueError("Task model proposal has unresolved guidance mapping conflicts")
    task_data = _mapping(yaml.safe_load(task_path.read_text(encoding="utf-8")), "task")
    if proposal.get("task_id") != task_data.get("id"):
        raise ValueError("Task model proposal task_id does not match its task pack")
    semantic_pointer = _mapping(task_data.get("semantic_experience"), "semantic_experience")
    current_revision = int(semantic_pointer.get("revision") or 0)
    if proposal.get("base_revision") != current_revision:
        raise ValueError("This task model proposal is stale; generate it again")
    experience_path = task_path.with_name(str(semantic_pointer.get("path") or "experience.yaml"))
    active_document = _mapping(
        yaml.safe_load(experience_path.read_text(encoding="utf-8")),
        "experience",
    )
    experience = load_semantic_experience(
        experience_path,
        task_id=str(task_data["id"]),
        action_count=_demonstration_action_count(task_path),
    )
    graph = _mapping(proposal.get("state_graph"), "proposal.state_graph")
    allowed_frames = {
        path.resolve().relative_to(task_path.parent.resolve()).as_posix()
        for path in (task_path.parent / "reference" / "frames").glob("*.png")
        if path.is_file()
    }
    completion = _mapping(proposal.get("completion"), "proposal.completion")
    completion_mode = _string(completion.get("mode"), "proposal.completion.mode")
    if completion_mode not in COMPLETION_MODES:
        raise ValueError("proposal.completion.mode must be state or cycle")
    normalized_completion = {
        "mode": completion_mode,
        "success_condition": _string(
            completion.get("success_condition"),
            "proposal.completion.success_condition",
        ),
        "reason": _string(completion.get("reason"), "proposal.completion.reason"),
    }
    validate_state_graph(
        graph,
        stages=experience.stages,
        completion_success_condition=normalized_completion["success_condition"],
        allowed_frames=allowed_frames,
    )
    mappings = (
        proposal.get("guidance_scope_mappings")
        if proposal.get("guidance_scope_mappings") is not None
        else proposal.get("guidance_mappings")
    )
    old_catalog = guidance_scope_catalog(
        stage_ids=set(experience.state_ids),
        transition_ids={transition.transition_id for transition in experience.transitions},
        terminal_ids=set(experience.terminal_ids),
    )
    new_catalog = guidance_scope_catalog(
        stage_ids={str(state["id"]) for state in graph["states"]},
        transition_ids={str(transition["id"]) for transition in graph["transitions"]},
        terminal_ids={str(terminal["id"]) for terminal in graph["terminals"]},
    )
    normalized_mappings, current_blocking = _guidance_analysis(
        task_path,
        old_catalog=old_catalog,
        new_catalog=new_catalog,
        raw_mappings=mappings,
    )
    if current_blocking:
        raise ValueError(
            "Task model proposal no longer has a safe Guidance migration: "
            + "; ".join(current_blocking)
        )
    proposed_operations = _structural_diff(
        experience.state_graph_payload(),
        graph,
        current_completion={
            "mode": experience.completion_mode,
            "success_condition": experience.completion_success_condition,
            "reason": experience.completion_reason,
        },
        proposed_completion=normalized_completion,
    )
    if proposal.get("operations") != proposed_operations:
        raise ValueError("Task model proposal diff does not match the current active model")
    revision = current_revision + 1
    history_dir = task_path.parent / "experience-revisions"
    baseline_path = history_dir / "revision-0000.yaml"
    if current_revision == 0 and not baseline_path.exists():
        _atomic_yaml(baseline_path, deepcopy(active_document))
    updated = deepcopy(active_document)
    updated["schema_version"] = "0.4"
    updated["canonical_instruction"] = _string(
        proposal.get("canonical_instruction"), "proposal.canonical_instruction"
    )
    updated["goal"] = _string(proposal.get("goal"), "proposal.goal")
    updated["summary"] = _string(proposal.get("summary"), "proposal.summary")
    updated["completion"] = normalized_completion
    updated["state_graph"] = graph
    updated["task_model_revision"] = {
        "revision": revision,
        "parent_revision": current_revision,
        "source_candidate": candidate_path.parent.relative_to(project_root).as_posix(),
        "feedback": _mapping(proposal.get("source"), "proposal.source").get("feedback"),
        "confirmed_at": datetime.now(UTC).isoformat(),
        "operations": proposal.get("operations", []),
    }
    review = _mapping(updated.get("review"), "experience.review")
    review["status"] = "confirmed"
    review["requires_confirmation"] = False
    updated["review"] = review
    revision_path = history_dir / f"revision-{revision:04d}.yaml"
    _atomic_yaml(revision_path, updated)
    _atomic_yaml(experience_path, updated)

    guidance_revision = _migrate_guidance(
        task_path,
        mappings=normalized_mappings,
        new_catalog=new_catalog,
    )
    task_data["instruction"] = updated["canonical_instruction"]
    verifier = _mapping(task_data.get("verifier"), "task.verifier")
    verifier["expected"] = normalized_completion["success_condition"]
    verifier["completion"] = {
        "mode": normalized_completion["mode"],
        "require_departure_from_reference": normalized_completion["mode"] == "cycle",
        "reason": normalized_completion["reason"],
    }
    task_data["verifier"] = verifier
    semantic_pointer.update(
        {
            "revision": revision,
            "state_count": len(graph["states"]),
            "transition_count": len(graph["transitions"]),
            "terminal_count": len(graph["terminals"]),
        }
    )
    task_data["semantic_experience"] = semantic_pointer
    if guidance_revision is not None and isinstance(task_data.get("human_guidance"), dict):
        task_data["human_guidance"]["revision"] = guidance_revision
    _atomic_yaml(task_path, task_data)
    proposal["status"] = "confirmed"
    proposal["confirmed_revision"] = revision
    proposal["confirmed_at"] = datetime.now(UTC).isoformat()
    _atomic_yaml(proposal_path, proposal)
    task_model = _mapping(candidate.get("task_model_revision"), "task_model_revision")
    task_model.update(
        {
            "status": "confirmed",
            "confirmed_revision": revision,
            "confirmed_at": proposal["confirmed_at"],
        }
    )
    candidate["task_model_revision"] = task_model
    _atomic_yaml(candidate_path, candidate)
    load_semantic_experience(
        experience_path,
        task_id=str(task_data["id"]),
        action_count=_demonstration_action_count(task_path),
    )
    return {
        "task_path": task_path.relative_to(project_root).as_posix(),
        "experience_path": experience_path.relative_to(project_root).as_posix(),
        "revision_path": revision_path.relative_to(project_root).as_posix(),
        "revision": revision,
        "state_count": len(graph["states"]),
        "transition_count": len(graph["transitions"]),
        "terminal_count": len(graph["terminals"]),
        "guidance_revision": guidance_revision,
        "status": "confirmed",
    }
