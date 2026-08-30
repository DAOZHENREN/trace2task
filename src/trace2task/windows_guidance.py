from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from trace2task import __version__
from trace2task.codex_agent import resolve_codex_binary
from trace2task.codex_app_server import CodexAppServerSession
from trace2task.windows_experience import SemanticExperience

DEFAULT_REVISION_MODEL = "gpt-5.6-sol"
DEFAULT_REVISION_REASONING_EFFORT = "high"
MAX_GUIDANCE_RULES = 12
MAX_GUIDANCE_OPERATIONS = 12
GUIDANCE_OPERATIONS = {"keep", "add", "update", "deprecate", "conflict"}
GUIDANCE_SCOPE_TYPES = {"global", "state", "transition", "terminal"}

BinaryResolver = Callable[[str], str]
SessionFactory = Callable[..., CodexAppServerSession]


@dataclass(frozen=True)
class GuidanceRule:
    rule_id: str
    scope_type: str
    scope_id: str
    when: str
    prefer: str
    avoid: tuple[str, ...]
    replan_when: tuple[str, ...]
    expected_effect: str
    priority: str

    @property
    def stage_id(self) -> str:
        """Legacy alias retained for callers that still display a state ID."""
        return "global" if self.scope_type == "global" else self.scope_id

    def prompt_payload(self) -> dict[str, Any]:
        return {
            "id": self.rule_id,
            "scope": {"type": self.scope_type, "id": self.scope_id},
            "when": self.when,
            "prefer": self.prefer,
            "avoid": list(self.avoid),
            "replan_when": list(self.replan_when),
            "expected_effect": self.expected_effect,
            "priority": self.priority,
        }


@dataclass(frozen=True)
class GuidanceScopeCatalog:
    state_ids: frozenset[str]
    transition_ids: frozenset[str] = frozenset()
    terminal_ids: frozenset[str] = frozenset()

    def contains(self, scope_type: str, scope_id: str) -> bool:
        if scope_type == "global":
            return scope_id == "global"
        return scope_id in {
            "state": self.state_ids,
            "transition": self.transition_ids,
            "terminal": self.terminal_ids,
        }.get(scope_type, frozenset())

    def prompt_payload(self) -> dict[str, list[str]]:
        return {
            "state": sorted(self.state_ids),
            "transition": sorted(self.transition_ids),
            "terminal": sorted(self.terminal_ids),
        }


@dataclass(frozen=True)
class HumanGuidance:
    revision: int
    summary: str
    rules: tuple[GuidanceRule, ...]
    model: str
    reasoning_effort: str
    source_path: Path

    def prompt_payload(
        self,
        stage_id: str | None = None,
        *,
        transition_ids: Sequence[str] = (),
        terminal_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        active_transitions = set(transition_ids)
        active_terminals = set(terminal_ids)
        selected = [
            rule
            for rule in self.rules
            if stage_id is None
            or rule.scope_type == "global"
            or (rule.scope_type == "state" and rule.scope_id == stage_id)
            or (rule.scope_type == "transition" and rule.scope_id in active_transitions)
            or (rule.scope_type == "terminal" and rule.scope_id in active_terminals)
        ]
        return {
            "revision": self.revision,
            "summary": self.summary,
            "active_context": (
                None
                if stage_id is None
                else {
                    "state_id": stage_id,
                    "eligible_transition_ids": sorted(active_transitions),
                    "candidate_terminal_ids": sorted(active_terminals),
                }
            ),
            "rules": [rule.prompt_payload() for rule in selected],
        }


@dataclass(frozen=True)
class GuidanceProposalResult:
    proposal_path: str
    task_path: str
    candidate_path: str
    proposed_revision: int
    summary: str
    rule_count: int
    model: str
    reasoning_effort: str
    base_revision: int
    operation_count: int
    conflict_count: int


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be a mapping")
    return value


def _string(value: object, label: str, *, limit: int = 1_000) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    normalized = " ".join(value.split())
    if len(normalized) > limit:
        raise ValueError(f"{label} must not exceed {limit} characters")
    return normalized


def _string_list(
    value: object,
    label: str,
    *,
    limit: int = 6,
) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f"{label} must be a list with at most {limit} items")
    return tuple(_string(item, f"{label} item", limit=500) for item in value)


def guidance_scope_catalog(
    *,
    stage_ids: set[str],
    transition_ids: set[str] | None = None,
    terminal_ids: set[str] | None = None,
) -> GuidanceScopeCatalog:
    return GuidanceScopeCatalog(
        state_ids=frozenset(stage_ids),
        transition_ids=frozenset(transition_ids or set()),
        terminal_ids=frozenset(terminal_ids or set()),
    )


def _scope(value: dict[str, Any], label: str) -> tuple[str, str]:
    raw_scope = value.get("scope")
    if raw_scope is None:
        legacy_stage = _string(value.get("stage_id"), f"{label}.stage_id")
        return ("global", "global") if legacy_stage == "global" else ("state", legacy_stage)
    scope = _mapping(raw_scope, f"{label}.scope")
    scope_type = _string(scope.get("type"), f"{label}.scope.type", limit=20)
    scope_id = _string(scope.get("id"), f"{label}.scope.id", limit=100)
    if scope_type not in GUIDANCE_SCOPE_TYPES:
        raise ValueError(f"{label} has an invalid guidance scope type")
    return scope_type, scope_id


def guidance_scope_payload(value: dict[str, Any]) -> dict[str, str]:
    scope_type, scope_id = _scope(value, "guidance rule")
    return {"type": scope_type, "id": scope_id}


def _rules(
    value: object,
    *,
    catalog: GuidanceScopeCatalog,
) -> tuple[GuidanceRule, ...]:
    if not isinstance(value, list) or len(value) > MAX_GUIDANCE_RULES:
        raise ValueError(
            f"guidance rules must contain at most {MAX_GUIDANCE_RULES} items"
        )
    parsed: list[GuidanceRule] = []
    seen_ids: set[str] = set()
    for index, raw_rule in enumerate(value, start=1):
        rule = _mapping(raw_rule, f"guidance rule {index}")
        raw_rule_id = rule.get("id")
        rule_id = (
            _string(raw_rule_id, f"guidance rule {index}.id", limit=100)
            if raw_rule_id is not None
            else f"trick-{index:04d}"
        )
        if rule_id in seen_ids:
            raise ValueError(f"guidance rule id is duplicated: {rule_id}")
        seen_ids.add(rule_id)
        scope_type, scope_id = _scope(rule, f"guidance rule {index}")
        if not catalog.contains(scope_type, scope_id):
            raise ValueError(
                f"guidance rule {index} refers to an unknown {scope_type} scope"
            )
        priority = _string(rule.get("priority"), f"guidance rule {index}.priority")
        if priority not in {"low", "medium", "high"}:
            raise ValueError(f"guidance rule {index} has an invalid priority")
        parsed.append(
            GuidanceRule(
                rule_id=rule_id,
                scope_type=scope_type,
                scope_id=scope_id,
                when=_string(rule.get("when"), f"guidance rule {index}.when"),
                prefer=_string(rule.get("prefer"), f"guidance rule {index}.prefer"),
                avoid=_string_list(rule.get("avoid"), f"guidance rule {index}.avoid"),
                replan_when=_string_list(
                    rule.get("replan_when"),
                    f"guidance rule {index}.replan_when",
                ),
                expected_effect=_string(
                    rule.get("expected_effect"),
                    f"guidance rule {index}.expected_effect",
                ),
                priority=priority,
            )
        )
    return tuple(parsed)


def _rules_payload(rules: Sequence[GuidanceRule]) -> list[dict[str, Any]]:
    return [rule.prompt_payload() for rule in rules]


def _next_rule_id(existing_ids: set[str]) -> str:
    numbers = [
        int(match.group(1))
        for rule_id in existing_ids
        if (match := re.fullmatch(r"trick-(\d+)", rule_id)) is not None
    ]
    number = max(numbers, default=0) + 1
    while f"trick-{number:04d}" in existing_ids:
        number += 1
    return f"trick-{number:04d}"


def _operation_rule(
    raw: dict[str, Any],
    *,
    rule_id: str,
    label: str,
    catalog: GuidanceScopeCatalog,
) -> GuidanceRule:
    payload = {**raw, "id": rule_id}
    parsed = _rules([payload], catalog=catalog)
    if not parsed:
        raise ValueError(f"{label} must contain a rule")
    return parsed[0]


def _apply_guidance_operations(
    active_rules: Sequence[GuidanceRule],
    value: object,
    *,
    catalog: GuidanceScopeCatalog,
) -> tuple[tuple[GuidanceRule, ...], list[dict[str, Any]], int]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_GUIDANCE_OPERATIONS:
        raise ValueError(
            "guidance operations must contain between 1 and "
            f"{MAX_GUIDANCE_OPERATIONS} items"
        )
    merged = {rule.rule_id: rule for rule in active_rules}
    touched: set[str] = set()
    normalized: list[dict[str, Any]] = []
    conflict_count = 0

    for index, raw_operation in enumerate(value, start=1):
        raw = _mapping(raw_operation, f"guidance operation {index}")
        operation = _string(
            raw.get("operation"),
            f"guidance operation {index}.operation",
            limit=20,
        )
        if operation not in GUIDANCE_OPERATIONS:
            raise ValueError(f"guidance operation {index} has an invalid operation")
        target_rule_id = str(raw.get("target_rule_id") or "").strip()
        reason = _string(raw.get("reason"), f"guidance operation {index}.reason")

        if operation == "add":
            if target_rule_id:
                raise ValueError(f"guidance add operation {index} cannot target an existing rule")
            rule_id = _next_rule_id(set(merged))
            expected_rule_id = str(raw.get("result_rule_id") or "").strip()
            if expected_rule_id and expected_rule_id != rule_id:
                raise ValueError("Guidance proposal rule allocation no longer matches its base")
            result_rule = _operation_rule(
                raw,
                rule_id=rule_id,
                label=f"guidance add operation {index}",
                catalog=catalog,
            )
            merged[rule_id] = result_rule
        else:
            if not target_rule_id or target_rule_id not in merged:
                raise ValueError(
                    f"guidance operation {index} refers to an unknown target rule"
                )
            if target_rule_id in touched:
                raise ValueError(
                    f"guidance rule {target_rule_id} is changed more than once in one revision"
                )
            touched.add(target_rule_id)
            rule_id = target_rule_id
            result_rule = merged[target_rule_id]
            if operation in {"update", "conflict"}:
                result_rule = _operation_rule(
                    raw,
                    rule_id=target_rule_id,
                    label=f"guidance {operation} operation {index}",
                    catalog=catalog,
                )
            if operation == "update":
                merged[target_rule_id] = result_rule
            elif operation == "deprecate":
                del merged[target_rule_id]
            elif operation == "conflict":
                conflict_count += 1

        normalized.append(
            {
                "operation": operation,
                "target_rule_id": target_rule_id,
                "result_rule_id": rule_id,
                "scope": {
                    "type": result_rule.scope_type,
                    "id": result_rule.scope_id,
                },
                "when": result_rule.when,
                "prefer": result_rule.prefer,
                "avoid": list(result_rule.avoid),
                "replan_when": list(result_rule.replan_when),
                "expected_effect": result_rule.expected_effect,
                "priority": result_rule.priority,
                "reason": reason,
            }
        )

    if len(merged) > MAX_GUIDANCE_RULES:
        raise ValueError(
            f"Merged guidance exceeds the {MAX_GUIDANCE_RULES}-rule limit; "
            "update or deprecate an existing rule instead of adding another"
        )
    return tuple(merged.values()), normalized, conflict_count


def _atomic_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )
    temporary.replace(path)


def load_human_guidance(
    path: Path,
    *,
    task_id: str,
    stage_ids: set[str],
    transition_ids: set[str] | None = None,
    terminal_ids: set[str] | None = None,
) -> HumanGuidance:
    source_path = path.resolve()
    root = _mapping(yaml.safe_load(source_path.read_text(encoding="utf-8")), "guidance")
    if _string(root.get("task_id"), "guidance.task_id") != task_id:
        raise ValueError("Human guidance task_id does not match the task pack")
    revision = root.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision <= 0:
        raise ValueError("Human guidance revision must be a positive integer")
    if root.get("status") != "confirmed":
        raise ValueError("Only confirmed human guidance can be loaded at runtime")
    revision_agent = _mapping(root.get("revision_agent"), "guidance.revision_agent")
    return HumanGuidance(
        revision=revision,
        summary=_string(root.get("summary"), "guidance.summary"),
        rules=_rules(
            root.get("rules"),
            catalog=guidance_scope_catalog(
                stage_ids=stage_ids,
                transition_ids=transition_ids,
                terminal_ids=terminal_ids,
            ),
        ),
        model=_string(revision_agent.get("model"), "guidance.revision_agent.model"),
        reasoning_effort=_string(
            revision_agent.get("reasoning_effort"),
            "guidance.revision_agent.reasoning_effort",
        ),
        source_path=source_path,
    )


def _revision_output_schema(catalog: GuidanceScopeCatalog) -> dict[str, Any]:
    operation_properties = {
        "operation": {
            "type": "string",
            "enum": ["keep", "add", "update", "deprecate", "conflict"],
        },
        "target_rule_id": {"type": "string", "maxLength": 100},
        "scope": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": sorted(GUIDANCE_SCOPE_TYPES),
                },
                "id": {"type": "string", "minLength": 1, "maxLength": 100},
            },
            "required": ["type", "id"],
            "additionalProperties": False,
        },
        "when": {"type": "string", "minLength": 1, "maxLength": 1000},
        "prefer": {"type": "string", "minLength": 1, "maxLength": 1000},
        "avoid": {
            "type": "array",
            "maxItems": 6,
            "items": {"type": "string", "minLength": 1, "maxLength": 500},
        },
        "replan_when": {
            "type": "array",
            "maxItems": 6,
            "items": {"type": "string", "minLength": 1, "maxLength": 500},
        },
        "expected_effect": {
            "type": "string",
            "minLength": 1,
            "maxLength": 1000,
        },
        "priority": {
            "type": "string",
            "enum": ["low", "medium", "high"],
        },
        "reason": {"type": "string", "minLength": 1, "maxLength": 1000},
    }
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "minLength": 1, "maxLength": 1000},
            "operations": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_GUIDANCE_OPERATIONS,
                "items": {
                    "type": "object",
                    "properties": operation_properties,
                    "required": list(operation_properties),
                    "additionalProperties": False,
                },
            },
        },
        "required": ["summary", "operations"],
        "additionalProperties": False,
    }


def _resolve_project_file(project_root: Path, raw_path: object, label: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError(f"{label} must be a project-relative path")
    root = project_root.resolve()
    path = (root / raw_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"{label} points outside the project or does not exist")
    return path


def _execution_evidence(
    project_root: Path,
    candidate: dict[str, Any],
) -> tuple[list[dict[str, Any]], tuple[Path, ...]]:
    trace_path = _resolve_project_file(
        project_root,
        candidate.get("execution_trace"),
        "candidate.execution_trace",
    )
    events: list[dict[str, Any]] = []
    frames: list[Path] = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        frame = event.get("frame")
        if isinstance(frame, str):
            frame_path = (trace_path.parent / frame).resolve()
            if frame_path.is_relative_to(trace_path.parent.resolve()) and frame_path.is_file():
                frames.append(frame_path)
        if event.get("type") == "windows_action":
            details = event.get("details") if isinstance(event.get("details"), dict) else {}
            action = details.get("parameterized_action")
            events.append(
                {
                    "seq": event.get("seq"),
                    "action": action,
                    "reason": details.get("model_reason"),
                    "confidence": details.get("model_confidence"),
                }
            )
    unique_frames = list(dict.fromkeys(frames))
    if len(unique_frames) > 4:
        indexes = {
            round(index * (len(unique_frames) - 1) / 3)
            for index in range(4)
        }
        unique_frames = [unique_frames[index] for index in sorted(indexes)]
    return events[-80:], tuple(unique_frames)


def compile_guidance_revision(
    project_root: Path,
    candidate_path: Path,
    task_path: Path,
    *,
    experience: SemanticExperience,
    reference_frame: Path,
    feedback: str,
    model: str = DEFAULT_REVISION_MODEL,
    reasoning_effort: str = DEFAULT_REVISION_REASONING_EFFORT,
    codex_bin: str = "codex",
    timeout_seconds: float = 300,
    binary_resolver: BinaryResolver = resolve_codex_binary,
    session_factory: SessionFactory = CodexAppServerSession,
) -> GuidanceProposalResult:
    normalized_feedback = _string(feedback, "human feedback", limit=2_000)
    project_root = project_root.resolve()
    candidate_path = candidate_path.resolve()
    task_path = task_path.resolve()
    candidate = _mapping(
        yaml.safe_load(candidate_path.read_text(encoding="utf-8")),
        "candidate",
    )
    task_id = _string(candidate.get("task_id"), "candidate.task_id")
    declared_task = _resolve_project_file(
        project_root,
        candidate.get("source_task"),
        "candidate.source_task",
    )
    if declared_task != task_path:
        raise ValueError("Candidate experience does not belong to the selected task pack")
    events, run_frames = _execution_evidence(project_root, candidate)
    catalog = guidance_scope_catalog(
        stage_ids=set(experience.state_ids),
        transition_ids={transition.transition_id for transition in experience.transitions},
        terminal_ids=set(experience.terminal_ids),
    )
    active_guidance_path = task_path.with_name("guidance.yaml")
    base_revision = 0
    active_rules: tuple[GuidanceRule, ...] = ()
    active_summary = "No confirmed human guidance exists yet."
    if active_guidance_path.is_file():
        active = load_human_guidance(
            active_guidance_path,
            task_id=task_id,
            stage_ids=set(catalog.state_ids),
            transition_ids=set(catalog.transition_ids),
            terminal_ids=set(catalog.terminal_ids),
        )
        base_revision = active.revision
        active_rules = active.rules
        active_summary = active.summary
    prompt = (
        "You are the Revision Agent for Trace2Task V0.14.1. Merge authoritative human feedback "
        "from one reviewed Agent run into the current confirmed guidance using incremental "
        "operations. Existing rules omitted from your operations are preserved automatically. "
        "Use update when feedback improves an existing rule, add only for genuinely new advice, "
        "deprecate only when feedback explicitly invalidates a rule, keep to record an explicit "
        "confirmation, and conflict when the evidence cannot safely choose between incompatible "
        "rules. For add, target_rule_id must be empty. For every other operation it must name an "
        "existing rule ID. Copy the complete resulting rule fields into every operation. The "
        "summary must describe the combined guidance after the operations, not only this round. "
        "The runtime executor already has a task-independent system policy to return multiple "
        "ordered actions in one model decision whenever the visible continuation is predictable, "
        "to keep adaptive waits inside that program, and to stop before unknown visual outcomes. "
        "Do not add or update a task rule, or mention in the combined summary, advice whose only "
        "content is batching more actions, reducing model calls, using a longer plan horizon, or "
        "avoiding one-click planning. If human feedback mixes that generic executor advice with "
        "task-specific knowledge, omit the generic portion and preserve only the task fact, such "
        "as how many controls this application requires, which visible branch follows an action, "
        "or which task-specific condition makes a target valid. "
        "Do not rewrite the preserved human Trace, invent fixed coordinates, or turn one dynamic "
        "choice into a universal rule. Scope every trick to exactly one graph location: global for "
        "task-wide invariants, state for observations/actions inside one state, transition for a "
        "specific legal edge, or terminal for success/failure recognition. Use an ID from the "
        "supplied guidance scope catalog, and use {type: global, id: global} for global rules. Human "
        "feedback is authoritative; the compiled semantic experience is derived context. Image 1 "
        "is the reviewed human success reference. Later images sample the Agent run. Return only "
        "the supplied JSON schema.\n\n"
        f"Task: {task_id}\n"
        f"Run instruction: {candidate.get('runtime_instruction')}\n"
        "Run outcome: "
        f"{json.dumps(candidate.get('outcome') or {}, ensure_ascii=False, separators=(',', ':'))}\n"
        f"Human feedback: {normalized_feedback}\n"
        f"Current confirmed guidance revision: {base_revision}\n"
        f"Current confirmed guidance summary: {active_summary}\n"
        "Current confirmed guidance rules: "
        f"{json.dumps(_rules_payload(active_rules), ensure_ascii=False, separators=(',', ':'))}\n"
        "Current semantic experience: "
        f"{json.dumps(experience.prompt_payload(), ensure_ascii=False, separators=(',', ':'))}\n"
        "Guidance scope catalog: "
        f"{json.dumps(catalog.prompt_payload(), ensure_ascii=False, separators=(',', ':'))}\n"
        "Observed Agent actions and reasons: "
        f"{json.dumps(events, ensure_ascii=False, separators=(',', ':'))}"
    )
    executable = binary_resolver(codex_bin)
    session = session_factory(
        executable,
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
            output_schema=_revision_output_schema(catalog),
        )
    finally:
        session.close()
    try:
        payload = json.loads(output.strip())
    except json.JSONDecodeError as error:
        raise RuntimeError("Revision Agent returned invalid JSON") from error
    root = _mapping(payload, "Revision Agent output")
    summary = _string(root.get("summary"), "Revision Agent summary")
    rules, operations, conflict_count = _apply_guidance_operations(
        active_rules,
        root.get("operations"),
        catalog=catalog,
    )
    operation_counts = {
        operation: sum(item["operation"] == operation for item in operations)
        for operation in sorted(GUIDANCE_OPERATIONS)
    }
    proposed_revision = base_revision + 1
    created_at = datetime.now(UTC).isoformat()
    proposal = {
        "schema_version": "0.3",
        "task_id": task_id,
        "status": "draft",
        "base_revision": base_revision,
        "proposed_revision": proposed_revision,
        "summary": summary,
        "model_summary": summary,
        "rules": _rules_payload(rules),
        "operations": operations,
        "operation_counts": operation_counts,
        "conflict_count": conflict_count,
        "source": {
            "type": "human_feedback",
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
    proposal_path = candidate_path.with_name("revision-proposal.yaml")
    _atomic_yaml(proposal_path, proposal)
    candidate["revision"] = {
        "status": "draft",
        "path": proposal_path.name,
        "proposed_revision": proposed_revision,
        "summary": summary,
        "summary_edited": False,
        "base_revision": base_revision,
        "rule_count": len(rules),
        "operation_count": len(operations),
        "operation_counts": operation_counts,
        "conflict_count": conflict_count,
        "changes": [
            {
                "operation": item["operation"],
                "target_rule_id": item["target_rule_id"],
                "result_rule_id": item["result_rule_id"],
                "scope": item["scope"],
                "reason": item["reason"],
            }
            for item in operations
        ],
        "model": model,
        "reasoning_effort": reasoning_effort,
    }
    _atomic_yaml(candidate_path, candidate)
    return GuidanceProposalResult(
        proposal_path=str(proposal_path),
        task_path=str(task_path),
        candidate_path=str(candidate_path),
        proposed_revision=proposed_revision,
        summary=summary,
        rule_count=len(rules),
        model=model,
        reasoning_effort=reasoning_effort,
        base_revision=base_revision,
        operation_count=len(operations),
        conflict_count=conflict_count,
    )


def update_guidance_proposal_summary(
    candidate_path: Path,
    summary: str,
) -> dict[str, Any]:
    candidate_path = candidate_path.resolve()
    candidate = _mapping(
        yaml.safe_load(candidate_path.read_text(encoding="utf-8")),
        "candidate",
    )
    candidate_revision = _mapping(candidate.get("revision"), "candidate.revision")
    if candidate_revision.get("status") != "draft":
        raise ValueError("Only a draft guidance proposal can be edited")
    proposal_path = candidate_path.with_name("revision-proposal.yaml")
    proposal = _mapping(
        yaml.safe_load(proposal_path.read_text(encoding="utf-8")),
        "guidance proposal",
    )
    if proposal.get("status") != "draft":
        raise ValueError("Only a draft guidance proposal can be edited")

    normalized_summary = _string(summary, "guidance summary")
    model_summary = _string(
        proposal.get("model_summary") or proposal.get("summary"),
        "model guidance summary",
    )
    edited = normalized_summary != model_summary
    edited_at = datetime.now(UTC).isoformat()
    proposal["model_summary"] = model_summary
    proposal["summary"] = normalized_summary
    proposal_review = (
        dict(proposal["review"]) if isinstance(proposal.get("review"), dict) else {}
    )
    proposal_review.update(
        {
            "summary_edited": edited,
            "summary_edited_at": edited_at,
        }
    )
    proposal["review"] = proposal_review
    candidate_revision["summary"] = normalized_summary
    candidate_revision["summary_edited"] = edited
    candidate["revision"] = candidate_revision
    _atomic_yaml(proposal_path, proposal)
    _atomic_yaml(candidate_path, candidate)
    return {
        "candidate_path": str(candidate_path),
        "proposal_path": str(proposal_path),
        "summary": normalized_summary,
        "summary_edited": edited,
        "status": "draft",
    }


def activate_guidance_revision(
    project_root: Path,
    candidate_path: Path,
    *,
    task_id: str,
    stage_ids: set[str],
    transition_ids: set[str] | None = None,
    terminal_ids: set[str] | None = None,
) -> dict[str, Any]:
    project_root = project_root.resolve()
    candidate_path = candidate_path.resolve()
    candidate = _mapping(
        yaml.safe_load(candidate_path.read_text(encoding="utf-8")),
        "candidate",
    )
    proposal_path = candidate_path.with_name("revision-proposal.yaml")
    proposal = _mapping(
        yaml.safe_load(proposal_path.read_text(encoding="utf-8")),
        "guidance proposal",
    )
    if proposal.get("status") != "draft":
        raise ValueError("Only a draft guidance proposal can be confirmed")
    if _string(proposal.get("task_id"), "proposal.task_id") != task_id:
        raise ValueError("Guidance proposal task_id does not match its task pack")
    catalog = guidance_scope_catalog(
        stage_ids=stage_ids,
        transition_ids=transition_ids,
        terminal_ids=terminal_ids,
    )
    proposal_rules = _rules(proposal.get("rules"), catalog=catalog)
    task_path = _resolve_project_file(
        project_root,
        candidate.get("source_task"),
        "candidate.source_task",
    )
    active_path = task_path.with_name("guidance.yaml")
    current_revision = 0
    current_rules: tuple[GuidanceRule, ...] = ()
    if active_path.is_file():
        current_guidance = load_human_guidance(
            active_path,
            task_id=task_id,
            stage_ids=stage_ids,
            transition_ids=transition_ids,
            terminal_ids=terminal_ids,
        )
        current_revision = current_guidance.revision
        current_rules = current_guidance.rules
    if proposal.get("base_revision") != current_revision:
        raise ValueError("This guidance proposal is stale; generate it again from the latest revision")
    operations_value = proposal.get("operations")
    if operations_value is not None:
        rules, normalized_operations, conflict_count = _apply_guidance_operations(
            current_rules,
            operations_value,
            catalog=catalog,
        )
        if conflict_count:
            raise ValueError(
                "This guidance proposal contains unresolved conflicts; add clarifying feedback "
                "and generate a new revision before confirming it"
            )
        if _rules_payload(rules) != _rules_payload(proposal_rules):
            raise ValueError("Guidance proposal merged rules do not match its incremental operations")
        proposal["operations"] = normalized_operations
    else:
        if current_revision > 0:
            raise ValueError(
                "This is a legacy whole-snapshot guidance draft and would overwrite confirmed "
                "rules. Generate a new V0.10 incremental merge draft before confirming it"
            )
        # A legacy first revision cannot overwrite earlier guidance and remains safe to import.
        rules = proposal_rules
    revision = current_revision + 1
    confirmed_at = datetime.now(UTC).isoformat()
    proposal_review = (
        dict(proposal["review"]) if isinstance(proposal.get("review"), dict) else {}
    )
    active = {
        **proposal,
        "status": "confirmed",
        "revision": revision,
        "parent_revision": current_revision,
        "rules": _rules_payload(rules),
        "review": {**proposal_review, "confirmed_at": confirmed_at},
    }
    active.pop("base_revision", None)
    active.pop("proposed_revision", None)
    revision_path = task_path.parent / "guidance-revisions" / f"revision-{revision:04d}.yaml"
    _atomic_yaml(revision_path, active)
    _atomic_yaml(active_path, active)
    task_root = _mapping(yaml.safe_load(task_path.read_text(encoding="utf-8")), "task")
    task_root["human_guidance"] = {
        "path": active_path.name,
        "revision": revision,
        "rule_count": len(rules),
    }
    _atomic_yaml(task_path, task_root)
    proposal["status"] = "confirmed"
    proposal["confirmed_revision"] = revision
    proposal["confirmed_at"] = confirmed_at
    _atomic_yaml(proposal_path, proposal)
    candidate["status"] = "feedback_applied"
    candidate["revision"] = {
        **_mapping(candidate.get("revision"), "candidate.revision"),
        "status": "confirmed",
        "confirmed_revision": revision,
        "confirmed_at": confirmed_at,
    }
    _atomic_yaml(candidate_path, candidate)
    return {
        "task_path": task_path.relative_to(project_root).as_posix(),
        "guidance_path": active_path.relative_to(project_root).as_posix(),
        "revision": revision,
        "rule_count": len(rules),
        "status": "confirmed",
    }
