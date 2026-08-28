from __future__ import annotations

import pytest

from trace2task.experience import route_experience


def _task(
    task_id: str,
    *,
    path: str,
    process_name: str,
    examples: list[str],
) -> dict[str, object]:
    return {
        "path": path,
        "task_id": task_id,
        "instruction": "Follow the reviewed workflow.",
        "confirmed": True,
        "process_name": process_name,
        "actions": ["click", "type_text", "press_key"],
        "experience_intent": task_id,
        "experience_examples": examples,
    }


def test_route_experience_prefers_the_trace_matching_the_instruction() -> None:
    match = route_experience(
        "给彭瀚发消息，问他能不能给我带饭",
        [
            _task(
                "external-daily",
                path="taskpacks/external/task.yaml",
                process_name="Weixin.exe",
                examples=["完成每日流程"],
            ),
            _task(
                "微信发消息",
                path="taskpacks/wechat/task.yaml",
                process_name="Weixin.exe",
                examples=["给联系人发消息"],
            ),
        ],
    )

    assert match.task_id == "微信发消息"
    assert match.task_path == "taskpacks/wechat/task.yaml"
    assert match.confidence >= 0.8
    assert "选择 Trace“微信发消息”" in match.reason


def test_route_experience_refuses_unknown_or_ambiguous_instructions() -> None:
    message_task = _task(
        "微信发消息",
        path="taskpacks/wechat/task.yaml",
        process_name="Weixin.exe",
        examples=["给联系人发消息"],
    )
    with pytest.raises(ValueError, match="足够匹配"):
        route_experience("整理桌面上的财务表格", [message_task])

    duplicate = {**message_task, "path": "taskpacks/wechat-copy/task.yaml"}
    with pytest.raises(ValueError, match="同样接近"):
        route_experience("在微信里发消息", [message_task, duplicate])


def test_route_experience_handles_inserted_words_and_small_asr_errors() -> None:
    fgo = {
        "path": "taskpacks/fgo/task.yaml",
        "task_id": "FGO刷副本",
        "instruction": "Repeat the reviewed workflow.",
        "confirmed": True,
        "experience_intent": "FGO刷副本",
        "experience_examples": ["FGO刷副本"],
        "process_name": "MuMuNxDevice.exe",
        "actions": ["click"],
    }

    inserted = route_experience("帮我刷一次FGO副本", [fgo])
    asr_variant = route_experience("F9刷副本", [fgo])

    assert inserted.task_id == "FGO刷副本"
    assert asr_variant.task_id == "FGO刷副本"
    assert any(term.startswith("模糊表达:") for term in inserted.matched_terms)
