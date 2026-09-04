from __future__ import annotations

import base64
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.request import ProxyHandler, build_opener

import pytest

from trace2task import cli, model_api
from trace2task.model_api import ModelAPIConfig, ModelAPISession, validate_api_model

SCHEMA = {
    "type": "object",
    "properties": {"actions": {"type": "array", "items": {"type": "string"}}},
    "required": ["actions"],
    "additionalProperties": False,
}
KEY = "test-only-secret-key"


def _completion(text: str = '{"actions": ["click", "wait"]}') -> dict[str, Any]:
    return {"choices": [{"finish_reason": "stop", "message": {"content": text}}]}


def _turn(session: ModelAPISession, **kwargs: Any) -> str:
    return session.run_turn(
        prompt="Plan the next safe actions.", image_path=None, output_schema=SCHEMA, **kwargs
    )


@pytest.mark.parametrize(
    "settings",
    [
        {"base_url": "http://remote.example/v1"},
        {"base_url": "https://key@example.com/v1"},
        {"base_url": "https://example.com/v1?key=secret"},
        {"base_url": "file:///tmp/model"},
        {"base_url": "https://example.com:0/v1"},
        {"base_url": "https://example.com:bad/v1"},
        {"base_url": "https://example.com/v1\n"},
        {"api_key": "key\n"},
        {"api_key_env": "bad var"},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": True},
        {"response_format": "text"},
    ],
)
def test_api_config_rejects_unsafe_or_invalid_settings(settings: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        ModelAPIConfig(**settings)


def test_credentials_are_explicit_and_never_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRACE2TASK_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    assert ModelAPIConfig().with_credentials().api_key == KEY
    with pytest.raises(ValueError, match="API Key"):
        ModelAPIConfig(base_url="https://other.example/v1").with_credentials()
    monkeypatch.setenv("CUSTOM_MODEL_KEY", "custom-secret")
    config = ModelAPIConfig(
        base_url="http://localhost:9000/v1/", api_key_env="CUSTOM_MODEL_KEY"
    ).with_credentials()
    assert config.endpoint == "http://localhost:9000/v1/chat/completions"
    assert config.api_key == "custom-secret"
    assert "custom-secret" not in repr(config)
    assert ModelAPIConfig(api_key=KEY).with_credentials().api_key == KEY
    assert ModelAPIConfig(
        base_url="https://other.example/v1/chat/completions", api_key=KEY
    ).endpoint == "https://other.example/v1/chat/completions"
    assert validate_api_model(" vendor/vision-model ") == "vendor/vision-model"
    with pytest.raises(ValueError):
        validate_api_model("")


def test_multimodal_request_schema_history_and_reset(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    def request(config: ModelAPIConfig, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(payload)
        assert config.api_key == KEY
        return _completion()

    current, reference = tmp_path / "current.png", tmp_path / "reference.png"
    current.write_bytes(b"current-png")
    reference.write_bytes(b"reference-png")
    session = ModelAPISession(
        ModelAPIConfig(api_key=KEY), model="vendor/vision-model", requester=request
    )
    session.run_turn(
        prompt="Current screenshot, then reference.", image_path=current,
        additional_image_paths=(reference,), output_schema=SCHEMA,
    )
    first = calls[0]
    assert first["model"] == "vendor/vision-model"
    assert first["stream"] is False
    assert "reasoning_effort" not in first
    assert first["response_format"]["json_schema"]["strict"] is True
    assert first["response_format"]["json_schema"]["schema"] == SCHEMA
    assert KEY not in json.dumps(first)
    images = first["messages"][-1]["content"][1:]
    assert [
        base64.b64decode(item["image_url"]["url"].split(",", 1)[1]) for item in images
    ] == [b"current-png", b"reference-png"]
    current.unlink()
    reference.unlink()
    assert _turn(session, reasoning_effort="low") == '{"actions": ["click", "wait"]}'
    assert [message["role"] for message in calls[1]["messages"]] == [
        "system", "user", "assistant", "user",
    ]
    assert calls[1]["reasoning_effort"] == "low"
    assert session.last_turn_metrics.thread_reused is True
    session.reset_thread()
    _turn(session)
    assert len(calls[2]["messages"]) == 2
    assert session.last_turn_metrics.thread_reused is False
    assert session.last_turn_metrics.thread_generation == 2
    session.close()
    with pytest.raises(RuntimeError, match="closed"):
        _turn(session)


def test_json_object_mode_omits_strict_schema() -> None:
    calls = []
    session = ModelAPISession(
        ModelAPIConfig(api_key=KEY, response_format="json_object"), model="vision-model",
        requester=lambda config, payload: calls.append(payload) or _completion(),
    )
    _turn(session)
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert json.dumps(SCHEMA, separators=(",", ":")) in calls[0]["messages"][0]["content"]


@pytest.mark.parametrize(
    "response",
    [
        {"choices": []},
        {"choices": [{"finish_reason": "length", "message": {"content": '{"actions":[]}'}}]},
        {"choices": [{"finish_reason": "stop", "message": {"refusal": "No"}}]},
        {"choices": [{"finish_reason": "stop", "message": {"tool_calls": [{"id": "1"}]}}]},
        _completion(""), _completion("not-json"), _completion("[]"),
        _completion(json.dumps({"credential": KEY})),
    ],
)
def test_invalid_or_partial_responses_never_enter_plan_history(response: dict[str, Any]) -> None:
    session = ModelAPISession(
        ModelAPIConfig(api_key=KEY), model="vision-model",
        requester=lambda config, payload: response,
    )
    with pytest.raises(RuntimeError) as error:
        _turn(session)
    assert KEY not in str(error.value)
    assert not session._history


def test_request_error_does_not_expose_key() -> None:
    def request(config: ModelAPIConfig, payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError(f"Remote error included {config.api_key}")

    session = ModelAPISession(ModelAPIConfig(api_key=KEY), model="vision", requester=request)
    with pytest.raises(RuntimeError) as error:
        _turn(session)
    assert KEY not in str(error.value)


def test_provider_error_detail_preserves_cause_but_redacts_secrets() -> None:
    raw = json.dumps({"error": {"message": (
        f"response_format json_schema is unsupported. Authorization: Bearer {KEY} "
        "api_key=another-secret https://service.example?key=secret "
        "data:image/png;base64,privatepixels"
    )}}).encode()
    detail = model_api._provider_error_detail(raw, KEY)
    assert "response_format json_schema is unsupported" in detail
    assert KEY not in detail
    assert "another-secret" not in detail
    assert "privatepixels" not in detail
    assert "https://" not in detail
    assert len(detail) <= 400
    assert model_api._provider_error_detail(b"<html>private proxy error</html>", KEY) == ""


def test_http_400_includes_sanitized_provider_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        model_api, "build_opener", lambda *handlers: build_opener(ProxyHandler({}), *handlers)
    )
    body = json.dumps({"error": {"message": (
        f"Unsupported parameter: response_format.json_schema. key={KEY}"
    )}}).encode()
    with _http_server(400, body) as (base, calls):
        session = ModelAPISession(
            ModelAPIConfig(base_url=base, api_key=KEY), model="custom-vision",
        )
        with pytest.raises(RuntimeError) as error:
            _turn(session)
        message = str(error.value)
        assert "response_format.json_schema" in message
        assert "format=json_schema, reasoning=default" in message
        assert KEY not in message
        assert len(calls) == 1


def test_stop_before_request_does_not_contact_provider() -> None:
    def stop() -> None:
        raise InterruptedError("Emergency stop")

    session = ModelAPISession(
        ModelAPIConfig(api_key=KEY), model="vision", stop_check=stop,
        requester=lambda *args: pytest.fail("Request must not start after stop"),
    )
    with pytest.raises(InterruptedError):
        _turn(session)


@pytest.mark.parametrize("cancel", [True, False])
def test_interrupt_or_timeout_discards_late_response(cancel: bool) -> None:
    started, release = threading.Event(), threading.Event()

    def request(config: ModelAPIConfig, payload: dict[str, Any]) -> dict[str, Any]:
        started.set()
        assert release.wait(timeout=5)
        return _completion()

    def stop() -> None:
        if cancel and started.is_set():
            raise InterruptedError("Emergency stop")

    session = ModelAPISession(
        ModelAPIConfig(api_key=KEY, timeout_seconds=1), model="vision",
        stop_check=stop, requester=request,
    )
    try:
        with pytest.raises(InterruptedError if cancel else RuntimeError):
            _turn(session)
        assert not session._history
    finally:
        session.close()
        release.set()


@contextmanager
def _http_server(status: int, body: bytes) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    calls: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            calls.append({
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "body": json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
            })
            self.send_response(status)
            if status == 302:
                self.send_header("Location", "/must-not-follow")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            calls.append({"unexpected_get": self.path})
            self.send_response(500)
            self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("status", [200, 302, 401, 429])
def test_http_transport_auth_errors_and_redirects(
    status: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        model_api, "build_opener", lambda *handlers: build_opener(ProxyHandler({}), *handlers)
    )
    body = json.dumps(_completion() if status == 200 else {"error": KEY}).encode()
    with _http_server(status, body) as (base, calls):
        session = ModelAPISession(ModelAPIConfig(base_url=base, api_key=KEY), model="vision")
        if status == 200:
            assert json.loads(_turn(session))["actions"] == ["click", "wait"]
        else:
            with pytest.raises(RuntimeError, match=f"HTTP {status}") as error:
                _turn(session)
            assert KEY not in str(error.value)
        assert len(calls) == 1
        assert calls[0]["authorization"] == f"Bearer {KEY}"
        assert calls[0]["path"] == "/v1/chat/completions"
        assert KEY not in json.dumps(calls[0]["body"])


@pytest.mark.parametrize("provider", ["codex", "api"])
def test_cli_dispatch_preserves_codex_and_supports_api(
    provider: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    @dataclass
    class Result:
        success: bool = True

    calls = []
    monkeypatch.setenv("TEST_MODEL_KEY", KEY)
    monkeypatch.setattr(
        cli, "run_windows_agent", lambda *args, **kwargs: calls.append(kwargs) or Result()
    )
    cli.main([
        "windows", "agent", "--task", "task.yaml", "--provider", provider,
        "--model", "vision-model", "--api-key-env", "TEST_MODEL_KEY",
        "--api-base-url", "https://provider.example/v1",
    ])
    assert calls[0]["model"] == "vision-model"
    if provider == "api":
        assert calls[0]["api_config"].api_key == KEY
        assert calls[0]["reasoning_effort"] == "default"
    else:
        assert calls[0]["api_config"] is None
        assert calls[0]["reasoning_effort"] == "low"
    assert KEY not in capsys.readouterr().out
