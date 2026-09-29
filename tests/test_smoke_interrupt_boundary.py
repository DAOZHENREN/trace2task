import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

_PATH = Path(__file__).parents[1] / "scripts/rsi/smoke_interrupt_boundary.py"
_SPEC = importlib.util.spec_from_file_location("rsi_interrupt_boundary", _PATH)
assert _SPEC and _SPEC.loader
interrupt = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(interrupt)


def official_event(kind, project):
    return {
        "kind": "official_event",
        "payload": {"type": kind, "payload": {"project_index": project}},
    }


def test_boundary_event_detection_distinguishes_closed_from_next_open():
    class Store:
        def events(self, run_id, after):
            assert run_id == "a" * 32
            assert after == 0
            return [
                {"seq": 1, **official_event("PROJECT_CLOSED", 1)},
                {"seq": 2, **official_event("PROJECT_OPENED", 2)},
            ]

    store = Store()
    assert interrupt._closed_project_observed(store, "a" * 32, 1)
    assert interrupt._next_project_opened(store, "a" * 32, 1)


def test_matching_worker_requires_exact_current_attempt_labels():
    run_id = "b" * 32

    class Worker:
        def __init__(self, attempt):
            self.labels = {
                "org.trace2task.rsi.worker": run_id,
                "org.trace2task.rsi.attempt": str(attempt),
            }
            self.attrs = {"State": {"Running": True}}

        def reload(self):
            pass

    current = Worker(2)
    client = SimpleNamespace(containers=SimpleNamespace(list=lambda **_: [current]))
    assert interrupt._matching_worker(client, run_id, 2) is current
    stale = Worker(1)
    client = SimpleNamespace(containers=SimpleNamespace(list=lambda **_: [stale]))
    with pytest.raises(RuntimeError, match="identity"):
        interrupt._matching_worker(client, run_id, 2)


def test_interrupt_executor_requires_one_confirmed_pid():
    class Worker:
        def exec_run(self, command, **kwargs):
            assert command[0] == "/trusted/controller-python"
            assert command[-2:] == ["c" * 32, "3"]
            return SimpleNamespace(exit_code=0, output=json.dumps({
                "matches": [123], "interrupted": True,
            }).encode())

    assert interrupt._interrupt_executor(Worker(), "/trusted/controller-python", "c" * 32, 3) == 123

    class Ambiguous(Worker):
        def exec_run(self, command, **kwargs):
            return SimpleNamespace(exit_code=4, output=b'{"matches": [1, 2], "interrupted": false}')

    with pytest.raises(RuntimeError, match="identity"):
        interrupt._interrupt_executor(Ambiguous(), "/trusted/controller-python", "c" * 32, 3)


def test_interrupt_receipts_are_immutable_per_attempt(tmp_path):
    run_id = "d" * 32
    run_root = tmp_path / run_id
    run_root.mkdir()
    store = SimpleNamespace(root=tmp_path)
    first = interrupt._write_receipt(store, run_id, {"attempt_no": 1, "passed": False})
    second = interrupt._write_receipt(store, run_id, {"attempt_no": 2, "passed": True})
    assert first.name == "interrupt-boundary-attempt-001.json"
    assert second.name == "interrupt-boundary-attempt-002.json"
    assert json.loads(first.read_text())["passed"] is False
    with pytest.raises(FileExistsError):
        interrupt._write_receipt(store, run_id, {"attempt_no": 2, "passed": False})
