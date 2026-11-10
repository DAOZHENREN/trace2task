import importlib.util
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
from trace2task.android_world_benchmark import SCOPE

spec = importlib.util.spec_from_file_location(
    "single", Path(__file__).resolve().parents[1] / "scripts/android_world/run_single.py"
)
single = importlib.util.module_from_spec(spec)
spec.loader.exec_module(single)
TASK = {"id": "test:0", "task_type": "test", "task_index": 0, "instruction": "Run stopwatch"}


class World:
    def __init__(self):
        self.actions = 0
        self.scored = 0
        self.closed = False

    def screenshot(self, **kwargs):
        return np.zeros((32, 32, 3), dtype=np.uint8)

    def request(self, path, *, data=None):
        if path == "/trace2task/identity":
            return {"scope": SCOPE, "instance": "test"}
        if path.startswith("/task/goal"):
            return {"goal": TASK["instruction"]}
        if path.startswith("/screenshot"):
            return {"pixels": np.zeros((32, 32, 3), dtype=np.uint8).tolist()}
        if path == "/trace2task/action":
            self.actions += 1
            return {"executed": True, "effect": "unverifiable"}
        if path.startswith("/task/score"):
            self.scored += 1
            return {"score": 0}
        if path.startswith("/task/tear_down"):
            self.closed = True
        return {}


class Runtime:
    def __init__(self, action):
        self.c = SimpleNamespace(decode=lambda _: action)
        self.receipts = []

    def inputs(self, turns):
        self.receipts.append(turns[-1]["receipt"])
        return {
            "input_ids": np.array([[1, 2]]),
            "slot_positions": np.array([[1]]),
            "lengths": np.array([2]),
        }

    def logits(self, inputs):
        return np.zeros((105, 3))


def run(tmp_path, runtime, world, budget=2):
    return single.evaluate(
        runtime, world, TASK, tmp_path / "episode", single.Stop(tmp_path), budget, lambda *a: None
    )


def test_budget_and_actual_receipts(tmp_path):
    w = World()
    r = Runtime({"action": "wait"})
    result = run(tmp_path, r, w)
    assert w.actions == 2 and w.scored == 1 and w.closed
    assert result["decisions"] == 2 and not result["success"] and result["valid"]
    assert r.receipts[0] is None and r.receipts[1]["executed_action"] == {"action": "wait"}


def test_self_report_is_not_success(tmp_path):
    w = World()
    result = run(tmp_path, Runtime({"action": "done"}), w)
    assert w.actions == 0 and w.scored == 1 and not result["success"]


def test_infrastructure_failure_not_zero_score(tmp_path):
    w = World()
    r = Runtime({"action": "wait"})

    def fail(_):
        raise RuntimeError("transport failed")

    r.inputs = fail
    with pytest.raises(RuntimeError):
        run(tmp_path, r, w)
    import json

    result = json.loads((tmp_path / "episode/result.json").read_text())
    assert result["score"] is None and not result["valid"] and w.scored == 0 and w.closed


def test_speed_record_and_task_cache_reset(tmp_path):
    import json

    r = Runtime({"action": "wait"})
    resets = []
    r.reset_cache = lambda: resets.append(True)
    r.last_metrics = {"forward_seconds": 0.25, "cached_tokens": 100, "new_tokens": 20}
    run(tmp_path, r, World())
    assert resets == [True]
    prediction = json.loads((tmp_path / "episode/prediction-0000.json").read_text())
    timing = prediction["timing"]
    assert timing["forward_seconds"] == 0.25 and timing["cached_tokens"] == 100
    assert timing["total_inference_seconds"] >= timing["input_seconds"] >= 0
    summary = json.loads((tmp_path / "episode/speed-summary.json").read_text())
    assert summary["steps"] == 2 and summary["cache_hit_steps"] == 2
    assert summary["forward_seconds"]["median"] == 0.25


def test_periodic_task_reminders():
    goal = "Save literal FILE.txt beside Original.txt."
    values = [single.periodic_instruction(goal, i, 8) for i in range(18)]
    assert [i + 1 for i, v in enumerate(values) if goal in v] == [1, 9, 17]
    assert all(v == single.STEP_INSTRUCTION for i, v in enumerate(values) if i % 8)
    assert single.periodic_instruction(goal, 3, 0) == goal


def test_evicted_reminder_only_changes_new_turn():
    import copy

    goal = "Save FILE.txt"
    turns = [{"instruction": single.periodic_instruction(goal, i, 8)} for i in range(3)]
    original = copy.deepcopy(turns)

    class Window:
        def __init__(self, count):
            self.count = count
            self.calls = 0

        def inputs(self, turns):
            self.calls += 1
            return {"image_grid_thw": np.zeros((self.count, 3))}

    keep = Window(3)
    _, reason = single.task_aware_inputs(keep, turns, goal, 8)
    assert reason == "none" and turns == original and keep.calls == 1
    trimmed = Window(1)
    _, reason = single.task_aware_inputs(trimmed, turns, goal, 8)
    assert reason == "last_task_reminder_evicted" and trimmed.calls == 2
    assert turns[:-1] == original[:-1]
    assert turns[-1]["instruction"] == single.periodic_instruction(goal, 0, 8)


def test_rejected_action_receipt_then_fresh_prediction(tmp_path):
    class PreflightWorld(World):
        def request(self, path, *, data=None):
            if path == "/trace2task/validate-action":
                bad = data["action"]["skill"] == "android_open_app"
                return {
                    "valid": not bad,
                    "executed": False,
                    "instance": "test",
                    "reason": "Unknown Android app label: 20_" if bad else "",
                }
            return super().request(path, data=data)

    w = PreflightWorld()
    r = Runtime(None)
    actions = iter([{"action": "open_app", "text": "20_"}, {"action": "wait"}])
    r.c.decode = lambda _: next(actions)
    result = single.evaluate(
        r,
        w,
        TASK,
        tmp_path / "episode",
        single.Stop(tmp_path),
        2,
        lambda *a: None,
        validate_actions=True,
    )
    assert result["decisions"] == 2 and result["delivered_actions"] == 1 and w.actions == 1
    assert r.receipts[1]["executed_action"] is None
    assert r.receipts[1]["proposed_action"]["text"] == "20_"
    assert "20_" in r.receipts[1]["reason"]
    assert r.receipts[1]["outcome"] == "rejected_before_execution"


def test_repeated_invalid_actions_consume_budget_not_halt(tmp_path):
    class RejectAll(World):
        def request(self, path, *, data=None):
            if path == "/trace2task/validate-action":
                return {
                    "valid": False,
                    "executed": False,
                    "instance": "test",
                    "reason": "Invalid app",
                }
            return super().request(path, data=data)

    w = RejectAll()
    r = Runtime({"action": "open_app", "text": "20_"})
    result = single.evaluate(
        r,
        w,
        TASK,
        tmp_path / "episode",
        single.Stop(tmp_path),
        4,
        lambda *a: None,
        validate_actions=True,
    )
    assert result["valid"] and result["decisions"] == 4 and w.actions == 0 and w.scored == 1
    assert all(x["outcome"] == "rejected_before_execution" for x in r.receipts[1:])


def test_preflight_service_error_remains_infrastructure_failure(tmp_path):
    class Broken(World):
        def request(self, path, *, data=None):
            if path == "/trace2task/validate-action":
                raise RuntimeError("HTTP 500")
            return super().request(path, data=data)

    import json

    w = Broken()
    with pytest.raises(RuntimeError, match="HTTP 500"):
        single.evaluate(
            Runtime({"action": "wait"}),
            w,
            TASK,
            tmp_path / "episode",
            single.Stop(tmp_path),
            2,
            lambda *a: None,
            validate_actions=True,
        )
    assert w.actions == 0 and w.scored == 0
    assert json.loads((tmp_path / "episode/result.json").read_text())["score"] is None
