from types import SimpleNamespace

import pygame
import pytest

from trace2task import trained_model_runner as runner
from trace2task.windows_runner import EmergencyStopRequested


def test_whole_batch_rejects_unknown_and_empty():
    for actions in [[], [{"skill": "wheel", "args": {"wheel_units": 1}}],
                    [{"done": True}, {"skill": "wait", "args": {"duration_ms": 1}}],
                    [{"skill": "wait", "args": {"duration_ms": 1}}] * 9]:
        with pytest.raises(ValueError):
            runner.parse_actions({"actions": actions})
    assert runner.parse_actions({"actions": [{"done": True}]}) == ([], [], True)


class Stop:
    def start(self): pass
    def close(self): pass
    def raise_if_requested(self): pass
    def sleep(self, seconds): pass


def setup_run(monkeypatch, tmp_path, predictions, *, approve=lambda batch: None, stop=None, change=False):
    frame = pygame.Surface((100, 100))
    frame.fill("white")
    backend = SimpleNamespace(foreground_handle=lambda: 1)
    capture = SimpleNamespace(capture=lambda window: frame)
    sent, histories = [], []

    class Motor:
        def __init__(self, *a, **kw): pass
        def execute(self, call):
            sent.append(call.to_payload())
            if change:
                frame.fill("black")
            return SimpleNamespace(elapsed_ms=1)

    def predict(task, **kw):
        histories.append(kw["history"])
        return {"status": "predicted", "prediction": {"actions": predictions.pop(0)}}

    monkeypatch.setattr(runner, "WindowsMotorExecutor", Motor)
    result = runner.run_trained_desktop(instruction="test", output_root=tmp_path,
        emergency_stop=stop or Stop(), status_callback=lambda _: None, approve=approve,
        backend=backend, capture=capture, size=lambda: (100, 100), predictor=predict)
    return result, sent, histories


CLICK = {"skill": "click", "args": {"x": .5, "y": .5, "button": "left"}}


def test_loop_only_sends_executed_history_and_done_is_not_success(monkeypatch, tmp_path):
    confirmations = []
    result, sent, histories = setup_run(monkeypatch, tmp_path,
        [[CLICK], [{"done": True}]], approve=confirmations.append)
    assert len(sent) == len(confirmations) == 1
    assert histories[0] == []
    assert histories[1] == [{"step_index": 0, "action": {"actions": [CLICK]}, "executed": True}]
    assert result["stop_reason"] == "model_done_unverified"
    assert not result["verified"] and not result["task_complete"]


def test_stop_during_confirmation_sends_nothing(monkeypatch, tmp_path):
    def reject(batch):
        raise EmergencyStopRequested("user stopped")
    result, sent, _ = setup_run(monkeypatch, tmp_path, [[CLICK]], approve=reject)
    assert not sent and not result["history"]
    assert result["stop_reason"] == "emergency_stop"


def test_screen_change_discards_remaining_actions(monkeypatch, tmp_path):
    _result, sent, histories = setup_run(monkeypatch, tmp_path,
        [[CLICK, CLICK], [{"done": True}]], change=True)
    assert len(sent) == 1
    assert len(histories[1][0]["action"]["actions"]) == 1


def test_no_progress_blocks_fourth_click(monkeypatch, tmp_path):
    result, sent, _ = setup_run(monkeypatch, tmp_path, [[CLICK]] * 4)
    assert len(sent) == 3
    assert result["stop_reason"] == "no_progress"


def test_web_d_provider_no_taskpack_and_safe_default(tmp_path, monkeypatch):
    from trace2task.web_console import WebConsoleController
    controller = WebConsoleController(tmp_path)
    monkeypatch.setattr(controller, "_run_job", lambda *args: None)
    job = controller.start_job(task_path="", instruction="hello", execute=True,
        provider="trained_d", execution_scope="desktop", use_experience=False)
    assert job["model"] == "D-5970"
    assert controller._jobs[job["job_id"]].continuous is False
    assert job["pending_batch"] is None


def test_stop_while_model_thinking_does_not_wait_for_response():
    import threading
    release = threading.Event()
    stopped = Stop()
    stopped.raise_if_requested = lambda: (_ for _ in ()).throw(EmergencyStopRequested("stop"))
    try:
        with pytest.raises(EmergencyStopRequested):
            runner.interruptible_prediction(lambda *a, **k: release.wait(5), stopped, "test")
    finally:
        release.set()
