"""Fixed-updater integration with synthetic receipts: no model, game, or desktop input."""
import hashlib
import json
import re
import threading
from copy import deepcopy
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest
from test_trace_evidence import recording
from test_trace_procedure import OPTIONS, PROCEDURE, Session, compile_fake, wait_job

from trace2task import procedure_evolution as evolution
from trace2task.procedure_evolution import (
    evolution_confirmation,
    evolve_procedure,
    prepare_evolution,
    review_evolution,
)
from trace2task.run_session import RunSession, RunStore
from trace2task.trace_combined import generate_c
from trace2task.trace_library import (
    execution_context,
    list_traces,
    read_asset,
    read_body,
    read_model_input,
)
from trace2task.trace_procedure import ProcedureCompileCancelled
from trace2task.web_console import WebConsoleController, create_web_server

REVISED = PROCEDURE.replace('输入 {文件名} 并确认保存。', '输入本次任务提供的 {文件名}，核对目标目录后确认保存。')


def seed_run(root, store=None, *, parent_id=None, run_id='source-run'):
    store = store or RunStore(root)
    if parent_id is None:
        trace, _ = recording(root, 3)
        parent_id = compile_fake(root, trace)['id']
    path = root / 'trace-library' / parent_id / 'model-input.txt'
    context = execution_context(path)
    run = {'job_id': run_id, 'task_id': '保存文档', 'instruction': '保存到指定目录',
           'mode': 'execute', 'kind': 'agent', 'use_experience': True, 'provider': 'codex',
           'model': 'test-model', 'status': 'completed', 'created_at': '2026-10-13T00:00:00Z',
           'updated_at': '2026-10-13T00:01:00Z', 'task_path': path.relative_to(root).as_posix(),
           'run_facts': {'experience_id': parent_id, 'experience_sha256': context['sha256'],
                         'experience_representation': read_body(root, parent_id)['representation']},
           'result': {'stop_reason': 'visual_completion_reviewed',
                      'task_verification_status': 'not_independently_verified'}}
    store.save(run)
    entry = {'step_index': 0, 'status': 'predicted', 'purpose': 'plan',
             'raw_output': 'clicked Save', 'execution': {'executed': True, 'status': 'delivered'},
             'experience_sha256': context['sha256'],
             'screenshot': str(root / 'runs/raw-demo/derived/official/frames/0000.png'),
             'input': {'do_not_forward_private_request': 'PRIVATE_WIRE_DATA'}}
    store.round(run_id, entry)
    return store, run, entry


def generate(root, store, run_id='source-run', *, session=None, **extra):
    session = session or Session(REVISED)
    prepared = prepare_evolution(root, store, run_id)
    return evolve_procedure(root, prepared, **OPTIONS, session_factory=session.factory,
                            binary_resolver=lambda _: 'fake-codex', **extra)


def approve(root, identifier, decision='approved_for_trial'):
    view = read_model_input(root, identifier)
    return review_evolution(root, identifier, candidate_sha256=view['review']['candidate_sha256'],
                            decision=decision)


def test_one_call_freezes_feedback_preserves_b_and_requires_digest_bound_review(tmp_path):
    store, run, _ = seed_run(tmp_path)
    parent_dir = tmp_path / 'trace-library' / run['run_facts']['experience_id']
    originals = {p: p.read_bytes() for p in parent_dir.rglob('*') if p.is_file()}
    session, rounds = Session(REVISED), []
    candidate = generate(tmp_path, store, session=session, on_round=lambda r: rounds.append(deepcopy(r)))
    identity = candidate['id']
    view = read_model_input(tmp_path, identity)
    assert len(session.calls) == 1 and session.closed
    assert session.options['thread_config']['features']['shell_tool'] is False
    assert session.options['thread_config']['web_search'] == 'disabled'
    assert view['content'] == REVISED and view['parent_content'] == PROCEDURE
    assert view['review']['decision'] == 'pending_review' and view['images'] == []
    assert view['evolution']['condition'] == 'trace-compile+fixed-updater'
    assert view['evolution']['meta_improvement'] is False
    assert view['evolution']['task_quality'] == 'not_evaluated'
    request = json.loads(view['compiler']['input'])
    assert request['parent_procedure'] == PROCEDURE
    assert request['limits']['independent_task_verification'] is False
    assert 'PRIVATE_WIRE_DATA' not in view['compiler']['input']
    assert len(view['compiler']['images']) == 1
    audit = json.loads(read_asset(tmp_path, identity, 'evidence/run-feedback.json'))
    assert audit['parent_procedure'] == PROCEDURE
    assert 'full_round_sha256' in audit['rounds'][0]
    assert 'sha256' not in session.calls[0]['prompt']
    assert read_asset(tmp_path, identity, 'compiler-input.txt').decode() == session.calls[0]['prompt']
    assert rounds[0]['purpose'] == 'improve' and rounds[-1]['status'] == 'compiled'
    assert all(r['executed'] is False for r in rounds)
    assert all(p.read_bytes() == data for p, data in originals.items())
    path = tmp_path / 'trace-library' / identity / 'model-input.txt'
    with pytest.raises(ValueError, match='尚未获准'):
        execution_context(path)
    with pytest.raises(ValueError, match='已变化'):
        review_evolution(tmp_path, identity, candidate_sha256='0' * 64, decision='approved_for_trial')
    receipt = approve(tmp_path, identity)
    assert receipt['task_quality'] == 'not_evaluated'
    context = execution_context(path)
    assert context['model_input'] == REVISED and 'images' not in context and 'evolution' not in context
    assert context['sha256'] == hashlib.sha256(REVISED.encode()).hexdigest()
    assert list_traces(tmp_path)[0]['review_status'] == 'approved_for_trial'
    with pytest.raises(ValueError, match='已审查'):
        approve(tmp_path, identity, 'rejected')
    with pytest.raises(ValueError, match='B 方法'):
        generate_c(tmp_path, identity)  # R must not be mislabeled as original B/C.


@pytest.mark.parametrize('change,match', [
    ({'status': 'running'}, '先结束'), ({'mode': 'plan'}, '已结束执行'),
    ({'use_experience': False}, '已结束执行'), ({'kind': 'compilation'}, '已结束执行'),
    ({'run_facts': {}}, '没有冻结'),
    ({'result': {'stop_reason': 'background_unavailable'}}, '后端故障'),
    ({'result': {'stop_reason': 'foreground_unavailable'}}, '后端故障'),
])
def test_ineligible_run_refused_before_any_candidate_write(tmp_path, change, match):
    store, run, _ = seed_run(tmp_path)
    store.save({**run, **change})
    before = set((tmp_path / 'trace-library').iterdir())
    with pytest.raises(ValueError, match=match):
        prepare_evolution(tmp_path, store, run['job_id'])
    assert set((tmp_path / 'trace-library').iterdir()) == before


@pytest.mark.parametrize('field,value,match', [
    ('experience_sha256', None, '实际经验哈希'),
    ('experience_sha256', '0' * 64, '实际经验哈希'),
    ('execution', {'executed': False}, '已交付操作'),
    ('screenshot', 'outside.png', '外部文件'),
])
def test_receipt_and_image_boundaries(tmp_path, field, value, match):
    store, run, entry = seed_run(tmp_path)
    store.round(run['job_id'], {**entry, field: value})
    with pytest.raises(ValueError, match=match):
        prepare_evolution(tmp_path, store, run['job_id'])


def test_selected_parent_hash_and_trashed_source_are_not_inferred(tmp_path):
    store, run, _ = seed_run(tmp_path)
    changed = deepcopy(run)
    changed['run_facts']['experience_sha256'] = '0' * 64
    store.save(changed)
    with pytest.raises(ValueError, match='不一致'):
        prepare_evolution(tmp_path, store, run['job_id'])
    store.save(run)
    store.trash_action(run['job_id'], 'move')
    with pytest.raises(ValueError, match='回收站'):
        prepare_evolution(tmp_path, store, run['job_id'])


def test_complete_round_projection_bounded_images_and_missing_observations(tmp_path):
    store, run, entry = seed_run(tmp_path)
    for i in range(12):
        store.round(run['job_id'], {**entry, 'step_index': i})
    prepared = prepare_evolution(tmp_path, store, run['job_id'])
    assert len(prepared['images']) == 8
    data = json.loads(prepared['input'])
    assert len(data['rounds']) == 12
    assert sum('image_id' in r for r in data['rounds']) == 8
    assert data['images'] == [p['id'] for p in prepared['images']]
    (tmp_path / entry['screenshot']).unlink()
    missing = prepare_evolution(tmp_path, store, run['job_id'])
    assert missing['images'] == []
    missing_rounds = json.loads(missing['input'])['rounds']
    assert all('image_id' not in r for r in missing_rounds)
    assert sum(bool(r.get('image_missing')) for r in missing_rounds) == 8


def test_long_feedback_has_no_fixed_byte_or_round_cap_and_is_not_truncated(tmp_path):
    store, run, entry = seed_run(tmp_path)
    text = '保持每条反馈原文  包括空白\n' * 6000
    for i in range(257):
        store.round(run['job_id'], {**entry, 'step_index': i, 'raw_output': text if i == 256 else str(i)})
    before = store.closed_evidence(run['job_id'])
    prepared = prepare_evolution(tmp_path, store, run['job_id'])
    data = json.loads(prepared['input'])
    assert len(prepared['input'].encode('utf-8')) > 160_000
    assert len(data['rounds']) == 257
    assert [r['step_index'] for r in data['rounds']] == list(range(257))
    assert data['rounds'][-1]['raw_output'] == text
    assert len(prepared['images']) == 8
    assert before == store.closed_evidence(run['job_id'])


@pytest.mark.parametrize('output', ['', '```python\npass\n```', PROCEDURE])
def test_invalid_or_unchanged_output_is_not_published(tmp_path, output):
    store, _, _ = seed_run(tmp_path)
    session = Session(output)
    with pytest.raises(ValueError):
        generate(tmp_path, store, session=session)
    assert session.closed and len(session.calls) == 1
    assert [t['representation'] for t in list_traces(tmp_path)] == ['B']
    assert any(p.read_text(encoding='utf-8') == output
               for p in (tmp_path / 'trace-library').glob('*/model-output.md'))


def test_cancellation_before_and_during_generation_never_publishes(tmp_path):
    store, _, _ = seed_run(tmp_path)
    stop = threading.Event()
    stop.set()
    session = Session(REVISED)
    with pytest.raises(ProcedureCompileCancelled):
        generate(tmp_path, store, session=session, stop_event=stop)
    assert session.options is None
    stop.clear()

    def turn(**kwargs):
        stop.set()
        return REVISED

    session.run_turn = turn
    with pytest.raises(ProcedureCompileCancelled):
        generate(tmp_path, store, session=session, stop_event=stop)
    assert session.closed and len(list_traces(tmp_path)) == 1


@pytest.mark.parametrize('filename', ['parent-procedure.md', 'compiler-input.txt', 'request.json'])
def test_frozen_input_mutation_during_generation_prevents_publication(tmp_path, filename):
    store, _, _ = seed_run(tmp_path)
    session = Session(REVISED)

    def turn(**kwargs):
        (session.options['cwd'].parent / filename).write_text('changed', encoding='utf-8')
        return REVISED

    session.run_turn = turn
    with pytest.raises(ValueError, match='冻结反馈'):
        generate(tmp_path, store, session=session)
    assert len(list_traces(tmp_path)) == 1


@pytest.mark.parametrize('filename', ['model-input.txt', 'evidence/run-feedback.json', 'manifest.json'])
def test_tampered_candidate_cannot_be_approved(tmp_path, filename):
    store, _, _ = seed_run(tmp_path)
    identity = generate(tmp_path, store)['id']
    view = read_model_input(tmp_path, identity)
    directory = tmp_path / 'trace-library' / identity
    (directory / filename).write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError):
        review_evolution(tmp_path, identity, candidate_sha256=view['review']['candidate_sha256'],
                         decision='approved_for_trial')
    assert not (directory / 'trial-review.json').exists()


def test_rejection_prevents_execution_and_duplicate_generation_needs_confirmation(tmp_path):
    store, _, _ = seed_run(tmp_path)
    identity = generate(tmp_path, store)['id']
    approve(tmp_path, identity, 'rejected')
    with pytest.raises(ValueError, match='尚未获准'):
        execution_context(tmp_path / 'trace-library' / identity / 'model-input.txt')
    prepared = prepare_evolution(tmp_path, store, 'source-run')
    for value in (False, None, 1, 'true'):
        reply = evolution_confirmation(tmp_path, prepared, confirm_duplicate=value)
        assert reply['confirmation_required'] and reply['existing_versions'][0]['id'] == identity
    assert evolution_confirmation(tmp_path, prepared, confirm_duplicate=True) is None


def test_approved_r_can_seed_next_generation_but_updater_stays_fixed(tmp_path):
    store, run, _ = seed_run(tmp_path)
    first = generate(tmp_path, store)['id']
    approve(tmp_path, first)
    seed_run(tmp_path, store, parent_id=first, run_id='next-run')
    second = generate(tmp_path, store, 'next-run', session=Session(REVISED.replace('检查标题', '再次核对标题')))
    assert second['evolution']['generation'] == 2
    assert second['evolution']['parent_id'] == first
    assert second['evolution']['root_b_id'] == run['run_facts']['experience_id']
    assert second['evolution']['updater'] == 'fixed'
    assert read_model_input(tmp_path, second['id'])['review']['decision'] == 'pending_review'


def test_candidate_survives_parent_and_source_trash_without_activation(tmp_path):
    controller = WebConsoleController(tmp_path)
    store, run, _ = seed_run(tmp_path, controller.run_store)
    identity = generate(tmp_path, store)['id']
    controller.delete_trace_experience(run['run_facts']['experience_id'])
    store.trash_action(run['job_id'], 'move')
    assert read_model_input(tmp_path, identity)['parent_content'] == PROCEDURE
    approve(tmp_path, identity)
    path = tmp_path / 'trace-library' / identity / 'model-input.txt'
    before = execution_context(path)
    deleted = controller.delete_trace_experience(identity)
    with pytest.raises(ValueError):
        execution_context(path)
    controller.restore_trashed_asset(deleted['trash_path'])
    assert execution_context(path) == before


def test_run_facts_bind_selected_version_without_changing_runtime_text(tmp_path):
    _, run, _ = seed_run(tmp_path)
    value = SimpleNamespace(job_id='f' * 32, provider='codex', model=OPTIONS['model'],
        task_path=run['task_path'], background=False, execution_scope='desktop',
        executor_backend='win32', cua_target=None, use_experience=True)
    facts = RunSession.for_job(value, tmp_path).public()
    assert all(facts[k] == v for k, v in run['run_facts'].items())


def test_http_generation_review_csrf_and_runtime_gate(monkeypatch, tmp_path):
    controller = WebConsoleController(tmp_path)
    _, run, _ = seed_run(tmp_path, controller.run_store)
    session = Session(REVISED)
    monkeypatch.setattr(evolution, 'CodexAppServerSession', session.factory)
    monkeypatch.setattr(evolution, 'resolve_codex_binary', lambda _: 'fake-codex')
    dispatched = []
    monkeypatch.setattr(controller, '_run_job', lambda *args: dispatched.append(args))
    server = create_web_server(tmp_path, port=0, controller=controller)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(base + '/') as response:
            csrf = re.search(r'name="trace2task-csrf" content="([^"]+)"', response.read().decode()).group(1)

        def post(endpoint, payload, token=csrf):
            with opener.open(Request(base + endpoint, data=json.dumps(payload).encode(),
                headers={'Content-Type': 'application/json', 'Origin': base,
                         'X-Trace2Task-CSRF': token}), timeout=5) as response:
                return response.status, json.load(response)

        payload = {'run_id': run['job_id'], **OPTIONS}
        with pytest.raises(HTTPError) as rejected:
            post('/api/traces/evolve', payload, '')
        assert rejected.value.code == 400 and not session.calls
        code, start = post('/api/traces/evolve', payload)
        assert code == 202
        final = wait_job(controller, start['job_id'])
        assert final['status'] == 'completed', final
        identity = final['result']['compiled_trace_id']
        assert final['result']['representation'] == 'R'
        assert final['run_facts']['execution_backend'] == 'none'
        assert controller.run_store.read_round(start['job_id'], 0)['purpose'] == 'improve'
        path = f'trace-library/{identity}/model-input.txt'
        with pytest.raises(ValueError, match='尚未获准'):
            controller.start_job(task_path=path, instruction='fixture, must never execute',
                                 execute=True, execution_scope='desktop', use_experience=True)
        assert not dispatched
        _, view = post('/api/traces/model-input', {'id': identity})
        _, reviewed = post('/api/traces/evolution/review', {'id': identity,
            'candidate_sha256': view['review']['candidate_sha256'], 'decision': 'approved_for_trial'})
        assert reviewed['decision'] == 'approved_for_trial'
        assert controller.active_job()['job_id'] == start['job_id']
        assert controller.active_job()['status'] == 'completed' and not dispatched
        code, duplicate = post('/api/traces/evolve', payload)
        assert code == 200 and duplicate['confirmation_required'] and len(session.calls) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize('backend', ['chat', 'local'])
def test_actual_audit_records_the_experience_supplied_to_predictor(tmp_path, backend):
    from trace2task.chat_run_audit import ChatRunAudit
    from trace2task.local_run_audit import LocalRunAudit

    _, run, entry = seed_run(tmp_path)
    context = execution_context(tmp_path / run['task_path'])
    folder = tmp_path / 'runs' / f'{backend}-audit'
    folder.mkdir()
    seen = []
    audit_cls = ChatRunAudit if backend == 'chat' else LocalRunAudit
    audit = audit_cls(folder, {}, lambda *args, **kwargs: None, lambda _: None)
    stop = SimpleNamespace(raise_if_requested=lambda: None)

    def predict(task, **kwargs):
        seen.append(kwargs['experience_context'])
        return {'status': 'predicted', 'raw_output': 'test, not executed',
                'input_messages': [], 'prediction': None}

    audit.predict(predict, stop, 'qwen3-vl-2b', 'fixture', entry['screenshot'], [], 0,
                  experience_context=context)
    assert seen == [context]
    assert audit.result['model_io'][0]['experience_sha256'] == context['sha256']
