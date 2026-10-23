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
    assert android_budget() == {'max_actions': 80, 'max_seconds': 1200}
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
