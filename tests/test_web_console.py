from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen

import pygame
import pytest
import yaml

from trace2task import web_console
from trace2task.web_console import WebConsoleController, create_web_server


def _write_windows_task(root: Path, *, confirmed: bool = True) -> Path:
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
    task_path = task_dir / "task.yaml"
    task_path.write_text(yaml.safe_dump(task, allow_unicode=True), encoding="utf-8")
    return task_path


def _write_windows_recording(root: Path) -> Path:
    run_dir = root / "runs" / "recording-example"
    run_dir.mkdir(parents=True)
    trace_path = run_dir / "trace.jsonl"
    trace_path.write_text('{"seq": 0}\n', encoding="utf-8")
    (run_dir / "metadata.json").write_text(
        json.dumps(
            {
                "source": "windows_human",
                "task_id": "wechat-send-example",
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


@dataclass(frozen=True)
class FakeResult:
    stop_reason: str = "dry_run_plan_only"
    proposed_actions: tuple[str, ...] = ("click",)


@dataclass(frozen=True)
class FakeCompilation:
    task_path: str
    review_status: str = "draft"


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
            "process_name": "Weixin.exe",
            "title": "微信",
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

    def compiler(source: Path, output: Path) -> FakeCompilation:
        calls.append((source, output))
        return FakeCompilation(task_path=str(output / "generated" / "task.yaml"))

    monkeypatch.setattr(web_console, "compile_trace", compiler)
    controller = WebConsoleController(tmp_path, runner=lambda *args, **kwargs: FakeResult())

    result = controller.compile_recording(trace_path.relative_to(tmp_path).as_posix())

    assert result["review_status"] == "draft"
    assert calls == [
        (
            trace_path,
            tmp_path / "taskpacks" / "generated" / "web",
        )
    ]
    with pytest.raises(ValueError, match="runs 目录"):
        controller.compile_recording("README.md")


def test_web_server_serves_console_state_and_job_api(tmp_path: Path) -> None:
    _write_windows_task(tmp_path)
    controller = WebConsoleController(tmp_path, runner=lambda *args, **kwargs: FakeResult())
    server = create_web_server(tmp_path, port=0, controller=controller)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urlopen(f"{base}/", timeout=2) as response:
            html = response.read().decode("utf-8")
        with urlopen(f"{base}/api/state", timeout=2) as response:
            state = json.loads(response.read())
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
        completed = controller.wait(job["job_id"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert "用一句话，调用一次示范经验" in html
    assert "录制新经验" in html
    assert "经验与录制" in html
    assert state["taskpacks"][0]["process_name"] == "Weixin.exe"
    assert state["recordings"] == []
    assert state["agent_options"]["models"] == [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
    ]
    assert state["agent_options"]["defaults"] == {
        "model": "gpt-5.6-terra",
        "reasoning_effort": "low",
    }
    assert completed["status"] == "completed"
    assert completed["model"] == "gpt-5.6-luna"
    assert completed["reasoning_effort"] == "medium"
