import pytest

from trace2task.execution_core import ExecutionResult
from trace2task.local_agent_loop import _delivered_cursor, run_local_agent_loop

CLICK = {"skill": "click", "args": {"x": 0.5, "y": 0.5, "button": "left"}}


class Stop:
    def raise_if_requested(self):
        return None

    def sleep(self, _seconds):
        return None


class Observer:
    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.observations = []

    def observe(self, target, index):
        self.observations.append((target, index))
        screenshot = self.tmp_path / f"{index}.png"
        screenshot.write_bytes(b"not decoded by this fake predictor")
        return target, {"frame": index}, screenshot

    def context(self, target, state, backend):
        return {"target": target, "frame": state["frame"]}


class Audit:
    def __init__(self, result):
        self.result = result
        self.predictions = []
        self.executions = []

    def elapsed(self, _field, _started):
        return 1.0

    def predict(self, predictor, stop, model, task, screenshot, history, step, context, **kwargs):
        self.predictions.append({"history": list(history), "context": context, "step": step,
                                 "experience_context": kwargs.get("experience_context"),
                                 "cursor_position": kwargs.get("cursor_position")})
        return predictor(task, history=history, step=step, context=context)

    def execution(self, value):
        self.executions.append(value)


class Core:
    def __init__(self):
        self.stop = Stop()
        self.backend = object()
        self.feedback = None
        self.calls = []
        self.receipt = {"effect": "unverifiable"}

    def execute(self, prediction, *, target, observation_id, state, execution_context=None):
        self.calls.append((prediction, target, observation_id, state))
        actions = prediction["actions"]
        if actions == [{"done": True}]:
            return ExecutionResult("completion_requested", target)
        return ExecutionResult(
            "delivered",
            target,
            actions[0],
            self.receipt,
            delivery_mode="foreground",
        )


def test_unverifiable_action_reobserves_without_replay_then_reviews_completion(tmp_path):
    result = {"actions": 0, "history": [], "model_io": []}
    observer, core = Observer(tmp_path), Core()
    core.receipt = {"effect": "unverifiable", "adapter_advisory": "delivery_path_warning"}
    audit = Audit(result)
    responses = [
        {"status": "predicted", "prediction": {"actions": [CLICK]}},
        {"status": "predicted", "prediction": {"actions": [{"done": True}]}},
        {"status": "reviewed", "verification": {
            "verdict": "complete", "evidence": "saved file is visible", "missing": ""}},
    ]

    def predictor(*_args, **_kwargs):
        # The loop's audit index is the authoritative per-round index.
        result["model_io"].append({"output_directory": str(tmp_path)})
        return responses.pop(0)

    events = []
    run_local_agent_loop(
        observer=observer,
        initial_target={"pid": 1, "window_id": 2},
        core=core,
        audit=audit,
        predictor=predictor,
        instruction="save text",
        model="gui-owl-2b",
        root=tmp_path / "run-id",
        result=result,
        record=lambda kind, **data: events.append({"type": kind, **data}),
        stop=core.stop,
        status_callback=lambda _message: None,
    )

    assert result["actions"] == 1
    assert result["history"][0]["effect"] == "unverifiable"
    assert result["history"][0]["delivery_advisory"] == "delivery_path_warning"
    assert result["task_complete"] is True
    assert result["stop_reason"] == "visual_completion_reviewed"
    assert len(core.calls) == 2
    assert core.calls[0][0]["actions"] == [CLICK]
    assert core.calls[1][0]["actions"] == [{"done": True}]
    assert observer.observations == [
        ({"pid": 1, "window_id": 2}, 0),
        ({"pid": 1, "window_id": 2}, 1),
        ({"pid": 1, "window_id": 2}, 2),
    ]
    assert audit.predictions[1]["history"][0]["action"] == {"actions": [CLICK]}
    assert audit.predictions[1]["history"][0]["delivery_advisory"] == "delivery_path_warning"
    assert audit.predictions[0]["cursor_position"] is None
    assert audit.predictions[1]["cursor_position"] == [.5, .5]
    assert any(event["type"] == "effect_reobservation" for event in events)
    assert len(audit.executions) == 2


def test_multi_action_plan_records_each_receipt_before_next_observation(tmp_path):
    result = {"actions": 0, "history": [], "model_io": []}
    observer, core = Observer(tmp_path), Core()
    audit = Audit(result)
    second = {"skill": "type_text", "args": {"text": "hello"}}
    responses = [
        {"status": "predicted", "prediction": {"actions": [CLICK, second]}},
        {"status": "model_control", "control": "answer", "text": "stopped"},
    ]

    def predictor(*_args, **_kwargs):
        result["model_io"].append({"output_directory": str(tmp_path)})
        return responses.pop(0)

    def execute_batch(prediction, *, target, observation_id, state, execution_context=None):
        core.calls.append((prediction, target, observation_id, state))
        steps = tuple({"action": action, "receipt": {"effect": "unverifiable"},
                       "target": target, "delivery_mode": "foreground",
                       "background_refusal": None, "elapsed_ms": 2.0}
                      for action in prediction["actions"])
        return ExecutionResult("delivered", target, second, steps[-1]["receipt"],
                               delivery_mode="foreground", steps=steps)
    core.execute = execute_batch

    run_local_agent_loop(
        observer=observer, initial_target={"pid": 1, "window_id": 2},
        core=core, audit=audit, predictor=predictor, instruction="type text",
        model="qwen3-vl-2b", root=tmp_path / "run-id", result=result,
        record=lambda *_args, **_kwargs: None, stop=core.stop,
        status_callback=lambda _message: None,
    )
    assert result["actions"] == 2
    assert [entry["action"]["actions"][0] for entry in result["history"]] == [CLICK, second]
    assert audit.predictions[1]["history"] == result["history"]
    assert len(observer.observations) == 2


def test_delivered_cursor_does_not_cross_window_or_use_rejected_action():
    history = [
        {"executed": True, "action": {"actions": [CLICK]}},
        {"executed": False, "action": {"actions": [{
            "skill": "move_cursor", "args": {"x": .9, "y": .9},
        }]}},
    ]
    assert _delivered_cursor(history) == [.5, .5]
    history.append({"executed": True, "action": {"actions": [{
        "skill": "switch_window", "args": {"pid": 1, "window_id": 2},
    }]}})
    assert _delivered_cursor(history) is None


def test_delivered_cursor_is_not_reused_after_observation_target_changes(tmp_path):
    class ChangedObserver(Observer):
        def observe(self, target, index):
            if index == 1:
                target = {"pid": 3, "window_id": 4}
            return super().observe(target, index)

    result = {"actions": 0, "history": [], "model_io": []}
    observer, core = ChangedObserver(tmp_path), Core()
    audit = Audit(result)
    responses = [
        {"status": "predicted", "prediction": {"actions": [CLICK]}},
        {"status": "model_control", "control": "interact", "text": "Stop"},
    ]

    def predictor(*_args, **_kwargs):
        result["model_io"].append({"output_directory": str(tmp_path)})
        return responses.pop(0)

    run_local_agent_loop(
        observer=observer, initial_target={"pid": 1, "window_id": 2},
        core=core, audit=audit, predictor=predictor, instruction="test",
        model="gui-owl-2b", root=tmp_path / "run-id", result=result,
        record=lambda *_args, **_kwargs: None, stop=core.stop,
        status_callback=lambda _message: None,
    )
    assert audit.predictions[1]["cursor_position"] is None


def test_adapter_rejection_reobserves_and_never_replays(tmp_path):
    result = {"actions": 0, "history": [], "model_io": []}
    observer, core = Observer(tmp_path), Core()
    audit = Audit(result)
    responses = [
        {"status": "adapter_rejected", "error": "native hover unavailable"},
        {"status": "model_control", "control": "interact", "text": "Need user input"},
    ]

    def predictor(*_args, **_kwargs):
        result["model_io"].append({"output_directory": str(tmp_path)})
        return responses.pop(0)

    events = []
    run_local_agent_loop(
        observer=observer, initial_target={"pid": 1, "window_id": 2},
        core=core, audit=audit, predictor=predictor, instruction="test",
        model="gui-owl-2b", root=tmp_path / "run-id", result=result,
        record=lambda kind, **data: events.append({"type": kind, **data}),
        stop=core.stop, status_callback=lambda _message: None,
    )
    assert len(observer.observations) == 2
    assert not core.calls
    assert result["stop_reason"] == "model_requests_user"
    assert core.feedback["status"] == "adapter_rejected"
    assert any(row["type"] == "adapter_rejected" for row in events)


def test_malformed_reply_regenerates_from_fresh_observation_without_input(tmp_path):
    result = {"actions": 0, "history": [], "model_io": []}
    observer, core = Observer(tmp_path), Core()
    audit = Audit(result)
    responses = [
        {"status": "format_rejected", "error": "Expected exactly one tool_call"},
        {"status": "predicted", "prediction": {"actions": [CLICK]}},
        {"status": "model_control", "control": "answer", "text": "Stopping"},
    ]

    def predictor(*_args, **_kwargs):
        result["model_io"].append({"output_directory": str(tmp_path)})
        return responses.pop(0)

    events = []
    run_local_agent_loop(
        observer=observer, initial_target={"pid": 1, "window_id": 2},
        core=core, audit=audit, predictor=predictor, instruction="test",
        model="gui-owl-2b", root=tmp_path / "run-id", result=result,
        record=lambda kind, **data: events.append({"type": kind, **data}),
        stop=core.stop, status_callback=lambda _message: None,
    )
    assert len(observer.observations) == 3
    assert len(core.calls) == 1
    assert result["actions"] == 1
    assert audit.predictions[1]["context"]["execution_feedback"]["status"] == "format_rejected"
    assert audit.predictions[1]["history"] == []
    assert any(row["type"] == "format_rejected" and row["executed"] is False for row in events)


def test_three_malformed_replies_stop_without_dispatch(tmp_path):
    result = {"actions": 0, "history": [], "model_io": []}
    observer, core = Observer(tmp_path), Core()
    audit = Audit(result)

    def predictor(*_args, **_kwargs):
        result["model_io"].append({"output_directory": str(tmp_path)})
        return {"status": "format_rejected", "error": "Invalid JSON"}

    run_local_agent_loop(
        observer=observer, initial_target={"pid": 1, "window_id": 2},
        core=core, audit=audit, predictor=predictor, instruction="test",
        model="gui-owl-2b", root=tmp_path / "run-id", result=result,
        record=lambda *_args, **_kwargs: None,
        stop=core.stop, status_callback=lambda _message: None,
    )
    assert len(observer.observations) == 3
    assert not core.calls
    assert result["stop_reason"] == "format_rejection_limit"
    assert result["history"] == []


def test_d_model_done_stops_without_visual_completion_review(tmp_path):
    result = {"actions": 0, "history": [], "model_io": []}
    observer, core = Observer(tmp_path), Core()
    audit = Audit(result)

    def predictor(*_args, **_kwargs):
        result["model_io"].append({"output_directory": str(tmp_path)})
        return {"status": "predicted", "prediction": {"actions": [{"done": True}]}}

    run_local_agent_loop(
        observer=observer,
        initial_target={"pid": 1, "window_id": 2},
        core=core,
        audit=audit,
        predictor=predictor,
        instruction="test",
        model="D-5970",
        root=tmp_path / "run-id",
        result=result,
        record=lambda *_args, **_kwargs: None,
        stop=core.stop,
        status_callback=lambda _message: None,
    )

    assert result["stop_reason"] == "completion_unverifiable"
    assert result.get("task_complete") is not True
    assert len(observer.observations) == 1
    assert len(core.calls) == 1


@pytest.mark.parametrize('with_images', [False, True])
def test_experience_context_reaches_every_non_d_model_request(tmp_path, with_images):
    from test_task_conversations import experience, image_experience

    result = {"actions": 0, "history": [], "model_io": []}
    observer, core = Observer(tmp_path), Core()
    audit = Audit(result)
    # A/D are frozen complete inputs, not the retired state-graph projection.
    experience_context = image_experience() if with_images else experience()

    responses = [
        {"status": "predicted", "prediction": {"actions": [CLICK]}},
        {"status": "predicted", "prediction": {"actions": [{"done": True}]}},
        {"status": "reviewed", "verification": {
            "verdict": "complete", "evidence": "text visible", "missing": ""}},
    ]

    def predictor(*_args, **_kwargs):
        result["model_io"].append({"output_directory": str(tmp_path)})
        return responses.pop(0)

    run_local_agent_loop(
        observer=observer,
        initial_target={"pid": 1, "window_id": 2},
        core=core,
        audit=audit,
        predictor=predictor,
        instruction="type text",
        model="mai-ui-2b",
        root=tmp_path / "run-id",
        result=result,
        record=lambda *_args, **_kwargs: None,
        stop=core.stop,
        status_callback=lambda _message: None,
        experience_context=experience_context,
    )

    assert len(audit.predictions) == 3
    assert all(item['experience_context'] == experience_context for item in audit.predictions)


def test_d_model_rejects_experience_without_mutating_its_task_input(tmp_path):
    result = {"actions": 0, "history": [], "model_io": []}
    with pytest.raises(ValueError, match="冻结输入记录"):
        run_local_agent_loop(
            observer=Observer(tmp_path), initial_target={"pid": 1, "window_id": 2},
            core=Core(), audit=Audit(result), predictor=lambda *_args, **_kwargs: None,
            instruction="test", model="D-5970", root=tmp_path / "run-id", result=result,
            record=lambda *_args, **_kwargs: None, stop=Stop(), status_callback=lambda _message: None,
            experience_context={"not": "a valid context"},
        )
    assert result["model_io"] == []
