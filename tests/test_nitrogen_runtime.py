import threading

import pytest

from trace2task.web_console import WebConsoleController


def test_nitrogen_runs_as_exclusive_stoppable_console_job(tmp_path, monkeypatch, synthetic_console_hotkey):
    started = threading.Event()
    def run_game(paths, process, seconds, root, stop, status, *, on_round, task_text):
        assert process == "celeste.exe" and seconds == 60
        assert task_text == "穿过右门"
        assert root.is_relative_to(tmp_path / "runs")
        on_round({"step_index": 0, "status": "predicted", "raw_output": '{"buttons": []}',
                  "screenshot": "input.png", "execution": {"executed": True, "submitted_steps": 1}})
        started.set()
        stop.sleep(10)

    monkeypatch.setattr("trace2task.nitrogen_runtime.runtime_paths", lambda: (None, None, None))
    monkeypatch.setattr("trace2task.nitrogen_runtime.run_game", run_game)
    controller = WebConsoleController(tmp_path)
    with pytest.raises(ValueError, match=r"\.exe"):
        controller.start_nitrogen("C:/game.exe", 60)
    job = controller.start_nitrogen("celeste.exe", 60, " 穿过右门 ")
    assert started.wait(2)
    with pytest.raises(RuntimeError):
        controller.start_nitrogen("celeste.exe", 60)
    controller.stop_job(job["job_id"])
    final = controller.wait(job["job_id"])
    assert final["status"] == "stopped"
    assert final["result"]["stop_reason"] == "emergency_stop"
    assert controller.run_store.read_round(job["job_id"], 0)["execution"]["submitted_steps"] == 1
