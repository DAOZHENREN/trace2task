"""Exercise shared mainline loops with a synthetic environment, never a real VM."""

import json
from pathlib import Path
import pytest
from PIL import Image
from test_chat_agent_bridge import Stop, Session
from trace2task import osworld_execution as ow, local_gui_client
from trace2task.execution_protocol import UnifiedAction, ActionUnavailable
from trace2task.trained_model_runner import run_trained_desktop
from trace2task.chat_agent_runner import run_chat_agent

TASK = {
    "id": "task-test",
    "app": "gimp",
    "instruction": "Click the visible control.\nKeep  exact spacing.",
}
CLICK = {"skill": "click", "args": {"x": 0.5, "y": 0.25, "button": "left"}}


@pytest.fixture
def environment(monkeypatch, tmp_path):
    monkeypatch.setattr(
        ow, "catalog", lambda: {"configured": True, "tasks": [TASK], "source": "synthetic"}
    )
    instances = []

    class Driver:
        def __init__(self, root, record, stop, config):
            self.root, self.record, self.stop = root, record, stop
            self.config, self.task = ow.selection(config)
            self.target = {"kind": "osworld", "task_id": TASK["id"]}
            self.failed, self.closed, self.started = False, False, False
            self.actions = []
            self.index = 0
            instances.append(self)

        def start(self):
            self.started = True

        def observe(self):
            self.index += 1
            path = self.root / f"screen{self.index}.png"
            Image.new("RGB", (100, 100)).save(path)
            return {"width": 100, "height": 100, "path": str(path)}

        def request(self, op, **payload):
            assert op == "act"
            self.actions.append(payload["action"])
            return {"executed": True, "reason": "delivered"}

        def evaluate(self):
            return {"valid": True, "raw_score": 0.5, "binary_reward": 0, "invalid_reason": None}

        def close(self):
            self.closed = True

    monkeypatch.setattr(ow, "OSWorldDriver", Driver)
    return instances


def test_local_model_shared_loop_uses_guest_and_official_score(environment, monkeypatch, tmp_path):
    monkeypatch.setattr(
        local_gui_client,
        "predict_gui",
        lambda *a, **k: {"status": "predicted", "prediction": {"actions": [CLICK]}},
    )
    result = run_trained_desktop(
        instruction=TASK["instruction"],
        model="gui-owl-2b",
        executor_backend="osworld",
        osworld_config={"task_id": TASK["id"], "max_actions": 1},
        output_root=tmp_path,
        emergency_stop=Stop(),
        status_callback=lambda _: None,
        approve=None,
        continuous=True,
    )
    assert result["actions"] == 1, result
    assert result["environment_evaluation"]["raw_score"] == 0.5
    assert not result["task_complete"]
    assert environment[0].actions == [{"action": "left_click", "coordinate": [500, 250]}]
    assert environment[0].closed
    assert Path(result["final_screenshot"]).exists()


def test_chat_shared_loop_uses_same_environment(environment, tmp_path):
    reply = json.dumps({"reason": "click it", "actions": [CLICK], "task_complete": False})
    session = Session([reply])
    result = run_chat_agent(
        instruction=TASK["instruction"],
        model="test-model",
        reasoning_effort="low",
        executor_backend="osworld",
        osworld_config={"task_id": TASK["id"], "max_actions": 1},
        output_root=tmp_path,
        emergency_stop=Stop(),
        status_callback=lambda _: None,
        session=session,
    )
    assert result["actions"] == 1, result
    assert result["environment_evaluation"]["raw_score"] == 0.5
    assert environment[0].closed


def test_native_mapping_uses_existing_guest_contract():
    scroll = UnifiedAction.from_payload(
        {"skill": "scroll", "args": {"direction": "down", "amount": 3, "by": "line"}}
    )
    assert ow.native_action(scroll) == {"action": "scroll", "pixels": -3}
    drag = UnifiedAction.from_payload(
        {
            "skill": "drag",
            "args": {
                "start_x": 0.1,
                "start_y": 0.2,
                "end_x": 0.8,
                "end_y": 0.9,
                "button": "left",
                "duration_ms": 1000,
            },
        }
    )
    assert ow.native_action(drag) == [
        {"action": "mouse_move", "coordinate": [100, 200]},
        {"action": "left_click_drag", "coordinate": [800, 900]},
    ]
    with pytest.raises(ActionUnavailable):
        ow.native_action(
            UnifiedAction.from_payload(
                {"skill": "scroll", "args": {"direction": "left", "amount": 3, "by": "line"}}
            )
        )


def test_edited_instruction_never_boots_guest(environment, tmp_path):
    with pytest.raises(ValueError, match="original task"):
        run_trained_desktop(
            instruction="edited",
            model="gui-owl-2b",
            executor_backend="osworld",
            osworld_config={"task_id": TASK["id"], "max_actions": 1},
            output_root=tmp_path,
            emergency_stop=Stop(),
            status_callback=lambda _: None,
            approve=None,
        )
    assert not environment


def test_hybrid_shared_loop(environment, monkeypatch, tmp_path):
    from test_hybrid_models import Session, directive, LOCAL, prediction

    monkeypatch.setattr(local_gui_client, "predict_gui", lambda *a, **k: prediction())
    result = run_chat_agent(
        instruction=TASK["instruction"],
        model="fake",
        reasoning_effort="low",
        output_root=tmp_path,
        emergency_stop=Stop(),
        status_callback=lambda _: None,
        executor_backend="osworld",
        osworld_config={"task_id": TASK["id"], "max_actions": 1},
        session=Session([directive()]),
        hybrid=LOCAL,
    )
    assert result["actions"] == 1, result
    assert result["hybrid"]["guide_calls"] == 1
    assert result["environment_evaluation"]["raw_score"] == 0.5
    assert environment[0].closed


def test_web_selection_and_stop_without_host_hotkey(environment, monkeypatch, tmp_path):
    from trace2task.web_console import WebConsoleController, EmergencyStopRequested

    def forbid():
        raise AssertionError("OSWorld must not install a Windows host hotkey")

    monkeypatch.setattr("trace2task.web_console.Win32EmergencyStop", forbid)
    calls = []

    def runner(**kwargs):
        calls.append(kwargs)
        stop = kwargs["emergency_stop"]
        stop.start()
        stop.stop_event.set()
        with pytest.raises(EmergencyStopRequested):
            stop.raise_if_requested()
        stop.close()
        return {"stop_reason": "emergency_stop", "task_complete": False, "verified": False}

    monkeypatch.setattr("trace2task.chat_agent_runner.run_chat_agent", runner)
    controller = WebConsoleController(tmp_path)
    job = controller.start_job(
        task_path="",
        instruction="client replacement",
        execute=True,
        execution_scope="osworld",
        executor_backend="osworld",
        osworld_config={"task_id": TASK["id"], "max_actions": 120},
    )
    final = controller.wait(job["job_id"])
    assert final["status"] == "stopped", final
    assert calls[0]["instruction"] == TASK["instruction"]
    assert calls[0]["osworld_config"]["max_actions"] == 120


def test_worker_path_overrides_service_path_without_forwarding_proxy(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HTTPS_PROXY", "http://restricted.invalid")
    monkeypatch.setenv("PYTHONPATH", "/wrong/source")
    env = ow.worker_environment({"tmp": str(tmp_path), "runtime_bin_dirs": [str(tmp_path)]})
    assert env["PATH"] == str(tmp_path) + ":/usr/bin"
    assert "HTTPS_PROXY" not in env and "PYTHONPATH" not in env
    assert env["TMPDIR"] == str(tmp_path)


def test_worker_startup_failure_reports_concrete_error(tmp_path):
    import subprocess
    import sys

    worker = Path(__file__).resolve().parents[1] / "scripts/osworld/worker.py"
    outcome = subprocess.run(
        [sys.executable, str(worker), "--deployment", str(tmp_path / "missing.json")],
        capture_output=True,
        text=True,
    )
    assert outcome.returncode != 0
    response = json.loads(outcome.stdout)
    assert response["ok"] is False
    assert "FileNotFoundError" in response["error"]
    assert "missing.json" in response["error"]


def test_tab_separated_headers_preserved_through_gui_protocol():
    from trace2task.execution_protocol import ActionPlan

    text = "Item\tReorder Point\tOnHand\tbelow reorder point"
    plan = ActionPlan.from_prediction(
        {
            "actions": [
                {"skill": "type_text", "args": {"text": text}},
                {"skill": "press_key", "args": {"key": "enter"}},
            ]
        }
    )
    assert len(plan.actions) == 2
    assert ow.native_action(plan.actions[0]) == {"action": "type", "text": text}
    assert ow.native_action(plan.actions[1]) == {"action": "key", "keys": ["enter"]}


@pytest.mark.parametrize("control", ["\n", "\r", "\x00", "\x1b", "\x7f"])
def test_tab_compatibility_does_not_allow_other_control_characters(control):
    from trace2task.actions import ActionCall, ActionValidationError

    with pytest.raises(ActionValidationError, match="control characters"):
        ActionCall("type_text", {"text": "A\tB" + control + "C"})
