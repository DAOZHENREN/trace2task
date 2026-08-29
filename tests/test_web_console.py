from __future__ import annotations

import base64
import json
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

import pygame
import pytest
import yaml

from trace2task import web_console
from trace2task.speech_transcription import LocalTranscription
from trace2task.web_console import ConsoleJob, WebConsoleController, create_web_server


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


def test_taskpack_listing_exposes_active_human_guidance(tmp_path: Path) -> None:
    _write_windows_task(tmp_path, semantic=True, guidance=True)
    controller = WebConsoleController(tmp_path, runner=lambda *args, **kwargs: FakeResult())

    guidance = controller.list_taskpacks()[0]["human_guidance"]

    assert guidance["revision"] == 1
    assert guidance["summary"] == "搜索联系人后等待会话稳定，再输入消息。"
    assert guidance["rules"] == [
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
        "stage_id": "send_message",
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
                "stage_id": "send_message",
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
        "stage_id": "send_message",
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
        return FakeCompilation(task_path=str(output / "generated" / "task.yaml"))

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
    server = create_web_server(tmp_path, port=0, controller=controller)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urlopen(f"{base}/", timeout=2) as response:
            html = response.read().decode("utf-8")
        with urlopen(f"{base}/app.js", timeout=2) as response:
            javascript = response.read().decode("utf-8")
        with urlopen(f"{base}/api/state", timeout=2) as response:
            state = json.loads(response.read())
        evidence_path = state["taskpacks"][0]["semantic_experience"]["stages"][0][
            "evidence_frame"
        ]
        with urlopen(
            f"{base}/api/local-image?path={quote(evidence_path)}", timeout=2
        ) as response:
            evidence_bytes = response.read()
        route_request = Request(
            f"{base}/api/experience-route",
            data=json.dumps({"instruction": "给文件传输助手发消息"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(route_request, timeout=2) as response:
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
        with urlopen(request, timeout=2) as response:
            job = json.loads(response.read())
        summary_request = Request(
            f"{base}/api/candidates/revisions/summary",
            data=json.dumps(
                {"path": "runs/candidates/example", "summary": "人工微调后的摘要"}
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(summary_request, timeout=2) as response:
            summary_result = json.loads(response.read())
        completed = controller.wait(job["job_id"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert "用一句话，调用一次示范经验" in html
    assert "录制新经验" in html
    assert "同时录制人工讲解" in html
    assert "经验与录制" in html
    assert "执行 Agent 模型" in html
    assert "经验编译模型（教师）" in html
    assert "后台执行 · 可同时使用电脑" in html
    assert "经验修订模型（教练）" in html
    assert "经验摘要（确认前可编辑）" in javascript
    assert "/api/recordings/narration" in javascript
    assert "/api/recordings/transcribe" in javascript
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
    }
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
