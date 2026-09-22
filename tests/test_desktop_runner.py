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


@pytest.mark.parametrize("orchestration", ["legacy", "langgraph"])
def test_no_progress_blocks_before_input_and_stops(tmp_path, orchestration):
    backend = Backend()
    backend.handle = 2
    make_plan = managed_plan if orchestration == "langgraph" else plan
    model = Model([make_plan(CLICK)] * 6)
    result = run(tmp_path, model, backend, orchestration=orchestration)
    assert result["stop_reason"] == "no_progress"
    assert result["actions"] == 3
    assert len(backend.inputs) == 9
    assert "Repeated click blocked BEFORE execution" in model.prompts[-1]["prompt"]
    events = [json.loads(line) for line in Path(result["trace_path"]).read_text().splitlines()]
    assert sum(e["type"] == "no_progress_blocked" for e in events) == 3


def test_guard_ignores_small_animation_but_allows_changed_scene():
    from trace2task.actions import ActionCall
    from trace2task.desktop_runner import NoProgressGuard
    guard = NoProgressGuard()
    frame = pygame.Surface((100, 80))
    frame.fill("black")
    guard.observe(frame, 1)
    action = ActionCall.from_payload(CLICK)
    for _ in range(3):
        guard.delivered(action)
    pygame.draw.rect(frame, "white", (0, 0, 3, 3))
    guard.observe(frame, 1)
    assert guard.repeated(action)
    nearby = ActionCall.from_payload({"skill": "click", "args": {"x": 0.505, "y": 0.5, "button": "left"}})
    assert guard.repeated(nearby)
    frame.fill("white")
    guard.observe(frame, 1)
    assert not guard.repeated(action)


def test_no_progress_allows_alternative_without_replaying_blocked_batch(tmp_path):
    backend = Backend()
    backend.handle = 2
    model = Model([plan(CLICK)] * 3 + [plan(CLICK, TEXT), plan(TEXT), plan(complete=True)])
    result = run(tmp_path, model, backend)
    assert result["task_complete"]
    assert result["actions"] == 4
    assert backend.inputs.count("hello") == 1


def test_web_execution_submits_orchestration_not_recording():
    script = (Path(__file__).parents[1] / "src/trace2task/web/app.js").read_text(encoding="utf-8")
    normal_payload = script.index('task_path: task?.path || "",')
    normal_job = script.rfind('const job = await request("/api/jobs", {', 0, normal_payload)
    assert normal_job >= 0
    execution = script[normal_job : script.index("renderJob(job)", normal_job)]
    assert 'orchestration: desktop ? elements.desktopOrchestration.value' in execution
    assert 'resume_from: desktop && elements.desktopOrchestration.value' in execution
    recording = script.split("async function startRecording()", 1)[1].split("async function upgradeTask", 1)[0]
    assert "orchestration:" not in recording


def managed_plan(*actions, complete=False):
    return {**plan(*actions, complete=complete), "progress": {
        "current_subgoal": "check submitted search",
        "remaining_subgoals": ["play requested song"],
        "observed_evidence": ["search field visible"],
        "pending_checks": ["search results loaded"],
    }}


def test_langgraph_records_progress_and_resumes_with_new_observation(tmp_path):
    first = run(tmp_path, Model([managed_plan(TEXT)]), Backend(),
                orchestration="langgraph", max_actions=1)
    assert first["actions"] == 1
    root = Path(first["trace_path"]).parent
    assert (root / "checkpoints.sqlite").is_file()
    assert first["task_state"]["pending_action"] is None
    assert first["task_state"]["last_outcome"]["effect"] == "unverified_until_next_observation"
    model = Model([managed_plan(complete=True)])
    backend = Backend()
    second = run(tmp_path, model, backend, orchestration="langgraph", resume_from=root)
    assert second["task_complete"] is True
    assert backend.inputs == []  # Never replay the first run's text input.
    assert "search results loaded" in model.prompts[0]["prompt"]
    assert "hello" in model.prompts[0]["prompt"]
    assert str(root) not in str(model.prompts[0]["image_path"])
    assert "progress" in model.prompts[0]["output_schema"]["required"]


def test_langgraph_rejects_invalid_progress_before_input(tmp_path):
    value = managed_plan(TEXT)
    value["progress"] = {"invented": "bad"}
    backend = Backend()
    result = run(tmp_path, Model([value]), backend, orchestration="langgraph")
    assert result["stop_reason"] == "error"
    assert backend.inputs == []


def test_langgraph_rejects_uncertain_resume_without_replay(tmp_path):
    from trace2task.actions import ActionCall
    from trace2task.desktop_workflow import DesktopWorkflow
    root = tmp_path / "previous"
    root.mkdir()
    workflow = DesktopWorkflow(root, "test desktop", None)
    workflow.before_action(ActionCall.from_payload(TEXT))
    workflow.close()
    backend = Backend()
    result = run(tmp_path, Model([]), backend, orchestration="langgraph", resume_from=root)
    assert "不能自动恢复" in result["error"]
    assert backend.inputs == []


def test_langgraph_resume_rejects_changed_task(tmp_path):
    from trace2task.desktop_workflow import DesktopWorkflow
    root = tmp_path / "previous"
    root.mkdir()
    workflow = DesktopWorkflow(root, "different instruction", None)
    workflow.close()
    result = run(tmp_path, Model([]), Backend(), orchestration="langgraph", resume_from=root)
    assert "原任务指令" in result["error"]


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
