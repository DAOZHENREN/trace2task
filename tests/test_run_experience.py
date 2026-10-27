import json
import threading
from copy import deepcopy
from urllib.request import ProxyHandler, Request, build_opener

import pytest
from test_web_console import _csrf_headers

from trace2task.codex_app_server import DEFAULT_CODEX_MODEL
from trace2task.model_text import model_json
from trace2task.run_experience import (
    compile_run_draft,
    edit_draft,
    encoded,
    prepare_draft_revision,
    prepare_run_experience,
    preview_run_experience,
    read_draft,
    save_draft,
    validate_experience_scope,
    with_compilation_guidance,
)
from trace2task.run_session import RunStore, compact_snapshot
from trace2task.trace_combined import generate_c
from trace2task.trace_library import execution_context, list_traces, read_model_input
from trace2task.trace_procedure import ProcedureCompileCancelled
from trace2task.web_console import WebConsoleController, create_web_server

METHOD = '# 精灵对战方法\n根据可见对战状态完成本次战斗。\n## 适用条件\n同一应用的对战画面。\n## 步骤\n1. 等待可交互状态。\n## 完成判断\n核对结算画面。\n## 注意与局限\n模型视觉复核不是独立成功验证。\n'
OPTIONS = {'model': DEFAULT_CODEX_MODEL, 'reasoning_effort': 'low'}


class Session:
    def __init__(self, output=METHOD, effect=None):
        self.output, self.effect, self.calls, self.closed = output, effect, [], False

    def factory(self, *args, **kwargs):
        self.options = kwargs
        return self

    def run_turn(self, **kwargs):
        self.calls.append(kwargs)
        if self.effect:
            self.effect()
        return self.output

    def close(self):
        self.closed = True


def seed(root, count=1):
    store = RunStore(root)
    image = root / 'runs/source/observation.png'
    image.parent.mkdir(parents=True)
    image.write_bytes(b'original-image')
    run = {'job_id': 'source-run', 'task_id': 'Android', 'instruction': '完成精灵对战',
           'kind': 'agent', 'mode': 'execute', 'status': 'completed', 'model': 'source-model',
           'created_at': '2026-10-22T00:00:00Z', 'updated_at': '2026-10-22T00:01:00Z',
           'use_experience': False, 'run_facts': {'operation_scope': 'android'},
           'result': {'stop_reason': 'visual_completion_reviewed'}}
    store.save(run)
    for i in range(count):
        store.round('source-run', {'step_index': i, 'raw_output': '等待画面加载', 'screenshot': str(image),
            'input': {'secret': 'PRIVATE_API_REQUEST'}, 'execution': {'executed': True, 'status': 'delivered',
                'target': {'package': 'com.example.game', 'serial': 'PRIVATE_DEVICE_ADDRESS'},
                'action': {'skill': 'wait', 'args': {'duration_ms': 100}}}})
    return store, run, image


def compile_draft(root, store, session=None, **kwargs):
    session = session or Session()
    return compile_run_draft(root, prepare_run_experience(root, store, 'source-run'), **OPTIONS,
        session_factory=session.factory, binary_resolver=lambda _: 'fake-codex', **kwargs)


def test_preview_is_local_bounded_and_does_not_leak_wire_requests(tmp_path):
    store, _, _ = seed(tmp_path, count=12)
    before = set(tmp_path.rglob('*'))
    result = preview_run_experience(tmp_path, store, 'source-run')
    assert set(tmp_path.rglob('*')) == before
    assert result['round_count'] == result['delivered_count'] == 12
    assert len(result['images']) == 1  # Same screenshot bytes are attached once.
    assert result['images'][0]['step_index'] == 0 and len(result['images'][0]['source_ids']) == 8
    assert '非独立验证' in result['outcome']
    assert result['drafts'] == []
    assert 'PRIVATE_API_REQUEST' not in result['input']
    assert 'PRIVATE_DEVICE_ADDRESS' not in result['input']


def test_long_preview_deduplicates_actions_without_dropping_rounds_or_original_text(tmp_path):
    store, _, _ = seed(tmp_path)
    original = store.read_round('source-run', 0)
    text = '第一行  保留两个空格\n第二行\t保留制表符与换行'
    action = original['execution']['action']
    for i in range(260):
        store.round('source-run', {**original, 'step_index': i, 'raw_output': f'{i}: {text}',
            'prediction': {'protocol_version': '1', 'coordinate_space': 'observation_normalized_0_1',
                           'actions': [action]},
            'execution': {**original['execution'], 'steps': [
                {'action': action, 'receipt': {'effect': 'unverifiable', 'status': 'delivered'}}]}})
    before = store.closed_evidence('source-run')
    prepared = prepare_run_experience(tmp_path, store, 'source-run')
    payload = json.loads(prepared['input'])
    assert len(encoded(payload)) > len(prepared['input'].encode('utf-8'))
    assert prepared['input'] == model_json(payload, sort_keys=True)
    assert len(payload['rounds']) == prepared['summary']['round_count'] == 260
    for i, entry in enumerate(payload['rounds']):
        assert entry.startswith(f'{i + 1}｜')
        assert f'当时回答：{i}: {text}' in entry
        assert '计划：等待' in entry and '已送达' in entry
        assert 'duration_ms' not in entry
    audit = json.loads(prepared['files']['evidence/run.json'])
    assert audit['rounds'][0]['execution']['action'] == original['execution']['action']
    assert 'source_snapshot_sha256' in audit and 'source_snapshot_sha256' not in payload
    assert prepared['source_sha256'] == prepare_run_experience(tmp_path, store, 'source-run')['source_sha256']
    assert before == store.closed_evidence('source-run')


def test_audit_only_source_change_invalidates_confirmation_without_entering_model_text(tmp_path):
    store, _, _ = seed(tmp_path)
    first = prepare_run_experience(tmp_path, store, 'source-run')
    entry = store.read_round('source-run', 0)
    store.round('source-run', {**entry, 'request_id': 'changed-audit-only-id'})
    second = prepare_run_experience(tmp_path, store, 'source-run')
    assert first['input'] == second['input']
    assert first['source_sha256'] != second['source_sha256']
    controller = WebConsoleController(tmp_path)
    with pytest.raises(ValueError, match='已变化'):
        controller.start_run_experience('source-run', source_sha256=first['source_sha256'], **OPTIONS)
    assert not (tmp_path / 'runs/experience-drafts').exists()


def test_small_and_large_preview_use_the_same_compact_layout_without_a_text_cap(tmp_path):
    store, _, _ = seed(tmp_path)
    prepared = prepare_run_experience(tmp_path, store, 'source-run')
    assert prepared['input'] == model_json(json.loads(prepared['input']), sort_keys=True)
    entry = store.read_round('source-run', 0)
    store.round('source-run', {**entry, 'raw_output': '证据不能截断' * 10000})
    preview = preview_run_experience(tmp_path, store, 'source-run')
    assert len(preview['input'].encode('utf-8')) > 160_000
    assert '证据不能截断' * 10000 in json.loads(preview['input'])['rounds'][0]
    assert not (tmp_path / 'runs/experience-drafts').exists()


def test_actual_context_error_preserves_large_input_and_never_publishes_a_draft(tmp_path):
    store, _, _ = seed(tmp_path)
    entry = store.read_round('source-run', 0)
    store.round('source-run', {**entry, 'raw_output': '完整证据' * 15000})
    before = store.closed_evidence('source-run')

    def reject_context():
        raise RuntimeError('context_length_exceeded: actual model capacity')

    session = Session(effect=reject_context)
    with pytest.raises(RuntimeError, match='context_length_exceeded'):
        compile_draft(tmp_path, store, session=session)
    assert len(session.calls) == 1 and session.closed
    sent = session.calls[0]['prompt']
    assert len(sent.encode('utf-8')) > 160_000
    assert '完整证据' * 15000 in json.loads(sent)['rounds'][0]
    assert list((tmp_path / 'runs/experience-drafts').glob('*/draft.json')) == []
    assert before == store.closed_evidence('source-run')


def test_draft_then_digest_bound_save_preserves_source_and_never_auto_activates(tmp_path):
    store, _, image = seed(tmp_path)
    original = store.closed_evidence('source-run')
    session, rounds = Session(), []
    result = compile_draft(tmp_path, store, session=session, on_round=lambda r: rounds.append(deepcopy(r)))
    identifier = result['experience_draft_id']
    assert list_traces(tmp_path) == []
    draft = read_draft(tmp_path, identifier)
    assert draft['content'] == METHOD and draft['saved_trace_id'] is None
    assert len(session.calls) == 1 and session.closed
    assert session.options['thread_config']['features']['shell_tool'] is False
    assert all(r['executed'] is False for r in rounds)
    assert rounds[-1]['status'] == 'compiled'
    with pytest.raises(ValueError, match='明确确认'):
        save_draft(tmp_path, identifier, draft_sha256=draft['draft_sha256'])
    with pytest.raises(ValueError, match='草稿已变化'):
        save_draft(tmp_path, identifier, draft_sha256='bad', confirmed=True)
    receipt = save_draft(tmp_path, identifier, draft_sha256=draft['draft_sha256'], confirmed=True)
    assert receipt == {'id': identifier, 'already_saved': False,
                       'versioning': {'mode': 'independent', 'parent_id': None}}
    assert save_draft(tmp_path, identifier, draft_sha256=draft['draft_sha256'], confirmed=True)['already_saved']
    assert len(list_traces(tmp_path)) == 1
    assert read_draft(tmp_path, identifier)['saved_trace_id'] == identifier
    context = execution_context(tmp_path / 'trace-library' / identifier / 'model-input.txt')
    assert context['model_input'] == METHOD and 'images' not in context
    assert read_model_input(tmp_path, identifier)['compiler']['images']
    assert original == store.closed_evidence('source-run') and image.read_bytes() == b'original-image'
    assert preview_run_experience(tmp_path, store, 'source-run')['drafts'][0]['draft_id'] == identifier
    with pytest.raises(ValueError, match='不含人工示范'):
        generate_c(tmp_path, identifier)


@pytest.mark.parametrize('change,match', [({'status': 'running'}, '先结束'),
    ({'mode': 'plan'}, '已结束'), ({'kind': 'compilation'}, '已结束')])
def test_rejects_non_execution_sources(tmp_path, change, match):
    store, run, _ = seed(tmp_path)
    store.save({**run, **change})
    with pytest.raises(ValueError, match=match):
        prepare_run_experience(tmp_path, store, 'source-run')


def test_no_delivery_external_screenshot_and_trashed_source_are_rejected(tmp_path):
    store, _, image = seed(tmp_path)
    r = store.read_round('source-run', 0)
    store.round('source-run', {**r, 'execution': {'executed': False}})
    with pytest.raises(ValueError, match='已交付'):
        prepare_run_experience(tmp_path, store, 'source-run')
    store.round('source-run', {**r, 'screenshot': str(tmp_path.parent / 'external.png')})
    with pytest.raises(ValueError, match='外部'):
        prepare_run_experience(tmp_path, store, 'source-run')
    store.round('source-run', r)
    image.unlink()
    assert prepare_run_experience(tmp_path, store, 'source-run')['images'] == []
    store.trash_action('source-run', 'move')
    with pytest.raises(ValueError, match='回收站'):
        prepare_run_experience(tmp_path, store, 'source-run')


@pytest.mark.parametrize('cancel', [False, True])
def test_malformed_or_cancelled_output_never_publishes_draft(tmp_path, cancel):
    store, _, _ = seed(tmp_path)
    stop = threading.Event()
    session = Session(METHOD if cancel else 'not a method', stop.set if cancel else None)
    with pytest.raises(ProcedureCompileCancelled if cancel else ValueError):
        compile_draft(tmp_path, store, session=session, stop_event=stop)
    assert list_traces(tmp_path) == []
    assert list((tmp_path / 'runs/experience-drafts').glob('*/draft.json')) == []
    assert session.closed


def test_tampered_draft_rejects_save_and_compact_snapshot_keeps_draft_link(tmp_path):
    store, _, _ = seed(tmp_path)
    result = compile_draft(tmp_path, store)
    identifier = result['experience_draft_id']
    draft = read_draft(tmp_path, identifier)
    assert compact_snapshot({'result': result})['result']['experience_draft_id'] == identifier
    (tmp_path / 'runs/experience-drafts' / identifier / 'model-input.txt').write_text('tampered')
    with pytest.raises(ValueError, match='证据已变化'):
        save_draft(tmp_path, identifier, draft_sha256=draft['draft_sha256'], confirmed=True)
    assert list_traces(tmp_path) == []
    with pytest.raises(ValueError, match='标识'):
        read_draft(tmp_path, '../not-a-draft')


def test_controller_duplicate_and_changed_evidence_gates(tmp_path, monkeypatch):
    store, _, image = seed(tmp_path)
    controller = WebConsoleController(tmp_path)
    prepared = prepare_run_experience(tmp_path, store, 'source-run')
    with pytest.raises(ValueError, match='已变化'):
        controller.start_run_experience('source-run', source_sha256='bad', **OPTIONS)
    compile_draft(tmp_path, store)
    reply = controller.start_run_experience('source-run', source_sha256=prepared['source_sha256'], **OPTIONS)
    assert reply['confirmation_required'] is True
    image.write_bytes(b'changed-image')
    with pytest.raises(ValueError, match='已变化'):
        controller.start_run_experience('source-run', source_sha256=prepared['source_sha256'], **OPTIONS)
    image.write_bytes(b'original-image')
    calls = []
    monkeypatch.setattr(controller, '_run_experience_compilation', lambda *args: calls.append(args))
    job = controller.start_run_experience('source-run', source_sha256=prepared['source_sha256'],
                                           confirm_duplicate=True, **OPTIONS)
    assert job['kind'] == 'experience_compilation'
    assert job['run_facts']['execution_backend'] == 'none'


def test_android_method_only_reused_in_matching_app(tmp_path, monkeypatch):
    store, _, _ = seed(tmp_path)
    identifier = compile_draft(tmp_path, store)['experience_draft_id']
    save_draft(tmp_path, identifier, draft_sha256=read_draft(tmp_path, identifier)['draft_sha256'], confirmed=True)
    path = tmp_path / 'trace-library' / identifier / 'model-input.txt'
    validate_experience_scope(tmp_path, path, 'android', 'com.example.game')
    with pytest.raises(ValueError, match='同一授权应用'):
        validate_experience_scope(tmp_path, path, 'android', 'com.other.app')
    with pytest.raises(ValueError, match='来源范围'):
        validate_experience_scope(tmp_path, path, 'desktop')
    adb = tmp_path / 'adb.exe'
    adb.touch()
    monkeypatch.setattr('trace2task.android_workbench.prepare', lambda value: {})
    controller = WebConsoleController(tmp_path)
    monkeypatch.setattr(controller, '_run_job', lambda *args: None)
    job = controller.start_job(task_path=str(path), instruction='test', execute=True, use_experience=True,
        executor_backend='android', execution_scope='android',
        android_config={'adb': str(adb), 'serial': '127.0.0.1:12345', 'package': 'com.example.game', 'server_port': 5038})
    assert job['use_experience'] is True
    assert job['run_facts']['experience_id'] == identifier


def test_guidance_is_separate_from_frozen_run_evidence_and_marked_on_save(tmp_path):
    store, _, _ = seed(tmp_path)
    source = prepare_run_experience(tmp_path, store, 'source-run')
    prepared = with_compilation_guidance(source, '只保留关键判断，不扩写未知机制。')
    assert prepared['source_sha256'] == source['source_sha256']
    assert prepared['files'] == source['files']
    session = Session()
    result = compile_run_draft(tmp_path, prepared, **OPTIONS,
        session_factory=session.factory, binary_resolver=lambda _: 'fake-codex')
    assert session.options['wait_on_rate_limit'] is True
    assert json.loads(session.calls[0]['prompt'])['compilation_guidance'] == '只保留关键判断，不扩写未知机制。'
    draft = read_draft(tmp_path, result['experience_draft_id'])
    saved = save_draft(tmp_path, draft['draft_id'], draft_sha256=draft['draft_sha256'], confirmed=True)
    body = json.loads((tmp_path / 'trace-library' / saved['id'] / 'body.json').read_bytes())
    assert body['condition'] == 'trace-compile+human-revision'
    assert body['human_confirmation_only'] is False
    assert body['authoring']['guidance'] == prepared['authoring']['guidance']


def test_direct_edit_preserves_original_model_and_requires_review_of_new_digest(tmp_path):
    store, _, _ = seed(tmp_path)
    identifier = compile_draft(tmp_path, store)['experience_draft_id']
    original = read_draft(tmp_path, identifier)
    directory = tmp_path / 'runs/experience-drafts' / identifier
    before = {p.relative_to(directory): p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    content = METHOD.replace('精灵对战方法', '人工修改的对战方法')
    revised = edit_draft(tmp_path, identifier, draft_sha256=original['draft_sha256'], content=content)
    assert revised['draft_id'] != identifier and revised['content'] == content
    assert revised['authoring']['kind'] == 'edit'
    assert revised['saved_trace_id'] is None and not list_traces(tmp_path)
    assert before == {p.relative_to(directory): p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    updated_dir = tmp_path / 'runs/experience-drafts' / revised['draft_id']
    assert (updated_dir / 'model-output.md').read_text(encoding='utf-8') == METHOD
    assert (updated_dir / 'previous-draft.md').read_text(encoding='utf-8') == METHOD
    with pytest.raises(ValueError, match='明确确认'):
        save_draft(tmp_path, revised['draft_id'], draft_sha256=revised['draft_sha256'])
    with pytest.raises(ValueError, match='已变化'):
        save_draft(tmp_path, revised['draft_id'], draft_sha256=original['draft_sha256'], confirmed=True)
    saved = save_draft(tmp_path, revised['draft_id'], draft_sha256=revised['draft_sha256'], confirmed=True)
    assert saved['id'] == revised['draft_id']
    assert list_traces(tmp_path)[0]['condition'] == 'trace-compile+human-revision'


@pytest.mark.parametrize('content,match', [('', 'Markdown'),
    (METHOD + '[第 999 轮]', '来源引用'),
    (METHOD + '沿用旧方法。', '独立可用')])
def test_invalid_edits_do_not_create_or_publish_drafts(tmp_path, content, match):
    store, _, _ = seed(tmp_path)
    original = read_draft(tmp_path, compile_draft(tmp_path, store)['experience_draft_id'])
    with pytest.raises(ValueError, match=match):
        edit_draft(tmp_path, original['draft_id'], draft_sha256=original['draft_sha256'], content=content)
    assert len(list((tmp_path / 'runs/experience-drafts').glob('*/draft.json'))) == 1
    assert list_traces(tmp_path) == []


def test_feedback_uses_reviewed_draft_and_frozen_evidence_even_if_source_changes(tmp_path):
    store, _, image = seed(tmp_path)
    original = read_draft(tmp_path, compile_draft(tmp_path, store)['experience_draft_id'])
    image.write_bytes(b'new unrelated source image')
    prepared = prepare_draft_revision(tmp_path, original['draft_id'],
        draft_sha256=original['draft_sha256'], guidance='重点解释结算条件。')
    payload = json.loads(prepared['input'])
    assert payload['draft_revision'] == {'previous_draft': METHOD, 'guidance': '重点解释结算条件。'}
    assert any(data == b'original-image' for data in prepared['files'].values())
    session = Session(METHOD.replace('核对结算画面。', '核对结算画面中的可见状态。'))
    result = compile_run_draft(tmp_path, prepared, **OPTIONS,
        session_factory=session.factory, binary_resolver=lambda _: 'fake-codex')
    revised = read_draft(tmp_path, result['experience_draft_id'])
    assert revised['authoring']['kind'] == 'feedback'
    assert revised['authoring']['base_draft_id'] == original['draft_id']
    assert read_draft(tmp_path, original['draft_id'])['content'] == METHOD
    with pytest.raises(ValueError, match='已变化'):
        prepare_draft_revision(tmp_path, original['draft_id'], draft_sha256='stale', guidance='test')
    with pytest.raises(ValueError, match='希望怎样修改'):
        prepare_draft_revision(tmp_path, original['draft_id'], draft_sha256=original['draft_sha256'], guidance=' ')


def test_controller_revision_routes_to_frozen_draft_without_duplicate_confirmation(tmp_path, monkeypatch):
    store, _, _ = seed(tmp_path)
    original = read_draft(tmp_path, compile_draft(tmp_path, store)['experience_draft_id'])
    controller = WebConsoleController(tmp_path)
    captured = []
    ready = threading.Event()
    def compile_job(job, prepared):
        captured.append(prepared)
        ready.set()
    monkeypatch.setattr(controller, '_run_experience_compilation', compile_job)
    job = controller.start_run_experience('', source_sha256='', draft_id=original['draft_id'],
        draft_sha256=original['draft_sha256'], guidance='精简重复等待。', **OPTIONS)
    assert ready.wait(2)
    assert job['kind'] == 'experience_compilation' and 'confirmation_required' not in job
    assert captured[0]['authoring']['kind'] == 'feedback'


def test_http_edit_and_feedback_revision_keep_new_versions_reviewable(tmp_path, monkeypatch):
    store, _, _ = seed(tmp_path)
    original = read_draft(tmp_path, compile_draft(tmp_path, store)['experience_draft_id'])
    controller = WebConsoleController(tmp_path)
    session = Session()
    monkeypatch.setattr('trace2task.run_experience.CodexAppServerSession', session.factory)
    monkeypatch.setattr('trace2task.run_experience.resolve_codex_binary', lambda _: 'fake-codex')
    server = create_web_server(tmp_path, port=0, controller=controller)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    opener = build_opener(ProxyHandler({}))
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        headers = {**_csrf_headers(opener, base), 'Content-Type': 'application/json'}
        def post(route, payload):
            with opener.open(Request(base + route, data=encoded(payload), headers=headers), timeout=5) as response:
                return json.loads(response.read())
        revised = post('/api/run-experience/edit', {'draft_id': original['draft_id'],
            'draft_sha256': original['draft_sha256'], 'content': METHOD.replace('对战方法', '对战经验')})
        assert revised['saved_trace_id'] is None and revised['draft_id'] != original['draft_id']
        job = post('/api/run-experience/generate', {'draft_id': revised['draft_id'],
            'draft_sha256': revised['draft_sha256'], 'guidance': '补充结果判断', **OPTIONS})
        finished = controller.wait(job['job_id'])
        assert finished['status'] == 'completed'
        new_draft = read_draft(tmp_path, finished['result']['experience_draft_id'])
        assert new_draft['authoring']['base_draft_id'] == revised['draft_id']
        assert json.loads(session.calls[0]['prompt'])['draft_revision']['previous_draft'] == revised['content']
        assert list_traces(tmp_path) == []
    finally:
        server.shutdown()
        server.server_close()
        worker.join(3)
