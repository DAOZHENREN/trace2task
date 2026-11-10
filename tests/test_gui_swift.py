"""Published Desktop131 integration, isolated from GPU and guest execution."""

import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_mai_swift import request
from test_local_gui_service import service_module as service_module
from trace2task.local_gui_swift import history_native, tool_call
from trace2task.local_gui_protocol import decode, prompt
from trace2task.local_gui_client import service_route
from trace2task.model_registry import profile_for


def test_profile_and_native_decode():
    assert (
        profile_for("gui-swift-4b", "gui-swift-native131").repository == "dongjintao/GUI-Swift-4B"
    )
    with pytest.raises(ValueError):
        profile_for("gui-swift-4b", "transformers")
    assert "131 value slots" in prompt("gui-swift-4b")
    raw = tool_call({"action": "left_click", "coordinate": [250, 750]})
    assert decode("gui-swift-4b", raw)["actions"][0]["args"] == {
        "x": 0.25,
        "y": 0.75,
        "button": "left",
    }
    assert (
        decode("gui-swift-4b", tool_call({"action": "terminate", "status": "failure"}))["control"]
        == "terminate_failure"
    )


def test_director_receipts_convert_coordinate_scale_and_preserve_text():
    raw = "Executed guidance action : " + json.dumps(
        {"skill": "click", "args": {"x": 0.25, "y": 0.75, "button": "left"}}
    )
    assert history_native(raw) == tool_call({"action": "left_click", "coordinate": [250, 750]})
    value = "=Supplies.A2\t=Supplies.D2\t=Supplies.C2"
    raw = "Executed guidance action : " + json.dumps(
        {"skill": "type_text", "args": {"text": value}}
    )
    assert history_native(raw) == tool_call({"action": "type", "text": value})


def test_routing_keeps_mai_separate(monkeypatch, tmp_path):
    monkeypatch.setenv("TRACE2TASK_DATA_ROOT", str(tmp_path))
    assert service_route("gui-swift-4b") == (
        "http://127.0.0.1:8769",
        tmp_path / "runs/local-gui/swift",
    )
    assert service_route("mai-swift-2b") == ("http://127.0.0.1:8768", tmp_path / "runs/local-gui")


def test_shared_service_guided_history_and_readonly_review(service_module, tmp_path):  # noqa: F811 - imported pytest fixture
    engine = service_module.Engine(tmp_path, tmp_path / "out", "gui-swift-native131")
    engine.mai.load = lambda: SimpleNamespace(model=object(), processor=object())
    calls = []

    def predict(messages, images, folder):
        calls.append(messages)
        return (
            tool_call({"action": "left_click", "coordinate": [250, 750]}),
            500,
            ["screenshot.png"],
        )

    engine.mai.predict = predict
    payload = request()
    payload.update(
        model="gui-swift-4b",
        expected_backend="gui-swift-native131",
        conversation_id="swift-test-conversation",
        conversation_start=True,
        guided_history_offset=0,
        guided_history=[],
    )
    first = engine.predict(payload)
    assert first["status"] == "predicted", first
    assert engine.conversation.total_turns == 0
    payload.update(
        conversation_start=False,
        guided_history=[{"raw_output": first["raw_output"], "image": payload["image"]}],
    )
    second = engine.predict(payload)
    assert second["status"] == "predicted", second
    assert engine.conversation.total_turns == 1
    record = json.loads((Path(second["output_directory"]) / "input.json").read_text())
    assert record["configuration"]["slots"] == 131
    payload.update(
        guided_history_offset=1,
        guided_history=[],
        execution_context={"purpose": "verify_completion"},
    )
    reviewed = engine.predict(payload)
    assert reviewed["verification"]["verdict"] == "unknown"
    assert len(calls) == 2


def test_image_archive_reuses_pixels_without_changing_history(tmp_path):
    from PIL import Image
    from trace2task.local_gui_swift import NativeRuntime

    runtime = NativeRuntime()
    requests = []

    def predict(value):
        requests.append(value)
        return {"action": {"action": "wait", "time": 1}, "parse_error": None, "input_tokens": 100}

    runtime.runtime = SimpleNamespace(inputs=lambda x: x, predict=predict)
    image = Image.new("RGB", (20, 10), "red")
    messages = [
        {"role": "user", "content": [{"type": "image"}, {"type": "text", "text": "Click Save"}]},
        {"role": "assistant", "content": tool_call({"action": "wait", "time": 1})},
        {"role": "user", "content": [{"type": "image"}, {"type": "text", "text": "Current goal"}]},
    ]
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    runtime.predict(messages, [image, image], first)
    runtime.predict(messages, [image.copy(), image.copy()], second)
    a = first / "context-image-000.png"
    b = second / "context-image-000.png"
    assert a.stat().st_ino == b.stat().st_ino
    assert Image.open(b).tobytes() == image.tobytes()
    assert requests[1]["instruction"] == "Click Save\nCurrent goal"
    assert requests[1]["history"][0]["raw_output"] == messages[1]["content"]
    assert requests[1]["history"][0]["image"]["path"] == str(b)
    image.putpixel((0, 0), (0, 0, 255))
    third = tmp_path / "third"
    third.mkdir()
    runtime.predict(messages, [image, image], third)
    assert (third / "context-image-000.png").stat().st_ino != a.stat().st_ino
    assert Image.open(a).getpixel((0, 0)) == (255, 0, 0)
