import json
from pathlib import Path
from types import SimpleNamespace

import pygame
import pytest

from trace2task.desktop_runner import DesktopSession, parse_plan, run_desktop_baseline
from trace2task.windows_control import WindowSafetyError
from trace2task.windows_runner import EmergencyStopRequested


class Backend:
    handle = 1

    def __init__(self):
        self.inputs = []

    def foreground_handle(self):
        return self.handle

    def set_cursor_position(self, x, y):
        self.inputs.append((x, y))

    def send_mouse_button(self, button, is_down):
        self.inputs.append((button, is_down))
        if not is_down:
            self.handle = 2

    def send_text(self, text):
        self.inputs.append(text)


class Stop:
    cancelled = False

    def start(self):
        pass

    def close(self):
        pass

    def raise_if_requested(self):
        if self.cancelled:
            raise EmergencyStopRequested("test")

    def sleep(self, seconds):
        self.raise_if_requested()


class Capture:
    def capture(self, window):
        return pygame.Surface((window.client_width, window.client_height))


class Model:
    def __init__(self, plans, hook=None):
        self.plans = iter(plans)
        self.prompts = []
        self.hook = hook
        self.closed = False

    def reset_thread(self):
        pass

    def close(self):
        self.closed = True

    def run_turn(self, **kwargs):
        self.prompts.append(kwargs)
        if self.hook:
            self.hook()
        return json.dumps(next(self.plans))


def plan(*actions, complete=False):
    return {"task_complete": complete, "reason": "test", "actions": list(actions)}


CLICK = {"skill": "click", "args": {"x": 0.5, "y": 0.5, "button": "left"}}
TEXT = {"skill": "type_text", "args": {"text": "hello"}}


def run(tmp_path, model, backend, **kwargs):
    return run_desktop_baseline(
        instruction="test desktop",
        execute=True,
        model="test",
        reasoning_effort="default",
        output_root=tmp_path,
        emergency_stop=kwargs.pop("stop", Stop()),
        status_callback=kwargs.pop("status_callback", lambda message: None),
        backend=backend,
        capture=Capture(),
        session=model,
        size=kwargs.pop("size", lambda: (100, 80)),
        **kwargs,
    )


def test_cross_window_observes_again_and_discards_stale_batch(tmp_path):
    backend = Backend()
    model = Model([plan(CLICK, TEXT), plan(complete=True)])
    result = run(tmp_path, model, backend)
    assert result["task_complete"] and not result["verified"]
    assert result["actions"] == 1
    assert "hello" not in backend.inputs
    assert backend.inputs[0] == (50, 40)
    assert len(model.prompts) == 2
    assert model.closed
    assert '"type": "model_output"' in Path(result["trace_path"]).read_text(encoding="utf-8")


def test_focus_changed_during_model_call_replans_without_stale_input(tmp_path):
    backend = Backend()
    model = Model(
        [plan(TEXT), plan(CLICK), plan(complete=True)], hook=lambda: setattr(backend, "handle", 2)
    )
    result = run(tmp_path, model, backend)
    assert result["task_complete"]
    assert "hello" not in backend.inputs
    assert result["actions"] == 1
    assert "were NOT executed" in model.prompts[1]["prompt"]


def test_continuous_focus_changes_stop_after_bounded_retries(tmp_path):
    backend = Backend()
    model = Model([plan(TEXT)] * 4, hook=lambda: setattr(backend, "handle", backend.handle + 1))
    result = run(tmp_path, model, backend)
    assert result["stop_reason"] == "error"
    assert not backend.inputs
    assert len(model.prompts) == 3


def test_stale_completion_is_not_accepted(tmp_path):
    backend = Backend()
    model = Model([plan(complete=True), plan(TEXT)], hook=lambda: setattr(backend, "handle", 2))
    result = run(tmp_path, model, backend, max_actions=2)
    assert not result["task_complete"]
    assert result["actions"] == 1


def test_resolution_change_uses_fresh_coordinates(tmp_path):
    backend = Backend()
    dimensions = [100, 80]
    model = Model(
        [plan(TEXT), plan(CLICK), plan(complete=True)], hook=lambda: dimensions.__setitem__(0, 200)
    )
    result = run(tmp_path, model, backend, size=lambda: tuple(dimensions))
    assert result["task_complete"]
    assert "hello" not in backend.inputs
    assert backend.inputs[0] == (100, 40)


def test_focus_race_at_motor_guard_replans(tmp_path, monkeypatch):
    backend = Backend()
    original = DesktopSession.require_foreground
    checks = 0

    def guard(self):
        nonlocal checks
        checks += 1
        if checks == 2:
            backend.handle = 2
        return original(self)

    monkeypatch.setattr(DesktopSession, "require_foreground", guard)
    result = run(tmp_path, Model([plan(TEXT), plan(CLICK), plan(complete=True)]), backend)
    assert result["task_complete"]
    assert "hello" not in backend.inputs


def test_dry_run_never_injects_input(tmp_path):
    backend = Backend()
    model = Model([plan(CLICK)])
    result = run_desktop_baseline(
        instruction="test",
        execute=False,
        model="test",
        reasoning_effort="default",
        output_root=tmp_path,
        emergency_stop=Stop(),
        status_callback=lambda message: None,
        backend=backend,
        capture=Capture(),
        session=model,
        size=lambda: (100, 80),
    )
    assert result["stop_reason"] == "plan_only"
    assert not backend.inputs


def test_cancel_during_planning_never_sends_input(tmp_path):
    backend = Backend()
    stop = Stop()
    model = Model([plan(TEXT)], hook=lambda: setattr(stop, "cancelled", True))
    result = run(tmp_path, model, backend, stop=stop)
    assert result["stop_reason"] == "emergency_stop"
    assert not backend.inputs


def test_action_budget(tmp_path):
    backend = Backend()
    result = run(tmp_path, Model([plan(TEXT, TEXT)]), backend, max_actions=1)
    assert result["actions"] == 1
    assert result["stop_reason"] == "action_limit"


def test_desktop_uses_semantics_and_guidance_not_recorded_actions(tmp_path, monkeypatch):
    from trace2task import desktop_runner

    contract = SimpleNamespace(
        task=SimpleNamespace(task_id="desktop-example", requires_confirmation=False),
        execution_scope="desktop", selector=SimpleNamespace(process_name="desktop", title_contains="all"),
        semantic_experience=SimpleNamespace(stage_index_payload=lambda: {"summary": "SEMANTIC_EVIDENCE"}),
        human_guidance=SimpleNamespace(prompt_payload=lambda: {"summary": "HUMAN_RULE"}),
        demonstration=[{"x": 0.123456, "text": "NEVER_COPY_THIS"}],
    )
    monkeypatch.setattr(desktop_runner, "load_windows_task", lambda _: contract)
    model = Model([plan(complete=True)])
    result = run(tmp_path, model, Backend(), task_path=tmp_path / "task.yaml", use_experience=True)
    assert result["use_experience"] is True
    assert result["mode"] == "desktop_agent"
    prompt = model.prompts[0]["prompt"]
    assert "SEMANTIC_EVIDENCE" in prompt and "HUMAN_RULE" in prompt
    assert "NEVER_COPY_THIS" not in prompt and "0.123456" not in prompt


def test_baseline_never_loads_task_even_if_path_supplied(tmp_path, monkeypatch):
    from trace2task import desktop_runner

    monkeypatch.setattr(desktop_runner, "load_windows_task", lambda _: pytest.fail("loaded experience"))
    result = run(tmp_path, Model([plan(complete=True)]), Backend(), task_path="unused.yaml")
    assert result["use_experience"] is False


def test_timing_is_logged_and_persisted(tmp_path, monkeypatch):
    from trace2task.windows_control import MotorResult, WindowsMotorExecutor

    monkeypatch.setattr(
        WindowsMotorExecutor, "execute", lambda self, action: MotorResult(action.skill, 1, 125)
    )
    messages = []
    model = Model(
        [plan(TEXT, {"skill": "wait", "args": {"duration_ms": 100}}), plan(complete=True)]
    )
    result = run(tmp_path, model, Backend(), status_callback=messages.append)
    perf = result["performance"]
    assert perf["action_ms"] == 125
    assert perf["explicit_wait_ms"] == 125
    assert perf["model_roundtrip_ms"] >= 0
    assert len(result["stage_timings"]) == 2
    assert result["stage_timings"][0]["executed_actions"] == 2
    saved = json.loads(Path(result["trace_path"]).with_name("summary.json").read_text("utf-8"))
    assert saved["performance"] == perf
    assert any("模型响应完成" in message for message in messages)
    assert any("耗时 125 ms" in message for message in messages)


def test_failed_model_call_keeps_performance(tmp_path):
    def fail():
        raise RuntimeError("provider failed")

    result = run(tmp_path, Model([], hook=fail), Backend())
    assert result["stop_reason"] == "error"
    assert result["performance"]["model_roundtrip_ms"] >= 0
    assert result["stage_timings"][0]["executed_actions"] == 0


@pytest.mark.parametrize(
    "payload",
    [
        plan(),
        plan(TEXT, complete=True),
        plan({"skill": "focus_window", "args": {}}),
        plan({"skill": "click", "args": {"x": 2, "y": 0}}),
    ],
)
def test_invalid_plans(payload):
    with pytest.raises(ValueError):
        parse_plan(json.dumps(payload))


def test_desktop_coordinates_and_resolution_guard():
    backend = Backend()
    size = [100, 80]
    desktop = DesktopSession(backend, lambda: tuple(size))
    window = desktop.observe()
    assert desktop.normalized_to_screen(window, 1, 1) == (99, 79)
    size[0] = 200
    with pytest.raises(WindowSafetyError):
        desktop.require_foreground()
