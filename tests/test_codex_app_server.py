from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any

import pytest

from trace2task.codex_app_server import CodexAppServerSession


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


def completed_turn(turn_id: str, text: str) -> dict[str, Any]:
    return {
        "method": "turn/completed",
        "params": {
            "threadId": "thread-1",
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
        cwd=tmp_path,
        transport_factory=lambda executable: transport,
    )
    image_path = tmp_path / "observation.png"
    image_path.write_bytes(b"png")
    schema = {"type": "object"}

    first = session.run_turn(prompt="first", image_path=image_path, output_schema=schema)
    second = session.run_turn(prompt="second", image_path=image_path, output_schema=schema)

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
    assert first_turn["outputSchema"] == schema
    assert first_turn["sandboxPolicy"] == {"type": "readOnly", "networkAccess": False}

    session.close()
    session.close()
    assert transport.closed


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

