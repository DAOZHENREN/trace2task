"""Unit tests for the Win32 unified-execution adapter; no native input."""

from types import SimpleNamespace

import pytest

from trace2task.execution_core import ExecutionCore
from trace2task.execution_protocol import ForegroundUnavailable, UnifiedAction
from trace2task.win32_execution import Win32ExecutionBackend

TARGET = {"foreground_handle": 700, "screen_size": (1920, 1080)}
CLICK = {"skill": "click", "args": {"x": 0.25, "y": 0.75, "button": "left"}}


class FakeMotor:
    def __init__(self, *, mode="foreground"):
        self.calls = []
        self.mode = mode
        self.window_handle = 700

    def execute(self, action):
        self.calls.append(action)
        return SimpleNamespace(
            skill=action.skill,
            window_handle=self.window_handle,
            elapsed_ms=12.5,
            screen_position=(480, 810),
            input_mode=self.mode,
        )


@pytest.fixture
def harness():
    motor = FakeMotor()
    live = {"handle": 700, "size": (1920, 1080)}
    backend = Win32ExecutionBackend(
        motor,
        foreground_handle=lambda: live["handle"],
        screen_size=lambda: live["size"],
    )
    records = []
    stop = SimpleNamespace(raise_if_requested=lambda: None)
    core = ExecutionCore(backend, stop, lambda kind, **data: records.append({"type": kind, **data}))
    return core, backend, motor, live, records


def execute(harness, actions, observation="frame-0", *, state=TARGET):
    return harness[0].execute({"actions": actions}, target=TARGET, observation_id=observation, state=state)


def test_rebuilt_action_dispatches_once_with_truthful_foreground_receipt(harness):
    result = execute(harness, [CLICK])
    motor = harness[2]
    assert result.status == "delivered"
    assert len(motor.calls) == 1
    assert motor.calls[0].to_payload() == CLICK
    assert result.receipt == {
        "effect": "unverifiable",
        "route": "win32_foreground_input",
        "delivery": {"mode": "foreground", "route": "win32_foreground_input"},
        "elapsed_ms": 12.5,
        "window_handle": 700,
        "screen_position": (480, 810),
    }


def test_all_existing_non_retargeting_actioncall_skills_authorize(harness):
    backend = harness[1]
    payloads = [
        CLICK,
        {"skill": "double_click", "args": {"x": 0, "y": 1}},
        {"skill": "hold_mouse", "args": {"x": .5, "y": .5, "duration_ms": 1}},
        {"skill": "drag", "args": {"start_x": 0, "start_y": 0, "end_x": 1, "end_y": 1, "duration_ms": 1}},
        {"skill": "type_text", "args": {"text": "hello"}},
        {"skill": "press_key", "args": {"key": "enter"}},
        {"skill": "hold_key", "args": {"key": "a", "duration_ms": 1}},
        {"skill": "hotkey", "args": {"keys": ["ctrl", "s"]}},
        {"skill": "wait", "args": {"duration_ms": 1}},
    ]
    for payload in payloads:
        action = UnifiedAction.from_payload(payload)
        backend.authorize(action, TARGET)
        assert backend.prepare(action, TARGET).to_payload() == action.to_payload()


def test_retargeting_focus_action_is_rejected_before_native_input(harness):
    action = UnifiedAction.from_payload({"skill": "focus_window", "args": {}})
    result = execute(harness, [action.to_payload()])
    assert result.status == 'rejected'
    assert 'does not support' in result.reason
    assert not harness[2].calls


def test_model_window_switch_on_win32_is_capability_feedback_not_fatal(harness):
    action = {'skill': 'switch_window', 'args': {'pid': 9, 'window_id': 10}}
    result = execute(harness, [action])
    assert result.status == 'rejected'
    assert 'switch_window' in result.reason
    assert harness[0].feedback['executed'] is False
    assert not harness[2].calls


@pytest.mark.parametrize("change", ["state_handle", "state_size", "live_handle", "live_size"])
def test_changed_foreground_or_screen_rejects_stale_coordinates(harness, change):
    state = dict(TARGET)
    if change == "state_handle":
        state["foreground_handle"] = 701
    elif change == "state_size":
        state["screen_size"] = (1921, 1080)
    elif change == "live_handle":
        harness[3]["handle"] = 701
    else:
        harness[3]["size"] = (1921, 1080)
    result = execute(harness, [CLICK], state=state)
    assert result.status == "reobserve" and "changed" in result.reason
    assert not harness[2].calls


def test_stale_foreground_reobserves_without_replaying_old_plan(harness):
    harness[3]["handle"] = 701
    assert execute(harness, [CLICK]).status == "reobserve"
    assert not harness[2].calls
    fresh = {"foreground_handle": 701, "screen_size": (1920, 1080)}
    harness[2].window_handle = 701
    result = harness[0].execute({"actions": [CLICK]}, target=fresh,
                                observation_id="frame-1", state=fresh)
    assert result.status == "delivered" and len(harness[2].calls) == 1


def test_wait_is_local_and_does_not_claim_app_effect(harness):
    result = execute(harness, [{"skill": "wait", "args": {"duration_ms": 5}}])
    assert result.status == "delivered"
    assert result.receipt["effect"] == "confirmed"
    assert result.receipt["route"] == "local_wait"
    assert result.receipt["delivery"]["mode"] == "local"


def test_native_pointer_move_and_wheel_use_bound_foreground_only(harness):
    motor, live = harness[2], harness[3]
    calls = []
    pointer = SimpleNamespace(
        set_cursor_position=lambda *xy: calls.append(("move", xy)),
        send_mouse_wheel=lambda *parts: calls.append(("wheel", parts)),
    )
    motor.session = SimpleNamespace(
        backend=pointer,
        require_foreground=lambda: SimpleNamespace(handle=live["handle"]),
        normalized_to_screen=lambda _window, x, y: (round(x * 1920), round(y * 1080)),
    )
    move = {"skill": "move_cursor", "args": {"x": .25, "y": .5}}
    result = execute(harness, [move], observation="frame-move")
    assert result.status == "delivered"
    assert result.receipt["effect"] == "unverifiable"
    assert calls == [("move", (480, 540))]
    wheel = {"skill": "scroll", "args": {
        "direction": "down", "amount": 3, "by": "line", "x": .25, "y": .5,
    }}
    result = execute(harness, [wheel], observation="frame-wheel")
    assert result.status == "delivered"
    assert calls[-2:] == [("move", (480, 540)), ("wheel", ("down", 3, "line"))]
    assert not motor.calls  # The legacy motor never receives an invented click.


def test_wheel_without_known_target_does_not_send_input(harness):
    result = execute(harness, [{"skill": "scroll", "args": {
        "direction": "down", "amount": 1, "by": "line",
    }}])
    assert result.status == "rejected"
    assert not harness[2].calls


def test_implicit_scroll_target_is_resolved_only_by_win32_adapter(harness):
    motor, live = harness[2], harness[3]
    calls = []
    motor.session = SimpleNamespace(
        backend=SimpleNamespace(
            set_cursor_position=lambda *xy: calls.append(("move", xy)),
            send_mouse_wheel=lambda *parts: calls.append(("wheel", parts)),
        ),
        require_foreground=lambda: SimpleNamespace(handle=live["handle"]),
        normalized_to_screen=lambda _window, x, y: (round(x * 1920), round(y * 1080)),
    )
    scroll = {"skill": "scroll", "args": {"direction": "down", "amount": 3, "by": "line"}}
    result = harness[0].execute(
        {"actions": [scroll]}, target=TARGET, observation_id="frame-scroll", state=TARGET,
        execution_context={"cursor_position": [.25, .5]},
    )
    assert result.status == "delivered"
    assert result.action == scroll
    assert calls == [("move", (480, 540)), ("wheel", ("down", 3, "line"))]
    request = harness[0].last_requests[0]
    assert request['status'] == 'delivered'
    assert request['request']['backend'] == 'win32'
    assert request['request']['action']['args'] == {
        **scroll['args'], 'x': .25, 'y': .5,
    }
    assert harness[0].last_plan['actions'][0]['args'] == scroll['args']


def test_coordinate_text_is_rejected_by_win32_adapter_not_model_decoder(harness):
    action = {"skill": "type_text", "args": {"text": "hello", "x": .25, "y": .5}}
    result = execute(harness, [action])
    assert result.status == "rejected"
    assert "cannot target" in result.reason
    assert not harness[2].calls


def test_non_foreground_motor_result_fails_closed_after_one_call(harness):
    harness[2].mode = "background"
    with pytest.raises(RuntimeError, match="requires foreground"):
        execute(harness, [CLICK])
    assert len(harness[2].calls) == 1
    assert harness[4][-1]["status"] == "interrupted_or_unknown"


def test_core_executes_batch_and_observation_cannot_replay(harness):
    assert execute(harness, [CLICK, CLICK]).status == "delivered"
    assert len(harness[2].calls) == 2
    assert not any(row["type"] == "discard_remaining" for row in harness[4])
    with pytest.raises(ValueError, match="consumed"):
        execute(harness, [CLICK])
    assert len(harness[2].calls) == 2


def test_foreground_retry_is_unreachable_and_never_sends_input(harness):
    action = UnifiedAction.from_payload(CLICK)
    prepared = harness[1].prepare(action, TARGET)
    with pytest.raises(ForegroundUnavailable, match="not retryable"):
        harness[1].retry_foreground(action, prepared, TARGET, SimpleNamespace())
    assert not harness[2].calls
