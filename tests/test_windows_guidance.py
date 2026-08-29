from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pygame
import pytest
import yaml

from trace2task.windows_guidance import (
    activate_guidance_revision,
    compile_guidance_revision,
    update_guidance_proposal_summary,
)
from trace2task.windows_task import load_windows_task


def _write_semantic_task(root: Path) -> Path:
    task_dir = root / "taskpacks" / "example"
    frames = task_dir / "reference" / "frames"
    frames.mkdir(parents=True)
    for name, color in (("before.png", (20, 30, 40)), ("after.png", (80, 90, 100))):
        surface = pygame.Surface((40, 30))
        surface.fill(color)
        pygame.image.save(surface, frames / name)
    pygame.image.save(pygame.Surface((40, 30)), task_dir / "reference" / "final.png")
    (task_dir / "demonstration.json").write_text(
        json.dumps(
            {
                "actions": [
                    {"action": {"skill": "focus_window", "args": {}}},
                    {"action": {"skill": "click", "args": {"x": 0.5, "y": 0.5}}},
                ]
            }
        ),
        encoding="utf-8",
    )
    experience = {
        "schema_version": "0.1",
        "task_id": "example-task",
        "source": {
            "type": "human_trace",
            "trace": "reference/trace.jsonl",
            "demonstration": "demonstration.json",
            "policy": "immutable_strong_evidence",
        },
        "compiler": {
            "type": "multimodal_agent",
            "version": "0.7.2",
            "model": "gpt-5.6-sol",
            "reasoning_effort": "high",
        },
        "goal": "Open the target efficiently.",
        "summary": "Use the visible target and verify the result.",
        "stages": [
            {
                "id": "open_target",
                "name": "Open target",
                "start_action_index": 0,
                "end_action_index": 1,
                "state_before": {
                    "description": "Target is visible.",
                    "evidence_frame": "reference/frames/before.png",
                    "visual_anchors": ["target"],
                },
                "action_intents": [
                    {
                        "start_action_index": 0,
                        "end_action_index": 1,
                        "description": "Open the target.",
                        "target": "target",
                        "provenance": "observed",
                        "confidence": 0.9,
                    }
                ],
                "preconditions": ["Target is visible"],
                "expected_effects": ["Target opens"],
                "state_after": {
                    "description": "Target is open.",
                    "evidence_frame": "reference/frames/after.png",
                    "visual_anchors": ["open content"],
                },
                "dynamic_decisions": [],
                "confidence": 0.9,
            }
        ],
        "review": {"status": "confirmed", "requires_confirmation": False},
    }
    (task_dir / "experience.yaml").write_text(
        yaml.safe_dump(experience, sort_keys=False),
        encoding="utf-8",
    )
    task = {
        "schema_version": "0.3",
        "id": "example-task",
        "instruction": "Open the target.",
        "environment": {
            "adapter": "trace2task.windows",
            "target": {"process_name": "target.exe", "title_contains": "Target"},
        },
        "actions": ["focus_window", "click"],
        "demonstration": {"path": "demonstration.json", "action_count": 2},
        "semantic_experience": {
            "path": "experience.yaml",
            "stage_count": 1,
            "source": "human_trace",
        },
        "verifier": {
            "type": "reviewed_reference_frame",
            "expected": "Target is open.",
            "reference_frame": "reference/final.png",
        },
        "limits": {"max_actions": 8},
        "review": {"status": "confirmed", "requires_confirmation": False},
    }
    task_path = task_dir / "task.yaml"
    task_path.write_text(yaml.safe_dump(task, sort_keys=False), encoding="utf-8")
    return task_path


def _write_candidate(
    root: Path,
    task_path: Path,
    *,
    candidate_id: str = "candidate-1",
) -> Path:
    run_dir = root / "runs" / f"run-{candidate_id}"
    frames = run_dir / "frames"
    frames.mkdir(parents=True)
    for index in range(3):
        surface = pygame.Surface((40, 30))
        surface.fill((30 + index * 20, 40, 50))
        pygame.image.save(surface, frames / f"{index:04d}.png")
    (run_dir / "trace.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "seq": index,
                    "type": "windows_action",
                    "frame": f"frames/{index:04d}.png",
                    "details": {
                        "parameterized_action": {
                            "skill": "click",
                            "args": {"x": 0.5, "y": 0.5},
                        },
                        "model_reason": "Clicked and replanned.",
                        "model_confidence": 0.7,
                    },
                }
            )
            + "\n"
            for index in range(3)
        ),
        encoding="utf-8",
    )
    candidate_dir = root / "runs" / "candidates" / candidate_id
    candidate_dir.mkdir(parents=True)
    candidate_path = candidate_dir / "candidate.yaml"
    candidate_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "0.1",
                "candidate_id": candidate_id,
                "status": "pending_review",
                "task_id": "example-task",
                "source_task": task_path.relative_to(root).as_posix(),
                "runtime_instruction": "Open it efficiently",
                "execution_trace": (run_dir / "trace.jsonl").relative_to(root).as_posix(),
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return candidate_path


class FakeRevisionSession:
    def __init__(self, executable: str, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.closed = False
        self.call: dict[str, Any] | None = None

    def run_turn(self, **kwargs: Any) -> str:
        self.call = kwargs
        return json.dumps(
            {
                "summary": "Avoid redundant replanning after a stable click.",
                "operations": [
                    {
                        "operation": "add",
                        "target_rule_id": "",
                        "stage_id": "open_target",
                        "when": "The target is stable and visibly clickable.",
                        "prefer": "Click once and wait for the expected content.",
                        "avoid": ["Requesting a new plan before the UI can change"],
                        "replan_when": ["The expected content does not appear"],
                        "expected_effect": "The target opens with one planning cycle.",
                        "priority": "high",
                        "reason": "The reviewed run replanned before the UI could respond.",
                    }
                ],
            }
        )

    def close(self) -> None:
        self.closed = True


class ConfiguredRevisionSession(FakeRevisionSession):
    def __init__(self, executable: str, response: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(executable, **kwargs)
        self.response = response

    def run_turn(self, **kwargs: Any) -> str:
        self.call = kwargs
        return json.dumps(self.response)


def _operation(
    operation: str,
    *,
    target_rule_id: str = "",
    prefer: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "operation": operation,
        "target_rule_id": target_rule_id,
        "stage_id": "open_target",
        "when": "The target is stable and visibly clickable.",
        "prefer": prefer,
        "avoid": ["Requesting a new plan before the UI can change"],
        "replan_when": ["The expected content does not appear"],
        "expected_effect": "The target opens with one planning cycle.",
        "priority": "high",
        "reason": reason,
    }


def test_human_feedback_creates_reviewable_revision_then_activates_it(tmp_path: Path) -> None:
    task_path = _write_semantic_task(tmp_path)
    candidate_path = _write_candidate(tmp_path, task_path)
    contract = load_windows_task(task_path)
    assert contract.semantic_experience is not None
    sessions: list[FakeRevisionSession] = []

    def factory(executable: str, **kwargs: Any) -> FakeRevisionSession:
        session = FakeRevisionSession(executable, **kwargs)
        sessions.append(session)
        return session

    proposal = compile_guidance_revision(
        tmp_path,
        candidate_path,
        task_path,
        experience=contract.semantic_experience,
        reference_frame=contract.reference_frame,
        feedback="点击后不要马上重新规划，先等待界面变化。",
        session_factory=factory,
        binary_resolver=lambda requested: requested,
    )

    assert proposal.proposed_revision == 1
    assert proposal.rule_count == 1
    assert sessions[0].closed is True
    assert sessions[0].call is not None
    assert len(sessions[0].call["additional_image_paths"]) == 3
    assert "点击后不要马上重新规划" in sessions[0].call["prompt"]
    assert "Current confirmed guidance revision: 0" in sessions[0].call["prompt"]
    assert not (task_path.parent / "guidance.yaml").exists()

    edited = update_guidance_proposal_summary(
        candidate_path,
        "点击一次后先等待目标内容出现，不要过早重新规划。",
    )
    draft = yaml.safe_load(
        (candidate_path.parent / "revision-proposal.yaml").read_text(encoding="utf-8")
    )

    assert edited["summary_edited"] is True
    assert draft["model_summary"] == "Avoid redundant replanning after a stable click."
    assert draft["summary"] == "点击一次后先等待目标内容出现，不要过早重新规划。"

    activated = activate_guidance_revision(
        tmp_path,
        candidate_path,
        task_id="example-task",
        stage_ids={"open_target"},
    )
    loaded = load_windows_task(task_path)

    assert activated["revision"] == 1
    assert loaded.human_guidance is not None
    assert loaded.human_guidance.summary == "点击一次后先等待目标内容出现，不要过早重新规划。"
    assert loaded.human_guidance.rules[0].stage_id == "open_target"
    assert loaded.human_guidance.rules[0].rule_id == "trick-0001"
    assert loaded.human_guidance.prompt_payload("open_target")["rules"][0]["priority"] == "high"
    assert (task_path.parent / "guidance-revisions" / "revision-0001.yaml").is_file()
    candidate = yaml.safe_load(candidate_path.read_text(encoding="utf-8"))
    assert candidate["status"] == "feedback_applied"
    active = yaml.safe_load(
        (task_path.parent / "guidance.yaml").read_text(encoding="utf-8")
    )
    assert active["model_summary"] == "Avoid redundant replanning after a stable click."
    assert active["review"]["summary_edited"] is True
    assert active["parent_revision"] == 0
    assert active["operation_counts"]["add"] == 1

    with pytest.raises(ValueError, match="draft"):
        update_guidance_proposal_summary(candidate_path, "不能修改已启用版本")


def test_guidance_revisions_merge_across_rounds_and_block_conflicts(tmp_path: Path) -> None:
    task_path = _write_semantic_task(tmp_path)
    contract = load_windows_task(task_path)
    assert contract.semantic_experience is not None
    candidate_number = 0

    def compile_response(response: dict[str, Any], feedback: str) -> Path:
        nonlocal candidate_number
        candidate_number += 1
        candidate_path = _write_candidate(
            tmp_path,
            task_path,
            candidate_id=f"candidate-{candidate_number}",
        )

        def factory(executable: str, **kwargs: Any) -> ConfiguredRevisionSession:
            return ConfiguredRevisionSession(executable, response, **kwargs)

        compile_guidance_revision(
            tmp_path,
            candidate_path,
            task_path,
            experience=contract.semantic_experience,
            reference_frame=contract.reference_frame,
            feedback=feedback,
            session_factory=factory,
            binary_resolver=lambda requested: requested,
        )
        return candidate_path

    first_candidate = compile_response(
        {
            "summary": "Click once, then wait for the UI.",
            "operations": [
                _operation(
                    "add",
                    prefer="Click once and wait for the expected content.",
                    reason="The first reviewed run replanned too early.",
                )
            ],
        },
        "点击一次后等待界面变化。",
    )
    activate_guidance_revision(
        tmp_path,
        first_candidate,
        task_id="example-task",
        stage_ids={"open_target"},
    )

    second_candidate = compile_response(
        {
            "summary": "Click once, wait, and verify the opened content.",
            "operations": [
                _operation(
                    "add",
                    prefer="Verify the opened content before continuing.",
                    reason="The second reviewed run continued without verification.",
                )
            ],
        },
        "继续之前确认目标内容已经打开。",
    )
    second_draft = yaml.safe_load(
        second_candidate.with_name("revision-proposal.yaml").read_text(encoding="utf-8")
    )
    assert second_draft["base_revision"] == 1
    assert [rule["id"] for rule in second_draft["rules"]] == [
        "trick-0001",
        "trick-0002",
    ]
    activate_guidance_revision(
        tmp_path,
        second_candidate,
        task_id="example-task",
        stage_ids={"open_target"},
    )

    third_candidate = compile_response(
        {
            "summary": "Click once, wait, then verify with the visible success marker.",
            "operations": [
                _operation(
                    "update",
                    target_rule_id="trick-0002",
                    prefer="Verify the visible success marker before continuing.",
                    reason="Human feedback identified the reliable verification anchor.",
                )
            ],
        },
        "用可见的成功标记确认已经打开。",
    )
    third_draft = yaml.safe_load(
        third_candidate.with_name("revision-proposal.yaml").read_text(encoding="utf-8")
    )
    assert third_draft["base_revision"] == 2
    assert third_draft["operation_counts"]["update"] == 1
    assert third_draft["rules"][0]["id"] == "trick-0001"
    assert third_draft["rules"][0]["prefer"] == (
        "Click once and wait for the expected content."
    )
    assert third_draft["rules"][1]["id"] == "trick-0002"
    assert third_draft["rules"][1]["prefer"] == (
        "Verify the visible success marker before continuing."
    )
    activate_guidance_revision(
        tmp_path,
        third_candidate,
        task_id="example-task",
        stage_ids={"open_target"},
    )
    loaded = load_windows_task(task_path)
    assert loaded.human_guidance is not None
    assert loaded.human_guidance.revision == 3
    assert [rule.rule_id for rule in loaded.human_guidance.rules] == [
        "trick-0001",
        "trick-0002",
    ]
    revision_one = yaml.safe_load(
        (task_path.parent / "guidance-revisions" / "revision-0001.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert len(revision_one["rules"]) == 1
    assert revision_one["rules"][0]["prefer"] == (
        "Click once and wait for the expected content."
    )

    conflict_candidate = compile_response(
        {
            "summary": "The latest feedback conflicts with the established click strategy.",
            "operations": [
                _operation(
                    "conflict",
                    target_rule_id="trick-0001",
                    prefer="Click repeatedly without waiting.",
                    reason="The new instruction contradicts the confirmed wait rule.",
                )
            ],
        },
        "连续点击，不要等待。",
    )
    conflict_manifest = yaml.safe_load(conflict_candidate.read_text(encoding="utf-8"))
    assert conflict_manifest["revision"]["conflict_count"] == 1
    with pytest.raises(ValueError, match="unresolved conflicts"):
        activate_guidance_revision(
            tmp_path,
            conflict_candidate,
            task_id="example-task",
            stage_ids={"open_target"},
        )
    assert load_windows_task(task_path).human_guidance.revision == 3


def test_legacy_snapshot_draft_cannot_overwrite_confirmed_guidance(tmp_path: Path) -> None:
    task_path = _write_semantic_task(tmp_path)
    candidate_path = _write_candidate(tmp_path, task_path, candidate_id="candidate-new")
    contract = load_windows_task(task_path)
    assert contract.semantic_experience is not None

    compile_guidance_revision(
        tmp_path,
        candidate_path,
        task_path,
        experience=contract.semantic_experience,
        reference_frame=contract.reference_frame,
        feedback="点击后等待界面变化。",
        session_factory=FakeRevisionSession,
        binary_resolver=lambda requested: requested,
    )
    activate_guidance_revision(
        tmp_path,
        candidate_path,
        task_id="example-task",
        stage_ids={"open_target"},
    )

    legacy_candidate = _write_candidate(
        tmp_path,
        task_path,
        candidate_id="candidate-legacy",
    )
    (legacy_candidate.parent / "revision-proposal.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "0.1",
                "task_id": "example-task",
                "status": "draft",
                "base_revision": 1,
                "proposed_revision": 2,
                "summary": "Legacy replacement draft.",
                "rules": [
                    {
                        "id": "trick-0001",
                        "stage_id": "open_target",
                        "when": "The target is visible.",
                        "prefer": "Replace every previous rule.",
                        "avoid": [],
                        "replan_when": ["The target disappears"],
                        "expected_effect": "The old rules are lost.",
                        "priority": "high",
                    }
                ],
                "revision_agent": {
                    "version": "0.9.1",
                    "model": "gpt-5.6-sol",
                    "reasoning_effort": "high",
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="legacy whole-snapshot"):
        activate_guidance_revision(
            tmp_path,
            legacy_candidate,
            task_id="example-task",
            stage_ids={"open_target"},
        )
    active = load_windows_task(task_path).human_guidance
    assert active is not None
    assert active.revision == 1
    assert active.rules[0].prefer == "Click once and wait for the expected content."
