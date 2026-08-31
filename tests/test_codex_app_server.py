from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any

import pytest

from trace2task.codex_app_server import (
    CodexAppServerSession,
    CodexTurnTimeoutError,
    classify_codex_failure,
    is_codex_connectivity_error,
)


class ScriptedTransport:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.messages = deque(messages)
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    def send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)

    def receive(self, timeout_seconds: float) -> dict[str, Any]:
        assert timeout_seconds > 0
        if not self.messages:
            raise TimeoutError
        return self.messages.popleft()

    def close(self) -> None:
        self.closed = True


def completed_turn(
    turn_id: str,
    text: str,
    *,
    thread_id: str = "thread-1",
) -> dict[str, Any]:
    return {
        "method": "turn/completed",
        "params": {
            "threadId": thread_id,
            "turn": {
                "id": turn_id,
                "status": "completed",
                "items": [
                    {
                        "id": f"message-{turn_id}",
                        "type": "agentMessage",
                        "phase": "final_answer",
                        "text": text,
                    }
                ],
            },
        },
    }


def test_session_initializes_once_and_reuses_thread_for_multiple_turns(tmp_path: Path) -> None:
    transport = ScriptedTransport(
        [
            {"id": 1, "result": {}},
            {"id": 2, "result": {"thread": {"id": "thread-1"}}},
            {"id": 3, "result": {"turn": {"id": "turn-1"}}},
            completed_turn("turn-1", '{"actions":["move_right"]}'),
            {"id": 4, "result": {"turn": {"id": "turn-2"}}},
            completed_turn("turn-2", '{"actions":["interact"]}'),
        ]
    )
    session = CodexAppServerSession(
        "codex",
        model="gpt-5.6-terra",
        reasoning_effort="high",
        cwd=tmp_path,
        transport_factory=lambda executable: transport,
    )
    image_path = tmp_path / "observation.png"
    image_path.write_bytes(b"png")
    reference_path = tmp_path / "reference.png"
    reference_path.write_bytes(b"png")
    schema = {"type": "object"}

    first = session.run_turn(
        prompt="first",
        image_path=image_path,
        additional_image_paths=(reference_path,),
        output_schema=schema,
    )
    second = session.run_turn(
        prompt="second",
        image_path=image_path,
        output_schema=schema,
        model="gpt-5.6-sol",
        reasoning_effort="max",
    )

    assert first == '{"actions":["move_right"]}'
    assert second == '{"actions":["interact"]}'
    assert session.thread_id == "thread-1"
    assert [message.get("method") for message in transport.sent] == [
        "initialize",
        "initialized",
        "thread/start",
        "turn/start",
        "turn/start",
    ]
    thread_params = transport.sent[2]["params"]
    assert thread_params["ephemeral"] is True
    assert thread_params["sandbox"] == "read-only"
    assert thread_params["approvalPolicy"] == "never"
    assert thread_params["model"] == "gpt-5.6-terra"

    first_turn = transport.sent[3]["params"]
    second_turn = transport.sent[4]["params"]
    assert first_turn["threadId"] == second_turn["threadId"] == "thread-1"
    assert first_turn["input"][1] == {
        "type": "localImage",
        "path": str(image_path.resolve()),
        "detail": "original",
    }
    assert first_turn["input"][2] == {
        "type": "localImage",
        "path": str(reference_path.resolve()),
        "detail": "original",
    }
    assert first_turn["outputSchema"] == schema
    assert first_turn["effort"] == "high"
    assert second_turn["model"] == "gpt-5.6-sol"
    assert second_turn["effort"] == "max"
    assert first_turn["sandboxPolicy"] == {"type": "readOnly", "networkAccess": False}
    assert session.last_turn_metrics is not None
    assert session.last_turn_metrics.prompt_chars == len("second")
    assert session.last_turn_metrics.image_count == 1
    assert session.last_turn_metrics.thread_reused is True
    assert session.last_turn_metrics.thread_generation == 1

    session.close()
    session.close()
    assert transport.closed


def test_session_supports_a_text_only_connectivity_turn(tmp_path: Path) -> None:
    transport = ScriptedTransport(
        [
            {"id": 1, "result": {}},
            {"id": 2, "result": {"thread": {"id": "thread-1"}}},
            {"id": 3, "result": {"turn": {"id": "turn-1"}}},
            completed_turn("turn-1", '{"status":"ok"}'),
        ]
    )
    session = CodexAppServerSession(
        "codex",
        model="gpt-5.6-luna",
        reasoning_effort="low",
        cwd=tmp_path,
        transport_factory=lambda executable: transport,
    )

    result = session.run_turn(
        prompt="preflight",
        image_path=None,
        output_schema={"type": "object"},
    )

    assert result == '{"status":"ok"}'
    assert transport.sent[3]["params"]["input"] == [
        {"type": "text", "text": "preflight"}
    ]
    assert session.last_turn_metrics is not None
    assert session.last_turn_metrics.image_count == 0


def test_connectivity_error_detection_checks_exception_chain() -> None:
    try:
        try:
            raise TimeoutError("tls handshake eof")
        except TimeoutError as error:
            raise RuntimeError("compiler preflight failed") from error
    except RuntimeError as error:
        assert is_codex_connectivity_error(error) is True

    assert is_codex_connectivity_error(ValueError("invalid semantic schema")) is False
    timeout = CodexTurnTimeoutError(
        "response_in_progress_timeout",
        "response stalled",
    )
    assert classify_codex_failure(timeout) == "response_in_progress_timeout"
    assert is_codex_connectivity_error(timeout) is False


def test_session_resets_only_the_thread_and_keeps_the_codex_process(tmp_path: Path) -> None:
    transport = ScriptedTransport(
        [
            {"id": 1, "result": {}},
            {"id": 2, "result": {"thread": {"id": "thread-1"}}},
            {"id": 3, "result": {"turn": {"id": "turn-1"}}},
            completed_turn("turn-1", "first"),
            {"id": 4, "result": {"thread": {"id": "thread-2"}}},
            {"id": 5, "result": {"turn": {"id": "turn-2"}}},
            completed_turn("turn-2", "second", thread_id="thread-2"),
        ]
    )
    session = CodexAppServerSession(
        "codex",
        model="gpt-5.6-terra",
        cwd=tmp_path,
        transport_factory=lambda executable: transport,
    )
    image_path = tmp_path / "observation.png"
    image_path.write_bytes(b"png")

    session.run_turn(prompt="first", image_path=image_path, output_schema={})
    session.reset_thread()
    session.run_turn(prompt="second", image_path=image_path, output_schema={})

    assert [message.get("method") for message in transport.sent] == [
        "initialize",
        "initialized",
        "thread/start",
        "turn/start",
        "thread/start",
        "turn/start",
    ]
    assert session.last_turn_metrics is not None
    assert session.last_turn_metrics.thread_reused is False
    assert session.last_turn_metrics.thread_generation == 2


def test_session_rejects_unknown_reasoning_effort(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="reasoning effort"):
        CodexAppServerSession(
            "codex",
            model="gpt-5.6-terra",
            reasoning_effort="extreme",
            cwd=tmp_path,
        )


def test_session_reports_failed_turn(tmp_path: Path) -> None:
    transport = ScriptedTransport(
        [
            {"id": 1, "result": {}},
            {"id": 2, "result": {"thread": {"id": "thread-1"}}},
            {"id": 3, "result": {"turn": {"id": "turn-1"}}},
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turn": {
                        "id": "turn-1",
                        "status": "failed",
                        "error": {"message": "model unavailable"},
                        "items": [],
                    },
                },
            },
        ]
    )
    session = CodexAppServerSession(
        "codex",
        model=None,
        cwd=tmp_path,
        transport_factory=lambda executable: transport,
    )
    image_path = tmp_path / "observation.png"
    image_path.write_bytes(b"png")

    with pytest.raises(RuntimeError, match="model unavailable"):
        session.run_turn(prompt="test", image_path=image_path, output_schema={})


def test_session_classifies_timeout_before_the_first_response_item(tmp_path: Path) -> None:
    transport = ScriptedTransport(
        [
            {"id": 1, "result": {}},
            {"id": 2, "result": {"thread": {"id": "thread-1"}}},
            {"id": 3, "result": {"turn": {"id": "turn-1"}}},
        ]
    )
    session = CodexAppServerSession(
        "codex",
        model="gpt-5.6-terra",
        cwd=tmp_path,
        timeout_seconds=300,
        transport_factory=lambda executable: transport,
    )
    image_path = tmp_path / "observation.png"
    image_path.write_bytes(b"png")

    with pytest.raises(CodexTurnTimeoutError, match="begin its model response") as raised:
        session.run_turn(prompt="test", image_path=image_path, output_schema={})

    assert raised.value.category == "first_token_timeout"


def test_session_extends_the_deadline_while_response_items_are_arriving(
    tmp_path: Path,
) -> None:
    transport = ScriptedTransport(
        [
            {"id": 1, "result": {}},
            {"id": 2, "result": {"thread": {"id": "thread-1"}}},
            {"id": 3, "result": {"turn": {"id": "turn-1"}}},
            {
                "method": "item/agentMessage/delta",
                "params": {"threadId": "thread-1", "turnId": "turn-1"},
            },
            completed_turn("turn-1", '{"status":"ok"}'),
        ]
    )
    session = CodexAppServerSession(
        "codex",
        model="gpt-5.6-sol",
        cwd=tmp_path,
        timeout_seconds=300,
        progress_timeout_seconds=90,
        hard_timeout_seconds=600,
        transport_factory=lambda executable: transport,
    )
    image_path = tmp_path / "observation.png"
    image_path.write_bytes(b"png")

    result = session.run_turn(prompt="test", image_path=image_path, output_schema={})

    assert result == '{"status":"ok"}'


def test_session_reports_when_an_in_progress_response_stalls(tmp_path: Path) -> None:
    transport = ScriptedTransport(
        [
            {"id": 1, "result": {}},
            {"id": 2, "result": {"thread": {"id": "thread-1"}}},
            {"id": 3, "result": {"turn": {"id": "turn-1"}}},
            {
                "method": "item/agentMessage/delta",
                "params": {"threadId": "thread-1", "turnId": "turn-1"},
            },
        ]
    )
    session = CodexAppServerSession(
        "codex",
        model="gpt-5.6-sol",
        cwd=tmp_path,
        timeout_seconds=300,
        progress_timeout_seconds=90,
        hard_timeout_seconds=600,
        transport_factory=lambda executable: transport,
    )
    image_path = tmp_path / "observation.png"
    image_path.write_bytes(b"png")

    with pytest.raises(CodexTurnTimeoutError, match="no progress") as raised:
        session.run_turn(prompt="test", image_path=image_path, output_schema={})

    assert raised.value.category == "response_in_progress_timeout"


def test_session_enforces_a_separate_hard_limit(tmp_path: Path) -> None:
    transport = ScriptedTransport(
        [
            {"id": 1, "result": {}},
            {"id": 2, "result": {"thread": {"id": "thread-1"}}},
            {"id": 3, "result": {"turn": {"id": "turn-1"}}},
            {"method": "item/agentMessage/delta", "params": {}},
        ]
    )
    session = CodexAppServerSession(
        "codex",
        model="gpt-5.6-sol",
        cwd=tmp_path,
        timeout_seconds=1,
        progress_timeout_seconds=100,
        hard_timeout_seconds=2,
        transport_factory=lambda executable: transport,
    )
    image_path = tmp_path / "observation.png"
    image_path.write_bytes(b"png")

    with pytest.raises(CodexTurnTimeoutError, match="hard limit") as raised:
        session.run_turn(prompt="test", image_path=image_path, output_schema={})

    assert raised.value.category == "hard_timeout"
