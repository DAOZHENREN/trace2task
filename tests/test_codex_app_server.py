from __future__ import annotations

import io
import json
import os
import queue
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

import pytest

from trace2task.codex_app_server import (
    CodexAppServerSession,
    CodexTurnTimeoutError,
    StdioJsonTransport,
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


def test_token_usage_marks_context_for_discard_without_compaction(tmp_path):
    transport = ScriptedTransport([
        {"id": 1, "result": {}},
        {"id": 2, "result": {"thread": {"id": "thread-1"}}},
        {"id": 3, "result": {"turn": {"id": "turn-1"}}},
        {"method": "thread/tokenUsage/updated", "params": {"threadId": "thread-1",
            "tokenUsage": {"modelContextWindow": 10000, "last": {"totalTokens": 8500}}}},
        completed_turn("turn-1", '{}'),
    ])
    session = CodexAppServerSession('codex', model='test', cwd=tmp_path,
        transport_factory=lambda _: transport,
        thread_config={"model_auto_compact_token_limit": 2_000_000_000})
    session.run_turn(prompt='task', image_path=None, output_schema={})
    assert session.context_near_limit
    session.reset_thread()
    assert not session.context_near_limit
    assert session.thread_id is None
    assert not any('compact' in message.get('method', '') for message in transport.sent)


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


def test_rsi_role_instructions_and_unconstrained_text_response(tmp_path: Path) -> None:
    transport = ScriptedTransport([
        {"id": 1, "result": {}},
        {"id": 2, "result": {"thread": {"id": "thread-1"}}},
        {"id": 3, "result": {"turn": {"id": "turn-1"}}},
        completed_turn("turn-1", "official role response"),
    ])
    config = {"features.shell_tool": False, "web_search": "disabled"}
    session = CodexAppServerSession(
        "codex", model=None, cwd=tmp_path,
        base_instructions="Official verifier role instructions",
        thread_config=config, transport_factory=lambda _: transport,
    )
    assert session.run_turn(prompt="verify", image_path=None, output_schema=None) == (
        "official role response"
    )
    assert transport.sent[2]["params"]["baseInstructions"] == (
        "Official verifier role instructions"
    )
    assert transport.sent[2]["params"]["config"] == config
    assert "outputSchema" not in transport.sent[3]["params"]
    session.close()


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


@pytest.mark.parametrize('thread_id,turn_id,item_type', [
    ('thread-1', 'turn-1', 'userMessage'),
    ('thread-1', 'turn-1', 'functionCallOutput'),
    ('another-thread', 'turn-1', 'agentMessage'),
    ('thread-1', 'previous-turn', 'reasoning'),
])
def test_user_receipts_and_other_turns_are_not_model_response_progress(
    tmp_path, thread_id, turn_id, item_type,
):
    transport = ScriptedTransport([
        {'id': 1, 'result': {}},
        {'id': 2, 'result': {'thread': {'id': 'thread-1'}}},
        {'id': 3, 'result': {'turn': {'id': 'turn-1'}}},
        *({'method': method, 'params': {'threadId': thread_id, 'turnId': turn_id,
            'item': {'id': 'input-item', 'type': item_type, 'content': 'private message'}}}
          for method in ('item/started', 'item/completed')),
    ])
    session = CodexAppServerSession('codex', model=None, cwd=tmp_path,
        timeout_seconds=300, progress_timeout_seconds=90,
        transport_factory=lambda _: transport)
    with pytest.raises(CodexTurnTimeoutError) as raised:
        session.run_turn(prompt='test', image_path=None, output_schema=None)
    assert raised.value.category == 'first_token_timeout'


def test_total_response_timeout_does_not_claim_whole_turn_was_idle(tmp_path, monkeypatch):
    now = [0.0]
    monkeypatch.setattr('trace2task.codex_app_server.time.monotonic', lambda: now[0])

    class LateResponseTransport(ScriptedTransport):
        def receive(self, timeout_seconds):
            if not self.messages:
                now[0] += timeout_seconds
                raise TimeoutError()
            result = super().receive(timeout_seconds)
            if result.get('method') == 'item/agentMessage/delta':
                now[0] = 290.0
            return result

    transport = LateResponseTransport([
        {'id': 1, 'result': {}},
        {'id': 2, 'result': {'thread': {'id': 'thread-1'}}},
        {'id': 3, 'result': {'turn': {'id': 'turn-1'}}},
        {'method': 'item/agentMessage/delta',
         'params': {'threadId': 'thread-1', 'turnId': 'turn-1', 'delta': 'private text'}},
    ])
    session = CodexAppServerSession('codex', model=None, cwd=tmp_path,
        timeout_seconds=300, transport_factory=lambda _: transport)
    recorded = []

    class Audit:
        def record(self, kind, **data):
            if kind == 'codex_timeout':
                recorded.append(data)

    session.audit = Audit()
    with pytest.raises(CodexTurnTimeoutError) as raised:
        session.run_turn(prompt='test', image_path=None, output_schema=None)
    assert raised.value.category == 'response_in_progress_timeout'
    assert 'did not complete within 300 seconds' in str(raised.value)
    assert 'no progress' not in str(raised.value)
    assert raised.value.diagnostics['turn_elapsed_seconds'] == 300
    assert raised.value.diagnostics['model_progress_idle_seconds'] == 10
    assert recorded[0]['last_event']['method'] == 'item/agentMessage/delta'
    assert 'private text' not in str(recorded)


def test_transport_timeout_diagnostics_report_markers_without_private_stderr():
    from types import SimpleNamespace

    transport = object.__new__(StdioJsonTransport)
    transport._process = SimpleNamespace(pid=123, poll=lambda: None)
    transport._stderr_lines = deque([
        'Authorization: Bearer private-secret',
        'tls handshake failed when connecting to private-host',
    ])
    assert transport.diagnostics() == {
        'process_pid': 123, 'process_exit_code': None,
        'stderr_connectivity_markers': ['tls handshake'],
    }


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
            {"method": "item/agentMessage/delta",
             "params": {"threadId": "thread-1", "turnId": "turn-1"}},
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


def retry_notice(*, turn_id="turn-1", seconds=589.0):
    return {"method": "trace2task/codexRetry", "params": {
        "turnId": turn_id, "httpStatusCode": 429, "retryAfterSeconds": seconds,
        "retryAttempt": 1, "maxRetries": 5,
    }}


def error_session(tmp_path, events, *, before_ack=False):
    transport = ScriptedTransport([
        {"id": 1, "result": {}},
        {"id": 2, "result": {"thread": {"id": "thread-1"}}},
        *(events if before_ack else []),
        {"id": 3, "result": {"turn": {"id": "turn-1"}}},
        *([] if before_ack else events),
    ])
    return CodexAppServerSession(
        "codex", model=None, cwd=tmp_path, timeout_seconds=300,
        transport_factory=lambda _: transport,
    ), transport


def test_stderr_rate_limit_wakes_receive_without_copying_private_log_fields():
    transport = object.__new__(StdioJsonTransport)
    transport._stderr_lines = deque(maxlen=40)
    transport._messages = queue.Queue()
    fields = {
        "message": "stream disconnected - retrying sampling request (1/5 in 588.9998234s)...",
        "turn_id": "turn-1", "retries": 1, "max_retries": 5,
        "sampling_error": "exceeded retry limit, last status: 429 Too Many Requests; private-secret",
    }
    logs = [
        "unstructured private-secret",
        json.dumps({"target": "other::logger", "fields": fields}),
        json.dumps({"target": "codex_core::responses_retry", "fields": {**fields, "turn_id": None}}),
        json.dumps({"target": "codex_core::responses_retry", "fields": fields}),
    ]
    transport._read_stderr(io.StringIO("\n".join(logs)))

    notice = transport.receive(0.01)
    assert notice == retry_notice(seconds=588.9998234)
    assert "private-secret" not in str(notice)
    assert transport._messages.empty()


@pytest.mark.parametrize("before_ack", [False, True])
def test_long_rate_limit_is_detected_without_waiting_for_response_timeout(tmp_path, before_ack):
    session, transport = error_session(tmp_path, [retry_notice()], before_ack=before_ack)
    recorded = []

    class Audit:
        def record(self, kind, **data):
            recorded.append((kind, data))

    session.audit = Audit()
    with pytest.raises(RuntimeError, match="429.*589") as raised:
        session.run_turn(prompt="synthetic", image_path=None, output_schema=None)
    assert not isinstance(raised.value, CodexTurnTimeoutError)
    assert classify_codex_failure(raised.value) == "codex_rate_limit"
    assert raised.value.diagnostics["retry_after_seconds"] == 589
    assert raised.value.diagnostics["turn_id"] == "turn-1"
    assert any(kind == "codex_retry" for kind, _ in recorded)
    assert any(kind == "codex_turn_error" for kind, _ in recorded)
    assert not any(kind == "codex_timeout" for kind, _ in recorded)
    assert sum(message.get("method") == "turn/start" for message in transport.sent) == 1


def test_short_rate_limit_leaves_retry_to_codex_and_can_complete(tmp_path):
    session, transport = error_session(tmp_path, [
        retry_notice(seconds=1), completed_turn("turn-1", "recovered"),
    ])
    assert session.run_turn(prompt="test", image_path=None, output_schema=None) == "recovered"
    assert sum(message.get("method") == "turn/start" for message in transport.sent) == 1


@pytest.mark.parametrize("seconds", [1, None])
def test_unresolved_rate_limit_keeps_its_cause_when_waiting_expires(tmp_path, seconds):
    session, _ = error_session(tmp_path, [retry_notice(seconds=seconds)])
    with pytest.raises(RuntimeError) as raised:
        session.run_turn(prompt="test", image_path=None, output_schema=None)
    assert classify_codex_failure(raised.value) == "codex_rate_limit"
    assert "429" in str(raised.value)


def test_recovered_rate_limit_does_not_mask_a_later_response_stall(tmp_path):
    session, _ = error_session(tmp_path, [retry_notice(seconds=1), {
        "method": "item/agentMessage/delta",
        "params": {"threadId": "thread-1", "turnId": "turn-1", "delta": "response"},
    }])
    with pytest.raises(CodexTurnTimeoutError) as raised:
        session.run_turn(prompt="test", image_path=None, output_schema=None)
    assert raised.value.category == "response_in_progress_timeout"


@pytest.mark.parametrize("info,category", [
    ("contextWindowExceeded", "codex_context_limit"),
    ("sessionBudgetExceeded", "codex_usage_limit"),
    ("usageLimitExceeded", "codex_usage_limit"),
    ("rateLimitExceeded", "codex_rate_limit"),
    ({"responseStreamDisconnected": {"httpStatusCode": 429}}, "codex_rate_limit"),
    ({"responseStreamConnectionFailed": {"httpStatusCode": 503}}, "codex_server_error"),
])
def test_terminal_error_is_detected_without_turn_completed(tmp_path, info, category):
    session, _ = error_session(tmp_path, [{"method": "error", "params": {
        "threadId": "thread-1", "turnId": "turn-1", "willRetry": False,
        "error": {"message": "explicit model failure", "codexErrorInfo": info},
    }}])
    with pytest.raises(RuntimeError, match="explicit model failure") as raised:
        session.run_turn(prompt="test", image_path=None, output_schema=None)
    assert classify_codex_failure(raised.value) == category
    wrapped = RuntimeError("stream disconnected while handling this turn")
    wrapped.__cause__ = raised.value
    assert classify_codex_failure(wrapped) == category


def test_retryable_errors_and_stale_events_do_not_stop_current_turn(tmp_path):
    events = [retry_notice(turn_id="old-turn")]
    for thread, turn, retry in [
        ("thread-1", "turn-1", True),
        ("other-thread", "turn-1", False),
        ("thread-1", "old-turn", False),
    ]:
        events.append({"method": "error", "params": {
            "threadId": thread, "turnId": turn, "willRetry": retry,
            "error": {"message": "temporary", "codexErrorInfo": "internalServerError"},
        }})
    events += [
        {"method": "thread/status/changed", "params": {
            "threadId": "other-thread", "status": {"type": "systemError"}}},
        {"method": "thread/status/changed", "params": {
            "threadId": "thread-1", "status": {"type": "idle"}}},
        completed_turn("turn-1", "ok"),
    ]
    session, _ = error_session(tmp_path, events)
    assert session.run_turn(prompt="test", image_path=None, output_schema=None) == "ok"


def test_thread_system_error_does_not_wait_for_missing_completion(tmp_path):
    session, _ = error_session(tmp_path, [{"method": "thread/status/changed", "params": {
        "threadId": "thread-1", "status": {"type": "systemError"},
    }}])
    with pytest.raises(RuntimeError) as raised:
        session.run_turn(prompt="test", image_path=None, output_schema=None)
    assert classify_codex_failure(raised.value) == "codex_thread_error"


@pytest.mark.parametrize("http_status,category", [
    (403, "codex_access_denied"), (429, "codex_rate_limit"),
])
def test_system_error_preserves_last_matching_retry(tmp_path, http_status, category):
    retry = {"method": "error", "params": {
        "threadId": "thread-1", "turnId": "turn-1", "willRetry": True,
        "error": {"message": "Reconnecting... 5/5",
                  "codexErrorInfo": {"responseStreamDisconnected": {"httpStatusCode": http_status}}},
    }}
    events = [retry]
    for thread, turn in [("other-thread", "turn-1"), ("thread-1", "old-turn")]:
        events.append({"method": "error", "params": {
            **retry["params"], "threadId": thread, "turnId": turn,
            "error": {"message": "unrelated", "codexErrorInfo": "usageLimitExceeded"},
        }})
    events.append({"method": "thread/status/changed", "params": {
        "threadId": "thread-1", "status": {"type": "systemError"},
    }})
    session, transport = error_session(tmp_path, events)
    with pytest.raises(RuntimeError, match=f"HTTP {http_status}") as raised:
        session.run_turn(prompt="synthetic", image_path=None, output_schema=None)
    assert classify_codex_failure(raised.value) == category
    assert raised.value.diagnostics["http_status_code"] == http_status
    assert raised.value.diagnostics["thread_status"] == "systemError"
    assert raised.value.diagnostics["error_source"] == "last_retry"
    assert sum(m.get("method") == "turn/start" for m in transport.sent) == 1


@pytest.mark.parametrize("before_ack", [False, True])
@pytest.mark.parametrize("terminal_kind", ["error", "completion"])
def test_system_error_drains_terminal_detail_before_failing(tmp_path, before_ack, terminal_kind):
    error = {"message": "HTTP 403 Forbidden: <html>private-error-page</html>",
             "codexErrorInfo": {"responseStreamDisconnected": {"httpStatusCode": 403}}}
    terminal = {"method": "error", "params": {
        "threadId": "thread-1", "turnId": "turn-1", "willRetry": False, "error": error,
    }}
    if terminal_kind == "completion":
        terminal = completed_turn("turn-1", "")
        terminal["params"]["turn"].update(status="failed", error=error)
    session, transport = error_session(tmp_path, [
        retry_notice(seconds=0),
        {"method": "thread/status/changed", "params": {
            "threadId": "thread-1", "status": {"type": "systemError"},
        }}, terminal,
    ], before_ack=before_ack)
    with pytest.raises(RuntimeError, match="HTTP 403") as raised:
        session.run_turn(prompt="synthetic", image_path=None, output_schema=None)
    assert classify_codex_failure(raised.value) == "codex_access_denied"
    assert raised.value.diagnostics["http_status_code"] == 403
    assert "private-error-page" not in str(raised.value)
    assert sum(m.get("method") == "turn/start" for m in transport.sent) == 1


def test_system_error_without_detail_has_a_short_bounded_grace(tmp_path, monkeypatch):
    now = [0.0]
    monkeypatch.setattr("trace2task.codex_app_server.time.monotonic", lambda: now[0])
    status = {"method": "thread/status/changed", "params": {
        "threadId": "thread-1", "status": {"type": "systemError"},
    }}
    session, transport = error_session(tmp_path, [status, status])
    receive = transport.receive
    statuses = 0

    def timed_receive(timeout_seconds):
        nonlocal statuses
        if not transport.messages:
            now[0] += timeout_seconds
            raise TimeoutError
        message = receive(timeout_seconds)
        if message is status:
            statuses += 1
            if statuses == 2:
                now[0] += .4
        return message

    transport.receive = timed_receive
    with pytest.raises(RuntimeError, match="systemError") as raised:
        session.run_turn(prompt="synthetic", image_path=None, output_schema=None)
    assert classify_codex_failure(raised.value) == "codex_thread_error"
    assert now[0] == 1.0
    assert sum(m.get("method") == "turn/start" for m in transport.sent) == 1


def test_system_error_never_returns_a_late_success(tmp_path):
    session, _ = error_session(tmp_path, [
        {"method": "thread/status/changed", "params": {
            "threadId": "thread-1", "status": {"type": "systemError"},
        }}, completed_turn("turn-1", "must not execute"),
    ])
    with pytest.raises(RuntimeError, match="systemError"):
        session.run_turn(prompt="synthetic", image_path=None, output_schema=None)


def test_stdio_system_error_receives_delayed_terminal_error(tmp_path):
    script = '''
import json, sys, time
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    method, result = request.get("method"), {}
    if method == "thread/start":
        result = {"thread": {"id": "thread-1"}}
    elif method == "turn/start":
        result = {"turn": {"id": "turn-1"}}
    print(json.dumps({"id": request["id"], "result": result}), flush=True)
    if method == "turn/start":
        print(json.dumps({"method": "thread/status/changed", "params": {
            "threadId": "thread-1", "status": {"type": "systemError"}}}), flush=True)
        time.sleep(.05)
        print(json.dumps({"method": "error", "params": {
            "threadId": "thread-1", "turnId": "turn-1", "willRetry": False,
            "error": {"message": "HTTP 403 Forbidden", "codexErrorInfo": {
                "responseStreamDisconnected": {"httpStatusCode": 403}}}}}), flush=True)
'''
    transport = StdioJsonTransport(sys.executable, command=[sys.executable, "-u", "-c", script])
    session = CodexAppServerSession("offline", model=None, cwd=tmp_path, timeout_seconds=2,
                                    transport_factory=lambda _: transport)
    try:
        with pytest.raises(RuntimeError, match="HTTP 403") as raised:
            session.run_turn(prompt="synthetic", image_path=None, output_schema=None)
        assert raised.value.category == "codex_access_denied"
    finally:
        session.close()
    assert transport._process.poll() is not None


def test_system_error_does_not_reuse_a_retry_that_already_recovered(tmp_path):
    session, _ = error_session(tmp_path, [
        retry_notice(seconds=0),
        {"method": "item/agentMessage/delta", "params": {
            "threadId": "thread-1", "turnId": "turn-1", "delta": "partial response",
        }},
        {"method": "thread/status/changed", "params": {
            "threadId": "thread-1", "status": {"type": "systemError"},
        }},
    ])
    with pytest.raises(RuntimeError) as raised:
        session.run_turn(prompt="synthetic", image_path=None, output_schema=None)
    assert classify_codex_failure(raised.value) == "codex_thread_error"


def test_interrupted_turn_has_a_distinct_category(tmp_path):
    completion = completed_turn("turn-1", "partial response")
    completion["params"]["turn"]["status"] = "interrupted"
    session, _ = error_session(tmp_path, [completion])
    with pytest.raises(RuntimeError) as raised:
        session.run_turn(prompt="test", image_path=None, output_schema=None)
    assert classify_codex_failure(raised.value) == "codex_interrupted"


def test_retry_respects_remaining_deadline_instead_of_original_timeout(tmp_path, monkeypatch):
    now = [0.0]
    monkeypatch.setattr("trace2task.codex_app_server.time.monotonic", lambda: now[0])
    session, transport = error_session(tmp_path, [retry_notice(seconds=10)])
    receive = transport.receive

    def delayed_receive(timeout_seconds):
        message = receive(timeout_seconds)
        if message.get("method") == "trace2task/codexRetry":
            now[0] = 295.0
        return message

    transport.receive = delayed_receive
    with pytest.raises(RuntimeError, match="remaining 5 seconds") as raised:
        session.run_turn(prompt="test", image_path=None, output_schema=None)
    assert classify_codex_failure(raised.value) == "codex_rate_limit"
    assert raised.value.diagnostics["remaining_seconds"] == 5


@pytest.mark.parametrize("inherited_format", [None, "text", "json"])
def test_real_stdio_reader_detects_rate_limit_while_child_is_sleeping(
    tmp_path, monkeypatch, inherited_format,
):
    if inherited_format is None:
        monkeypatch.delenv("LOG_FORMAT", raising=False)
    else:
        monkeypatch.setenv("LOG_FORMAT", inherited_format)
    monkeypatch.setenv("RUST_LOG", "error")
    # An offline child uses the observed CLI log format and deliberately emits
    # no terminal stdout notification, reproducing the original detection gap.
    script = '''
import json, os, sys, time
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if "id" not in request:
        continue
    result = {}
    if method == "thread/start":
        result = {"thread": {"id": "thread-1"}}
    elif method == "turn/start":
        result = {"turn": {"id": "turn-1"}}
    print(json.dumps({"id": request["id"], "result": result}), flush=True)
    if method == "turn/start":
        notice = {"target": "codex_core::responses_retry", "fields": {
            "message": "stream disconnected - retrying sampling request (1/5 in 588.9998234s)...",
            "turn_id": "turn-1", "retries": 1, "max_retries": 5,
            "sampling_error": "exceeded retry limit, last status: 429 Too Many Requests"
        }}
        if "codex_core::responses_retry=warn" in os.environ.get("RUST_LOG", ""):
            line = json.dumps(notice) if os.environ.get("LOG_FORMAT") == "json" else (
                "WARN codex_core::responses_retry: " + notice["fields"]["message"]
            )
            print(line, file=sys.stderr, flush=True)
        time.sleep(30)
'''
    transport = StdioJsonTransport(sys.executable, command=[sys.executable, "-u", "-c", script])
    session = CodexAppServerSession(
        "offline", model=None, cwd=tmp_path, timeout_seconds=2,
        transport_factory=lambda _: transport,
    )
    try:
        assert os.environ.get("LOG_FORMAT") == inherited_format
        assert os.environ["RUST_LOG"] == "error"
        with pytest.raises(RuntimeError, match="429.*589") as raised:
            session.run_turn(prompt="synthetic", image_path=None, output_schema=None)
        assert classify_codex_failure(raised.value) == "codex_rate_limit"
        assert raised.value.diagnostics["turn_elapsed_seconds"] < 2
        assert transport._process.poll() is None
    finally:
        session.close()
    assert transport._process.poll() is not None


@pytest.mark.parametrize('before_ack', [False, True])
def test_cooldown_longer_than_all_timeouts_completes_the_same_turn(tmp_path, monkeypatch, before_ack):
    clock = [0.0]
    monkeypatch.setattr('trace2task.codex_app_server.time.monotonic', lambda: clock[0])
    events = [retry_notice(seconds=590), {
        'method': 'item/agentMessage/delta',
        'params': {'threadId': 'thread-1', 'turnId': 'turn-1', 'delta': 'resumed'},
    }, completed_turn('turn-1', 'recovered')]
    session, transport = error_session(tmp_path, events[:1] if before_ack else events, before_ack=before_ack)
    if before_ack:
        transport.messages.extend(events[1:])
    session.timeout_seconds = 120
    session.hard_timeout_seconds = 300
    session.progress_timeout_seconds = 90
    session.wait_on_rate_limit = True
    received = transport.receive
    notifications = []
    session.rate_limit_callback = notifications.append

    def receive(timeout):
        message = received(timeout)
        if message.get('method') == 'item/agentMessage/delta':
            assert timeout >= 710
            clock[0] = 595
        if message.get('method') == 'turn/completed':
            clock[0] = 620
        return message

    transport.receive = receive
    assert session.run_turn(prompt='synthetic', image_path=None, output_schema=None) == 'recovered'
    assert session.last_rate_limit_wait_seconds == 590
    assert notifications == [590, None]
    assert sum(m.get('method') == 'turn/start' for m in transport.sent) == 1


def test_cooldown_credits_only_actual_wait_and_does_not_hide_later_stall(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr('trace2task.codex_app_server.time.monotonic', lambda: clock[0])
    session, transport = error_session(tmp_path, [retry_notice(seconds=590), {
        'method': 'item/agentMessage/delta', 'params': {'threadId': 'thread-1', 'turnId': 'turn-1'},
    }])
    session.wait_on_rate_limit = True
    received = transport.receive
    timeouts = []

    def receive(timeout):
        timeouts.append(timeout)
        message = received(timeout)
        if message.get('method') == 'item/agentMessage/delta':
            clock[0] = 10
        return message

    transport.receive = receive
    with pytest.raises(CodexTurnTimeoutError) as raised:
        session.run_turn(prompt='test', image_path=None, output_schema=None)
    assert raised.value.category == 'response_in_progress_timeout'
    assert timeouts[-1] == 300
    assert session.last_rate_limit_wait_seconds == 10


def test_cooldown_does_not_retry_terminal_limits(tmp_path):
    session, transport = error_session(tmp_path, [retry_notice(), {
        'method': 'error', 'params': {'threadId': 'thread-1', 'turnId': 'turn-1', 'willRetry': False,
                                    'error': {'message': 'quota exhausted', 'codexErrorInfo': 'usageLimitExceeded'}},
    }])
    session.wait_on_rate_limit = True
    notifications = []
    session.rate_limit_callback = notifications.append
    with pytest.raises(RuntimeError) as raised:
        session.run_turn(prompt='test', image_path=None, output_schema=None)
    assert classify_codex_failure(raised.value) == 'codex_usage_limit'
    assert notifications[-1] is None
    assert sum(m.get('method') == 'turn/start' for m in transport.sent) == 1


@pytest.mark.parametrize('cancel', [False, True])
def test_real_stdio_cooldown_resumes_or_can_be_cancelled_without_new_request(tmp_path, cancel):
    script = '''
import json, sys, time
for line in sys.stdin:
    request = json.loads(line)
    method = request.get('method')
    if 'id' not in request:
        continue
    result = {'thread': {'id': 'thread-1'}} if method == 'thread/start' else {}
    if method == 'turn/start':
        result = {'turn': {'id': 'turn-1'}}
    print(json.dumps({'id': request['id'], 'result': result}), flush=True)
    if method == 'turn/start':
        print(json.dumps({'target': 'codex_core::responses_retry', 'fields': {
            'message': 'retrying sampling request in 0.4s', 'turn_id': 'turn-1',
            'sampling_error': '429 Too Many Requests'
        }}), file=sys.stderr, flush=True)
        time.sleep(float(sys.argv[1]))
        print(json.dumps({'method': 'turn/completed', 'params': {'threadId': 'thread-1',
            'turn': {'id': 'turn-1', 'status': 'completed',
                     'items': [{'type': 'agentMessage', 'text': 'recovered'}]}}}), flush=True)
'''
    transport = StdioJsonTransport(sys.executable,
        command=[sys.executable, '-u', '-c', script, '30' if cancel else '0.4'])
    waiting = threading.Event()
    session = CodexAppServerSession('offline', model=None, cwd=tmp_path, timeout_seconds=5,
        wait_on_rate_limit=True, transport_factory=lambda _: transport,
        rate_limit_callback=lambda seconds: waiting.set() if seconds is not None else None)
    watcher = None
    try:
        session.start()
        session.timeout_seconds = 0.2
        session.hard_timeout_seconds = 0.3
        if cancel:
            def stop_wait():
                if waiting.wait(3):
                    session.close()
            watcher = threading.Thread(target=stop_wait)
            watcher.start()
        began = time.perf_counter()
        if cancel:
            with pytest.raises(RuntimeError):
                session.run_turn(prompt='synthetic', image_path=None, output_schema=None)
            assert time.perf_counter() - began < 5
        else:
            assert session.run_turn(prompt='synthetic', image_path=None, output_schema=None) == 'recovered'
            assert session.last_rate_limit_wait_seconds >= .39 - 1e-9  # Monotonic float subtraction.
    finally:
        session.close()
        if watcher:
            watcher.join(5)
    assert transport._process.poll() is not None


def waiting_session(tmp_path, monkeypatch, *, status='active', completion_at=None,
                    during_check=(), hard_timeout=None):
    clock = [0.0]
    monkeypatch.setattr('trace2task.codex_app_server.time.monotonic', lambda: clock[0])

    class StatusTransport(ScriptedTransport):
        def send(self, message):
            super().send(message)
            if message.get('method') == 'thread/read':
                self.messages.extend(during_check)
                if status is not None:
                    thread = {'id': 'thread-1', 'status': {'type': status}}
                    self.messages.append({'id': message['id'], 'result': {'thread': thread}})

        def receive(self, timeout_seconds):
            if self.messages:
                return super().receive(timeout_seconds)
            if completion_at is not None and clock[0] + timeout_seconds >= completion_at:
                clock[0] = completion_at
                return completed_turn('turn-1', 'recovered')
            clock[0] += timeout_seconds
            raise TimeoutError()

    transport = StatusTransport([
        {'id': 1, 'result': {}},
        {'id': 2, 'result': {'thread': {'id': 'thread-1'}}},
        {'id': 3, 'result': {'turn': {'id': 'turn-1'}}},
        {'method': 'item/completed', 'params': {'threadId': 'thread-1', 'turnId': 'turn-1',
                                              'item': {'type': 'userMessage'}}},
    ])
    session = CodexAppServerSession('offline', model=None, cwd=tmp_path, timeout_seconds=300,
        wait_while_active=True, hard_timeout_seconds=hard_timeout,
        transport_factory=lambda _: transport)
    return session, transport, clock


def test_silent_active_turn_survives_300_seconds_without_resubmission(tmp_path, monkeypatch):
    session, transport, clock = waiting_session(tmp_path, monkeypatch, completion_at=435)
    notices = []
    session.active_wait_callback = notices.append
    assert session.run_turn(prompt='synthetic', image_path=None, output_schema=None) == 'recovered'
    assert clock[0] == 435
    assert notices == [300, 360, 420]
    assert session.last_turn_wait_extended
    assert session.last_rate_limit_wait_seconds == 0  # Silence never pauses the run budget.
    assert sum(m.get('method') == 'turn/start' for m in transport.sent) == 1
    checks = [m for m in transport.sent if m.get('method') == 'thread/read']
    assert len(checks) == 3
    assert all(m['params'] == {'threadId': 'thread-1', 'includeTurns': False} for m in checks)
    # The flag applies only to the delayed turn, not all subsequent plans.
    transport.messages.extend([{'id': 7, 'result': {'turn': {'id': 'turn-2'}}},
                               completed_turn('turn-2', 'next')])
    assert session.run_turn(prompt='next', image_path=None, output_schema=None) == 'next'
    assert not session.last_turn_wait_extended


@pytest.mark.parametrize('status', ['active', 'idle', None])
def test_completion_arriving_during_health_check_is_not_lost(tmp_path, monkeypatch, status):
    session, transport, _ = waiting_session(tmp_path, monkeypatch, status=status,
        during_check=[completed_turn('turn-1', 'late final')])
    assert session.run_turn(prompt='synthetic', image_path=None, output_schema=None) == 'late final'
    assert sum(m.get('method') == 'turn/start' for m in transport.sent) == 1


def test_idle_status_allows_a_short_grace_for_final_notification(tmp_path, monkeypatch):
    session, _, clock = waiting_session(tmp_path, monkeypatch, status='idle', completion_at=302)
    assert session.run_turn(prompt='synthetic', image_path=None, output_schema=None) == 'recovered'
    assert clock[0] == 302


@pytest.mark.parametrize('status,category,elapsed', [
    ('idle', 'codex_thread_error', 305),
    ('notLoaded', 'codex_thread_error', 300),
    ('systemError', 'codex_thread_error', 301),
    ('unknown', 'codex_status_unavailable', 300),
    (None, 'codex_status_unavailable', 305),
])
def test_inactive_or_unresponsive_status_fails_boundedly(tmp_path, monkeypatch, status, category, elapsed):
    session, transport, clock = waiting_session(tmp_path, monkeypatch, status=status)
    with pytest.raises(RuntimeError) as raised:
        session.run_turn(prompt='synthetic', image_path=None, output_schema=None)
    assert classify_codex_failure(raised.value) == category
    assert clock[0] == elapsed
    assert sum(m.get('method') == 'thread/read' for m in transport.sent) == 1
    assert sum(m.get('method') == 'turn/start' for m in transport.sent) == 1


def test_active_status_does_not_override_hard_limit(tmp_path, monkeypatch):
    session, transport, clock = waiting_session(tmp_path, monkeypatch, hard_timeout=350)
    with pytest.raises(CodexTurnTimeoutError) as raised:
        session.run_turn(prompt='synthetic', image_path=None, output_schema=None)
    assert raised.value.category == 'hard_timeout'
    assert clock[0] == 350
    assert sum(m.get('method') == 'thread/read' for m in transport.sent) == 1


def test_terminal_error_during_health_check_preserves_its_cause(tmp_path, monkeypatch):
    session, _, _ = waiting_session(tmp_path, monkeypatch, during_check=[
        {'method': 'error', 'params': {'threadId': 'thread-1', 'turnId': 'turn-1',
            'willRetry': False, 'error': {'message': 'usage exhausted', 'codexErrorInfo': 'usageLimitExceeded'}}},
    ])
    with pytest.raises(RuntimeError) as raised:
        session.run_turn(prompt='synthetic', image_path=None, output_schema=None)
    assert classify_codex_failure(raised.value) == 'codex_usage_limit'


@pytest.mark.parametrize('cancel', [False, True])
def test_real_stdio_silent_wait_can_complete_or_be_stopped(tmp_path, cancel):
    script = '''
import json, sys, threading, time
def complete():
    time.sleep(.1)
    print(json.dumps({'method': 'turn/completed', 'params': {'threadId': 'thread-1',
        'turn': {'id': 'turn-1', 'status': 'completed',
                 'items': [{'type': 'agentMessage', 'text': 'recovered'}]}}}), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    if 'id' not in request:
        continue
    method, result = request.get('method'), {}
    if method == 'thread/start':
        result = {'thread': {'id': 'thread-1'}}
    elif method == 'turn/start':
        result = {'turn': {'id': 'turn-1'}}
    elif method == 'thread/read':
        result = {'thread': {'id': 'thread-1', 'status': {'type': 'active'}}}
    print(json.dumps({'id': request['id'], 'result': result}), flush=True)
    if method == 'thread/read' and sys.argv[1] == 'complete':
        threading.Thread(target=complete, daemon=True).start()
'''
    transport = StdioJsonTransport(sys.executable, command=[sys.executable, '-u', '-c', script,
                                                          'cancel' if cancel else 'complete'])
    waiting = threading.Event()
    session = CodexAppServerSession('offline', model=None, cwd=tmp_path, timeout_seconds=5,
        wait_while_active=True, transport_factory=lambda _: transport,
        active_wait_callback=lambda _: waiting.set())
    watcher = None
    try:
        session.start()
        session.timeout_seconds = .2
        if cancel:
            def stop_wait():
                if waiting.wait(3):
                    session.close()
            watcher = threading.Thread(target=stop_wait)
            watcher.start()
        began = time.perf_counter()
        if cancel:
            with pytest.raises(RuntimeError):
                session.run_turn(prompt='synthetic', image_path=None, output_schema=None)
        else:
            assert session.run_turn(prompt='synthetic', image_path=None, output_schema=None) == 'recovered'
        assert waiting.is_set() and session.last_turn_wait_extended
        assert time.perf_counter() - began < 5
    finally:
        session.close()
        if watcher:
            watcher.join(5)
    assert transport._process.poll() is not None
