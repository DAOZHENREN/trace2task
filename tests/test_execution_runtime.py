import threading
from types import SimpleNamespace

import pytest

from trace2task.actions import ActionCall
from trace2task.execution_runtime import ExecutionRuntime


class Cancelled(Exception):
    pass


class Stop:
    def __init__(self):
        self.cancelled = False
        self.threads = []

    def raise_if_requested(self):
        self.threads.append(threading.get_ident())
        if self.cancelled:
            raise Cancelled()


def test_planning_worker_and_guards_are_on_correct_threads():
    stop = Stop()
    messages = []
    runtime = ExecutionRuntime(stop=stop, status_callback=messages.append, max_actions=2)
    caller = threading.get_ident()
    worker = runtime.plan(threading.get_ident)
    assert worker != caller
    assert set(stop.threads) == {caller}
    assert runtime.last_plan_ms >= 0
    assert any("耗时" in message for message in messages)


def test_cancel_does_not_wait_for_slow_model_or_execute_input():
    stop = Stop()
    release = threading.Event()
    finished = threading.Event()
    runtime = ExecutionRuntime(stop=stop, status_callback=lambda _: None, max_actions=2)

    def slow():
        stop.cancelled = True
        try:
            release.wait(2)
        finally:
            finished.set()

    try:
        with pytest.raises(Cancelled):
            runtime.plan(slow)
        assert not finished.is_set()
    finally:
        release.set()
        assert finished.wait(2)


def test_provider_error_is_relayed_with_timing():
    runtime = ExecutionRuntime(stop=Stop(), status_callback=lambda _: None, max_actions=2)

    def fail():
        raise ValueError("provider failure")

    with pytest.raises(ValueError, match="provider failure"):
        runtime.plan(fail)
    assert runtime.last_plan_ms >= 0


def test_action_budget_and_stop_checked_before_motor():
    stop = Stop()
    runtime = ExecutionRuntime(stop=stop, status_callback=lambda _: None, max_actions=1)
    calls = []
    motor = SimpleNamespace(
        execute=lambda action: (calls.append(action), SimpleNamespace(elapsed_ms=12))[1]
    )
    action = ActionCall("press_key", {"key": "enter"})
    runtime.execute(motor, action)
    with pytest.raises(RuntimeError, match="budget"):
        runtime.execute(motor, action)
    assert len(calls) == 1
    stop.cancelled = True
    with pytest.raises(Cancelled):
        runtime.execute(motor, action)
    assert len(calls) == 1


def test_progress_heartbeat():
    release = threading.Event()
    clock_value = [0]
    messages = []

    def clock():
        clock_value[0] += 11
        return clock_value[0]

    def status(message):
        messages.append(message)
        if "仍在响应" in message:
            release.set()

    runtime = ExecutionRuntime(stop=Stop(), status_callback=status, max_actions=1, clock=clock)
    runtime.plan(lambda: release.wait(2))
    assert any("仍在响应" in message for message in messages)
