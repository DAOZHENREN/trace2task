"""Fake-only contract tests for the shared local GUI-agent loop.

These tests deliberately use real model-output decoders, ``ExecutionCore`` and
the shared loop, but replace every native input/capture boundary.  They prove
that backend choice does not change the model-adapter contract.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from trace2task.cua_execution import CuaExecutionBackend
from trace2task.execution_core import ExecutionCore
from trace2task.execution_protocol import COORDINATE_SPACE, PROTOCOL_VERSION
from trace2task.local_agent_loop import run_local_agent_loop
from trace2task.local_gui_protocol import decode
from trace2task.win32_execution import Win32ExecutionBackend

WINDOW_TARGET = {"pid": 10, "window_id": 20}
WINDOW_BOUNDS = {"x": 1, "y": 2, "width": 100, "height": 80}
DESKTOP_TARGET = {"foreground_handle": 70, "screen_size": (100, 80)}


class FakeStop:
    def raise_if_requested(self):
        return None

    def sleep(self, _seconds):
        return None


class FakeObserver:
    def __init__(self, tmp_path, backend_name, *, stale=False):
        self.tmp_path = tmp_path
        self.backend_name = backend_name
        self.stale = stale
        self.observations = []

    def observe(self, target, index):
        self.observations.append((dict(target), index))
        screenshot = self.tmp_path / f"observation-{index}.png"
        screenshot.write_bytes(b"fake image; model predictor is scripted")
        if self.backend_name == "cua":
            state = {
                **WINDOW_TARGET,
                "session": "cross-backend-test",
                "window_bounds": dict(WINDOW_BOUNDS),
                "screenshot_width": 100,
                "screenshot_height": 80,
                "elements_complete": False,
                "elements": [],
            }
            if self.stale == "geometry":
                state["window_bounds"]["x"] += 1
            elif self.stale:
                state["window_id"] += 1
        else:
            state = dict(DESKTOP_TARGET)
            if self.stale:
                state["foreground_handle"] += 1
        return dict(target), state, screenshot

    def context(self, target, state, backend):
        if self.backend_name == "win32":
            return None
        return {"current_window": dict(target),
                "windows": [{"pid": 11, "window_id": 21}],
                "apps": [{"app_id": 0}]}


class FakeAudit:
    def __init__(self, result):
        self.result = result
        self.executions = []

    def elapsed(self, _field, _started):
        return 1.0

    def predict(self, predictor, _stop, model, instruction, screenshot, history, step, context, **kwargs):
        self.result["model_io"].append(
            {"step": step, "history": list(history), "context": context, "screenshot": str(screenshot)}
        )
        return predictor(instruction, model=model, history=history, step=step, context=context, **kwargs)

    def execution(self, payload):
        self.executions.append(payload)


class FakeCuaScope:
    def require_window(self, target):
        if target != WINDOW_TARGET:
            raise ValueError("unauthorized Cua target")

    def require_app(self, _app_id):
        raise ValueError("no application launch is authorized in this test")


class FakeCuaDriver:
    def __init__(self):
        self.calls = []

    def window(self, target):
        if target != WINDOW_TARGET:
            raise RuntimeError("Cua target changed")
        return {**WINDOW_TARGET, "bounds": dict(WINDOW_BOUNDS)}

    def call(self, tool, payload):
        self.calls.append((tool, payload))
        # Driver acceptance is intentionally not confused with an app effect.
        return {"effect": "unverifiable", "route": "fake_background"}


class FakeMotor:
    def __init__(self):
        self.calls = []

    def execute(self, action):
        self.calls.append(action)
        return SimpleNamespace(
            input_mode="foreground",
            elapsed_ms=2.0,
            window_handle=70,
            screen_position=(50, 40),
        )


def _native_tool_call(model, action):
    tool = "computer_use" if model == "gui-owl-2b" else "mobile_use"
    return "<tool_call>" + json.dumps({"name": tool, "arguments": action}) + "</tool_call>"


def _model_script(model, backend_name):
    scale = 1000 if model == "gui-owl-2b" else 999
    click_action = "left_click" if model == "gui-owl-2b" else "click"
    native = [
        _native_tool_call(model, {"action": click_action, "coordinate": [scale / 2, scale / 2]}),
        _native_tool_call(model, {"action": "terminate", "status": "success"}),
    ]

    def predictor(_instruction, *, history, context, **_kwargs):
        if context == {"purpose": "verify_completion"}:
            return {
                "status": "reviewed",
                "verification": {"verdict": "complete", "evidence": "fake visible result", "missing": ""},
            }
        return {"status": "predicted", "prediction": decode(model, native.pop(0), cua=backend_name == "cua")}

    return predictor


def _backend(backend_name):
    if backend_name == "cua":
        driver = FakeCuaDriver()
        return CuaExecutionBackend(driver, FakeCuaScope()), driver, WINDOW_TARGET
    motor = FakeMotor()
    backend = Win32ExecutionBackend(
        motor,
        foreground_handle=lambda: DESKTOP_TARGET["foreground_handle"],
        screen_size=lambda: DESKTOP_TARGET["screen_size"],
    )
    return backend, motor, DESKTOP_TARGET


@pytest.mark.parametrize("model", ["gui-owl-2b", "mai-ui-2b"])
@pytest.mark.parametrize("backend_name", ["win32", "cua"])
def test_native_model_formats_share_loop_and_do_not_replay_unconfirmed_action(tmp_path, model, backend_name):
    backend, native, target = _backend(backend_name)
    stop, events = FakeStop(), []
    core = ExecutionCore(backend, stop, lambda kind, **data: events.append({"type": kind, **data}))
    result = {"actions": 0, "history": [], "model_io": []}
    observer = FakeObserver(tmp_path, backend_name)
    audit = FakeAudit(result)

    run_local_agent_loop(
        observer=observer,
        initial_target=target,
        core=core,
        audit=audit,
        predictor=_model_script(model, backend_name),
        instruction="click once and visibly finish",
        model=model,
        root=tmp_path / f"{backend_name}-{model}",
        result=result,
        record=lambda kind, **data: events.append({"type": kind, **data}),
        stop=stop,
        status_callback=lambda _message: None,
    )

    # Both native formats normalize to the same versioned plan, and each
    # backend receives one normalized click only.
    dispatch = next(event for event in events if event["type"] == "dispatch")
    envelope = next(event for event in events if event["type"] == "execution_plan")
    assert envelope["protocol_version"] == PROTOCOL_VERSION
    assert envelope["coordinate_space"] == COORDINATE_SPACE
    assert dispatch["action"] == {
        "skill": "click", "args": {"x": 0.5, "y": 0.5, "button": "left"}
    }
    assert result["actions"] == 1
    assert result["history"] == [
        {
            "step_index": 0,
            "action": {"actions": [dispatch["action"]]},
            "executed": True,
            "effect": "unverifiable",
            "delivery_mode_requested": "background" if backend_name == "cua" else "foreground",
        }
    ]
    assert sum(event["type"] == "dispatch" for event in events) == 1
    model_context = result["model_io"][0]["context"]
    if backend_name == "win32":
        assert model_context is None
    else:
        assert "current_window" in model_context and "capabilities" not in model_context
    assert any(event["type"] == "effect_reobservation" for event in events)
    assert len(observer.observations) == 3  # first action, re-observation, read-only review
    assert result["task_complete"] is True
    assert result["verified"] is not True  # same-model visual review is explicitly not independent verification
    assert result["stop_reason"] == "visual_completion_reviewed"
    assert len(audit.executions) == 2  # click plus non-input completion claim
    first_execution = audit.executions[0]
    assert first_execution['normalized_plan']['actions'] == [dispatch['action']]
    assert first_execution['executor_requests'][0]['status'] == 'delivered'
    assert first_execution['executor_requests'][0]['request']['backend'] == backend_name
    if backend_name == "cua":
        assert len(native.calls) == 1 and native.calls[0][0] == "click"
    else:
        assert len(native.calls) == 1
        assert native.calls[0].to_payload() == dispatch["action"]


@pytest.mark.parametrize("backend_name", ["win32", "cua"])
def test_qwen_native_batch_uses_same_core_and_backend_adapters(tmp_path, backend_name):
    backend, native, target = _backend(backend_name)
    stop, events = FakeStop(), []
    core = ExecutionCore(backend, stop, lambda kind, **data: events.append({"type": kind, **data}))
    result = {"actions": 0, "history": [], "model_io": []}
    observer = FakeObserver(tmp_path, backend_name)
    audit = FakeAudit(result)
    click = {"skill": "click", "args": {"x": .5, "y": .5, "button": "left"}}

    def predictor(_instruction, *, step, context, **_kwargs):
        if context == {"purpose": "verify_completion"}:
            return {"status": "reviewed", "verification": {
                "verdict": "complete", "evidence": "fake visible result", "missing": ""}}
        actions = [click, click] if step == 0 else [{"done": True}]
        return {"status": "predicted", "prediction": decode(
            "qwen3-vl-2b", json.dumps({"actions": actions}))}

    run_local_agent_loop(
        observer=observer, initial_target=target, core=core, audit=audit,
        predictor=predictor, instruction="click twice", model="qwen3-vl-2b",
        root=tmp_path / backend_name, result=result,
        record=lambda kind, **data: events.append({"type": kind, **data}),
        stop=stop, status_callback=lambda _message: None,
    )
    assert result["actions"] == 2
    assert len(result["history"]) == 2
    assert len(observer.observations) == 3  # one batch, reobserve, completion review
    assert sum(event["type"] == "dispatch" for event in events) == 2
    assert len(native.calls) == 2


@pytest.mark.parametrize("backend_name", ["win32", "cua"])
def test_stale_target_observation_never_reaches_native_input(tmp_path, backend_name):
    backend, native, target = _backend(backend_name)
    stop, events = FakeStop(), []
    core = ExecutionCore(backend, stop, lambda kind, **data: events.append({"type": kind, **data}))
    result = {"actions": 0, "history": [], "model_io": []}
    audit = FakeAudit(result)
    model = "gui-owl-2b"
    native_click = _native_tool_call(model, {"action": "left_click", "coordinate": [500, 500]})

    def predictor(_instruction, **_kwargs):
        return {"status": "predicted", "prediction": decode(model, native_click, cua=backend_name == "cua")}

    kwargs = {
        "observer": FakeObserver(tmp_path, backend_name, stale=True),
        "initial_target": target,
        "core": core,
        "audit": audit,
        "predictor": predictor,
        "instruction": "click once",
        "model": model,
        "root": tmp_path / f"stale-{backend_name}",
        "result": result,
        "record": lambda kind, **data: events.append({"type": kind, **data}),
        "stop": stop,
        "status_callback": lambda _message: None,
    }
    if backend_name == "cua":
        # Cua's exact window identity/bounds are an authorization boundary:
        # it fails closed rather than retargeting or replaying on another HWND.
        with pytest.raises(RuntimeError):
            run_local_agent_loop(**kwargs)
    else:
        run_local_agent_loop(**kwargs)

    assert not native.calls
    assert not any(event["type"] == "dispatch" for event in events)
    if backend_name == "win32":
        assert result["stop_reason"] == "observation_changed_limit"


def test_cua_window_move_reobserves_without_old_coordinates(tmp_path):
    backend, native, target = _backend("cua")
    events = []
    stop = FakeStop()
    result = {"actions": 0, "history": [], "model_io": []}
    model = "gui-owl-2b"
    native_click = _native_tool_call(model, {"action": "left_click", "coordinate": [500, 500]})

    run_local_agent_loop(
        observer=FakeObserver(tmp_path, "cua", stale="geometry"),
        initial_target=target,
        core=ExecutionCore(backend, stop, lambda kind, **data: events.append({"type": kind, **data})),
        audit=FakeAudit(result),
        predictor=lambda _instruction, **_kwargs: {
            "status": "predicted", "prediction": decode(model, native_click, cua=True)},
        instruction="click once",
        model=model,
        root=tmp_path / "moved-cua",
        result=result,
        record=lambda kind, **data: events.append({"type": kind, **data}),
        stop=stop,
        status_callback=lambda _message: None,
    )

    assert result["stop_reason"] == "observation_changed_limit"
    assert result["actions"] == 0
    assert not native.calls
    assert sum(event["type"] == "observation_changed" for event in events) == 3
