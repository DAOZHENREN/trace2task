"""UI summaries must share a shape without exposing image bytes or credentials."""

from pathlib import Path

from trace2task.io_audit import IOAudit
from trace2task.model_io_projection import project_model_io


def test_api_round_redacts_image_and_preserves_messages_and_receipts(tmp_path: Path):
    audit = IOAudit(tmp_path)
    (tmp_path / "frames").mkdir()
    (tmp_path / "frames" / "0000.png").write_bytes(b"frame")
    audit.record(
        "model_request",
        payload={
            "model": "vision-test",
            "messages": [
                {"role": "system", "content": "system instructions"},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Open Notepad"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,SECRET"}},
                    ],
                },
            ],
        },
    )
    audit.record(
        "model_response",
        payload={
            "choices": [{"finish_reason": "stop", "message": {"content": '{"actions": []}'}}],
            "usage": {"prompt_tokens": 120},
        },
    )
    audit.record("executor_result", result={"effect": "unverifiable", "elapsed_ms": 23})

    rounds = project_model_io(tmp_path)
    assert len(rounds) == 1
    assert rounds[0]["input"]["messages"][0]["content"] == "system instructions"
    assert rounds[0]["input"]["messages"][1]["content"][1]["image_url"]["url"].startswith("[image")
    assert rounds[0]["raw_output"] == '{"actions": []}'
    assert Path(rounds[0]["screenshot"]).read_bytes() == b"frame"
    assert rounds[0]["execution"]["receipts"][0]["effect"] == "unverifiable"
    assert rounds[0]["model_roundtrip_ms"] is not None
    assert "SECRET" not in str(rounds)


def test_codex_round_uses_archived_image_and_only_final_answer(tmp_path: Path):
    image = tmp_path / "screen.png"
    image.write_bytes(b"fake image")
    audit = IOAudit(tmp_path)
    audit.record("codex_request", payload={"method": "thread/start", "params": {}})
    audit.record(
        "codex_request",
        payload={
            "method": "turn/start",
            "params": {
                "model": "test",
                "effort": "low",
                "input": [
                    {"type": "text", "text": "current task"},
                    {"type": "localImage", "path": str(image)},
                ],
            },
        },
    )
    audit.record(
        "codex_response",
        payload={
            "method": "turn/completed",
            "params": {
                "turn": {
                    "status": "completed",
                    "items": [
                        {"type": "agentMessage", "phase": "commentary", "text": "not final"},
                        {"type": "agentMessage", "phase": "final_answer", "text": "click"},
                    ],
                }
            },
        },
    )

    rounds = project_model_io(tmp_path)
    assert len(rounds) == 1
    assert rounds[0]["input"]["messages"] == [{"role": "user", "content": "current task"}]
    assert Path(rounds[0]["screenshot"]).read_bytes() == b"fake image"
    assert rounds[0]["raw_output"] == "click"
