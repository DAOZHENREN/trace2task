"""Frozen v6 contract tests use fake providers/drivers, never real GUI input."""
import hashlib
import json
from pathlib import Path

import pytest
from test_chat_agent_bridge import Driver, Session, Stop

from trace2task.chat_agent_runner import run_chat_agent
from trace2task.chat_model_adapter import ChatModelAdapter
from trace2task.hybrid_model_adapter import HybridModelAdapter
from trace2task.hybrid_v7_profile import CONFIG, MULTITURN_NOTE, PROTOCOL, SUPERVISOR_PROMPT
from trace2task.windows_runner import EmergencyStopRequested

LOCAL = {'local_model': 'gui-owl-2b', 'inference_backend': 'llama-server'}
CLICK = {'skill': 'click', 'args': {'x': .5, 'y': .5, 'button': 'left'}}


@pytest.mark.parametrize('provider', ['codex', 'api'])
def test_chat_executor_continues_with_only_new_delivery_receipts(tmp_path, provider):
    import base64

    from trace2task.hybrid_chat_executor import HybridChatExecutor
    response = json.dumps({'task_complete': False, 'reason': 'Click menu', 'actions': [CLICK]})
    session = Session([response, response, response])
    session.reset_thread = lambda: pytest.fail('A motor turn reset the task session')
    motor = HybridChatExecutor(provider=provider, model='motor', reasoning_effort='low',
        root=tmp_path, stop=Stop(), android=False, selected_windows=False, session=session)
    encoded = base64.b64encode(b'current-image').decode()
    output = motor.predict('Only this subgoal', image=encoded, request_id='one',
        guided_history=[{'raw_output': 'old delivered click'}], execution_context={})
    assert output['status'] == 'predicted'
    assert output['prediction']['actions'] == [CLICK]
    request = session.requests[0]
    assert request['image_path'].read_bytes() == b'current-image'
    assert 'old delivered click' in request['prompt']
    assert request['output_schema']['properties']['actions']['maxItems'] == 1
    motor.predict('Check the menu', image=encoded, request_id='two',
        guided_history=[{'raw_output': 'new delivered click'}], execution_context={})
    assert 'Check the menu' in session.requests[1]['prompt']
    assert 'new delivered click' in session.requests[1]['prompt']
    assert 'old delivered click' not in session.requests[1]['prompt']
    motor.predict('Check the menu', image=encoded, request_id='three',
        guided_history=[], execution_context={})
    assert session.requests[2]['prompt'] == 'Use the attached current screenshot.'
    motor.close()
    with pytest.raises(RuntimeError, match='closed'):
        motor.predict('Subgoal', image=encoded, request_id='two', guided_history=[], execution_context={})


def test_api_executor_resubmits_same_prefix_and_records_cache_usage(tmp_path):
    import base64

    from trace2task.hybrid_chat_executor import HybridChatExecutor
    from trace2task.model_api import ModelAPIConfig, ModelAPISession

    requests = []
    answer = json.dumps({'task_complete': False, 'reason': 'Click', 'actions': [CLICK]})

    def requester(_config, payload):
        requests.append(payload)
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': answer}}],
                'usage': {'prompt_tokens_details': {'cached_tokens': 1024 if len(requests) > 1 else 0}}}

    stop = Stop()
    session = ModelAPISession(ModelAPIConfig(base_url='http://localhost:8812/v1', api_key='test-secret'),
                              model='executor', requester=requester)
    motor = HybridChatExecutor(provider='api', model='executor', reasoning_effort='default',
        root=tmp_path, stop=stop, android=False, selected_windows=False, session=session)
    for step in range(2):
        result = motor.predict(f'Subgoal {step}', image=base64.b64encode(bytes([step])).decode(),
            request_id=str(step), guided_history=[], execution_context={})
        assert result['status'] == 'predicted'
    assert requests[1]['messages'][:2] == requests[0]['messages']
    assert requests[1]['messages'][2] == {'role': 'assistant', 'content': answer}
    assert result['tokens']['cached_tokens'] == 1024
    motor.close()


@pytest.mark.parametrize('actions,done', [([CLICK, CLICK], False), ([], False), ([CLICK], True)])
def test_chat_executor_rejects_ambiguous_or_batch_output(tmp_path, actions, done):
    from trace2task.hybrid_chat_executor import HybridChatExecutor
    session = Session([json.dumps({'task_complete': done, 'reason': 'Test', 'actions': actions})])
    motor = HybridChatExecutor(provider='codex', model='motor', reasoning_effort='low',
        root=tmp_path, stop=Stop(), android=False, selected_windows=False, session=session)
    assert motor.predict('Subgoal', image='eA==', request_id='one', guided_history=[],
                         execution_context={})['status'] == 'format_rejected'


@pytest.mark.parametrize('director_provider,executor_provider', [('api', 'codex'), ('codex', 'api'), ('api', 'api')])
def test_independent_provider_connections_reach_worker_without_leaking_keys(
        tmp_path, monkeypatch, synthetic_console_hotkey, director_provider, executor_provider):
    from test_android_workbench import configuration

    from trace2task.application_knowledge import save_knowledge
    from trace2task.web_console import WebConsoleController
    calls = []
    monkeypatch.setattr('trace2task.android_workbench.prepare', lambda value: {})
    def runner(**kwargs):
        calls.append(kwargs)
        return {'stop_reason': 'emergency_stop', 'task_complete': False, 'verified': False}
    monkeypatch.setattr('trace2task.chat_agent_runner.run_chat_agent', runner)
    controller = WebConsoleController(tmp_path)
    monkeypatch.setattr(controller.codex_catalog, 'validate', lambda *args: None)
    executor = {'provider': executor_provider, 'model': 'executor-model',
                'reasoning_effort': 'low' if executor_provider == 'codex' else 'default',
                'controller_config': {'max_decisions': 180}}
    if executor_provider == 'api':
        executor['api'] = {'base_url': 'http://localhost:8812/v1', 'api_key': 'executor-secret'}
    android = configuration(tmp_path)
    knowledge = save_knowledge(tmp_path, scope='android', application=android['package'],
        content='Auto battle indicator is in the combat toolbar.', expected_sha256='')
    job = controller.start_job(task_path='', instruction='Goal', execute=True,
        execution_scope='android', executor_backend='android', android_config=android,
        provider=director_provider, model='director-model', reasoning_effort='low',
        api_options={'base_url': 'http://localhost:8811/v1', 'api_key': 'director-secret'},
        execution_mode='hybrid', hybrid=executor,
        knowledge_selection={'id': knowledge['id'], 'sha256': knowledge['sha256']})
    final = controller.wait(job['job_id'])
    assert final['status'] == 'stopped', final
    assert calls[0]['model'] == 'director-model'
    assert calls[0]['hybrid']['model'] == 'executor-model'
    assert calls[0]['application_knowledge'] == final['run_facts']['application_knowledge'] == knowledge
    assert calls[0]['hybrid']['controller_config'] == {**CONFIG, 'max_decisions': 180}
    assert final['run_facts']['hybrid']['controller_config'] == calls[0]['hybrid']['controller_config']
    if director_provider == 'api':
        assert calls[0]['api_config'].api_key == 'director-secret'
    if executor_provider == 'api':
        assert calls[0]['hybrid_executor_api_config'].api_key == 'executor-secret'
        assert calls[0]['hybrid_executor_api_config'].base_url.endswith('8812/v1')
    snapshot = json.dumps(final)
    assert 'executor-secret' not in snapshot and 'director-secret' not in snapshot


def directive(*, decision='stage', goal='Open menu', notices=None):
    return json.dumps({'decision': decision, 'observation': 'visible screen evidence',
        'goal': goal, 'completion_hint': 'Menu is visible', 'notices': ['Stay in this app'] if notices is None else notices,
        'fail_conditions': [], 'actions': []})


def prediction(actions=None):
    actions = [CLICK] if actions is None else actions
    return {'status': 'predicted', 'prediction': {'actions': actions}, 'raw_output': json.dumps(actions)}


def adapter(tmp_path, replies, predict=None, stop=None, *, controller_config=None):
    session = Session(replies)
    worker = HybridModelAdapter(ChatModelAdapter(session, selected_windows=False),
        **LOCAL, root=tmp_path, stop=stop or Stop(), record=lambda *a, **k: None,
        status_callback=lambda text: None, predictor=predict or (lambda *a, **k: prediction()),
        controller_config=controller_config)
    image = tmp_path / 'image.png'
    image.write_bytes(b'synthetic')
    return worker, session, {'screenshot': image, 'history': [], 'context': {}}


def deliver(worker, args, *, change=True):
    output = worker.predict('SECRET OVERALL GOAL', **args)
    assert output['status'] == 'predicted', output
    args['history'].append({'action': {'actions': output['prediction']['actions']}})
    if change:
        path = args['screenshot'].parent / f"screen-{len(args['history'])}.png"
        path.write_bytes(str(len(args['history'])).encode())
        args['screenshot'] = path
    return output


def test_frozen_source_hashes_prompt_schema_and_limits_match_4090():
    from trace2task.hybrid_v6_profile import CONFIG, DIRECTOR_SCHEMA, SUPERVISOR_PROMPT
    root = Path(__file__).parents[1] / 'docs/evidence/terra-guided-v6'
    receipt = json.loads((root / 'provenance.json').read_text())
    manifest = json.loads((root / 'source-manifest.json').read_text())
    for item in receipt['files']:
        assert hashlib.sha256((root / item['path']).read_bytes()).hexdigest() == item['sha256']
        if item['source_manifest_verified']:
            assert manifest[item['path'][5:]] == item.get('original_sha256', item['sha256'])
    prompt = (root / 'task-00/teacher/000.prompt.txt').read_text()
    assert prompt.startswith(SUPERVISOR_PROMPT + '\n')
    assert DIRECTOR_SCHEMA == json.loads((root / 'task-00/teacher/000.schema.json').read_text())
    assert CONFIG == json.loads((root / 'plan.json').read_text())['config']


def test_teacher_payload_excludes_execution_budget_and_diagnostics(tmp_path):
    worker, session, args = adapter(tmp_path, [directive()])
    args['history'] = [{'action': {'actions': [CLICK]}}]
    args['context'] = {'execution_feedback': {'secret': 'diagnostic'}}
    worker.predict('帮我继续完成这个活动', **args, experience_context={
        'model_input': '先核对关卡进度。\n按当前画面判断自动战斗状态。', 'sha256': 'reference-hash'})
    prompt = session.requests[0]['prompt']
    handoff = json.dumps({
        'handoff_reason': 'task_start',
        'current_subgoal': None, 'prior_subgoals': []}, ensure_ascii=False)
    assert prompt.startswith(SUPERVISOR_PROMPT + MULTITURN_NOTE
                             + '\nTask: 帮我继续完成这个活动\nHandoff: ' + handoff)
    assert prompt.endswith('先核对关卡进度。\n按当前画面判断自动战斗状态。')
    assert 'reference-hash' not in prompt
    assert 'diagnostic' not in prompt and 'remaining_decisions' not in prompt


def test_local_executor_receives_knowledge_once_and_current_notices(tmp_path):
    calls = []

    def predict(task, **kwargs):
        calls.append((task, kwargs))
        return prediction([{'done': True}])

    worker, session, args = adapter(tmp_path, [
        directive(notices=['If auto is active, wait.']),
        directive(goal='Next', notices=['Stay inside the combat panel.']),
    ], predict)
    worker.application_knowledge = {'application': 'Game', 'content': 'Combat toolbar layout'}
    worker.predict('Goal', **args, experience_context={'model_input': 'Full historical method'})
    worker.predict('Goal', **args)
    worker.predict('Goal', **args)
    worker.predict('Goal', **args)
    assert 'Application knowledge for Game' in session.requests[0]['prompt']
    assert 'Combat toolbar layout' in session.requests[0]['prompt']
    assert 'Combat toolbar layout' not in session.requests[1]['prompt']
    assert 'notices' in session.requests[0]['output_schema']['required']
    assert 'constraints' not in session.requests[0]['output_schema']['properties']
    assert 'Full historical method' in session.requests[0]['prompt']
    assert 'Full historical method' not in calls[0][0]
    assert calls[0][0] == 'Open menu\nCompletion: Menu is visible\nNotices: ["If auto is active, wait."]\nFail conditions: []'
    assert 'Combat toolbar layout' in calls[0][1]['guided_knowledge']
    assert 'Task experience' in calls[0][1]['guided_knowledge']
    assert 'Full historical method' in calls[0][1]['guided_knowledge']
    assert calls[1][0] == 'Next\nCompletion: Menu is visible\nNotices: ["Stay inside the combat panel."]\nFail conditions: []'
    assert 'guided_knowledge' not in calls[1][1]


@pytest.mark.parametrize('provider', ['codex', 'api'])
@pytest.mark.parametrize('with_knowledge', [False, True])
def test_chat_executor_receives_full_experience_once_with_or_without_knowledge(tmp_path, provider, with_knowledge):
    from trace2task.hybrid_chat_executor import HybridChatExecutor
    answer = json.dumps({'stage_status': 'working', 'reason': 'Next', 'actions': [CLICK]})
    motor_session = Session([answer, answer])
    motor = HybridChatExecutor(provider=provider, model='motor', reasoning_effort='low',
        root=tmp_path / 'motor', stop=Stop(), android=False, selected_windows=False, session=motor_session)
    worker, _, args = adapter(tmp_path, [directive()])
    worker.chat_executor, worker.predictor = motor, motor.predict
    if with_knowledge:
        worker.application_knowledge = {'application': 'Game', 'content': 'Shared battle mechanics'}
    experience = '完整经验：先检查进度。\n分支：按钮禁用时检查提示。\n完成标准：出现结算。'
    worker.predict('Goal', **args, experience_context={'model_input': experience})
    # Executor rounds can omit experience_context: use the selected director snapshot.
    deliver(worker, args)
    deliver(worker, args)
    first, second = [request['prompt'] for request in motor_session.requests]
    assert experience in first
    assert ('Shared battle mechanics' in first) == with_knowledge
    assert 'Task experience' in first
    assert second == 'Use the attached current screenshot.'
    assert motor.session is motor_session


def test_v7_source_hashes_and_local_prompt_provenance():
    from trace2task.hybrid_v7_profile import (
        PROMPT_SHA256,
        SOURCE_PROMPT_SHA256,
        SOURCE_SUPERVISOR_PROMPT,
    )
    root = Path(__file__).parents[1] / 'docs/evidence/terra-guided-v7'
    receipt = json.loads((root / 'provenance.json').read_text(encoding='utf-8'))
    manifest = json.loads((root / 'source-manifest.json').read_text(encoding='utf-8'))
    for item in receipt['files']:
        assert hashlib.sha256((root / item['path']).read_bytes()).hexdigest() == item['sha256']
        if item['source_manifest_key']:
            assert manifest[item['source_manifest_key']] == item['sha256']
    assert (root / '000.prompt.txt').read_text(encoding='utf-8').startswith(SOURCE_SUPERVISOR_PROMPT + MULTITURN_NOTE)
    assert hashlib.sha256(SOURCE_SUPERVISOR_PROMPT.encode()).hexdigest() == SOURCE_PROMPT_SHA256
    assert hashlib.sha256(SUPERVISOR_PROMPT.encode()).hexdigest() == PROMPT_SHA256
    assert PROMPT_SHA256 != SOURCE_PROMPT_SHA256
    assert CONFIG == json.loads((root / 'plan.json').read_text())['config']


def guidance_actions(actions, **kwargs):
    value = json.loads(directive(**kwargs))
    return json.dumps({**value, 'actions': actions})


def test_director_keeps_one_conversation_and_sends_incremental_handoffs(tmp_path):
    worker, session, args = adapter(tmp_path, [directive(), directive(goal='Next')])
    resets = []
    session.reset_thread = lambda: resets.append(True)
    worker.predict('Goal', **args)
    worker.needs_guide, worker.reason = True, 'executor_stage_report'
    worker.predict('Goal', **args)
    assert resets == [True]
    handoff = json.loads(session.requests[1]['prompt'])
    assert handoff['handoff_reason'] == 'executor_stage_report'
    assert handoff['stage_feedback']['delivered_actions'] == 0
    assert set(handoff) == {'handoff_reason', 'stage_feedback'}


def test_failure_conditions_reach_executor_and_report_reaches_director(tmp_path):
    calls = []
    first = json.loads(directive())
    first['fail_conditions'] = ['Automatic battle is disabled']
    def predict(task, **kwargs):
        calls.append(task)
        return {**prediction([{'done': True}]),
                'stage_report': {'status': 'failed', 'reason': 'Automatic battle disabled message is visible'}}
    worker, session, args = adapter(tmp_path, [json.dumps(first), directive(goal='Recover')], predict)
    worker.predict('Goal', **args)
    assert worker.predict('Goal', **args)['reason'] == 'executor_stage_failed'
    assert 'Completion: Menu is visible' in calls[0]
    assert 'Fail conditions: ["Automatic battle is disabled"]' in calls[0]
    worker.predict('Goal', **args)
    feedback = json.loads(session.requests[-1]['prompt'])['stage_feedback']
    assert feedback['executor_report']['status'] == 'failed'
    assert 'disabled message' in feedback['executor_report']['reason']
    assert feedback['delivered_actions'] == 0
    assert feedback['decisions'] == 1
    assert 'fail_conditions' in worker.director_schema['required']
    assert 'handoff_conditions' not in worker.director_schema['properties']


def test_stage_feedback_counts_delivered_actions_across_periodic_reviews(tmp_path):
    worker, session, args = adapter(tmp_path, [directive(), directive(decision='continue'), directive(goal='Next')],
                                    controller_config={'review_interval': 2})
    worker.predict('Goal', **args)
    for _ in range(2):
        deliver(worker, args)
    worker.predict('Goal', **args)
    first = json.loads(session.requests[-1]['prompt'])['stage_feedback']
    assert first['executor_actions'] == first['actions_with_visual_change'] == 2
    for _ in range(2):
        deliver(worker, args)
    worker.predict('Goal', **args)
    second = json.loads(session.requests[-1]['prompt'])['stage_feedback']
    assert second['delivered_actions'] == second['decisions'] == 4
    assert worker.stage_start_decisions == 4
    assert worker.stage_start_pairs == 4


@pytest.mark.parametrize('status', ['working', 'completed', 'failed'])
def test_chat_executor_explicit_stage_outcomes(tmp_path, status):
    import base64

    from trace2task.hybrid_chat_executor import HybridChatExecutor
    response = {'stage_status': status, 'reason': 'Visible evidence', 'actions': [CLICK] if status == 'working' else []}
    session = Session([json.dumps(response)])
    motor = HybridChatExecutor(provider='codex', model='motor', reasoning_effort='low',
        root=tmp_path, stop=Stop(), android=False, selected_windows=False, session=session)
    result = motor.predict('Stage', image=base64.b64encode(b'image').decode(), request_id='one',
        guided_history=[], execution_context={})
    assert result['stage_report'] == {'status': status, 'reason': 'Visible evidence'}
    assert result['status'] == 'predicted'
    assert 'stage_status' in session.requests[0]['output_schema']['required']
    assert 'task_complete' not in session.requests[0]['output_schema']['properties']


def test_previous_director_contract_is_readable(tmp_path):
    reply = json.loads(directive())
    reply['handoff_conditions'] = reply.pop('fail_conditions')
    worker, _, args = adapter(tmp_path, [json.dumps(reply)])
    worker.predict('Goal', **args)
    assert worker.current['fail_conditions'] == []
    assert 'handoff_conditions' not in worker.current


def test_teacher_actions_execute_before_student_and_only_receipts_enter_history(tmp_path):
    calls = []
    def predict(task, **kwargs):
        calls.append((task, kwargs))
        return prediction()
    worker, _, args = adapter(tmp_path, [guidance_actions([CLICK, CLICK])], predict)
    assert deliver(worker, args)['model_role'] == 'director'
    assert deliver(worker, args)['model_role'] == 'director'
    assert not calls
    worker.predict('SECRET', **args)
    assert len(calls[0][1]['guided_history']) == 2
    assert all('Executed guidance action' in item['raw_output'] for item in calls[0][1]['guided_history'])
    assert calls[0][0] == 'Open menu\nCompletion: Menu is visible\nNotices: ["Stay in this app"]\nFail conditions: []'
    assert all('image' not in item for item in calls[0][1]['guided_history'])
    assert 'SECRET' not in calls[0][0]
    assert worker.decisions == 3


def test_teacher_first_action_returns_in_same_round_with_original_prompt(tmp_path):
    worker, session, args = adapter(tmp_path, [guidance_actions([CLICK])])
    output = worker.predict('Goal', **args)
    assert output['status'] == 'predicted'
    assert output['prediction']['actions'] == [CLICK]
    assert output['prompt'] == session.requests[0]['prompt']
    assert json.loads(output['raw_output'])['actions'] == [CLICK]
    assert worker.pending['image_path'] == str(args['screenshot'])
    assert worker.executed_pairs == []


def test_teacher_correction_uses_shared_core_before_executor(tmp_path, monkeypatch):
    calls = []
    def predict(*args, **kwargs):
        calls.append(kwargs)
        return prediction([{'done': True}])
    monkeypatch.setattr('trace2task.local_gui_client.predict_gui', predict)
    director = Session([guidance_actions([CLICK]), directive(decision='finish'),
        json.dumps({'verdict': 'complete', 'evidence': 'Goal visible', 'missing': ''})])
    # Driver changes every screenshot, including ambient animation before input.
    driver = Driver(tmp_path)
    result = run_chat_agent(instruction='Goal', model='director', reasoning_effort='low',
        output_root=tmp_path, emergency_stop=Stop(), status_callback=lambda text: None,
        executor_backend='cua', cua_target={'targets': [driver.target], 'initial_index': 0},
        cua_driver=driver, session=director, max_actions=5, hybrid=LOCAL)
    assert result['task_complete'], result
    assert len(driver.calls) == len(calls) == 1
    assert len(calls[0]['guided_history']) == 1


@pytest.mark.parametrize('reason', ['execution_rejected', 'human_intervention', 'stale'])
def test_teacher_queue_discarded_when_evidence_invalidated(tmp_path, reason):
    worker, _, args = adapter(tmp_path, [guidance_actions([CLICK, CLICK]), directive(goal='Correct')])
    worker.predict('Goal', **args)
    if reason == 'human_intervention':
        args['context'] = {'human_intervention': {'revision': 1, 'commands': []}}
    elif reason == 'stale':
        args['execution_feedback'] = {'status': 'stale'}
    assert worker.predict('Goal', **args)['model_role'] == 'director'
    assert worker.teacher_actions == []
    assert worker.executed_pairs == []


@pytest.mark.parametrize('reply', [guidance_actions([CLICK], decision='finish'),
    guidance_actions([CLICK], decision='blocked'), guidance_actions([CLICK] * 13),
    guidance_actions([{'done': True}])])
def test_invalid_teacher_action_plans_never_reach_execution(tmp_path, reply):
    worker, _, args = adapter(tmp_path, [reply])
    with pytest.raises(ValueError):
        worker.predict('Goal', **args)
    assert worker.teacher_actions == []


def test_stage_keeps_executor_conversation_with_only_current_goal(tmp_path):
    calls = []
    outputs = iter([*(prediction([{'skill': 'click', 'args': {'x': x, 'y': .5, 'button': 'left'}}])
                     for x in (.2, .4, .6)), prediction([{'done': True}]), prediction()])
    def predict(task, **kwargs):
        calls.append((task, kwargs))
        return next(outputs)
    worker, _, args = adapter(tmp_path, [directive(), directive(goal='Correct menu')], predict)
    worker.predict('SECRET OVERALL GOAL', **args)
    for _ in range(3):
        deliver(worker, args)
    assert worker.predict('SECRET OVERALL GOAL', **args)['status'] == 'handoff'
    assert worker.predict('SECRET OVERALL GOAL', **args)['status'] == 'handoff'
    worker.predict('SECRET OVERALL GOAL', **args)
    assert 'SECRET OVERALL GOAL' not in calls[-1][0]
    assert calls[-1][0] == 'Correct menu\nCompletion: Menu is visible\nNotices: ["Stay in this app"]\nFail conditions: []'
    assert [call['guided_history_offset'] for _, call in calls] == [0, 0, 1, 2, 3]
    assert calls[-1][1]['guided_history'] == []
    assert all(call['conversation_id'] == calls[0][1]['conversation_id'] for _, call in calls)
    assert [call['conversation_start'] for _, call in calls] == [True, False, False, False, False]


def test_new_stage_without_delivered_actions_only_sends_current_goal(tmp_path):
    calls = []
    def predict(task, **kwargs):
        calls.append(task)
        return prediction([{'done': True}])
    worker, _, args = adapter(tmp_path, [directive(), directive(), directive(goal='Next')], predict)
    for _ in range(3):
        worker.predict('SECRET', **args)
        worker.predict('SECRET', **args)
    assert calls[-1] == 'Next\nCompletion: Menu is visible\nNotices: ["Stay in this app"]\nFail conditions: []'
    assert worker.executed_pairs == []


@pytest.mark.parametrize('interval', [None, 2])
def test_periodic_continue_preserves_goal_and_stage_history(tmp_path, interval):
    calls = []
    def predict(task, **kwargs):
        calls.append(kwargs)
        return prediction([{'skill': 'click', 'args': {'x': len(calls) / 100, 'y': .5, 'button': 'left'}}])
    config = {'review_interval': interval} if interval else None
    worker, session, args = adapter(tmp_path, [directive(), directive(decision='continue')], predict,
                                    controller_config=config)
    expected = interval or CONFIG['review_interval']
    worker.predict('Goal', **args)
    for _ in range(expected):
        deliver(worker, args)
    output = worker.predict('Goal', **args)
    assert output['status'] == 'handoff' and output['reason'] == 'director_continue'
    assert 'periodic_review' in session.requests[-1]['prompt']
    worker.predict('Goal', **args)
    assert worker.stage_goals == ['Open menu']
    assert calls[-1]['guided_history_offset'] == expected - 1
    assert sum(len(call['guided_history']) for call in calls) == expected
    assert len(calls[-1]['guided_history']) == 1


@pytest.mark.parametrize('periodic,goal', [(False, 'Open menu'), (True, 'Changed')])
def test_continue_rejects_wrong_time_or_changed_contract(tmp_path, periodic, goal):
    worker, _, args = adapter(tmp_path, [directive(), directive(decision='continue', goal=goal)])
    worker.predict('Goal', **args)
    worker.reason = 'periodic_review' if periodic else 'executor_stage_report'
    worker.needs_guide = True
    with pytest.raises(ValueError, match='unchanged contract'):
        worker.predict('Goal', **args)


@pytest.mark.parametrize('limit', [None, 2])
def test_identical_actions_without_visual_change_handoff(tmp_path, limit):
    config = {'repeated_no_change_limit': limit} if limit else None
    worker, session, args = adapter(tmp_path, [directive(), directive(goal='Try another route')],
                                    controller_config=config)
    expected = limit or CONFIG['repeated_no_change_limit']
    worker.predict('Goal', **args)
    for _ in range(expected):
        deliver(worker, args, change=False)
    assert worker.predict('Goal', **args)['model_role'] == 'director'
    assert 'repeated_action_without_visual_change' in session.requests[-1]['prompt']
    assert len(args['history']) == expected


def test_unchanged_picture_alone_does_not_trigger_repetition_detector(tmp_path):
    index = [0]
    def predict(*a, **kw):
        index[0] += 1
        return prediction([{'skill': 'click', 'args': {'x': index[0]/10, 'y': .5, 'button': 'left'}}])
    worker, _, args = adapter(tmp_path, [directive()], predict)
    worker.predict('Goal', **args)
    for _ in range(4):
        deliver(worker, args, change=False)
    assert worker.guide_calls == 1


@pytest.mark.parametrize('limit', [2, 3])
def test_repeated_corner_clicks_review_even_with_animated_frames(tmp_path, limit):
    corner = {'skill': 'click', 'args': {'x': 1, 'y': 1, 'button': 'left'}}
    worker, session, args = adapter(tmp_path, [directive(), directive(goal='Correct target')],
        lambda *a, **kw: prediction([corner]), controller_config={'repeated_no_change_limit': limit})
    worker.predict('Goal', **args)
    for _ in range(limit):
        deliver(worker, args, change=True)
    assert worker.predict('Goal', **args)['model_role'] == 'director'
    handoff = json.loads(session.requests[-1]['prompt'])
    assert handoff['handoff_reason'] == 'repeated_action_requires_review'
    feedback = handoff['stage_feedback']
    assert feedback['consecutive_identical_executor_actions'] == limit
    assert feedback['last_delivered_action'] == corner
    assert feedback['repeated_no_change'] == 0
    assert worker.same_action_count == 0


def test_repeated_wait_with_changing_frames_does_not_trigger_action_watchdog(tmp_path):
    worker, _, args = adapter(tmp_path, [directive()],
        lambda *a, **kw: prediction([{'skill': 'wait', 'args': {'duration_ms': 1000}}]))
    worker.predict('Goal', **args)
    for _ in range(5):
        deliver(worker, args)
    assert worker.guide_calls == 1
    assert worker.same_action_count == 0


def test_chat_executor_defines_normalized_coordinates_in_first_turn(tmp_path):
    from trace2task.hybrid_chat_executor import HybridChatExecutor
    session = Session([json.dumps({'stage_status': 'working', 'reason': 'Attack', 'actions': [CLICK]})])
    motor = HybridChatExecutor(provider='codex', model='motor', reasoning_effort='low',
        root=tmp_path, stop=Stop(), android=True, selected_windows=False, session=session)
    result = motor.predict('Stage', image='eA==', request_id='one', guided_history=[], execution_context={})
    assert result['status'] == 'predicted'
    prompt = session.requests[0]['prompt']
    assert 'normalized to 0..1' in prompt
    assert 'x=pixel_x/image_width, y=pixel_y/image_height' in prompt
    assert '(1,1) is bottom-right' in prompt


def test_rejected_action_not_retained_and_teacher_can_correct(tmp_path):
    worker, session, args = adapter(tmp_path, [directive(), directive(goal='Retry differently')])
    worker.predict('Goal', **args)
    worker.predict('Goal', **args)
    # No delivered receipt; this was only a proposal.
    output = worker.predict('Goal', **args)
    assert output['model_role'] == 'director'
    assert worker.executed_pairs == []
    assert 'execution_rejected' in session.requests[-1]['prompt']
    assert worker.predict('Goal', **args)['model_role'] == 'executor'


@pytest.mark.parametrize('kind', ['guide_calls', 'decisions'])
@pytest.mark.parametrize('limit', [None, 2])
def test_controller_limits_stop_without_another_model_request(tmp_path, kind, limit):
    key = f'max_{kind}'
    worker, session, args = adapter(tmp_path, [directive()],
                                    controller_config={key: limit} if limit else None)
    setattr(worker, kind, limit or CONFIG[key])
    output = worker.predict('Goal', **args)
    assert output['status'] == 'hybrid_stop'
    assert not session.requests


def test_blocked_is_a_stop_not_a_completion_claim(tmp_path):
    worker, _, args = adapter(tmp_path, [directive(decision='blocked')])
    output = worker.predict('Goal', **args)
    assert output['status'] == 'hybrid_stop' and output['reason'] == 'guide_blocked'
    assert 'prediction' not in output


def test_batches_are_rejected_instead_of_inventing_executed_history(tmp_path):
    worker, _, args = adapter(tmp_path, [directive()], lambda *a, **k: prediction([CLICK, CLICK]))
    worker.predict('Goal', **args)
    assert worker.predict('Goal', **args)['status'] == 'handoff'
    assert worker.pending is None


def test_stop_during_local_prediction_never_returns_late_plan(tmp_path):
    class Stoppable(Stop):
        stopped = False
        def raise_if_requested(self):
            if self.stopped:
                raise EmergencyStopRequested('stopped')
    stop = Stoppable()
    def predict(*a, **k):
        stop.stopped = True
        return prediction()
    worker, _, args = adapter(tmp_path, [directive()], predict, stop)
    worker.predict('Goal', **args)
    with pytest.raises(EmergencyStopRequested):
        worker.predict('Goal', **args)


def test_actual_shared_loop_codex_local_codex_review(tmp_path, monkeypatch):
    outputs = iter([prediction(), prediction([{'done': True}])])
    calls = []
    def predict(task, **kwargs):
        calls.append(kwargs)
        return next(outputs)
    monkeypatch.setattr('trace2task.local_gui_client.predict_gui', predict)
    session = Session([directive(), directive(decision='finish'),
        json.dumps({'verdict': 'complete', 'evidence': 'Goal visible', 'missing': ''})])
    driver = Driver(tmp_path)
    result = run_chat_agent(instruction='Goal', model='fake-codex', reasoning_effort='low',
        output_root=tmp_path, emergency_stop=Stop(), status_callback=lambda text: None,
        executor_backend='cua', cua_target={'targets': [driver.target], 'initial_index': 0},
        cua_driver=driver, session=session, max_actions=5, hybrid=LOCAL)
    assert result['task_complete'], result
    assert result['stop_reason'] == 'visual_completion_reviewed'
    assert result['actions'] == len(driver.calls) == 1
    assert len(session.requests) == 3 and len(calls) == 2
    assert session.requests[-1]['image_path'] != session.requests[-2]['image_path']
    assert [r['model_role'] for r in result['model_io']] == [
        'director', 'executor', 'executor', 'director', 'director']
    assert result['hybrid']['protocol'] == PROTOCOL
    assert result['hybrid']['controller_config'] == CONFIG


@pytest.mark.parametrize('provider', ['api', 'codex'])
def test_remote_motor_runs_shared_loop_with_isolated_director(tmp_path, monkeypatch, provider):
    from trace2task.hybrid_chat_executor import HybridChatExecutor
    from trace2task.model_api import ModelAPIConfig

    motor_session = Session([
        json.dumps({'task_complete': False, 'reason': 'Click', 'actions': [CLICK]}),
        json.dumps({'task_complete': True, 'reason': 'Subgoal done', 'actions': []})])
    def factory(**kwargs):
        return HybridChatExecutor(**kwargs, session=motor_session)
    monkeypatch.setattr('trace2task.hybrid_chat_executor.HybridChatExecutor', factory)
    director = Session([guidance_actions([CLICK]), directive(decision='finish'),
        json.dumps({'verdict': 'complete', 'evidence': 'Goal visible', 'missing': ''})])
    driver = Driver(tmp_path)
    result = run_chat_agent(instruction='Secret overall task', model='director', reasoning_effort='low',
        output_root=tmp_path, emergency_stop=Stop(), status_callback=lambda text: None,
        executor_backend='cua', cua_target={'targets': [driver.target], 'initial_index': 0},
        cua_driver=driver, session=director, max_actions=5,
        hybrid={'provider': provider, 'model': 'motor', 'reasoning_effort': 'low'},
        application_knowledge={'application': 'Game', 'content': 'Combat toolbar layout'},
        hybrid_executor_api_config=ModelAPIConfig(base_url='http://localhost:8812/v1') if provider == 'api' else None)
    assert result['task_complete'], result
    assert len(driver.calls) == 2
    assert len(director.requests) == 3 and len(motor_session.requests) == 2
    assert all('Secret overall task' not in request['prompt'] for request in motor_session.requests)
    assert 'New delivered guidance actions: [' in motor_session.requests[0]['prompt']
    assert 'click' in motor_session.requests[0]['prompt']
    assert 'Combat toolbar layout' in motor_session.requests[0]['prompt']
    assert 'Notices: ["Stay in this app"]' in motor_session.requests[0]['prompt']
    assert motor_session.requests[1]['prompt'] == 'Use the attached current screenshot.'


def test_handoff_does_not_consume_single_action_budget(tmp_path, monkeypatch):
    monkeypatch.setattr('trace2task.local_gui_client.predict_gui', lambda *a, **k: prediction())
    driver = Driver(tmp_path)
    result = run_chat_agent(instruction='Goal', model='fake', reasoning_effort='low',
        output_root=tmp_path, emergency_stop=Stop(), status_callback=lambda text: None,
        executor_backend='cua', cua_target={'targets': [driver.target], 'initial_index': 0},
        cua_driver=driver, session=Session([directive()]), max_actions=1, hybrid=LOCAL)
    assert result['actions'] == 1, result
    assert result['stop_reason'] == 'action_limit'


@pytest.mark.parametrize('reply', ['not json', '{}', directive(goal='')])
def test_invalid_director_response_does_not_retry_or_execute(tmp_path, reply):
    worker, _, args = adapter(tmp_path, [reply])
    with pytest.raises(ValueError):
        worker.predict('Goal', **args)
    assert worker.current is None


@pytest.mark.parametrize('change', [
    {'provider': 'api'}, {'executor_backend': 'browser'}, {'execution_mode': 'unknown'},
    {'hybrid': {'local_model': 'unknown', 'inference_backend': 'llama-server'}},
    {'hybrid': {'local_model': 'gui-owl-2b', 'inference_backend': 'slotread'}},
    {'execution_mode': 'original'}, {'hybrid': None},
])
def test_server_rejects_invalid_hybrid_before_launch(tmp_path, change):
    from trace2task.web_console import WebConsoleController
    controller = WebConsoleController(tmp_path)
    with pytest.raises(ValueError):
        controller.start_job(**{'task_path': '', 'instruction': 'Goal', 'execute': True,
            'execution_scope': 'desktop', 'executor_backend': 'cua', 'provider': 'codex',
            'execution_mode': 'hybrid', 'hybrid': LOCAL, **change})


def test_android_worker_receives_hybrid_selection(tmp_path, monkeypatch, synthetic_console_hotkey):
    from test_android_workbench import configuration

    from trace2task.web_console import WebConsoleController
    calls = []
    monkeypatch.setattr('trace2task.android_workbench.prepare', lambda value: {})
    def runner(**kwargs):
        calls.append(kwargs)
        return {'stop_reason': 'emergency_stop', 'task_complete': False, 'verified': False}
    monkeypatch.setattr('trace2task.chat_agent_runner.run_chat_agent', runner)
    controller = WebConsoleController(tmp_path)
    job = controller.start_job(task_path='', instruction='Goal', execute=True,
        execution_scope='android', executor_backend='android', android_config=configuration(tmp_path),
        execution_mode='hybrid', hybrid=LOCAL)
    final = controller.wait(job['job_id'])
    assert final['status'] == 'stopped', final
    assert calls[0]['hybrid']['local_model'] == LOCAL['local_model']
    assert calls[0]['hybrid']['inference_backend'] == LOCAL['inference_backend']
    assert calls[0]['max_actions'] == 160
    assert calls[0]['interventions'] is not None
    assert job['run_facts']['hybrid']['protocol'] == PROTOCOL
    assert job['run_facts']['hybrid']['controller_config'] == CONFIG
