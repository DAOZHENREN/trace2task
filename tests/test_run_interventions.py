"""Human Android handoffs use fake ADB/model transports; never send real input."""
import hashlib
import json
import subprocess
import threading
from pathlib import Path

import pytest
from test_android_execution import action
from test_android_execution import (
    phone as phone,  # noqa: PLC0414 - register the shared pytest fixture
)
from test_chat_agent_bridge import Session, Stop, _reply

from trace2task.android_execution import AndroidExecutionBackend
from trace2task.chat_agent_runner import run_chat_agent
from trace2task.execution_budget import BudgetStop
from trace2task.execution_core import ExecutionCore
from trace2task.run_interventions import RunInterventions
from trace2task.windows_runner import EmergencyStopRequested


class HookSession(Session):
    def __init__(self, replies, hook=None):
        super().__init__(replies)
        self.hook = hook or (lambda _: None)
        self.active = self.peak = 0

    def run_turn(self, **request):
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            reply = super().run_turn(**request)
            self.hook(len(self.requests))
            return reply
        finally:
            self.active -= 1


def run_phone(tmp_path, control, session, **kwargs):
    return run_chat_agent(instruction='test task', model='fake', reasoning_effort='low',
        output_root=tmp_path / 'runs', emergency_stop=kwargs.pop('emergency_stop', Stop()),
        status_callback=lambda _: None, executor_backend='android',
        android_config={'adb': 'adb', 'serial': 'phone', 'package': 'com.example.game'},
        session=session, interventions=control, **kwargs)


def inputs(data):
    return [c[7:] for c in data['commands'] if c[5:7] == ['shell', 'input']]


def test_instruction_invalidates_pending_reply_and_is_submitted_once(phone, tmp_path):
    _, data = phone
    control = RunInterventions()
    def correct(index):
        if index == 1:
            control.request(request_id='correct-1', kind='instruction', text='只点击右侧按钮')
    session = HookSession([_reply([action('click', x=.1, y=.1).to_payload()]),
                           _reply([action('click', x=.8, y=.8).to_payload()]),
                           _reply([action('wait', duration_ms=1).to_payload()])], correct)
    result = run_phone(tmp_path, control, session, max_actions=2)
    assert result['stop_reason'] == 'action_limit' and result['actions'] == 2
    assert inputs(data) == [['tap', '1024', '576']]
    assert result['model_io'][0]['execution']['status'] == 'intervened'
    assert result['model_io'][0]['execution']['executed'] is False
    assert '只点击右侧按钮' in session.requests[1]['prompt']
    assert '只点击右侧按钮' not in session.requests[2]['prompt']  # Existing provider session retains it.
    assert 'User update:' not in session.requests[2]['prompt']
    assert 'Human intervention from the current user' not in session.requests[2]['prompt']
    command = result['interventions']['commands'][0]
    assert command['status'] == 'responded' and command['submitted_round'] == command['responded_round'] == 1
    assert result['assisted_by_user'] and result['interventions']['phase'] == 'closed'
    assert session.peak == 1


def test_pause_in_batch_waits_for_atomic_action_then_discards_suffix(phone, tmp_path, monkeypatch):
    _, data = phone
    control = RunInterventions()
    atomic_active = False
    acknowledged = []
    original = subprocess.run
    def adb(command, **kwargs):
        nonlocal atomic_active
        if command[5:7] != ['shell', 'input']:
            return original(command, **kwargs)
        atomic_active = True
        result = original(command, **kwargs)
        if len(inputs(data)) == 1:
            control.request(request_id='pause-001', kind='pause')
            assert control.snapshot()['phase'] == 'pausing'
        atomic_active = False
        return result
    monkeypatch.setattr(subprocess, 'run', adb)
    def changed(state):
        if state['phase'] == 'paused':
            assert not atomic_active and len(inputs(data)) == 1
            acknowledged.append(state)
            data.update(rotation=0, size=(720, 1280))
            control.request(request_id='resume-01', kind='resume', text='我已转为竖屏，继续右侧操作')
    control.on_change = changed
    session = HookSession([_reply([action('click', x=.1, y=.1).to_payload(),
                                  action('click', x=.2, y=.2).to_payload()]),
                           _reply([action('click', x=.8, y=.8).to_payload()])])
    result = run_phone(tmp_path, control, session, max_actions=2)
    assert result['stop_reason'] == 'action_limit' and result['actions'] == 2
    assert len(acknowledged) == 1
    assert inputs(data) == [['tap', '128', '72'], ['tap', '576', '1024']]
    assert result['model_io'][0]['execution']['status'] == 'intervened'
    assert len(result['model_io'][0]['execution']['steps']) == 1
    assert len(result['history']) == 2
    segment = result['interventions']['takeovers'][0]
    assert segment['status'] == 'returned' and segment['before']['rotation'] == 1 and segment['after']['rotation'] == 0
    for phase in ('before', 'after'):
        picture = segment[phase]
        assert hashlib.sha256(Path(picture['path']).read_bytes()).hexdigest() == picture['sha256']
    assert segment['before']['sha256'] != segment['after']['sha256']
    request = session.requests[1]
    assert '我已转为竖屏' in request['prompt']
    assert len(request['additional_image_paths']) == 2
    assert [Path(p).name for p in request['additional_image_paths']] == ['takeover-1-before.png', 'takeover-1-after.png']
    assert request['image_path'] not in request['additional_image_paths']
    assert result['model_io'][1]['intervention_command_ids'] == ['pause-001', 'resume-01']


def test_pause_during_model_wait_acknowledges_without_concurrent_request(phone, tmp_path):
    _, data = phone
    control, release = RunInterventions(), threading.Event()
    observed = []
    def waiting(index):
        if index == 1:
            control.request(request_id='pause-001', kind='pause')
            assert release.wait(5), 'Waiting thread must acknowledge the pause while model is still busy'
    def changed(state):
        if state['phase'] == 'paused':
            assert not inputs(data)
            observed.append(state)
            control.request(request_id='resume-01', kind='resume', text='已检查，继续')
            release.set()
    control.on_change = changed
    session = HookSession([_reply([action('click', x=.1, y=.1).to_payload()]),
                           _reply([action('click', x=.8, y=.8).to_payload()])], waiting)
    result = run_phone(tmp_path, control, session, max_actions=1)
    assert result['actions'] == 1 and len(observed) == 1
    assert inputs(data) == [['tap', '1024', '576']]
    assert session.peak == 1 and result['interventions']['takeovers'][0]['status'] == 'returned'


@pytest.mark.parametrize('reason', ['stop', 'budget'])
def test_stop_or_budget_while_paused_never_fabricates_return_or_sends_input(phone, tmp_path, reason):
    _, data = phone
    class Stoppable(Stop):
        requested = False
        def raise_if_requested(self):
            if self.requested:
                raise EmergencyStopRequested('test F9')
    stop = Stoppable()
    guarded = BudgetStop(stop, 120)
    control = RunInterventions()
    def changed(state):
        if state['phase'] == 'paused':
            if reason == 'budget':
                guarded.deadline = 0
            else:
                stop.requested = True
    control.on_change = changed
    session = HookSession([_reply([action('click', x=.1, y=.1).to_payload()])],
        lambda _: control.request(request_id='pause-001', kind='pause'))
    result = run_phone(tmp_path, control, session, emergency_stop=guarded, max_actions=2)
    assert result['stop_reason'] == ('time_limit' if reason == 'budget' else 'emergency_stop')
    assert not inputs(data) and not result['task_complete']
    segment = result['interventions']['takeovers'][0]
    assert segment['before'] is not None and segment['after'] is None and segment['status'] == 'not_returned'


def test_out_of_scope_return_stays_paused_until_new_explicit_retry(phone, tmp_path):
    _, data = phone
    control, attempts = RunInterventions(), []
    def changed(state):
        if state['phase'] == 'paused':
            attempts.append(state)
            if len(attempts) == 1:
                data['package'] = 'com.other.app'
                control.request(request_id='resume-01', kind='resume')
            else:
                assert state['commands'][-1]['status'] == 'scope_rejected'
                assert state['takeovers'][0]['after'] is None
                data['package'] = 'com.example.game'
                control.request(request_id='resume-02', kind='resume', text='已回授权应用')
    control.on_change = changed
    def pause(index):
        if index == 1:
            control.request(request_id='pause-001', kind='pause')
    session = HookSession([_reply([action('click', x=.1, y=.1).to_payload()]),
                           _reply([action('wait', duration_ms=1).to_payload()])], pause)
    result = run_phone(tmp_path, control, session, max_actions=1)
    assert len(attempts) == 2 and result['actions'] == 1 and not inputs(data)
    assert result['interventions']['commands'][1]['status'] == 'scope_rejected'
    assert result['interventions']['takeovers'][0]['after']['package'] == 'com.example.game'


def test_completion_review_is_invalidated_by_new_instruction(phone, tmp_path):
    control = RunInterventions()
    def correct(index):
        if index == 2:
            control.request(request_id='correct-1', kind='instruction', text='还需核对任务奖励')
    review = json.dumps({'verdict': 'complete', 'evidence': 'visible', 'missing': ''})
    session = HookSession([_reply(complete=True), review, _reply(complete=True), review], correct)
    result = run_phone(tmp_path, control, session, max_actions=1)
    assert result['stop_reason'] == 'visual_completion_reviewed' and result['actions'] == 0
    assert result['completion_reviews'][0]['discarded_by_intervention'] is True
    assert result['model_io'][1]['execution']['status'] == 'intervened'
    assert '还需核对任务奖励' in session.requests[2]['prompt']
    assert len(result['completion_reviews']) == 2
    with pytest.raises(RuntimeError, match='结束'):
        control.request(request_id='too-late-1', kind='instruction', text='late')


def test_command_racing_completion_commit_is_kept_and_old_review_is_discarded(phone, tmp_path):
    control = RunInterventions()
    original = control.seal_completion
    seals = []
    def racing(revision):
        seals.append(revision)
        if len(seals) == 1:
            control.request(request_id='correct-1', kind='instruction', text='还要核对奖励')
        return original(revision)
    control.seal_completion = racing
    review = json.dumps({'verdict': 'complete', 'evidence': 'visible', 'missing': ''})
    session = HookSession([_reply(complete=True), review, _reply(complete=True), review])
    result = run_phone(tmp_path, control, session, max_actions=1)
    assert result['stop_reason'] == 'visual_completion_reviewed' and result['actions'] == 0
    assert len(seals) == 2 and result['completion_reviews'][0]['discarded_by_intervention']
    assert result['model_io'][1]['execution']['status'] == 'intervened'
    assert '还要核对奖励' in session.requests[2]['prompt']


def test_failed_image_preparation_is_not_reported_as_submitted_guidance(phone, tmp_path):
    control = RunInterventions()
    changed_picture = False
    def changed(state):
        nonlocal changed_picture
        if state['phase'] == 'paused':
            control.request(request_id='resume-01', kind='resume', text='继续')
        if state['phase'] == 'running' and state['takeovers'] and not changed_picture:
            changed_picture = True
            Path(state['takeovers'][0]['after']['path']).write_bytes(b'tampered')
    control.on_change = changed
    session = HookSession([_reply([action('click', x=.1, y=.1).to_payload()])],
        lambda _: control.request(request_id='pause-001', kind='pause'))
    result = run_phone(tmp_path, control, session, max_actions=2)
    assert result['stop_reason'] == 'error' and result['actions'] == 0
    assert len(session.requests) == 1
    assert all('submitted_round' not in c for c in result['interventions']['commands'])


def test_intervention_between_prepare_and_admission_sends_nothing(phone):
    driver, data = phone
    control = RunInterventions()
    control.activate(lambda *a, **k: None)
    backend = AndroidExecutionBackend(driver)
    original = backend.prepare
    def prepare(*args, **kwargs):
        value = original(*args, **kwargs)
        control.request(request_id='correct-1', kind='instruction', text='stop old plan')
        return value
    backend.prepare = prepare
    core = ExecutionCore(backend, Stop(), lambda *a, **k: None, interventions=control)
    result = core.execute({'actions': [action('click', x=.1, y=.1).to_payload()]},
        target=driver.target, state=driver.state(), observation_id='first',
        execution_context={'intervention_revision': 0})
    assert result.status == 'intervened' and not core.stopped and core.attempts == 0
    assert not inputs(data)


def test_ids_phases_and_limits_are_enforced_without_duplicate_commands():
    control = RunInterventions()
    with pytest.raises(RuntimeError):
        control.request(request_id='correct-1', kind='instruction', text='test')
    control.activate(lambda *a, **k: None)
    first = control.request(request_id='correct-1', kind='instruction', text='test')
    assert control.request(request_id='correct-1', kind='instruction', text='test') == first
    with pytest.raises(ValueError, match='不同内容'):
        control.request(request_id='correct-1', kind='instruction', text='changed')
    with pytest.raises(RuntimeError, match='确认暂停'):
        control.request(request_id='resume-01', kind='resume')
    with pytest.raises(ValueError):
        control.request(request_id='bad', kind='pause')
    with pytest.raises(ValueError):
        control.request(request_id='invalid-1', kind='instruction', text='x' * 2001)
    assert not control.seal_completion(0)
    for i in range(30):
        control.request(request_id=f'correct-{i+100}', kind='instruction', text='test')
    with pytest.raises(ValueError, match='接管与交还'):
        control.request(request_id='pause-001', kind='pause')
    control.request(request_id='correct-last', kind='instruction', text='last')
    with pytest.raises(ValueError, match='最多'):
        control.request(request_id='correct-over', kind='instruction', text='overflow')
    assert control.seal_completion(control.snapshot()['revision'])
