import json
from pathlib import Path

import pytest
from test_chat_agent_bridge import Driver, Session, Stop, _reply

from trace2task.chat_agent_runner import run_chat_agent
from trace2task.execution_budget import BudgetStop, TimeBudgetExceeded, android_budget
from trace2task.windows_runner import EmergencyStopRequested


@pytest.mark.parametrize('value', [{}, {'max_actions': 0, 'max_seconds': 1200},
    {'max_actions': 501, 'max_seconds': 1200}, {'max_actions': True, 'max_seconds': 1200},
    {'max_actions': 80, 'max_seconds': 0}, {'max_actions': 80, 'max_seconds': 7201},
    {'max_actions': 80, 'max_seconds': 30.5}])
def test_invalid_budget(value):
    with pytest.raises(ValueError):
        android_budget(value)


def test_budget_defaults_and_copy():
    assert android_budget() == {'max_actions': 160, 'max_seconds': 2400}
    value = {'max_actions': 33, 'max_seconds': 180}
    result = android_budget(value)
    value['max_actions'] = 1
    assert result['max_actions'] == 33


def test_time_budget_clamps_wait_and_preserves_user_stop(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr('trace2task.execution_budget.time.monotonic', lambda: clock[0])
    class WaitingStop(Stop):
        def sleep(self, duration):
            clock[0] += duration
    stop = BudgetStop(WaitingStop(), 5)
    with pytest.raises(TimeBudgetExceeded):
        stop.sleep(100)
    assert clock[0] == 105
    class UserStop(Stop):
        def raise_if_requested(self):
            raise EmergencyStopRequested()
    with pytest.raises(EmergencyStopRequested) as exc:
        BudgetStop(UserStop(), 5).raise_if_requested()
    assert not isinstance(exc.value, TimeBudgetExceeded)


def test_expired_model_answer_cannot_dispatch(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr('trace2task.execution_budget.time.monotonic', lambda: clock[0])
    class SlowSession(Session):
        def run_turn(self, **request):
            clock[0] = 31
            return _reply([{'skill': 'click', 'args': {'x': .5, 'y': .5, 'button': 'left'}}])
    driver = Driver(tmp_path)
    result = run_chat_agent(instruction='test', model='fake', reasoning_effort='low',
        output_root=tmp_path, emergency_stop=Stop(), status_callback=lambda msg: None,
        executor_backend='cua', cua_target=Driver.target, cua_driver=driver,
        session=SlowSession([]), max_actions=80, max_seconds=30)
    assert result['stop_reason'] == 'time_limit'
    assert result['actions'] == 0 and not result['task_complete']
    assert driver.calls == []
    saved = json.loads((Path(result['trace_path']).parent / 'result.json').read_text())
    assert saved['budget'] == {'max_actions': 80, 'max_seconds': 30}


@pytest.mark.parametrize('reason', ['action_limit', 'time_limit'])
def test_budget_stop_is_not_completed_job(tmp_path, monkeypatch, reason, synthetic_console_hotkey):
    from test_android_workbench import configuration

    from trace2task import android_workbench, chat_agent_runner
    from trace2task.web_console import WebConsoleController

    monkeypatch.setattr(android_workbench, 'prepare', lambda value: {})
    calls = []
    def runner(**kwargs):
        calls.append(kwargs)
        return {'stop_reason': reason, 'task_complete': False, 'actions': 12}
    monkeypatch.setattr(chat_agent_runner, 'run_chat_agent', runner)
    controller = WebConsoleController(tmp_path)
    value = {'max_actions': 123, 'max_seconds': 3600}
    job = controller.start_job(task_path='', instruction='test', execute=True,
        executor_backend='android', execution_scope='android', android_config=configuration(tmp_path),
        android_budget=value)
    value['max_actions'] = 1
    result = controller.wait(job['job_id'])
    assert result['status'] == 'stopped'
    assert any('预算耗尽，任务未完成' in line for line in result['logs'])
    assert calls[0]['max_actions'] == 123 and calls[0]['max_seconds'] == 3600


def test_loop_can_continue_past_ten_and_stops_at_selected_budget(tmp_path):
    driver = Driver(tmp_path)
    reply = _reply([{'skill': 'wait', 'args': {'duration_ms': 1}}])
    result = run_chat_agent(instruction='test', model='fake', reasoning_effort='low',
        output_root=tmp_path, emergency_stop=Stop(), status_callback=lambda msg: None,
        executor_backend='cua', cua_target=Driver.target, cua_driver=driver,
        session=Session([reply] * 12), max_actions=12, max_seconds=30)
    assert result['actions'] == 12
    assert result['stop_reason'] == 'action_limit'
    assert not result['task_complete']


def test_cooldown_pauses_budget_but_keeps_stop_and_normal_expiry(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr('trace2task.execution_budget.time.monotonic', lambda: clock[0])
    class Cancellable(Stop):
        stopped = False
        def raise_if_requested(self):
            if self.stopped:
                raise EmergencyStopRequested('stopped')
    stop = Cancellable()
    budget = BudgetStop(stop, 30)
    clock[0] = 5
    budget.rate_limit_wait(590)
    clock[0] = 400
    budget.raise_if_requested()
    stop.stopped = True
    with pytest.raises(EmergencyStopRequested):
        budget.raise_if_requested()
    stop.stopped = False
    clock[0] = 600
    budget.rate_limit_wait(None)
    assert budget.paused_seconds == 590
    budget.raise_if_requested()
    clock[0] = 620
    with pytest.raises(TimeBudgetExceeded):
        budget.raise_if_requested()


def test_early_cooldown_end_returns_unused_budget_pause(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr('trace2task.execution_budget.time.monotonic', lambda: clock[0])
    budget = BudgetStop(Stop(), 30)
    budget.rate_limit_wait(590)
    clock[0] = 10
    budget.rate_limit_wait(None)
    assert budget.paused_seconds == 10
    clock[0] = 40
    with pytest.raises(TimeBudgetExceeded):
        budget.raise_if_requested()


def test_agent_resumes_after_cooldown_and_replans_changed_screen_before_input(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr('trace2task.execution_budget.time.monotonic', lambda: clock[0])
    class CoolingSession(Session):
        def run_turn(self, **request):
            self.last_rate_limit_wait_seconds = 0
            if not self.requests:
                self.rate_limit_callback(590)
                clock[0] += 590
                self.rate_limit_callback(None)
                self.last_rate_limit_wait_seconds = 590
            return super().run_turn(**request)
    click = _reply([{'skill': 'click', 'args': {'x': .5, 'y': .5, 'button': 'left'}}])
    session = CoolingSession([click, click])
    driver = Driver(tmp_path)
    result = run_chat_agent(instruction='test', model='fake', reasoning_effort='low',
        output_root=tmp_path, emergency_stop=Stop(), status_callback=lambda msg: None,
        executor_backend='cua', cua_target=Driver.target, cua_driver=driver,
        session=session, max_actions=1, max_seconds=30)
    assert len(session.requests) == 2
    assert result['actions'] == 1 and len(driver.calls) == 1
    assert result['model_io'][0]['execution']['status'] == 'stale'
    assert result['model_io'][0]['execution']['executed'] is False
    assert result['budget']['rate_limit_wait_seconds'] == 590
    assert result['stop_reason'] == 'action_limit'


@pytest.mark.parametrize('max_seconds,expected_actions', [(600, 1), (330, 0)])
def test_silent_model_wait_preserves_budget_and_rechecks_screen(tmp_path, monkeypatch, max_seconds, expected_actions):
    clock = [0.0]
    monkeypatch.setattr('trace2task.execution_budget.time.monotonic', lambda: clock[0])

    class DelayedSession(Session):
        def run_turn(self, **request):
            self.last_turn_wait_extended = not self.requests
            if self.last_turn_wait_extended:
                self.active_wait_callback(300)
                clock[0] += 350
            return super().run_turn(**request)

    click = _reply([{'skill': 'click', 'args': {'x': .5, 'y': .5, 'button': 'left'}}])
    session, driver = DelayedSession([click, click]), Driver(tmp_path)
    result = run_chat_agent(instruction='synthetic', model='fake', reasoning_effort='low',
        output_root=tmp_path, emergency_stop=Stop(), status_callback=lambda _: None,
        executor_backend='cua', cua_target=Driver.target, cua_driver=driver,
        session=session, max_actions=1, max_seconds=max_seconds)
    assert result['actions'] == len(driver.calls) == expected_actions
    assert not result['budget'].get('rate_limit_wait_seconds')
    if expected_actions:
        assert len(session.requests) == 2
        assert result['model_io'][0]['execution']['status'] == 'stale'
        assert result['model_io'][0]['execution']['executed'] is False
    else:
        assert result['stop_reason'] == 'time_limit'
        assert len(session.requests) == 1
