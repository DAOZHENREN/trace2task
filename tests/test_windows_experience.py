from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pygame
import yaml

from trace2task.compiler import confirm_taskpack
from trace2task.windows_experience import compile_windows_semantic_experience
from trace2task.windows_task import load_windows_task


def _write_taskpack(tmp_path: Path) -> Path:
    task_root = tmp_path / "semantic-task"
    frames = task_root / "reference" / "frames"
    frames.mkdir(parents=True)
    events: list[dict[str, Any]] = []
    for seq, color in enumerate((20, 50, 90, 140, 190)):
        frame = f"frames/{seq:04d}.png"
        surface = pygame.Surface((80, 45))
        surface.fill((color, 60, 110))
        pygame.image.save(surface, task_root / "reference" / frame)
        events.append(
            {
                "seq": seq,
                "elapsed_ms": seq * 700,
                "type": "success_marker" if seq == 4 else "windows_input",
                "frame": frame,
            }
        )
    (task_root / "reference" / "trace.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    (task_root / "reference" / "metadata.json").write_text("{}\n", encoding="utf-8")
    demonstration = {
        "schema_version": "0.1",
        "task_id": "semantic-smoke",
        "actions": [
            {
                "index": 0,
                "action": {"skill": "focus_window", "args": {}},
                "source": {
                    "seqs": [0],
                    "start_elapsed_ms": 0,
                    "end_elapsed_ms": 0,
                    "evidence_frame": "reference/frames/0000.png",
                    "inference": "recording_start",
                },
            },
            {
                "index": 1,
                "action": {
                    "skill": "click",
                    "args": {"x": 0.25, "y": 0.5, "button": "left"},
                },
                "source": {
                    "seqs": [1],
                    "start_elapsed_ms": 700,
                    "end_elapsed_ms": 750,
                    "evidence_frame": "reference/frames/0001.png",
                    "inference": "click",
                },
            },
            {
                "index": 2,
                "action": {"skill": "wait", "args": {"duration_ms": 1600}},
                "source": {
                    "seqs": [2],
                    "start_elapsed_ms": 800,
                    "end_elapsed_ms": 2400,
                    "evidence_frame": "reference/frames/0002.png",
                    "inference": "wait",
                },
            },
            {
                "index": 3,
                "action": {
                    "skill": "click",
                    "args": {"x": 0.75, "y": 0.5, "button": "left"},
                },
                "source": {
                    "seqs": [3],
                    "start_elapsed_ms": 2500,
                    "end_elapsed_ms": 2550,
                    "evidence_frame": "reference/frames/0003.png",
                    "inference": "click",
                },
            },
        ],
    }
    (task_root / "demonstration.json").write_text(
        json.dumps(demonstration), encoding="utf-8"
    )
    task = {
        "schema_version": "0.3",
        "id": "semantic-smoke",
        "instruction": "Reach the successful demonstrated state.",
        "environment": {
            "adapter": "trace2task.windows",
            "target": {"process_name": "target.exe", "title_contains": "Target"},
        },
        "actions": ["focus_window", "click", "wait"],
        "demonstration": {"path": "demonstration.json", "action_count": 4},
        "verifier": {
            "type": "reviewed_reference_frame",
            "expected": "The workflow is complete.",
            "reference_frame": "reference/frames/0004.png",
        },
        "limits": {"max_actions": 16},
        "review": {"status": "draft", "requires_confirmation": True, "checklist": []},
    }
    task_path = task_root / "task.yaml"
    task_path.write_text(
        yaml.safe_dump(task, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return task_path


def _semantic_response() -> dict[str, Any]:
    return {
        "goal": "完成示范工作流",
        "summary": "进入目标界面，等待转换，然后完成确认。",
        "stages": [
            {
                "id": "open_target",
                "name": "打开目标",
                "start_action_index": 0,
                "end_action_index": 1,
                "state_before": {
                    "description": "目标窗口可见",
                    "evidence_frame": "reference/frames/0000.png",
                    "visual_anchors": ["目标窗口内容可见"],
                },
                "action_intents": [
                    {
                        "start_action_index": 0,
                        "end_action_index": 1,
                        "description": "聚焦并打开目标入口",
                        "target": "可见入口",
                        "provenance": "inferred",
                        "confidence": 0.86,
                    }
                ],
                "preconditions": ["目标窗口已打开"],
                "expected_effects": ["界面开始切换"],
                "state_after": {
                    "description": "入口已触发",
                    "evidence_frame": "reference/frames/0001.png",
                    "visual_anchors": ["入口画面发生变化"],
                },
                "dynamic_decisions": [],
                "confidence": 0.84,
            },
            {
                "id": "finish_target",
                "name": "完成确认",
                "start_action_index": 2,
                "end_action_index": 3,
                "state_before": {
                    "description": "等待后的目标界面",
                    "evidence_frame": "reference/frames/0002.png",
                    "visual_anchors": ["目标控件已经出现"],
                },
                "action_intents": [
                    {
                        "start_action_index": 2,
                        "end_action_index": 3,
                        "description": "等待界面稳定并确认当前可见选项",
                        "target": "当前可见选项",
                        "provenance": "inferred",
                        "confidence": 0.7,
                    }
                ],
                "preconditions": ["目标控件可见"],
                "expected_effects": ["工作流进入完成状态"],
                "state_after": {
                    "description": "完成状态已经出现",
                    "evidence_frame": "reference/frames/0003.png",
                    "visual_anchors": ["完成后的界面可见"],
                },
                "dynamic_decisions": [
                    {
                        "description": "具体选择取决于运行时内容",
                        "generalization": "runtime_agent_decides",
                        "confidence": 0.35,
                    }
                ],
                "confidence": 0.72,
            },
        ],
    }


class FakeSession:
    def __init__(
        self,
        codex_executable: str,
        *,
        responses: Iterator[dict[str, Any]],
        calls: list[dict[str, Any]],
        **kwargs: Any,
    ) -> None:
        self.responses = responses
        self.calls = calls
        self.closed = False

    def run_turn(
        self,
        *,
        prompt: str,
        image_path: Path,
        output_schema: dict[str, Any],
        additional_image_paths: tuple[Path, ...] = (),
    ) -> str:
        self.calls.append(
            {
                "prompt": prompt,
                "image_path": image_path,
                "additional": additional_image_paths,
                "schema": output_schema,
            }
        )
        return json.dumps(next(self.responses), ensure_ascii=False)

    def close(self) -> None:
        self.closed = True


def test_compiler_agent_adds_replaceable_grounded_semantic_layer(tmp_path: Path) -> None:
    task_path = _write_taskpack(tmp_path)
    trace_path = task_path.parent / "reference" / "trace.jsonl"
    demonstration_path = task_path.parent / "demonstration.json"
    original_trace = trace_path.read_bytes()
    original_demonstration = demonstration_path.read_bytes()
    calls: list[dict[str, Any]] = []
    sessions: list[FakeSession] = []

    def session_factory(codex_executable: str, **kwargs: Any) -> FakeSession:
        session = FakeSession(
            codex_executable,
            responses=iter([_semantic_response()]),
            calls=calls,
            **kwargs,
        )
        sessions.append(session)
        return session

    result = compile_windows_semantic_experience(
        task_path,
        model="gpt-5.6-sol",
        reasoning_effort="high",
        binary_resolver=lambda requested: requested,
        session_factory=session_factory,
    )
    contract = load_windows_task(task_path)
    root = yaml.safe_load(task_path.read_text(encoding="utf-8"))
    experience = yaml.safe_load(
        (task_path.parent / "experience.yaml").read_text(encoding="utf-8")
    )

    assert result.stage_count == 2
    assert result.review_status == "draft"
    assert trace_path.read_bytes() == original_trace
    assert demonstration_path.read_bytes() == original_demonstration
    assert root["semantic_experience"]["source"] == "human_trace"
    assert experience["source"]["policy"] == "immutable_strong_evidence"
    assert experience["compiler"]["policy"] == "replaceable_derived_interpretation"
    assert contract.semantic_experience is not None
    assert contract.semantic_experience.stages[1].dynamic_decisions[0].generalization == (
        "runtime_agent_decides"
    )
    assert "single trajectory does not prove a general strategy" in calls[0]["prompt"]
    assert calls[0]["schema"]["properties"]["stages"]["items"]["additionalProperties"] is False
    assert calls[0]["image_path"].name == "contact-sheet-01.png"
    assert "Contact sheet Image 1, cell Frame 1" in calls[0]["prompt"]
    assert sessions[0].closed is True

    confirmed = confirm_taskpack(task_path)
    confirmed_experience = yaml.safe_load(
        (task_path.parent / "experience.yaml").read_text(encoding="utf-8")
    )
    assert confirmed.review_status == "confirmed"
    assert confirmed_experience["review"]["status"] == "confirmed"
    assert load_windows_task(task_path).semantic_experience is not None
