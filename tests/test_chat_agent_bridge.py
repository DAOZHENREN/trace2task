"""No native input: chat providers exercise the actual shared loop and Cua core."""

import json
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pygame
from PIL import Image

from trace2task.chat_agent_runner import run_chat_agent
from trace2task.chat_model_adapter import ChatModelAdapter, plan_schema
from trace2task.model_api import ModelAPIConfig, ModelAPISession


class Session:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.audit = None

    def reset_thread(self):
        return None

    def run_turn(self, **request):
        self.requests.append(request)
        return self.replies.pop(0)

    def close(self):
        return None


class Stop:
    def start(self):
        return None

    def close(self):
        return None

    def raise_if_requested(self):
        return None

    def sleep(self, _duration):
        return None


class Driver:
    target: ClassVar = {"pid": 10, "window_id": 20}
    bounds: ClassVar = {"x": 0, "y": 0, "width": 100, "height": 100}

    def __init__(self, root):
        self.root = root
        self.calls = []
        self.observations = []

    def start(self):
        return None

    def close(self):
        return None

    def bind(self, target):
        assert target == self.target
        return dict(self.target)

    def window(self, target):
        assert target == self.target
        return {**self.target, "bounds": dict(self.bounds),
                "title": "Temporary Test", "app_name": "fake.exe"}

    def observe(self, target, index):
        assert target == self.target
        self.observations.append(index)
        path = self.root / f"{index}.png"
        Image.new("RGB", (100, 100), (index * 40 % 255, 0, 0)).save(path)
        return {**self.target, "session": "fake", "window_bounds": dict(self.bounds),
                "screenshot_width": 100, "screenshot_height": 100,
                "elements_complete": False, "elements": []}, path

    def call(self, name, payload):
        self.calls.append((name, payload))
        return {"effect": "unverifiable", "route": "fake"}


def _reply(actions=None, *, complete=False):
    return json.dumps({"task_complete": complete, "reason": "visible test choice",
                       "actions": actions or []})


def _capture_round(sink, entry):
    sink.append(dict(entry))


def test_adapter_keeps_chat_json_and_rejects_malformed_plan_without_dispatch(tmp_path):
    screenshot = tmp_path / "screen.png"
    screenshot.write_bytes(b"image")
    session = Session([_reply([{"skill": "click", "args": {"x": 1.5, "y": .5}}]),
                       _reply([], complete=True)])
    adapter = ChatModelAdapter(session, selected_windows=True)
    invalid = adapter.predict("test", screenshot=screenshot, history=[])
    assert invalid["status"] == "format_rejected"
    assert len(session.requests) == 1
    assert session.requests[0]["image_path"] == screenshot
    assert "switch_window" in json.dumps(plan_schema(selected_windows=True))
    done = adapter.predict("test", screenshot=screenshot, history=[])
    assert done["prediction"]["actions"] == [{"done": True}]


def test_actual_model_api_transport_keeps_native_messages_and_full_image(tmp_path):
    screenshot = tmp_path / "screen.png"
    Image.new("RGB", (16, 16), "white").save(screenshot)
    requests = []

    def requester(_config, payload):
        requests.append(payload)
        return {"choices": [{"finish_reason": "stop", "message": {"content": _reply(
            [{"skill": "click", "args": {"x": .5, "y": .5, "button": "left"}}])}}]}

    session = ModelAPISession(
        ModelAPIConfig(base_url="http://127.0.0.1:8081/v1", api_key="local-only"),
        model="qwen-local", reasoning_effort="default", requester=requester,
    )
    session.system_guidance = "Prefer the temporary test window."
    try:
        predicted = ChatModelAdapter(session, selected_windows=False, provider="api").predict(
            "Click the test", screenshot=screenshot, history=[])
        assert predicted["status"] == "predicted"
        assert requests[0]["model"] == "qwen-local"
        assert requests[0]["messages"][0]["role"] == "system"
        assert "Prefer the temporary test window." in requests[0]["messages"][0]["content"]
        assert predicted["input_messages"][0]["content"] == requests[0]["messages"][0]["content"]
        assert "base64" not in json.dumps(predicted["input_messages"])
        assert requests[0]["messages"][-1]["content"][1]["image_url"]["url"].startswith(
            "data:image/png;base64,")
        assert requests[0]["response_format"]["type"] == "json_schema"
        assert requests[0]["response_format"]["json_schema"]["schema"]["properties"]["actions"]["maxItems"] == 8
    finally:
        session.close()


def test_codex_and_api_session_formats_share_cua_loop_and_live_receipts(tmp_path):
    for provider in ("codex", "api"):
        root = tmp_path / provider
        root.mkdir()
        driver = Driver(root)
        session = Session([
            _reply([{"skill": "click", "args": {"x": .5, "y": .5, "button": "left"}}]),
            _reply(complete=True),
            json.dumps({"verdict": "complete", "evidence": "test result visible", "missing": ""}),
        ])
        live = []
        result = run_chat_agent(
            instruction="click the temporary test", model="fake-chat", reasoning_effort="low",
            output_root=root, emergency_stop=Stop(), status_callback=lambda _message: None,
            executor_backend="cua", cua_target=Driver.target,
            api_config=SimpleNamespace() if provider == "api" else None,
            cua_driver=driver, session=session, prompt_guidance="Open the temporary test.",
            on_model_round=partial(_capture_round, live),
        )
        assert result["actions"] == 1
        assert result["stop_reason"] == "visual_completion_reviewed"
        assert result["task_complete"] is True and result["verified"] is False
        assert len(driver.calls) == 1 and driver.calls[0][0] == "click"
        assert len(driver.observations) == 3
        assert result["history"][0]["effect"] == "unverifiable"
        assert any(item.get("execution", {}).get("status") == "delivered" for item in live)
        first_round = result['model_io'][0]
        assert 'visible test choice' in first_round['raw_output']
        assert first_round['execution']['normalized_plan']['actions'] == [
            {'skill': 'click', 'args': {'x': .5, 'y': .5, 'button': 'left'}}]
        actual_request = first_round['execution']['executor_requests'][0]
        assert actual_request['status'] == 'delivered'
        assert actual_request['request']['args'] == driver.calls[0][1]
        assert (Path(result['trace_path']).parent / 'model-io.json').is_file()
        assert any(item.get("purpose") == "verify_completion" for item in live)
        assert "Authorized target context" in session.requests[0]["prompt"]
        if provider == "codex":
            assert "Open the temporary test." in session.requests[0]["prompt"]
            assert any(item.get("system_managed_externally") is True for item in live)
        assert result["audit_path"].endswith("events.jsonl")


def test_api_live_round_shows_real_system_message_without_inline_image(tmp_path):
    replies = [
        _reply([{"skill": "click", "args": {"x": .5, "y": .5, "button": "left"}}]),
        _reply(complete=True),
        json.dumps({"verdict": "complete", "evidence": "visible", "missing": ""}),
    ]

    def requester(_config, _payload):
        return {"choices": [{"finish_reason": "stop", "message": {"content": replies.pop(0)}}]}

    session = ModelAPISession(
        ModelAPIConfig(base_url="http://127.0.0.1:8081/v1", api_key="local-only"),
        model="qwen-local", requester=requester,
    )
    result = run_chat_agent(
        instruction="click", model="qwen-local", reasoning_effort="default",
        output_root=tmp_path, emergency_stop=Stop(), status_callback=lambda _message: None,
        executor_backend="cua", cua_target=Driver.target,
        api_config=SimpleNamespace(), cua_driver=Driver(tmp_path), session=session,
        prompt_guidance="Use the visible result only.",
    )
    messages = result["model_io"][0]["input"]["messages"]
    assert [message["role"] for message in messages] == ["system", "user"]
    assert "Use the visible result only." in messages[0]["content"]
    assert "data:image" not in json.dumps(messages)


def test_chat_plan_uses_the_same_loop_with_win32_backend_without_native_input(tmp_path):
    class Backend:
        def __init__(self):
            self.calls = []

        def foreground_handle(self):
            return 99

        def set_cursor_position(self, x, y):
            self.calls.append(("cursor", x, y))

        def send_mouse_button(self, button, down):
            self.calls.append(("button", button, down))

    class Capture:
        def capture(self, _window):
            return pygame.Surface((100, 100))

    backend = Backend()
    session = Session([
        _reply([{"skill": "click", "args": {"x": .5, "y": .5, "button": "left"}}]),
        _reply(complete=True),
        json.dumps({"verdict": "complete", "evidence": "test result visible", "missing": ""}),
    ])
    result = run_chat_agent(
        instruction="click", model="fake-chat", reasoning_effort="low",
        output_root=tmp_path, emergency_stop=Stop(), status_callback=lambda _message: None,
        executor_backend="win32", backend=backend, capture=Capture(), size=lambda: (100, 100),
        session=session,
    )
    assert result["actions"] == 1 and result["stop_reason"] == "visual_completion_reviewed"
    assert backend.calls == [("cursor", 50, 50), ("button", "left", True),
                             ("button", "left", False)]
    assert result["history"][0]["delivery_mode_requested"] == "foreground"


def test_invalid_chat_action_is_repaired_from_new_observation_without_input(tmp_path):
    driver = Driver(tmp_path)
    session = Session([
        "not json",
        _reply([{"skill": "click", "args": {"x": .5, "y": .5, "button": "left"}}]),
        _reply(complete=True),
        json.dumps({"verdict": "unknown", "evidence": "offscreen", "missing": "result"}),
    ])
    result = run_chat_agent(
        instruction="click", model="fake-chat", reasoning_effort="low",
        output_root=tmp_path, emergency_stop=Stop(), status_callback=lambda _message: None,
        executor_backend="cua", cua_target=Driver.target, cua_driver=driver, session=session,
    )
    assert result["actions"] == 1
    assert result["stop_reason"] == "completion_unverifiable"
    assert len(driver.calls) == 1
    assert len(driver.observations) == 4


def test_chat_multi_action_batch_keeps_order_and_reobserves_after_batch(tmp_path):
    driver = Driver(tmp_path)
    first = {"skill": "click", "args": {"x": .25, "y": .25, "button": "left"}}
    second = {"skill": "click", "args": {"x": .75, "y": .75, "button": "left"}}
    session = Session([
        _reply([first, second]), _reply(complete=True),
        json.dumps({"verdict": "unknown", "evidence": "test view", "missing": "not verified"}),
    ])
    result = run_chat_agent(
        instruction="click twice", model="fake-chat", reasoning_effort="low",
        output_root=tmp_path, emergency_stop=Stop(), status_callback=lambda _message: None,
        executor_backend="cua", cua_target=Driver.target, cua_driver=driver, session=session,
    )
    assert result["actions"] == 2
    assert [call[1]["x"] for call in driver.calls] == [25, 75]
    assert len(driver.observations) == 3
    assert [entry["action"]["actions"][0] for entry in result["history"]] == [first, second]
