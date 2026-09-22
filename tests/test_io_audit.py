import base64
import json
from types import SimpleNamespace

import pytest

from trace2task.actions import ActionCall
from trace2task.execution_runtime import ExecutionRuntime
from trace2task.io_audit import IOAudit
from trace2task.model_api import ModelAPIConfig, ModelAPISession


def read_events(tmp_path):
    return [json.loads(line) for line in (tmp_path / "io-audit/events.jsonl").read_text(encoding="utf-8").splitlines()]


def test_api_audit_captures_system_schema_images_history_and_response(tmp_path):
    captured = []
    response = {"choices": [{"finish_reason": "stop", "message": {
        "content": '{"actions": []}', "reasoning_content": "provider explanation"}}],
        "usage": {"total_tokens": 123}}

    def requester(config, payload):
        captured.append(json.loads(json.dumps(payload)))
        return response

    session = ModelAPISession(ModelAPIConfig(api_key="private-test-key"), model="test", requester=requester)
    session.audit = IOAudit(tmp_path)
    image = tmp_path / "test.png"
    image.write_bytes(b"image bytes")
    schema = {"type": "object", "properties": {"actions": {"type": "array"}}}
    for _ in range(2):
        session.run_turn(prompt="task", image_path=image, output_schema=schema)
    events = read_events(tmp_path)
    assert events[0]["payload"] == captured[0]
    assert events[2]["payload"] == captured[1]
    assert len(events[2]["payload"]["messages"]) == 4
    assert events[0]["payload"]["messages"][0]["role"] == "system"
    assert events[0]["payload"]["response_format"]["json_schema"]["schema"] == schema
    url = events[0]["payload"]["messages"][1]["content"][1]["image_url"]["url"]
    assert base64.b64decode(url.split(",")[1]) == b"image bytes"
    assert events[1]["payload"] == response
    assert "private-test-key" not in (tmp_path / "io-audit/events.jsonl").read_text()


def test_rejected_response_is_saved_and_secrets_redacted(tmp_path):
    response = {"choices": [], "error": "secret-key Bearer abc123"}
    session = ModelAPISession(ModelAPIConfig(api_key="secret-key"), model="test",
                              requester=lambda *_: response)
    session.audit = IOAudit(tmp_path)
    with pytest.raises(RuntimeError):
        session.run_turn(prompt="task", image_path=None, output_schema={})
    text = (tmp_path / "io-audit/events.jsonl").read_text()
    assert "secret-key" not in text and "abc123" not in text
    assert read_events(tmp_path)[1]["type"] == "model_response"


def test_codex_temporary_images_are_archived(tmp_path):
    path = tmp_path / "frame.png"
    path.write_bytes(b"evidence")
    audit = IOAudit(tmp_path)
    audit.record("codex_request", payload={"input": [{"type": "localImage", "path": str(path)}]})
    asset = read_events(tmp_path)[0]["image_assets"][str(path)]
    assert (audit.root / asset["file"]).read_bytes() == b"evidence"


def test_executor_audit_includes_blocked_and_successful_inputs(tmp_path):
    runtime = ExecutionRuntime(stop=None, status_callback=lambda _: None, max_actions=1)
    runtime.audit = IOAudit(tmp_path)
    motor = SimpleNamespace(execute=lambda _: SimpleNamespace(elapsed_ms=1, screen_position=[10, 20]))
    action = ActionCall("click", {"x": .1, "y": .2, "button": "left"})
    runtime.execute(motor, action)
    with pytest.raises(RuntimeError, match="budget"):
        runtime.execute(motor, action)
    events = read_events(tmp_path)
    assert [e["type"] for e in events] == ["executor_input", "executor_result", "executor_input", "executor_error"]
    assert events[0]["action"] == action.to_payload()
    assert events[1]["result"]["screen_position"] == [10, 20]
