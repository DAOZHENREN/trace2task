import base64
import hashlib
import json
import threading
from types import SimpleNamespace

import pytest

from trace2task.rsi_codex import (
    CODEX_AUTHENTICATION_REQUIRED,
    CodexRSITransport,
    PracticeBudgetExceeded,
    classify_codex_authentication_failure,
    install_upstream_transport,
    prepare_transcript,
    safe_codex_failure_text,
)

PNG = b"\x89PNG\r\n\x1a\narchived-image"


def test_transcript_preserves_roles_and_all_image_evidence(tmp_path):
    history = [{"role": "user", "content": "before", "_rsiagent_images": [{
        "data": base64.b64encode(PNG).decode(), "sha256": hashlib.sha256(PNG).hexdigest(),
    }]}, {"role": "assistant", "content": '{"action":"look"}'}]
    prompt, images = prepare_transcript("actor", "now", history, PNG, tmp_path)
    assert [p.read_bytes() for p in images] == [PNG, PNG]
    assert '"attachment": 1' in prompt and '"attachment": 2' in prompt
    assert prompt.index("before") < prompt.index("look") < prompt.index("now")
    assert "_rsiagent_images" in history[0]  # no mutation of official evidence


def test_corrupted_image_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="digest"):
        prepare_transcript("", "", [{"role": "user", "content": "", "_rsiagent_images": [
            {"data": base64.b64encode(PNG).decode(), "sha256": "wrong"},
        ]}], None, tmp_path)


class Session:
    def __init__(self, answer="answer"):
        self.answer = answer
        self.closed = False

    def run_turn(self, **kwargs):
        self.request = kwargs
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    def close(self):
        self.closed = True


def test_roles_separate_and_budget_audited(tmp_path):
    sessions = []
    def factory(system, model, effort, call_dir):
        assert call_dir.is_dir()
        session = Session()
        sessions.append((system, model, effort, session))
        return session
    adapter = CodexRSITransport(session_factory=factory, model="selected-model",
                               reasoning_effort="low", audit_dir=tmp_path,
                               max_calls=2, wall_seconds=10)
    assert adapter.chat("original-model", "actor", "do") == "answer"
    assert adapter.chat("original-model", "verifier", "check") == "answer"
    assert sessions[0][0].startswith("actor\n\nTrace2Task execution boundary")
    assert sessions[1][0].startswith("verifier\n\nTrace2Task execution boundary")
    assert "native Codex applies only to that\ncontainer" in sessions[0][0]
    assert all(s[3].closed for s in sessions)
    with pytest.raises(PracticeBudgetExceeded):
        adapter.chat("original-model", "curriculum", "next")
    result = json.loads((tmp_path / "call-000001/result.json").read_text())
    assert result["answer"] == "answer"
    assert result["model"] == "selected-model"
    assert result["upstream_model"] == "original-model"
    assert result["system"] == "actor"  # official role text remains verbatim
    assert result["model_system"] == sessions[0][0]


def test_transport_failure_not_an_empty_agent_reply(tmp_path):
    session = Session(RuntimeError("disconnected"))
    adapter = CodexRSITransport(session_factory=lambda *args: session, model="model",
                               reasoning_effort="low", audit_dir=tmp_path,
                               max_calls=2, wall_seconds=10)
    class TransportError(RuntimeError):
        def __init__(self, model, cause, **kwargs):
            self.recoverable = kwargs["recoverable"]
    client = SimpleNamespace(_LAST_REASONING=SimpleNamespace(value="old"),
                             LLMTransportError=TransportError)
    install_upstream_transport(client, adapter)
    with pytest.raises(TransportError):
        client.chat("model", "actor", "do")
    assert session.closed
    assert client._LAST_REASONING.value == ""
    assert json.loads((tmp_path / "call-000001/result.json").read_text())["status"] == "error"


@pytest.mark.parametrize(("failure", "expected"), [
    (RuntimeError("HTTP 401 Unauthorized"), CODEX_AUTHENTICATION_REQUIRED),
    (RuntimeError("refresh_token invalid_grant"), CODEX_AUTHENTICATION_REQUIRED),
    (RuntimeError("access token has expired"), CODEX_AUTHENTICATION_REQUIRED),
    (TimeoutError("request timed out"), None),
    (ConnectionError("network disconnected"), None),
    (RuntimeError("HTTP 500 upstream failure"), None),
])
def test_only_explicit_login_failures_get_a_stable_authentication_category(failure, expected):
    assert classify_codex_authentication_failure(failure) == expected


def test_explicit_login_failure_is_redacted_in_audit_text():
    error = RuntimeError("HTTP 401 Unauthorized: private provider body")
    assert safe_codex_failure_text(error) == "codex_authentication_required; manual login required"
    assert "private" not in safe_codex_failure_text(error)


def test_explicit_login_failure_is_nonrecoverable_and_does_not_expose_provider_text(tmp_path):
    adapter = CodexRSITransport(
        session_factory=lambda *args: Session(RuntimeError("HTTP 401 Unauthorized: private provider body")),
        model="model", reasoning_effort="low", audit_dir=tmp_path, max_calls=1, wall_seconds=5,
    )

    class TransportError(RuntimeError):
        def __init__(self, model, cause, **kwargs):
            self.recoverable = kwargs["recoverable"]
            self.detail = kwargs["detail"]

    client = SimpleNamespace(_LAST_REASONING=SimpleNamespace(value=""), LLMTransportError=TransportError)
    install_upstream_transport(client, adapter)
    with pytest.raises(TransportError) as result:
        client.chat("model", "actor", "do")
    assert result.value.recoverable is False
    assert result.value.detail == (
        "Codex subscription transport: codex_authentication_required; manual login required"
    )


def test_stop_closes_inflight_session_and_discards_response(tmp_path):
    stop = threading.Event()
    closed = threading.Event()
    class BlockingSession(Session):
        def run_turn(self, **kwargs):
            stop.set()
            assert closed.wait(2)
            return "must not execute"
        def close(self):
            closed.set()
    adapter = CodexRSITransport(session_factory=lambda *args: BlockingSession(), model="model",
                               reasoning_effort="low", audit_dir=tmp_path,
                               max_calls=2, wall_seconds=10, stop_event=stop)
    with pytest.raises(PracticeBudgetExceeded):
        adapter.chat("model", "actor", "do")
    assert closed.is_set()


@pytest.mark.parametrize(("failure", "recoverable"), [
    (TimeoutError("request timed out"), True),
    (ConnectionError("transport disconnected"), True),
    (RuntimeError("Model-only process requested an operational tool"), False),
    (PermissionError("evidence directory denied"), False),
    (RuntimeError("unclassified server failure"), False),
])
def test_only_concrete_transport_faults_are_recoverable(tmp_path, failure, recoverable):
    adapter = CodexRSITransport(
        session_factory=lambda *args: Session(failure), model="model", reasoning_effort="low",
        audit_dir=tmp_path, max_calls=1, wall_seconds=5)
    class TransportError(RuntimeError):
        def __init__(self, model, cause, **kwargs):
            self.recoverable = kwargs["recoverable"]
    client = SimpleNamespace(_LAST_REASONING=SimpleNamespace(value=""),
                             LLMTransportError=TransportError)
    install_upstream_transport(client, adapter)
    with pytest.raises(TransportError) as result:
        client.chat("model", "actor", "do")
    assert result.value.recoverable is recoverable


def test_persistent_call_sequence_is_reserved_before_network_io(tmp_path):
    events = []
    def reserve():
        events.append("reserved")
        return 8
    def factory(*args):
        events.append("network")
        return Session()
    adapter = CodexRSITransport(session_factory=factory, model="model", reasoning_effort="low",
                               audit_dir=tmp_path, max_calls=1, wall_seconds=5, reserve_call=reserve)
    assert adapter.chat("model", "actor", "do") == "answer"
    assert events == ["reserved", "network"]
    assert (tmp_path / "call-000008/result.json").is_file()
    assert not (tmp_path / "call-000001").exists()
