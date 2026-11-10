import pytest
from PIL import Image
from trace2task import androidworld_execution as aw
from trace2task.chat_agent_runner import run_chat_agent
from trace2task.execution_protocol import UnifiedAction
from test_chat_agent_bridge import Stop
from test_hybrid_models import Session, directive

TASK = {
    "id": "ClockStopWatchRunning:0",
    "task_type": "ClockStopWatchRunning",
    "task_index": 0,
    "instruction": "Run the stopwatch.",
    "app": "Clock",
}
HYBRID = {"local_model": "mai-swift-2b", "inference_backend": "mai-swift-native105"}


@pytest.fixture
def environment(monkeypatch):
    monkeypatch.setattr(aw, "catalog", lambda: {"configured": True, "tasks": [TASK]})
    instances = []

    class Driver:
        def __init__(self, root, record, stop, config):
            self.root = root
            self.config, self.task = aw.selection(config)
            self.target = {"kind": "androidworld", "instance": "test-instance"}
            self.client = self
            self.closed = False
            self.actions = []
            self.index = 0
            instances.append(self)

        def start(self):
            pass

        def observe(self):
            self.index += 1
            p = self.root / f"aw-{self.index}.png"
            Image.new("RGB", (100, 100)).save(p)
            return {"path": str(p), "instance": "test-instance"}

        def request(self, path, *, data=None):
            assert path == "/trace2task/action"
            self.actions.append(data)
            return {"executed": True, "effect": "unverifiable"}

        def evaluate(self):
            return {
                "valid": True,
                "raw_score": 1.0,
                "binary_reward": 1,
                "benchmark": "AndroidWorld",
            }

        def close(self):
            self.closed = True

    monkeypatch.setattr(aw, "AndroidWorldDriver", Driver)
    monkeypatch.setattr(
        aw,
        "predict",
        lambda *a, **k: {
            "status": "predicted",
            "raw_output": '{"action":"tap","coordinate":[500,250]}',
            "prediction": {
                "actions": [{"skill": "click", "args": {"x": 0.5, "y": 0.25, "button": "left"}}]
            },
        },
    )
    return instances


def test_mainline_hybrid_dispatch_scores_and_cleans(environment, tmp_path):
    result = run_chat_agent(
        instruction=TASK["instruction"],
        model="fake",
        reasoning_effort="low",
        output_root=tmp_path,
        emergency_stop=Stop(),
        status_callback=lambda _: None,
        executor_backend="androidworld",
        androidworld_config={"task_id": TASK["id"], "max_actions": 1},
        session=Session([directive()]),
        hybrid=HYBRID,
    )
    assert result["actions"] == 1, result
    assert result["environment_evaluation"]["raw_score"] == 1
    assert result["task_complete"] and environment[0].closed
    assert environment[0].actions[0]["instance"] == "test-instance"


def test_web_selects_catalog_goal_and_passes_scope(environment, monkeypatch, tmp_path):
    from trace2task.web_console import WebConsoleController

    calls = []

    def run(**kwargs):
        calls.append(kwargs)
        return {"stop_reason": "completed", "task_complete": True, "verified": True}

    monkeypatch.setattr("trace2task.chat_agent_runner.run_chat_agent", run)
    c = WebConsoleController(tmp_path)
    job = c.start_job(
        task_path="",
        instruction="client replacement",
        execute=True,
        execution_scope="androidworld",
        executor_backend="androidworld",
        androidworld_config={"task_id": TASK["id"], "max_actions": 120},
        execution_mode="hybrid",
        hybrid=HYBRID,
    )
    final = c.wait(job["job_id"])
    assert calls, final
    assert calls[0]["instruction"] == TASK["instruction"]
    assert calls[0]["androidworld_config"]["task_id"] == TASK["id"]


def test_bad_scope_and_budget_are_rejected(environment, tmp_path):
    with pytest.raises(ValueError):
        aw.selection({"task_id": TASK["id"], "max_actions": True})
    from trace2task.web_console import WebConsoleController

    with pytest.raises(ValueError, match="hybrid"):
        WebConsoleController(tmp_path).start_job(
            task_path="",
            instruction="x",
            execute=True,
            execution_scope="androidworld",
            executor_backend="androidworld",
            androidworld_config={"task_id": TASK["id"], "max_actions": 120},
        )


def test_native_scope_rejects_other_instance():
    backend = aw.WorldBackend(None, "ours")
    action = UnifiedAction.from_payload({"skill": "android_text", "args": {"text": "Hello"}})
    with pytest.raises(ValueError):
        backend.authorize(action, {"kind": "androidworld", "instance": "other"})
    with pytest.raises(ValueError):
        UnifiedAction.from_payload({"skill": "android_scroll", "args": {"direction": "diagonal"}})
