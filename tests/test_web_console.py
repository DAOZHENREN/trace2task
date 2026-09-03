from __future__ import annotations

import base64
import json
import queue
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener

import pygame
import pytest
import yaml

from trace2task import web_console
from trace2task.codex_app_server import CodexTurnTimeoutError
from trace2task.speech_transcription import LocalTranscription
from trace2task.web_console import ConsoleJob, WebConsoleController, create_web_server
from trace2task.windows_runner import WindowsAgentRunFailed


@pytest.fixture(autouse=True)
def _stub_compiler_connectivity_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        web_console,
        "probe_codex_compiler_connection",
        lambda **kwargs: {
            "status": "completed",
            "model": kwargs.get("model", "gpt-5.6-luna"),
            "reasoning_effort": "low",
            "elapsed_ms": 12.0,
        },
    )


def _write_windows_task(
    root: Path,
    *,
    confirmed: bool = True,
    semantic: bool = False,
    guidance: bool = False,
) -> Path:
    task_dir = root / "taskpacks" / "wechat-example"
    reference_dir = task_dir / "reference"
    reference_dir.mkdir(parents=True)
    surface = pygame.Surface((32, 24))
    surface.fill((240, 245, 242))
    pygame.image.save(surface, reference_dir / "final.png")
    (task_dir / "demonstration.json").write_text(
        json.dumps(
            {
                "actions": [
                    {"action": {"skill": "focus_window", "args": {}}},
                    {
                        "action": {
                            "skill": "click",
                            "args": {"x": 0.5, "y": 0.5, "button": "left"},
                        }
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    task = {
        "id": "wechat-example",
        "instruction": "Follow the recorded WeChat workflow.",
        "environment": {
            "adapter": "trace2task.windows",
            "target": {"process_name": "Weixin.exe", "title_contains": "微信"},
        },
        "actions": ["focus_window", "click"],
        "experience": {
            "intent": "微信发消息",
            "examples": ["给联系人发消息", "给文件传输助手发送消息"],
        },
        "demonstration": {"path": "demonstration.json", "action_count": 2},
        "verifier": {
            "type": "reviewed_reference_frame",
            "expected": "Reach the analogous successful state.",
            "reference_frame": "reference/final.png",
        },
        "limits": {"max_actions": 12},
        "review": {
            "status": "confirmed" if confirmed else "draft",
            "requires_confirmation": not confirmed,
        },
    }
    if semantic:
        frames_dir = reference_dir / "frames"
        frames_dir.mkdir()
        pygame.image.save(surface, frames_dir / "before.png")
        pygame.image.save(surface, frames_dir / "after.png")
        task["semantic_experience"] = {
            "path": "experience.yaml",
            "stage_count": 1,
            "source": "human_trace",
        }
        (task_dir / "experience.yaml").write_text(
            yaml.safe_dump(
                {
                    "schema_version": "0.1",
                    "task_id": "wechat-example",
                    "source": {
                        "type": "human_trace",
                        "trace": "reference/trace.jsonl",
                        "demonstration": "demonstration.json",
                    },
                    "compiler": {
                        "type": "multimodal_agent",
                        "version": "0.8.0",
                        "model": "gpt-5.6-sol",
                        "reasoning_effort": "high",
                    },
                    "goal": "Send a message.",
                    "summary": "Open the conversation and send the requested message.",
                    "stages": [
                        {
                            "id": "send_message",
                            "name": "Send message",
                            "start_action_index": 0,
                            "end_action_index": 1,
                            "state_before": {
                                "description": "WeChat is visible.",
                                "evidence_frame": "reference/frames/before.png",
                                "visual_anchors": ["conversation list"],
                            },
                            "action_intents": [
                                {
                                    "start_action_index": 0,
                                    "end_action_index": 1,
                                    "description": "Open the conversation.",
                                    "target": "conversation",
                                    "provenance": "observed",
                                    "confidence": 0.9,
                                }
                            ],
                            "preconditions": ["WeChat is visible"],
                            "expected_effects": ["Conversation opens"],
                            "state_after": {
                                "description": "Conversation is open.",
                                "evidence_frame": "reference/frames/after.png",
                                "visual_anchors": ["message field"],
                            },
                            "dynamic_decisions": [],
                            "confidence": 0.9,
                        }
                    ],
                    "review": {
                        "status": "confirmed" if confirmed else "draft",
                        "requires_confirmation": not confirmed,
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
    if guidance:
        if not semantic:
            raise ValueError("guidance test fixture requires semantic experience")
        task["human_guidance"] = {
            "path": "guidance.yaml",
            "revision": 1,
            "rule_count": 1,
        }
        (task_dir / "guidance.yaml").write_text(
            yaml.safe_dump(
                {
                    "schema_version": "0.1",
                    "task_id": "wechat-example",
                    "status": "confirmed",
                    "revision": 1,
                    "summary": "搜索联系人后等待会话稳定，再输入消息。",
                    "rules": [
                        {
                            "id": "trick-01",
                            "stage_id": "send_message",
                            "when": "联系人搜索结果已经出现。",
                            "prefer": "单击正确联系人并等待输入框出现。",
                            "avoid": ["连续重复点击联系人"],
                            "replan_when": ["输入框没有出现"],
                            "expected_effect": "目标会话稳定打开。",
                            "priority": "high",
                        }
                    ],
                    "revision_agent": {
                        "version": "0.8.0",
                        "model": "gpt-5.6-sol",
                        "reasoning_effort": "high",
                    },
                },
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
    task_path = task_dir / "task.yaml"
    task_path.write_text(yaml.safe_dump(task, allow_unicode=True), encoding="utf-8")
    return task_path


def _write_windows_recording(
    root: Path,
    *,
    run_name: str = "recording-example",
    task_id: str = "wechat-send-example",
    started_at: str = "2026-08-28T01:02:03+00:00",
) -> Path:
    run_dir = root / "runs" / run_name
    run_dir.mkdir(parents=True)
    trace_path = run_dir / "trace.jsonl"
    trace_path.write_text('{"seq": 0}\n', encoding="utf-8")
    (run_dir / "metadata.json").write_text(
        json.dumps(
            {
                "source": "windows_human",
                "task_id": task_id,
                "started_at": started_at,
                "success": True,
                "stop_reason": "success_key",
                "input_event_count": 7,
                "initial_window": {
                    "process_name": "Weixin.exe",
                    "title": "微信",
                },
            }
        ),
        encoding="utf-8",
    )
    return trace_path


def test_experience_family_inherits_confirmed_guidance_without_raw_coordinates(
    tmp_path: Path,
) -> None:
    source_task = _write_windows_task(tmp_path, semantic=True, guidance=True)
    source_root = yaml.safe_load(source_task.read_text(encoding="utf-8"))
    source_root["experience"]["family_id"] = "wechat-message-family"
    source_task.write_text(
        yaml.safe_dump(source_root, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    target_dir = tmp_path / "taskpacks" / "wechat-rerecord"
    shutil.copytree(source_task.parent, target_dir)
    target_task = target_dir / "task.yaml"
    target_root = yaml.safe_load(target_task.read_text(encoding="utf-8"))
    target_root["id"] = "wechat-message-rerecord"
    target_root["experience"]["family_id"] = "wechat-message-family"
    target_root.pop("human_guidance", None)
    target_task.write_text(
        yaml.safe_dump(target_root, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    experience_path = target_dir / "experience.yaml"
    target_experience = yaml.safe_load(experience_path.read_text(encoding="utf-8"))
    target_experience["task_id"] = "wechat-message-rerecord"
    experience_path.write_text(
        yaml.safe_dump(target_experience, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    (target_dir / "guidance.yaml").unlink()

    controller = WebConsoleController(tmp_path, runner=lambda *args, **kwargs: FakeResult())
    inherited = controller._inherit_family_guidance(
        source_task,
        target_task,
        family_id="wechat-message-family",
    )
    listed = next(
        task for task in controller.list_taskpacks() if task["task_id"] == "wechat-message-rerecord"
    )

    assert inherited == {
        "family_id": "wechat-message-family",
        "source_task_id": "wechat-example",
        "revision": 1,
        "rule_count": 1,
        "local_rule_count": 0,
    }
    assert listed["human_guidance"]["inheritance"]["source_task_id"] == "wechat-example"
    assert listed["human_guidance"]["rules"][0]["prefer"] == (
        "单击正确联系人并等待输入框出现。"
    )


@dataclass(frozen=True)
class FakeResult:
    stop_reason: str = "dry_run_plan_only"
    proposed_actions: tuple[str, ...] = ("click",)


@dataclass(frozen=True)
class FakeCompilation:
    task_path: str
    review_status: str = "draft"


@dataclass(frozen=True)
class FakeSemanticCompilation:
    experience_path: str
    stage_count: int = 3
    review_status: str = "draft"


@dataclass(frozen=True)
class FakeGuidanceRevision:
    proposal_path: str
    proposed_revision: int = 1
    rule_count: int = 2


@dataclass(frozen=True)
class SuccessfulResult:
    task_complete: bool
    stop_reason: str
    trace_path: str
    executed_actions: int = 3
    replans: int = 2
    planning_ms: float = 1250.0
    batch_count: int = 2
    planned_actions: int = 5
    interrupted_batches: int = 0
    average_batch_size: float = 2.5
    max_batch_size: int = 3
    visual_checkpoints: int = 4
    visual_checkpoint_failures: int = 1
    visual_stability_wait_ms: int = 800
    local_wait_until_count: int = 1
    local_wait_until_ms: int = 1400
    wait_only_plans: int = 0
    short_batch_count: int = 1
    session_resets: int = 1
    performance: dict[str, object] = field(
        default_factory=lambda: {
            "total_elapsed_ms": 2400.0,
            "planning_ms": 1250.0,
            "model_roundtrip_ms": 1200.0,
        }
    )
    stage_timings: list[dict[str, object]] = field(
        default_factory=lambda: [
            {
                "stage_id": "send_message",
                "plans": 2,
                "executed_actions": 3,
                "planning_ms": 1250.0,
            }
        ]
    )
    failure_message: str | None = None
    verification_outcome: str | None = None
    verified: bool = False
    verification_receipt_path: str | None = None


def test_web_controller_discovers_taskpacks_and_runs_single_instruction(tmp_path: Path) -> None:
    task_path = _write_windows_task(tmp_path)
    calls: list[tuple[Path, dict[str, object]]] = []

    def runner(path: Path, **kwargs: object) -> FakeResult:
        calls.append((path, kwargs))
        callback = kwargs["status_callback"]
        assert callable(callback)
        callback("模型已经返回计划。")
        return FakeResult()

    controller = WebConsoleController(tmp_path, runner=runner)
    taskpacks = controller.list_taskpacks()

    assert len(taskpacks) == 1
    assert taskpacks[0]["task_id"] == "wechat-example"
    assert taskpacks[0]["confirmed"] is True
    assert taskpacks[0]["experience_intent"] == "微信发消息"
    assert taskpacks[0]["missing_message_capabilities"] == ["type_text", "press_key"]

    job = controller.start_job(
        task_path=taskpacks[0]["path"],
        instruction="  给文件传输助手   发送：网页控制台测试  ",
        execute=False,
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )
    completed = controller.wait(job["job_id"])

    assert completed["status"] == "completed"
    assert completed["instruction"] == "给文件传输助手 发送：网页控制台测试"
    assert completed["model"] == "gpt-5.6-sol"
    assert completed["reasoning_effort"] == "high"
    assert completed["result"]["stop_reason"] == "dry_run_plan_only"
    assert "模型已经返回计划。" in completed["logs"]
    assert calls[0][0] == task_path
    assert calls[0][1]["instruction"] == "给文件传输助手 发送：网页控制台测试"
    assert calls[0][1]["execute"] is False
    assert calls[0][1]["focus"] is True
    assert calls[0][1]["model"] == "gpt-5.6-sol"
    assert calls[0][1]["reasoning_effort"] == "high"


def test_web_controller_can_auto_select_a_trace_from_one_instruction(tmp_path: Path) -> None:
    task_path = _write_windows_task(tmp_path)
    calls: list[Path] = []

    def runner(path: Path, **kwargs: object) -> FakeResult:
        calls.append(path)
        return FakeResult()

    controller = WebConsoleController(tmp_path, runner=runner)
    route = controller.route_instruction("给文件传输助手发消息：自动路由测试")
    job = controller.start_job(
        task_path="",
        instruction="给文件传输助手发消息：自动路由测试",
        execute=False,
    )
    completed = controller.wait(job["job_id"])

    assert route["task_id"] == "wechat-example"
    assert route["confidence"] >= 0.8
    assert completed["selection_mode"] == "auto"
    assert completed["selection_confidence"] == route["confidence"]
    assert calls == [task_path]
    assert completed["logs"][0].startswith("自动选择经验：wechat-example")

    with pytest.raises(ValueError, match="足够匹配"):
        controller.route_instruction("整理桌面财务表格")


def test_web_controller_forwards_background_and_adaptive_execution_settings(
    tmp_path: Path,
) -> None:
    task_path = _write_windows_task(tmp_path)
    calls: list[dict[str, object]] = []

    def runner(path: Path, **kwargs: object) -> FakeResult:
        calls.append(kwargs)
        return FakeResult()

    controller = WebConsoleController(tmp_path, runner=runner)
    job = controller.start_job(
        task_path=task_path.relative_to(tmp_path).as_posix(),
        instruction="后台规划测试",
        execute=False,
        background=True,
        adaptive_reasoning=False,
    )
    completed = controller.wait(job["job_id"])

    assert completed["input_mode"] == "background"
    assert completed["adaptive_reasoning"] is False
    assert calls[0]["background"] is True
    assert calls[0]["adaptive_reasoning"] is False
    assert calls[0]["focus"] is False


def test_successful_execution_creates_a_pending_candidate_experience(
    tmp_path: Path,
) -> None:
    task_path = _write_windows_task(tmp_path)
    root = yaml.safe_load(task_path.read_text(encoding="utf-8"))
    root["actions"].extend(["type_text", "press_key"])
    task_path.write_text(yaml.safe_dump(root, allow_unicode=True), encoding="utf-8")
    trace_path = tmp_path / "runs" / "successful-agent" / "trace.jsonl"
    trace_path.parent.mkdir(parents=True)
    trace_path.write_text('{"seq": 0}\n', encoding="utf-8")

    controller = WebConsoleController(
        tmp_path,
        runner=lambda *args, **kwargs: SuccessfulResult(
            task_complete=True,
            stop_reason="model_complete",
            trace_path=str(trace_path),
        ),
    )
    job = controller.start_job(
        task_path=task_path.relative_to(tmp_path).as_posix(),
        instruction="给文件传输助手发送候选经验测试",
        execute=True,
    )
    completed = controller.wait(job["job_id"])
    candidates = controller.list_candidates()

    assert completed["status"] == "completed"
    assert completed["result"]["candidate_experience"]["status"] == "pending_review"
    assert len(candidates) == 1
    assert candidates[0]["instruction"] == "给文件传输助手发送候选经验测试"
    assert candidates[0]["outcome"] == {
        "task_complete": True,
        "stop_reason": "model_complete",
    }
    assert candidates[0]["metrics"] == {
        "executed_actions": 3,
        "replans": 2,
        "planning_ms": 1250.0,
        "batch_count": 2,
        "planned_actions": 5,
        "interrupted_batches": 0,
        "average_batch_size": 2.5,
        "max_batch_size": 3,
        "visual_checkpoints": 4,
        "visual_checkpoint_failures": 1,
        "visual_stability_wait_ms": 800,
        "local_wait_until_count": 1,
        "local_wait_until_ms": 1400,
        "wait_only_plans": 0,
        "short_batch_count": 1,
        "session_resets": 1,
        "performance": {
            "total_elapsed_ms": 2400.0,
            "planning_ms": 1250.0,
            "model_roundtrip_ms": 1200.0,
        },
        "stage_timings": [
            {
                "stage_id": "send_message",
                "plans": 2,
                "executed_actions": 3,
                "planning_ms": 1250.0,
            }
        ],
    }
    manifest = tmp_path / candidates[0]["local_path"] / "candidate.yaml"
    manifest_data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    assert manifest_data["status"] == "pending_review"
    assert manifest_data["outcome"] == {
        "task_complete": True,
        "stop_reason": "model_complete",
    }


def test_incomplete_execution_with_trace_is_available_for_feedback(tmp_path: Path) -> None:
    task_path = _write_windows_task(tmp_path)
    root = yaml.safe_load(task_path.read_text(encoding="utf-8"))
    root["actions"].extend(["type_text", "press_key"])
    task_path.write_text(yaml.safe_dump(root, allow_unicode=True), encoding="utf-8")
    trace_path = tmp_path / "runs" / "incomplete-agent" / "trace.jsonl"
    trace_path.parent.mkdir(parents=True)
    trace_path.write_text('{"seq": 0}\n', encoding="utf-8")

    controller = WebConsoleController(
        tmp_path,
        runner=lambda *args, **kwargs: SuccessfulResult(
            task_complete=False,
            stop_reason="action_limit",
            trace_path=str(trace_path),
        ),
    )
    job = controller.start_job(
        task_path=task_path.relative_to(tmp_path).as_posix(),
        instruction="尝试一次并记录跑偏原因",
        execute=True,
    )
    completed = controller.wait(job["job_id"])
    candidates = controller.list_candidates()

    assert completed["status"] == "completed"
    assert completed["result"]["candidate_experience"]["status"] == "pending_review"
    assert candidates[0]["outcome"] == {
        "task_complete": False,
        "stop_reason": "action_limit",
    }


def test_feedback_candidate_preserves_effect_verification_outcome(tmp_path: Path) -> None:
    task_path = _write_windows_task(tmp_path)
    task = yaml.safe_load(task_path.read_text(encoding="utf-8"))
    task["actions"].extend(["type_text", "press_key"])
    task_path.write_text(yaml.safe_dump(task, allow_unicode=True), encoding="utf-8")
    trace_path = tmp_path / "runs" / "verified-agent" / "trace.jsonl"
    trace_path.parent.mkdir(parents=True)
    trace_path.write_text('{"seq": 0}\n', encoding="utf-8")
    receipt_path = trace_path.with_name("verification.json")
    receipt_path.write_text('{"outcome": "verified"}\n', encoding="utf-8")
    controller = WebConsoleController(
        tmp_path,
        runner=lambda *args, **kwargs: SuccessfulResult(
            task_complete=True,
            stop_reason="model_complete",
            trace_path=str(trace_path),
            verification_outcome="verified",
            verified=True,
            verification_receipt_path=str(receipt_path),
        ),
    )

    job = controller.start_job(
        task_path=task_path.relative_to(tmp_path).as_posix(),
        instruction="执行并保存独立验证结果",
        execute=True,
    )
    controller.wait(job["job_id"])
    candidate = controller.list_candidates()[0]

    assert candidate["outcome"]["verification_outcome"] == "verified"
    assert candidate["outcome"]["verified"] is True
    assert candidate["outcome"]["verification_receipt_path"] == str(receipt_path)


def test_failed_execution_with_trace_is_available_for_feedback(tmp_path: Path) -> None:
    task_path = _write_windows_task(tmp_path)
    root = yaml.safe_load(task_path.read_text(encoding="utf-8"))
    root["actions"].extend(["type_text", "press_key"])
    task_path.write_text(yaml.safe_dump(root, allow_unicode=True), encoding="utf-8")
    trace_path = tmp_path / "runs" / "failed-agent" / "trace.jsonl"
    trace_path.parent.mkdir(parents=True)
    trace_path.write_text('{"seq": 0}\n', encoding="utf-8")
    failure_message = "Stage batch recovery limit reached after 3 consecutive failures"
    result = SuccessfulResult(
        task_complete=False,
        stop_reason="failed:RuntimeError",
        trace_path=str(trace_path),
        failure_message=failure_message,
    )

    def runner(*args: object, **kwargs: object) -> SuccessfulResult:
        raise WindowsAgentRunFailed(result, RuntimeError(failure_message))

    controller = WebConsoleController(tmp_path, runner=runner)
    job = controller.start_job(
        task_path=task_path.relative_to(tmp_path).as_posix(),
        instruction="尝试一次并反馈失败原因",
        execute=True,
    )
    completed = controller.wait(job["job_id"])
    candidates = controller.list_candidates()

    assert completed["status"] == "failed"
    assert completed["error"] == f"RuntimeError: {failure_message}"
    assert completed["result"]["candidate_experience"]["status"] == "pending_review"
    assert any("未完成运行已保存为待反馈运行" in log for log in completed["logs"])
    assert len(candidates) == 1
    assert candidates[0]["outcome"] == {
        "task_complete": False,
        "stop_reason": "failed:RuntimeError",
        "failure_message": failure_message,
    }


def test_web_revision_job_uses_separate_revision_agent_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_path = _write_windows_task(tmp_path, semantic=True)
    trace_path = tmp_path / "runs" / "agent-run" / "trace.jsonl"
    trace_path.parent.mkdir(parents=True)
    trace_path.write_text('{"seq": 0}\n', encoding="utf-8")
    candidate_dir = tmp_path / "runs" / "candidates" / "candidate-revision"
    candidate_dir.mkdir(parents=True)
    candidate_path = candidate_dir / "candidate.yaml"
    candidate_path.write_text(
        yaml.safe_dump(
            {
                "status": "pending_review",
                "candidate_id": "candidate-revision",
                "task_id": "wechat-example",
                "source_task": task_path.relative_to(tmp_path).as_posix(),
                "execution_trace": trace_path.relative_to(tmp_path).as_posix(),
                "runtime_instruction": "发送测试消息",
            }
        ),
        encoding="utf-8",
    )
    calls: list[dict[str, object]] = []

    def revision_compiler(*args: object, **kwargs: object) -> FakeGuidanceRevision:
        calls.append(kwargs)
        return FakeGuidanceRevision(
            proposal_path=str(candidate_dir / "revision-proposal.yaml")
        )

    monkeypatch.setattr(web_console, "compile_guidance_revision", revision_compiler)
    controller = WebConsoleController(tmp_path, runner=lambda *args, **kwargs: FakeResult())

    job = controller.start_revision(
        candidate_dir.relative_to(tmp_path).as_posix(),
        "发送后不要立即重新规划，先等待消息出现。",
    )
    completed = controller.wait(job["job_id"])

    assert completed["status"] == "completed"
    assert completed["kind"] == "revision"
    assert completed["model"] == "gpt-5.6-sol"
    assert completed["reasoning_effort"] == "high"
    assert calls[0]["feedback"] == "发送后不要立即重新规划，先等待消息出现。"


def test_web_controller_rejects_empty_instruction_and_outside_task(tmp_path: Path) -> None:
    task_path = _write_windows_task(tmp_path)
    controller = WebConsoleController(tmp_path, runner=lambda *args, **kwargs: FakeResult())
    relative = task_path.relative_to(tmp_path).as_posix()

    with pytest.raises(ValueError, match="请输入"):
        controller.start_job(task_path=relative, instruction="   ", execute=False)
    with pytest.raises(ValueError, match="taskpacks"):
        controller.start_job(
            task_path="outside/task.yaml",
            instruction="测试",
            execute=False,
        )
    with pytest.raises(ValueError, match="缺少文本输入能力"):
        controller.start_job(
            task_path=relative,
            instruction="给文件传输助手发送测试消息",
            execute=True,
        )
    with pytest.raises(ValueError, match="不支持的模型"):
        controller.start_job(
            task_path=relative,
            instruction="测试",
            execute=False,
            model="not-a-model",
        )
    with pytest.raises(ValueError, match="不支持的思考强度"):
        controller.start_job(
            task_path=relative,
            instruction="测试",
            execute=False,
            reasoning_effort="extreme",
        )


def test_web_controller_lists_recordings_and_upgrades_then_confirms_taskpack(
    tmp_path: Path,
) -> None:
    task_path = _write_windows_task(tmp_path)
    trace_path = _write_windows_recording(tmp_path)
    controller = WebConsoleController(tmp_path, runner=lambda *args, **kwargs: FakeResult())

    recordings = controller.list_recordings()
    assert recordings == [
        {
            "task_id": "wechat-send-example",
            "success": True,
            "stop_reason": "success_key",
            "input_events": 7,
            "narrated": False,
            "narration_chars": 0,
            "process_name": "Weixin.exe",
            "title": "微信",
            "created_at": "2026-08-28T01:02:03+00:00",
            "trace_path": trace_path.relative_to(tmp_path).as_posix(),
            "local_path": trace_path.parent.relative_to(tmp_path).as_posix(),
        }
    ]

    relative = task_path.relative_to(tmp_path).as_posix()
    upgraded = controller.upgrade_taskpack(relative)
    upgraded_task = yaml.safe_load(task_path.read_text(encoding="utf-8"))
    assert upgraded["status"] == "draft"
    assert {"type_text", "press_key", "hotkey"}.issubset(upgraded_task["actions"])
    assert upgraded_task["review"]["requires_confirmation"] is True

    confirmed = controller.confirm_local_taskpack(relative)
    assert confirmed["review_status"] == "confirmed"
    assert confirmed["requires_confirmation"] is False


def test_narrated_recording_waits_for_review_then_archives_and_compiles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace_path = _write_windows_recording(tmp_path)
    controller = WebConsoleController(tmp_path, runner=lambda *args, **kwargs: FakeResult())
    job = ConsoleJob(
        job_id="narrated-job",
        task_path="",
        task_id="讲解示范",
        instruction="录制带讲解的示范",
        mode="record",
        kind="recording",
        narrated=True,
        status="awaiting_narration",
        result={"trace_path": str(trace_path), "success": True},
    )
    controller._jobs[job.job_id] = job
    controller._active_job_id = job.job_id
    compiled = threading.Event()

    def run_compilation(
        active_job: ConsoleJob,
        source: Path,
        payload: dict[str, object],
    ) -> None:
        assert active_job is job
        assert source == trace_path
        assert payload["narration"]["status"] == "archived"
        compiled.set()

    monkeypatch.setattr(controller, "_run_recording_compilation", run_compilation)

    submitted = controller.submit_recording_narration(
        job.job_id,
        transcript="先点攻击，再按克制关系选择三张卡。",
        segments=[{"start_ms": 0, "end_ms": 1500, "text": "先点攻击"}],
        audio_base64=base64.b64encode(b"webm").decode("ascii"),
        mime_type="audio/webm",
    )

    assert submitted["status"] == "queued"
    assert compiled.wait(2)
    narration = json.loads(
        (trace_path.parent / "narration.json").read_text(encoding="utf-8")
    )
    assert narration["transcript"] == "先点攻击，再按克制关系选择三张卡。"
    assert (trace_path.parent / "narration.webm").read_bytes() == b"webm"
    listed = controller.list_recordings()[0]
    assert listed["narrated"] is True
    assert listed["narration_chars"] == len(narration["transcript"])

    with pytest.raises(RuntimeError, match="讲解已经提交"):
        controller.submit_recording_narration(job.job_id, transcript="重复提交")


def test_narrated_recording_can_be_discarded_while_waiting_for_review(
    tmp_path: Path,
) -> None:
    trace_path = _write_windows_recording(tmp_path)
    audio_path = trace_path.parent / "narration.webm"
    audio_path.write_bytes(b"poor audio")
    controller = WebConsoleController(tmp_path, runner=lambda *args, **kwargs: FakeResult())
    job = ConsoleJob(
        job_id="discard-narration-job",
        task_path="",
        task_id="讲解示范",
        instruction="录制带讲解的示范",
        mode="record",
        kind="recording",
        narrated=True,
        status="awaiting_narration",
        result={
            "trace_path": str(trace_path),
            "success": True,
            "narration": {
                "status": "awaiting_review",
                "audio_path": str(audio_path),
                "transcript": "错误的转写",
            },
        },
    )
    controller._jobs[job.job_id] = job
    controller._active_job_id = job.job_id

    stopped = controller.stop_job(job.job_id)

    assert stopped["status"] == "stopped"
    assert stopped["stop_requested"] is True
    assert stopped["result"]["narration"] == {
        "status": "discarded",
        "audio_discarded": True,
    }
    assert trace_path.is_file()
    assert not audio_path.exists()
    assert "保留原始 Trace" in stopped["logs"][-1]


def test_narrated_recording_uses_local_turbo_before_human_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace_path = _write_windows_recording(tmp_path)

    class FakeTranscriber:
        def transcribe(
            self,
            audio_path: Path,
            *,
            cache_dir: Path,
            initial_prompt: str,
        ) -> LocalTranscription:
            assert audio_path.read_bytes() == b"webm"
            assert cache_dir == tmp_path / ".cache" / "faster-whisper"
            assert "讲解示范" in initial_prompt
            return LocalTranscription(
                transcript="先点攻击，再选择三张卡。",
                segments=[
                    {"start_ms": 0.0, "end_ms": 1500.0, "text": "先点攻击"}
                ],
                language="zh",
                language_probability=0.99,
                model="turbo",
                device="cuda",
                compute_type="float16",
            )

    controller = WebConsoleController(
        tmp_path,
        runner=lambda *args, **kwargs: FakeResult(),
        narration_transcriber=FakeTranscriber(),
    )
    job = ConsoleJob(
        job_id="turbo-job",
        task_path="",
        task_id="讲解示范",
        instruction="录制带讲解的示范",
        mode="record",
        kind="recording",
        narrated=True,
        status="awaiting_narration",
        result={"trace_path": str(trace_path), "success": True},
    )
    controller._jobs[job.job_id] = job
    controller._active_job_id = job.job_id
    compiled = threading.Event()
    monkeypatch.setattr(
        controller,
        "_run_recording_compilation",
        lambda *args: compiled.set(),
    )

    transcribed = controller.transcribe_recording_narration(
        job.job_id,
        audio_base64=base64.b64encode(b"webm").decode("ascii"),
        mime_type="audio/webm",
    )

    assert transcribed["transcription"]["transcript"] == "先点攻击，再选择三张卡。"
    assert transcribed["transcription"]["device"] == "cuda"
    assert (trace_path.parent / "narration.webm").read_bytes() == b"webm"
    assert transcribed["job"]["status"] == "awaiting_narration"
    assert transcribed["job"]["result"]["narration"]["transcript"] == (
        "先点攻击，再选择三张卡。"
    )

    submitted = controller.submit_recording_narration(
        job.job_id,
        transcript="先点攻击，然后按克制关系选择三张卡。",
        segments=transcribed["transcription"]["segments"],
        mime_type=None,
        transcription_engine="faster_whisper:turbo",
    )

    assert submitted["status"] == "queued"
    assert compiled.wait(2)
    narration = json.loads(
        (trace_path.parent / "narration.json").read_text(encoding="utf-8")
    )
    assert narration["transcript"] == "先点攻击，然后按克制关系选择三张卡。"
    assert narration["audio"]["path"] == "narration.webm"
    assert narration["transcription_engine"] == "faster_whisper:turbo"


def test_voice_dictation_reuses_local_turbo_without_archiving_audio(
    tmp_path: Path,
) -> None:
    seen: dict[str, object] = {}

    class FakeTranscriber:
        def transcribe(
            self,
            audio_path: Path,
            *,
            cache_dir: Path,
            initial_prompt: str,
        ) -> LocalTranscription:
            seen["audio_path"] = audio_path
            assert audio_path.read_bytes() == b"webm"
            assert cache_dir == tmp_path / ".cache" / "faster-whisper"
            assert "人工运行反馈" in initial_prompt
            return LocalTranscription(
                transcript="失败后跳过这个技能，改用普通攻击。",
                segments=[],
                language="zh",
                language_probability=0.99,
                model="turbo",
                device="cuda",
                compute_type="float16",
            )

    controller = WebConsoleController(
        tmp_path,
        runner=lambda *args, **kwargs: FakeResult(),
        narration_transcriber=FakeTranscriber(),
    )

    result = controller.transcribe_dictation(
        audio_base64=base64.b64encode(b"webm").decode("ascii"),
        mime_type="audio/webm",
        context="人工运行反馈",
    )

    assert result["transcription"]["transcript"] == (
        "失败后跳过这个技能，改用普通攻击。"
    )
    assert not Path(seen["audio_path"]).exists()
    assert not list(tmp_path.rglob("dictation.webm"))


def test_web_controller_starts_and_releases_a_narrated_waa_recording(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waa_root = tmp_path / "WindowsAgentArena"
    client_root = waa_root / "src" / "win-arena-container" / "client"
    example_path = client_root / "evaluation_examples_windows" / "example.json"
    example_path.parent.mkdir(parents=True)
    example_path.write_text(
        json.dumps({"id": "waa-example", "instruction": "Type hello in Notepad."}),
        encoding="utf-8",
    )
    reset_spec = (
        tmp_path
        / "integrations"
        / "windows_agent_arena"
        / "reset_specs"
        / "notepad.json"
    )
    reset_spec.parent.mkdir(parents=True)
    reset_spec.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "tasks": {
                    "waa-example": {
                        "must_not_exist": [
                            r"C:\Users\Docker\Documents\draft.txt"
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    bundled_client = reset_spec.parents[1] / "client"
    bundled_client.mkdir()
    (bundled_client / "trace2task_reset.py").write_text(
        "# reset helper\n",
        encoding="utf-8",
    )
    (client_root / "trace2task_human_trace.py").write_text("# recorder\n", encoding="utf-8")
    process_created = threading.Event()

    class BlockingStdout:
        def __init__(self) -> None:
            self.lines: queue.Queue[str | None] = queue.Queue()

        def __iter__(self):
            return self

        def __next__(self) -> str:
            line = self.lines.get(timeout=2)
            if line is None:
                raise StopIteration
            return line

        def readline(self) -> str:
            try:
                return next(self)
            except StopIteration:
                return ""

    class FakeStdin:
        def __init__(self, process) -> None:
            self.process = process
            self.values: list[str] = []

        def write(self, value: str) -> int:
            self.values.append(value)
            if value.strip() == "GO":
                self.process.stdout.lines.put(
                    web_console.WAA_CONTROL_EVENT_PREFIX
                    + json.dumps(
                        {
                            "type": "started",
                            "trace_started_at": "1970-01-01T00:16:40.500+00:00",
                        }
                    )
                    + "\n"
                )
            return len(value)

        def flush(self) -> None:
            return None

    class FakeProcess:
        def __init__(self, command: list[str], **kwargs: object) -> None:
            self.args = command
            self.kwargs = kwargs
            self.stdout = BlockingStdout()
            self.stdin = FakeStdin(self)
            self.returncode: int | None = None
            self.finished = threading.Event()
            session_index = command.index("--session-id")
            self.session_id = command[session_index + 1]
            self.host_run_dir = client_root / "trace2task_recordings" / self.session_id
            self.host_trace_path = self.host_run_dir / "trace.jsonl"
            self.stdout.lines.put(
                web_console.WAA_CONTROL_EVENT_PREFIX
                + json.dumps(
                    {
                        "type": "ready",
                        "waa_task_id": "waa-example",
                        "run_dir": f"/client/trace2task_recordings/{self.session_id}",
                        "reset_receipt": {
                            "status": "verified",
                            "task_id": "waa-example",
                        },
                    }
                )
                + "\n"
            )
            process_created.set()

        def poll(self) -> int | None:
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            if not self.finished.wait(timeout):
                raise TimeoutError("fake WAA recorder did not finish")
            return int(self.returncode or 0)

        def finish(self) -> None:
            self.host_run_dir.mkdir(parents=True)
            self.host_trace_path.write_text('{"seq":0}\n', encoding="utf-8")
            (self.host_run_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "task_id": "waa-narrated",
                        "success": True,
                        "source": "windows_human",
                        "started_at": "2026-08-31T00:00:00+00:00",
                        "stop_reason": "success_marked",
                        "input_event_count": 1,
                    }
                ),
                encoding="utf-8",
            )
            (self.host_run_dir / "reset-receipt.json").write_text(
                json.dumps(
                    {
                        "schema_version": "0.1",
                        "status": "verified",
                        "task_id": "waa-example",
                    }
                ),
                encoding="utf-8",
            )
            self.stdout.lines.put(
                web_console.WAA_CONTROL_EVENT_PREFIX
                + json.dumps(
                    {
                        "type": "completed",
                        "result": {
                            "trace_path": (
                                f"/client/trace2task_recordings/{self.session_id}/trace.jsonl"
                            ),
                            "task_id": "waa-narrated",
                            "success": True,
                            "stop_reason": "success_marked",
                            "input_event_count": 1,
                        },
                    }
                )
                + "\n"
            )
            self.returncode = 0
            self.stdout.lines.put(None)
            self.finished.set()

        def terminate(self) -> None:
            self.returncode = 1
            self.stdout.lines.put(None)
            self.finished.set()

        kill = terminate

    spawned: list[FakeProcess] = []

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        process = FakeProcess(command, **kwargs)
        spawned.append(process)
        return process

    monkeypatch.setattr(web_console.subprocess, "Popen", fake_popen)
    controller = WebConsoleController(tmp_path, runner=lambda *args, **kwargs: FakeResult())

    started = controller.start_waa_recording(
        waa_root=waa_root,
        example_path=example_path,
        task_id="waa-narrated",
        narrated=True,
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )

    assert started["kind"] == "waa_recording"
    assert process_created.wait(2)
    deadline = time.monotonic() + 2
    while controller.get_job(started["job_id"])["status"] != "awaiting_recording_start":
        if time.monotonic() >= deadline:
            raise AssertionError("WAA recording never became ready")
        time.sleep(0.01)

    released = controller.go_waa_recording(
        started["job_id"],
        audio_started_at_epoch_ms=1_000_000.0,
    )
    assert released["status"] == "running"
    assert spawned[0].stdin.values == ["GO\n"]
    assert "--wait-for-go" in spawned[0].args
    assert "--no-task-narration" in spawned[0].args
    assert "-e" in spawned[0].args
    reset_env = next(
        value
        for value in spawned[0].args
        if value.startswith("TRACE2TASK_WAA_RESET_SPEC=")
    )
    assert reset_env.endswith(".json")

    spawned[0].finish()
    finished = controller.wait(started["job_id"], timeout=2)
    assert finished["status"] == "awaiting_narration"
    imported_trace = Path(finished["result"]["trace_path"])
    assert imported_trace.is_relative_to((tmp_path / "runs").resolve())
    assert imported_trace.read_bytes() == spawned[0].host_trace_path.read_bytes()
    assert finished["result"]["audio_start_trace_elapsed_ms"] == -500.0
    imported_receipt = Path(finished["result"]["reset_receipt_path"])
    assert imported_receipt.parent == imported_trace.parent
    assert json.loads(imported_receipt.read_text(encoding="utf-8"))["status"] == (
        "verified"
    )


def test_waa_recording_refuses_a_task_without_a_reset_spec(tmp_path: Path) -> None:
    waa_root = tmp_path / "WindowsAgentArena"
    client_root = waa_root / "src" / "win-arena-container" / "client"
    example_path = client_root / "evaluation_examples_windows" / "example.json"
    example_path.parent.mkdir(parents=True)
    example_path.write_text(
        json.dumps({"id": "uncovered-task", "instruction": "Type hello."}),
        encoding="utf-8",
    )
    (client_root / "trace2task_human_trace.py").write_text(
        "# recorder\n",
        encoding="utf-8",
    )
    controller = WebConsoleController(tmp_path, runner=lambda *args, **kwargs: FakeResult())

    with pytest.raises(FileNotFoundError, match="没有匹配的 reset spec"):
        controller.start_waa_recording(
            waa_root=waa_root,
            example_path=example_path,
            task_id="uncovered",
        )


def test_web_controller_lists_only_reset_ready_waa_tasks(tmp_path: Path) -> None:
    waa_root = tmp_path / "WindowsAgentArena"
    example_root = (
        waa_root
        / "src"
        / "win-arena-container"
        / "client"
        / "evaluation_examples_windows"
        / "examples"
        / "notepad"
    )
    example_root.mkdir(parents=True)
    covered = example_root / "covered.json"
    covered.write_text(
        json.dumps(
            {
                "id": "covered-task",
                "instruction": "Create draft.txt in Documents.",
                "related_apps": ["notepad"],
                "evaluator": {"func": ["exact_match", "compare_text_file"]},
            }
        ),
        encoding="utf-8",
    )
    (example_root / "uncovered.json").write_text(
        json.dumps({"id": "uncovered-task", "instruction": "Ignore me."}),
        encoding="utf-8",
    )
    reset_spec = (
        tmp_path
        / "integrations"
        / "windows_agent_arena"
        / "reset_specs"
        / "notepad.json"
    )
    reset_spec.parent.mkdir(parents=True)
    reset_spec.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "tasks": {
                    "covered-task": {
                        "must_not_exist": [
                            r"C:\Users\Docker\Documents\draft.txt"
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    controller = WebConsoleController(tmp_path, runner=lambda *args, **kwargs: FakeResult())

    tasks = controller.list_waa_tasks(waa_root)

    assert tasks == [
        {
            "id": "covered-task",
            "domain": "notepad",
            "instruction": "Create draft.txt in Documents.",
            "related_apps": ["notepad"],
            "evaluator": ["exact_match", "compare_text_file"],
            "example_path": (
                "evaluation_examples_windows/examples/notepad/covered.json"
            ),
            "reset_spec": str(reset_spec.resolve()),
            "reset_paths": [r"C:\Users\Docker\Documents\draft.txt"],
            "experience_family_id": None,
            "variant_id": None,
            "variant_role": None,
            "recordable": True,
        }
    ]


def test_web_controller_hides_and_rejects_held_out_waa_variants(
    tmp_path: Path,
) -> None:
    waa_root = tmp_path / "WindowsAgentArena"
    client_root = waa_root / "src" / "win-arena-container" / "client"
    example_root = client_root / "evaluation_examples_windows" / "examples" / "notepad"
    example_root.mkdir(parents=True)
    recorder = client_root / "trace2task_human_trace.py"
    recorder.write_text("# recorder\n", encoding="utf-8")
    demonstration = example_root / "demo.json"
    held_out = example_root / "held-out.json"
    for path, task_id, variant_id, role, recordable in (
        (demonstration, "demo-task", "D0", "demonstration", True),
        (held_out, "eval-task", "E1", "held_out_evaluation", False),
    ):
        path.write_text(
            json.dumps(
                {
                    "id": task_id,
                    "instruction": f"Run {variant_id}",
                    "trace2task": {
                        "family_id": "count-token-occurrences",
                        "variant_id": variant_id,
                        "variant_role": role,
                        "recordable": recordable,
                    },
                }
            ),
            encoding="utf-8",
        )
    reset_spec = (
        tmp_path
        / "integrations"
        / "windows_agent_arena"
        / "reset_specs"
        / "notepad.json"
    )
    reset_spec.parent.mkdir(parents=True)
    reset_spec.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "tasks": {
                    task_id: {"must_not_exist": [rf"C:\\Documents\\{task_id}.txt"]}
                    for task_id in ("demo-task", "eval-task")
                },
            }
        ),
        encoding="utf-8",
    )
    controller = WebConsoleController(tmp_path, runner=lambda *args, **kwargs: FakeResult())

    tasks = controller.list_waa_tasks(waa_root)

    assert [task["id"] for task in tasks] == ["demo-task"]
    assert tasks[0]["variant_id"] == "D0"
    assert tasks[0]["experience_family_id"] == "count-token-occurrences"
    with pytest.raises(ValueError, match="held-out"):
        controller.start_waa_recording(
            waa_root=waa_root,
            example_path=held_out,
            task_id="forbidden-evaluation-recording",
        )


def test_waa_recording_reuses_turbo_review_and_narration_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace_path = _write_windows_recording(tmp_path, run_name="waa-recording")

    class FakeTranscriber:
        def transcribe(
            self,
            audio_path: Path,
            *,
            cache_dir: Path,
            initial_prompt: str,
        ) -> LocalTranscription:
            return LocalTranscription(
                transcript="先打开菜单，再选择目标。",
                segments=[{"start_ms": 800.0, "end_ms": 1400.0, "text": "选择目标"}],
                language="zh",
                language_probability=0.99,
                model="turbo",
                device="cuda",
                compute_type="float16",
            )

    controller = WebConsoleController(
        tmp_path,
        runner=lambda *args, **kwargs: FakeResult(),
        narration_transcriber=FakeTranscriber(),
    )
    job = ConsoleJob(
        job_id="waa-narration-job",
        task_path="",
        task_id="WAA 人工讲解",
        instruction="录制 WAA 示范",
        mode="record",
        kind="waa_recording",
        narrated=True,
        status="awaiting_narration",
        result={
            "trace_path": str(trace_path),
            "success": True,
            "audio_start_trace_elapsed_ms": -500.0,
        },
    )
    controller._jobs[job.job_id] = job
    controller._active_job_id = job.job_id
    compiled = threading.Event()
    monkeypatch.setattr(
        controller,
        "_run_recording_compilation",
        lambda *args: compiled.set(),
    )

    transcribed = controller.transcribe_recording_narration(
        job.job_id,
        audio_base64=base64.b64encode(b"webm").decode("ascii"),
        mime_type="audio/webm",
    )
    submitted = controller.submit_recording_narration(
        job.job_id,
        transcript=transcribed["transcription"]["transcript"],
        segments=transcribed["transcription"]["segments"],
        transcription_engine="faster_whisper:turbo",
    )

    assert submitted["status"] == "queued"
    assert compiled.wait(2)
    narration = json.loads(
        (trace_path.parent / "narration.json").read_text(encoding="utf-8")
    )
    assert narration["audio_start_trace_elapsed_ms"] == -500.0
    assert narration["transcription_engine"] == "faster_whisper:turbo"


def test_taskpack_listing_exposes_active_human_guidance(tmp_path: Path) -> None:
    _write_windows_task(tmp_path, semantic=True, guidance=True)
    controller = WebConsoleController(tmp_path, runner=lambda *args, **kwargs: FakeResult())

    guidance = controller.list_taskpacks()[0]["human_guidance"]

    assert guidance["revision"] == 1
    assert guidance["summary"] == "搜索联系人后等待会话稳定，再输入消息。"
    assert guidance["rules"] == [
        {
            "id": "trick-01",
            "scope": {"type": "state", "id": "send_message"},
            "when": "联系人搜索结果已经出现。",
            "prefer": "单击正确联系人并等待输入框出现。",
            "avoid": ["连续重复点击联系人"],
            "replan_when": ["输入框没有出现"],
            "expected_effect": "目标会话稳定打开。",
            "priority": "high",
        }
    ]
    assert guidance["history"] == [
        {
            "revision": 1,
            "parent_revision": None,
            "summary": "搜索联系人后等待会话稳定，再输入消息。",
            "rule_count": 1,
            "rules": guidance["rules"],
            "merge_mode": "legacy_snapshot",
            "operations": [],
            "feedback": "",
            "model": "gpt-5.6-sol",
            "reasoning_effort": "high",
            "created_at": "",
            "confirmed_at": "",
            "is_active": True,
        }
    ]


def test_taskpack_listing_exposes_guidance_fusion_history(tmp_path: Path) -> None:
    task_path = _write_windows_task(tmp_path, semantic=True, guidance=True)
    task_dir = task_path.parent
    first = yaml.safe_load((task_dir / "guidance.yaml").read_text(encoding="utf-8"))
    revision_dir = task_dir / "guidance-revisions"
    revision_dir.mkdir()
    (revision_dir / "revision-0001.yaml").write_text(
        yaml.safe_dump(first, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    second_rule = {
        "id": "trick-0002",
        "scope": {"type": "state", "id": "send_message"},
        "when": "消息已经输入。",
        "prefer": "检查文本后再发送。",
        "avoid": ["发送错误文本"],
        "replan_when": ["文本与指令不一致"],
        "expected_effect": "正确消息被发送。",
        "priority": "high",
    }
    second = {
        **first,
        "schema_version": "0.2",
        "revision": 2,
        "parent_revision": 1,
        "summary": "等待会话稳定并检查消息后发送。",
        "rules": [*first["rules"], second_rule],
        "operations": [
            {
                "operation": "add",
                "target_rule_id": "",
                "result_rule_id": "trick-0002",
            "scope": {"type": "state", "id": "send_message"},
                "reason": "人工反馈要求发送前检查文本。",
            }
        ],
        "source": {"type": "human_feedback", "feedback": "发送前检查一下消息内容。"},
        "review": {"confirmed_at": "2026-08-29T08:00:00+00:00"},
    }
    (revision_dir / "revision-0002.yaml").write_text(
        yaml.safe_dump(second, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    (task_dir / "guidance.yaml").write_text(
        yaml.safe_dump(second, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    task = yaml.safe_load(task_path.read_text(encoding="utf-8"))
    task["human_guidance"] = {
        "path": "guidance.yaml",
        "revision": 2,
        "rule_count": 2,
    }
    task_path.write_text(
        yaml.safe_dump(task, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    controller = WebConsoleController(tmp_path, runner=lambda *args, **kwargs: FakeResult())
    history = controller.list_taskpacks()[0]["human_guidance"]["history"]

    assert [revision["revision"] for revision in history] == [2, 1]
    assert history[0]["merge_mode"] == "incremental"
    assert history[0]["parent_revision"] == 1
    assert history[0]["feedback"] == "发送前检查一下消息内容。"
    assert history[0]["operations"][0] == {
        "operation": "add",
        "target_rule_id": "",
        "result_rule_id": "trick-0002",
        "scope": {"type": "state", "id": "send_message"},
        "reason": "人工反馈要求发送前检查文本。",
    }
    assert history[0]["rule_count"] == 2
    assert history[0]["is_active"] is True
    assert history[1]["merge_mode"] == "legacy_snapshot"
    assert history[1]["rule_count"] == 1
    assert history[1]["is_active"] is False


def test_recording_name_must_be_unique_across_recordings_and_taskpacks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_windows_task(tmp_path, semantic=True)
    _write_windows_recording(tmp_path)
    controller = WebConsoleController(tmp_path, runner=lambda *args, **kwargs: FakeResult())
    monkeypatch.setattr(
        controller,
        "list_windows",
        lambda: [{"handle": 42, "process_name": "Weixin.exe", "title": "微信"}],
    )

    with pytest.raises(ValueError, match="原始录制"):
        controller.start_recording(handle=42, task_id="WECHAT-SEND-EXAMPLE")
    with pytest.raises(ValueError, match="任务经验"):
        controller.start_recording(handle=42, task_id="  WECHAT-EXAMPLE  ")


def test_web_controller_opens_only_local_experience_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_path = _write_windows_task(tmp_path)
    controller = WebConsoleController(tmp_path, runner=lambda *args, **kwargs: FakeResult())
    opened: list[Path] = []
    monkeypatch.setattr(web_console.os, "startfile", opened.append)

    result = controller.open_local(task_path.relative_to(tmp_path).as_posix())

    assert opened == [task_path.parent]
    assert result["opened"] == task_path.parent.relative_to(tmp_path).as_posix()
    with pytest.raises(ValueError, match="runs 或 taskpacks"):
        controller.open_local("README.md")


def test_web_controller_can_retry_compiling_a_saved_recording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace_path = _write_windows_recording(tmp_path)
    calls: list[tuple[Path, Path]] = []
    semantic_calls: list[tuple[Path, str, str]] = []

    def compiler(source: Path, output: Path) -> FakeCompilation:
        calls.append((source, output))
        task_path = output / "generated" / "task.yaml"
        task_path.parent.mkdir(parents=True)
        task_path.write_text("id: generated\n", encoding="utf-8")
        return FakeCompilation(task_path=str(task_path))

    def semantic_compiler(
        task_path: Path,
        *,
        model: str,
        reasoning_effort: str,
    ) -> FakeSemanticCompilation:
        semantic_calls.append((task_path, model, reasoning_effort))
        return FakeSemanticCompilation(experience_path=str(task_path.with_name("experience.yaml")))

    monkeypatch.setattr(web_console, "compile_trace", compiler)
    monkeypatch.setattr(
        web_console,
        "compile_windows_semantic_experience",
        semantic_compiler,
    )
    controller = WebConsoleController(tmp_path, runner=lambda *args, **kwargs: FakeResult())

    result = controller.compile_recording(
        trace_path.relative_to(tmp_path).as_posix(),
        model="gpt-5.6-sol",
        reasoning_effort="medium",
    )

    assert result["review_status"] == "draft"
    assert calls == [
        (
            trace_path,
            tmp_path / "taskpacks" / "generated" / "web",
        )
    ]
    assert result["semantic_compilation"]["status"] == "completed"
    assert semantic_calls == [
        (
            tmp_path / "taskpacks" / "generated" / "web" / "generated" / "task.yaml",
            "gpt-5.6-sol",
            "medium",
        )
    ]
    with pytest.raises(ValueError, match="runs 目录"):
        controller.compile_recording("README.md")


def test_waa_narration_pair_preserves_trace_and_compiler_variants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace_path = _write_windows_recording(tmp_path, task_id="waa-pair-example")
    compile_calls: list[tuple[Path, str]] = []
    semantic_calls: list[tuple[Path, bool]] = []

    def compiler(
        source: Path,
        output: Path,
        *,
        task_id_override: str,
    ) -> FakeCompilation:
        compile_calls.append((source, task_id_override))
        task_dir = output / f"pair-{len(compile_calls)}"
        reference_dir = task_dir / "reference"
        reference_dir.mkdir(parents=True)
        (reference_dir / "trace.jsonl").write_bytes(source.read_bytes())
        task_path = task_dir / "task.yaml"
        task_path.write_text(
            yaml.safe_dump(
                {"id": task_id_override, "experience": {}},
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        return FakeCompilation(task_path=str(task_path))

    def semantic_compiler(
        task_path: Path,
        *,
        model: str,
        reasoning_effort: str,
        use_narration: bool,
    ) -> FakeSemanticCompilation:
        assert model == "gpt-5.6-sol"
        assert reasoning_effort == "high"
        semantic_calls.append((task_path, use_narration))
        return FakeSemanticCompilation(
            experience_path=str(task_path.with_name("experience.yaml"))
        )

    monkeypatch.setattr(web_console, "compile_trace", compiler)
    monkeypatch.setattr(
        web_console,
        "compile_windows_semantic_experience",
        semantic_compiler,
    )
    controller = WebConsoleController(tmp_path, runner=lambda *args, **kwargs: FakeResult())

    result = controller._compile_waa_narration_pair(
        trace_path,
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )

    compiled_path = Path(result["variants"]["compiled"]["task_path"])
    narrated_path = Path(result["variants"]["narrated_compiled"]["task_path"])
    compiled_task = yaml.safe_load(compiled_path.read_text(encoding="utf-8"))
    narrated_task = yaml.safe_load(narrated_path.read_text(encoding="utf-8"))
    compiled_trace = compiled_path.parent / "reference" / "trace.jsonl"
    narrated_trace = narrated_path.parent / "reference" / "trace.jsonl"

    assert [source for source, _task_id in compile_calls] == [trace_path, trace_path]
    assert semantic_calls == [(compiled_path, False), (narrated_path, True)]
    assert compiled_trace.read_bytes() == narrated_trace.read_bytes() == trace_path.read_bytes()
    assert compiled_task["id"] != narrated_task["id"]
    assert compiled_task["experience"]["family_id"] == result["family_id"]
    assert narrated_task["experience"]["family_id"] == result["family_id"]
    for variant in ("compiled", "narrated_compiled"):
        snapshot = result["variants"][variant]["automatic_snapshot"]
        snapshot_task = Path(snapshot["task_path"])
        assert snapshot_task.is_file()
        assert snapshot_task.is_relative_to(
            (tmp_path / "evaluations" / "compiler-snapshots").resolve()
        )
        assert snapshot["fresh_taskpack"] is True
        assert json.loads(
            (Path(snapshot["taskpack_root"]).parent / "snapshot.json").read_text(
                encoding="utf-8"
            )
        )["tree_sha256"] == snapshot["tree_sha256"]
        source_task = Path(result["variants"][variant]["task_path"])
        marker = json.loads(
            source_task.with_name(".automatic-compiler-snapshot.json").read_text(
                encoding="utf-8"
            )
        )
        frozen_before = snapshot_task.read_bytes()
        source_task.write_text("id: human-reviewed-later\n", encoding="utf-8")
        assert snapshot_task.read_bytes() == frozen_before
        assert marker["task_path"] == str(snapshot_task)


@pytest.mark.parametrize(
    ("compiler_failure", "expected_category"),
    [
        (
            RuntimeError(
                "Codex did not complete its model response or network reconnect within 300 seconds"
            ),
            "codex_connectivity",
        ),
        (
            CodexTurnTimeoutError(
                "response_in_progress_timeout",
                "Codex response stopped making progress",
            ),
            "response_in_progress_timeout",
        ),
    ],
)
def test_waa_narration_pair_stops_on_retryable_failure_and_reuses_trace_on_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    compiler_failure: Exception,
    expected_category: str,
) -> None:
    trace_path = _write_windows_recording(tmp_path, task_id="waa-retry-example")
    semantic_calls: list[tuple[Path, bool]] = []
    deterministic_calls: list[str] = []

    def deterministic_compiler(
        source: Path,
        output: Path,
        *,
        task_id_override: str,
    ) -> FakeCompilation:
        deterministic_calls.append(task_id_override)
        task_path = _write_windows_task(output / f"variant-{len(deterministic_calls)}")
        task = yaml.safe_load(task_path.read_text(encoding="utf-8"))
        task["id"] = task_id_override
        task_path.write_text(
            yaml.safe_dump(task, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        (task_path.parent / "compiler-report.json").write_text(
            json.dumps({"source": {"trace": str(source)}}),
            encoding="utf-8",
        )
        return FakeCompilation(task_path=str(task_path))

    def unavailable_compiler(
        task_path: Path,
        *,
        model: str,
        reasoning_effort: str,
        use_narration: bool,
    ) -> FakeSemanticCompilation:
        semantic_calls.append((task_path, use_narration))
        raise compiler_failure

    monkeypatch.setattr(
        web_console,
        "compile_windows_semantic_experience",
        unavailable_compiler,
    )
    monkeypatch.setattr(web_console, "compile_trace", deterministic_compiler)
    controller = WebConsoleController(tmp_path, runner=lambda *args, **kwargs: FakeResult())

    failed = controller._compile_waa_narration_pair(
        trace_path,
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )

    assert failed["status"] == "failed"
    assert failed["variants"]["compiled"]["status"] == "failed"
    assert failed["variants"]["compiled"]["failure_category"] == expected_category
    assert failed["variants"]["compiled"]["retryable"] is True
    assert failed["variants"]["narrated_compiled"]["status"] == "skipped", failed[
        "variants"
    ]
    assert [use_narration for _path, use_narration in semantic_calls] == [False]
    assert deterministic_calls == ["waa-retry-example · 纯Trace"]

    def available_compiler(
        task_path: Path,
        *,
        model: str,
        reasoning_effort: str,
        use_narration: bool,
    ) -> FakeSemanticCompilation:
        semantic_calls.append((task_path, use_narration))
        return FakeSemanticCompilation(
            experience_path=str(task_path.with_name("experience.yaml"))
        )

    monkeypatch.setattr(
        web_console,
        "compile_windows_semantic_experience",
        available_compiler,
    )
    retried = controller._compile_waa_narration_pair(
        trace_path,
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )

    assert retried["status"] == "completed"
    assert retried["variants"]["compiled"]["deterministic_compilation"][
        "reused_taskpack"
    ] is True
    assert retried["variants"]["narrated_compiled"]["deterministic_compilation"][
        "reused_taskpack"
    ] is False
    assert [use_narration for _path, use_narration in semantic_calls] == [False, False, True]
    assert deterministic_calls == [
        "waa-retry-example · 纯Trace",
        "waa-retry-example · 人工讲解",
    ]


def test_waa_narration_pair_preflight_failure_skips_both_variants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace_path = _write_windows_recording(tmp_path, task_id="waa-preflight-example")
    monkeypatch.setattr(
        web_console,
        "probe_codex_compiler_connection",
        lambda **kwargs: (_ for _ in ()).throw(
            RuntimeError(
                "Codex did not complete its model response or network reconnect within 45 seconds"
            )
        ),
    )
    monkeypatch.setattr(
        web_console,
        "compile_trace",
        lambda *args, **kwargs: pytest.fail("preflight failure must stop deterministic compile"),
    )
    controller = WebConsoleController(tmp_path, runner=lambda *args, **kwargs: FakeResult())

    result = controller._compile_waa_narration_pair(
        trace_path,
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )

    assert result["status"] == "failed"
    assert result["compiler_preflight"]["status"] == "failed"
    assert {variant["status"] for variant in result["variants"].values()} == {"skipped"}


def test_web_compilation_job_gives_immediate_feedback_and_rejects_duplicates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace_path = _write_windows_recording(tmp_path)
    controller = WebConsoleController(tmp_path, runner=lambda *args, **kwargs: FakeResult())
    started = threading.Event()
    release = threading.Event()

    def compile_bundle(
        source: Path,
        *,
        model: str,
        reasoning_effort: str,
        status_callback: object,
    ) -> dict[str, object]:
        assert source == trace_path
        assert model == "gpt-5.6-sol"
        assert reasoning_effort == "high"
        assert callable(status_callback)
        status_callback("Compiler Agent 正在分析。")
        started.set()
        assert release.wait(2)
        return {
            "semantic_compilation": {"status": "completed"},
            "task_path": "taskpacks/generated/example/task.yaml",
        }

    monkeypatch.setattr(controller, "_compile_trace_bundle", compile_bundle)

    job = controller.start_compilation(trace_path.relative_to(tmp_path).as_posix())
    assert started.wait(2)
    active = controller.get_job(job["job_id"])

    assert active["kind"] == "compilation"
    assert active["status"] == "running"
    assert "编译请求已接收" in active["logs"][0]
    assert "Compiler Agent 正在分析。" in active["logs"]
    with pytest.raises(RuntimeError, match="已有任务正在运行"):
        controller.start_compilation(trace_path.relative_to(tmp_path).as_posix())

    release.set()
    completed = controller.wait(job["job_id"])
    assert completed["status"] == "completed"
    assert completed["result"]["semantic_compilation"]["status"] == "completed"


def test_web_retry_routes_a_narrated_waa_recording_to_both_compiler_variants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace_path = _write_windows_recording(tmp_path, task_id="waa-retry-route")
    metadata_path = trace_path.with_name("metadata.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["waa_task_id"] = "waa-task-id"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    trace_path.with_name("narration.json").write_text(
        json.dumps({"transcript": "human narration", "segments": []}),
        encoding="utf-8",
    )
    calls: list[Path] = []
    controller = WebConsoleController(tmp_path, runner=lambda *args, **kwargs: FakeResult())

    def compile_pair(source: Path, **kwargs: Any) -> dict[str, Any]:
        calls.append(source)
        return {
            "status": "completed",
            "source_trace": str(source),
            "variants": {
                "compiled": {"status": "completed"},
                "narrated_compiled": {"status": "completed"},
            },
        }

    monkeypatch.setattr(controller, "_compile_waa_narration_pair", compile_pair)
    monkeypatch.setattr(
        controller,
        "_compile_trace_bundle",
        lambda *args, **kwargs: pytest.fail("WAA narrated retry used the single compiler"),
    )

    started = controller.start_compilation(trace_path.relative_to(tmp_path).as_posix())
    completed = controller.wait(started["job_id"])

    assert completed["status"] == "completed"
    assert calls == [trace_path]
    assert set(completed["result"]["variants"]) == {"compiled", "narrated_compiled"}


def test_recompiling_same_trace_reuses_latest_taskpack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace_path = _write_windows_recording(tmp_path)
    task_path = _write_windows_task(tmp_path, confirmed=False)
    (task_path.parent / "compiler-report.json").write_text(
        json.dumps({"source": {"trace": str(trace_path)}}),
        encoding="utf-8",
    )
    semantic_calls: list[Path] = []

    def semantic_compiler(
        existing_task: Path,
        *,
        model: str,
        reasoning_effort: str,
    ) -> FakeSemanticCompilation:
        semantic_calls.append(existing_task)
        return FakeSemanticCompilation(
            experience_path=str(existing_task.with_name("experience.yaml"))
        )

    monkeypatch.setattr(
        web_console,
        "compile_windows_semantic_experience",
        semantic_compiler,
    )
    monkeypatch.setattr(
        web_console,
        "compile_trace",
        lambda *args, **kwargs: pytest.fail("deterministic compiler created a duplicate"),
    )
    controller = WebConsoleController(tmp_path, runner=lambda *args, **kwargs: FakeResult())

    result = controller.compile_recording(trace_path.relative_to(tmp_path).as_posix())

    assert result["reused_taskpack"] is True
    assert Path(result["task_path"]) == task_path
    assert semantic_calls == [task_path]


def test_compiling_a_different_trace_rejects_duplicate_task_name(tmp_path: Path) -> None:
    _write_windows_task(tmp_path)
    trace_path = _write_windows_recording(tmp_path, task_id="wechat-example")
    controller = WebConsoleController(tmp_path, runner=lambda *args, **kwargs: FakeResult())

    with pytest.raises(ValueError, match="同名任务经验"):
        controller.compile_recording(trace_path.relative_to(tmp_path).as_posix())


def test_same_named_recordings_are_distinguished_and_deleted_independently(
    tmp_path: Path,
) -> None:
    first = _write_windows_recording(
        tmp_path,
        run_name="external-one",
        task_id="external-daily",
        started_at="2026-08-27T17:39:27+00:00",
    )
    second = _write_windows_recording(
        tmp_path,
        run_name="external-two",
        task_id="external-daily",
        started_at="2026-08-27T17:48:48+00:00",
    )
    controller = WebConsoleController(tmp_path, runner=lambda *args, **kwargs: FakeResult())

    before = controller.list_recordings()
    deleted = controller.delete_recording(first.relative_to(tmp_path).as_posix())
    after = controller.list_recordings()

    assert [item["created_at"] for item in before] == [
        "2026-08-27T17:48:48+00:00",
        "2026-08-27T17:39:27+00:00",
    ]
    assert deleted["recoverable"] is True
    assert len(after) == 1
    assert after[0]["trace_path"] == second.relative_to(tmp_path).as_posix()


def test_locked_recording_is_copied_to_trash_and_hidden_until_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace_path = _write_windows_recording(
        tmp_path,
        run_name="locked-external",
        task_id="external-daily",
    )
    source_dir = trace_path.parent.resolve()
    original_replace = Path.replace
    original_rmtree = web_console.shutil.rmtree

    def locked_replace(path: Path, destination: Path) -> Path:
        if path.resolve() == source_dir:
            raise PermissionError(5, "directory is in use")
        return original_replace(path, destination)

    def locked_rmtree(path: object, *args: object, **kwargs: object) -> None:
        if Path(path).resolve() == source_dir:
            raise PermissionError(5, "directory is in use")
        original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(Path, "replace", locked_replace)
    monkeypatch.setattr(web_console.shutil, "rmtree", locked_rmtree)
    controller = WebConsoleController(tmp_path, runner=lambda *args, **kwargs: FakeResult())

    deleted = controller.delete_recording(trace_path.relative_to(tmp_path).as_posix())

    assert deleted["deleted"] is True
    assert deleted["pending_cleanup"] is True
    assert (tmp_path / deleted["trash_path"] / "trace.jsonl").is_file()
    assert (source_dir / ".trace2task-deleted.json").is_file()
    assert controller.list_recordings() == []

    monkeypatch.undo()
    WebConsoleController(tmp_path, runner=lambda *args, **kwargs: FakeResult())
    assert not source_dir.exists()


def test_web_deletes_local_assets_recoverably_and_rejects_outside_paths(
    tmp_path: Path,
) -> None:
    task_path = _write_windows_task(tmp_path)
    trace_path = _write_windows_recording(tmp_path)
    candidate_dir = tmp_path / "runs" / "candidates" / "candidate-example"
    candidate_dir.mkdir(parents=True)
    (candidate_dir / "candidate.yaml").write_text(
        yaml.safe_dump(
            {
                "status": "pending_review",
                "candidate_id": "candidate-example",
                "task_id": "wechat-example",
            }
        ),
        encoding="utf-8",
    )
    controller = WebConsoleController(tmp_path, runner=lambda *args, **kwargs: FakeResult())

    task_result = controller.delete_taskpack(task_path.relative_to(tmp_path).as_posix())
    recording_result = controller.delete_recording(
        trace_path.relative_to(tmp_path).as_posix()
    )
    candidate_result = controller.delete_candidate(
        candidate_dir.relative_to(tmp_path).as_posix()
    )

    assert not task_path.parent.exists()
    assert not trace_path.parent.exists()
    assert not candidate_dir.exists()
    for result in (task_result, recording_result, candidate_result):
        assert result["recoverable"] is True
        assert (tmp_path / result["trash_path"]).is_dir()
    assert controller.list_taskpacks() == []
    assert controller.list_recordings() == []
    assert controller.list_candidates() == []
    with pytest.raises(ValueError):
        controller.delete_taskpack("README.md")
    with pytest.raises(ValueError):
        controller.delete_recording("README.md")
    with pytest.raises(ValueError):
        controller.delete_candidate("runs")


def test_human_guidance_is_deleted_without_deleting_the_task_or_trace(
    tmp_path: Path,
) -> None:
    task_path = _write_windows_task(tmp_path, semantic=True, guidance=True)
    task_dir = task_path.parent
    revisions = task_dir / "guidance-revisions"
    revisions.mkdir()
    shutil.copy2(task_dir / "guidance.yaml", revisions / "revision-0001.yaml")
    demonstration_before = (task_dir / "demonstration.json").read_bytes()
    experience_before = (task_dir / "experience.yaml").read_bytes()
    controller = WebConsoleController(
        tmp_path,
        runner=lambda *args, **kwargs: FakeResult(),
    )

    result = controller.delete_human_guidance(
        task_path.relative_to(tmp_path).as_posix()
    )

    assert result["kind"] == "human_guidance"
    assert result["recoverable"] is True
    assert task_path.is_file()
    assert (task_dir / "demonstration.json").read_bytes() == demonstration_before
    assert (task_dir / "experience.yaml").read_bytes() == experience_before
    assert not (task_dir / "guidance.yaml").exists()
    assert not revisions.exists()
    trash = tmp_path / result["trash_path"]
    assert (trash / "guidance.yaml").is_file()
    assert (trash / "guidance-revisions" / "revision-0001.yaml").is_file()
    assert (trash / "restore.json").is_file()
    listed = controller.list_taskpacks()
    assert len(listed) == 1
    assert listed[0]["human_guidance"] is None


def test_web_server_serves_console_state_and_job_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_windows_task(tmp_path, semantic=True)
    controller = WebConsoleController(tmp_path, runner=lambda *args, **kwargs: FakeResult())
    summary_updates: list[tuple[str, str]] = []

    def update_summary(path: str, summary: str) -> dict[str, object]:
        summary_updates.append((path, summary))
        return {"status": "draft", "summary": summary}

    monkeypatch.setattr(controller, "update_candidate_revision_summary", update_summary)
    monkeypatch.setattr(
        controller,
        "list_waa_tasks",
        lambda root: [{"id": "waa-task", "example_path": "example.json"}],
    )
    server = create_web_server(tmp_path, port=0, controller=controller)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(f"{base}/", timeout=5) as response:
            html = response.read().decode("utf-8")
        with opener.open(f"{base}/app.js", timeout=5) as response:
            javascript = response.read().decode("utf-8")
        with opener.open(f"{base}/api/state", timeout=5) as response:
            state = json.loads(response.read())
        with opener.open(
            f"{base}/api/waa/tasks?root={quote('D:/WAA')}", timeout=5
        ) as response:
            waa_tasks = json.loads(response.read())
        evidence_path = state["taskpacks"][0]["semantic_experience"]["stages"][0][
            "evidence_frame"
        ]
        with opener.open(
            f"{base}/api/local-image?path={quote(evidence_path)}", timeout=5
        ) as response:
            evidence_bytes = response.read()
        route_request = Request(
            f"{base}/api/experience-route",
            data=json.dumps({"instruction": "给文件传输助手发消息"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with opener.open(route_request, timeout=5) as response:
            route = json.loads(response.read())
        payload = json.dumps(
            {
                "task_path": state["taskpacks"][0]["path"],
                "instruction": "给文件传输助手发送测试消息",
                "mode": "plan",
                "model": "gpt-5.6-luna",
                "reasoning_effort": "medium",
            }
        ).encode()
        request = Request(
            f"{base}/api/jobs",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with opener.open(request, timeout=5) as response:
            job = json.loads(response.read())
        summary_request = Request(
            f"{base}/api/candidates/revisions/summary",
            data=json.dumps(
                {"path": "runs/candidates/example", "summary": "人工微调后的摘要"}
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with opener.open(summary_request, timeout=5) as response:
            summary_result = json.loads(response.read())
        completed = controller.wait(job["job_id"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert "用一句话，调用一次示范经验" in html
    assert "录制新经验" in html
    assert "同时录制人工讲解" in html
    assert "放弃本次录制" in html
    assert '<select id="waa-example">' in html
    assert "经验与录制" in html
    assert "执行 Agent 模型" in html
    assert "后台执行 · 可同时使用电脑" in html
    assert "经验模型设置（教师 / 教练）" in html
    assert "任务经验详情" in html
    assert "经验摘要（确认前可编辑）" in javascript
    assert "删除人工反馈经验" in javascript
    assert "删除整个任务" in javascript
    assert "#task/" in javascript
    assert "查看详情" in javascript
    assert "运行 Agent 实际读取什么" in javascript
    assert "Agent 当前使用" in javascript
    assert "Agent 使用对象" in javascript
    assert "历史存档，Agent 不读取" in javascript
    assert "重新规划条件 replan_when" in javascript
    assert "系统执行策略 · 多动作规划" in javascript
    assert "所有任务共用" in javascript
    assert "/api/taskpacks/guidance/delete" in javascript
    assert "/api/recordings/narration" in javascript
    assert "/api/recordings/transcribe" in javascript
    assert "/api/waa/recordings" in javascript
    assert "/api/waa/recordings/go" in javascript
    assert "/api/waa/tasks" in javascript
    assert "/api/transcribe" in javascript
    assert "语音输入" in javascript
    assert "不保存录音" in javascript
    assert "查看轨迹与截图" in javascript
    assert "人工审查视图" in javascript
    assert "WAA 人工运行反馈" in javascript
    assert state["taskpacks"][0]["process_name"] == "Weixin.exe"
    assert evidence_bytes.startswith(b"\x89PNG")
    assert state["recordings"] == []
    assert state["candidates"] == []
    assert state["capabilities"] == {
        "incremental_guidance": True,
        "guidance_history": True,
        "narrated_trace": True,
        "local_whisper_turbo": True,
        "cycle_completion": True,
        "narration_claim_audit": True,
        "coordinate_isolation": True,
        "experience_family_inheritance": True,
        "directed_task_graph": True,
        "task_model_revision": True,
        "task_model_history": True,
        "graph_native_guidance": True,
        "independent_guidance_delete": True,
        "system_multi_action_planning": True,
        "voice_dictation": True,
        "waa_narrated_recording": True,
        "waa_task_catalog": True,
        }
    assert waa_tasks["tasks"] == [
        {"id": "waa-task", "example_path": "example.json"}
    ]
    assert route["task_id"] == "wechat-example"
    assert summary_result["summary"] == "人工微调后的摘要"
    assert summary_updates == [("runs/candidates/example", "人工微调后的摘要")]
    assert state["agent_options"]["models"] == [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
    ]
    assert state["agent_options"]["defaults"] == {
        "model": "gpt-5.6-terra",
        "reasoning_effort": "low",
    }
    assert state["agent_options"]["compiler_defaults"] == {
        "model": "gpt-5.6-sol",
        "reasoning_effort": "high",
    }
    assert state["agent_options"]["revision_defaults"] == {
        "model": "gpt-5.6-sol",
        "reasoning_effort": "high",
    }
    assert completed["status"] == "completed"
    assert completed["model"] == "gpt-5.6-luna"
    assert completed["reasoning_effort"] == "medium"
