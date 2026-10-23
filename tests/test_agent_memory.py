"""Automatic experience uses fake models/drivers; never sends native input."""
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_chat_agent_bridge import Driver, Session, Stop, _reply
from test_run_experience import compile_draft, read_draft, save_draft, seed
from test_trace_evidence import recording
from test_trace_procedure import PROCEDURE, compile_fake

from trace2task.agent_memory import MemoryCatalog, _selection_scope, selection_prompt
from trace2task.chat_agent_runner import run_chat_agent
from trace2task.web_console import WebConsoleController


def published(root):
    trace, _ = recording(root)
    return compile_fake(root, trace)['id']


def request(tool, query='', memory_id='', reason='chosen for this task'):
    return json.dumps({'tool': tool, 'query': query, 'memory_id': memory_id, 'reason': reason})


def prompt_field(prompt, label):
    return json.loads(prompt.split('\n' + label + ': ', 1)[1].split('\n', 1)[0])


@pytest.mark.parametrize('scope,package,context,expected', [
    ('desktop', '', None, {'scope': 'desktop'}),
    ('android', 'com.example.game', {
        'target': {'kind': 'android', 'serial': 'PRIVATE_ADDRESS', 'server_port': 5038,
                   'transport_id': 7, 'boot_id': 'PRIVATE_BOOT', 'package': 'com.example.game'},
        'scope': 'selected Android foreground package, pixels only', 'width': 1280, 'height': 720,
        'capabilities': {'available_skills': ['click', 'joystick_move', 'navigate_to'],
                         'coordinate_space': 'android_screenshot', 'delivery_mode': 'remote_android',
                         'max_duration_ms': 1500, 'multi_touch': False, 'future_internal_field': 'PRIVATE'},
    }, {'scope': 'android', 'package': 'com.example.game',
        'boundary': 'selected Android foreground package, pixels only',
        'available_skills': ['click', 'joystick_move', 'navigate_to']}),
    ('selected_windows', '', {
        'current_window': {'pid': 10, 'window_id': 20},
        'windows': [{'pid': 10, 'window_id': 20, 'title': '两  个空格\n窗口', 'app_name': 'editor.exe'},
                    {'pid': 11, 'window_id': 21, 'title': '其他窗口', 'app_name': 'viewer.exe'}],
        'apps': [{'app_id': 8, 'name': '浏览器', 'executable': 'PRIVATE_PATH'}],
    }, {'scope': 'selected_windows',
        'windows': [{'title': '两  个空格\n窗口', 'app_name': 'editor.exe', 'current': True},
                    {'title': '其他窗口', 'app_name': 'viewer.exe'}], 'apps': ['浏览器']}),
    ('browser', '', {'target': {'kind': 'browser', 'session_id': 'PRIVATE_SESSION'},
                     'url': 'https://example.test/game', 'scope': 'isolated browser viewport',
                     'capabilities': {'available_skills': []}},
     {'scope': 'browser', 'url': 'https://example.test/game', 'boundary': 'isolated browser viewport',
      'available_skills': []}),
])
def test_selection_scope_keeps_applicability_without_executor_identity(scope, package, context, expected):
    before = deepcopy(context)
    catalog = SimpleNamespace(scope=scope, package=package)
    assert _selection_scope(catalog, context) == expected
    assert context == before


def test_selection_prompt_whitelists_fields_and_preserves_human_text():
    candidate = {'id': 'published-id', 'name': 'SHA-256 核对方法',
                 'description': '保留 sha256 术语、两  个空格\n和用户原文',
                 'outcome': '运行未完成 · 未独立验证', 'representation': 'B',
                 'source_kind': 'agent_run', 'scope': 'android', 'package': 'com.example.game',
                 'source_run_id': 'PRIVATE_RUN', 'created_at': 'PRIVATE_TIMESTAMP',
                 'lineage': {'parent_id': 'PRIVATE_PARENT', 'parent_sha256': 'a' * 64},
                 'condition': 'trace-compile+run-feedback', 'revision_changed': False,
                 'sha256': 'b' * 64, 'metadata_sha256': 'c' * 64, 'future_internal_field': 'PRIVATE'}
    operations = [{'tool': 'search_memory', 'query': '查询 sha256  两空格\n原文', 'memory_id': '',
                   'reason': 'PRIVATE_REASON', 'returned_ids': ['published-id']}]
    catalog = SimpleNamespace(scope='android', package='com.example.game', entries={'published-id': candidate})
    context = {'target': {'serial': 'PRIVATE_ADDRESS', 'boot_id': 'PRIVATE_BOOT'},
               'width': 1280, 'height': 720, 'capabilities': {'available_skills': ['click']}}
    before = deepcopy((candidate, operations, context))
    task = '任务原文：sha256 两  个空格\n下一行'
    prompt = selection_prompt(catalog=catalog, task=task, target_context=context,
                              results=[candidate], operations=operations, searches=1)
    assert '\nCurrent task: ' + task + '\nAuthorized scope: ' in prompt
    assert prompt_field(prompt, 'Search results (metadata only)') == [
        {key: candidate[key] for key in ('id', 'name', 'description', 'outcome')}]
    assert prompt_field(prompt, 'Previous searches') == [
        {'query': operations[0]['query'], 'returned_ids': ['published-id']}]
    assert 'Searches remaining: 1.' in prompt
    assert 'PRIVATE' not in prompt and all(digest * 64 not in prompt for digest in ('a', 'b', 'c'))
    assert (candidate, operations, context) == before
    # Audit-only changes must not alter model-visible evidence or weaken load checks.
    candidate['sha256'] = 'd' * 64
    candidate['lineage']['parent_id'] = 'ANOTHER_PARENT'
    context['target']['serial'] = 'ANOTHER_ADDRESS'
    assert selection_prompt(catalog=catalog, task=task, target_context=context,
                            results=[candidate], operations=operations, searches=1) == prompt


def test_first_selection_prompt_has_no_empty_search_history():
    prompt = selection_prompt(catalog=SimpleNamespace(scope='desktop', package='', entries={'id': {}}),
                              task='保存', target_context=None, results=[], operations=[], searches=0)
    assert 'Eligible methods: 1. Searches remaining: 2.' in prompt
    assert 'Search results (metadata only):' not in prompt
    assert 'Previous searches:' not in prompt and 'Previous requests:' not in prompt


def run(root, replies, **kwargs):
    session, driver = Session(replies), Driver(root)
    result = run_chat_agent(instruction='保存文档', model='fake', reasoning_effort='low',
        output_root=root / 'runs', emergency_stop=kwargs.pop('emergency_stop', Stop()), status_callback=lambda _: None,
        executor_backend='cua', cua_target=Driver.target, cua_driver=driver, session=session,
        memory_root=root, memory_mode='auto', **kwargs)
    return result, session, driver


def test_catalog_is_metadata_only_scope_filtered_and_newest_per_source(tmp_path):
    old = published(tmp_path)
    trace = next((tmp_path / 'runs').glob('*/events.jsonl'))
    newest = compile_fake(tmp_path, trace, confirm_duplicate=True)['id']
    catalog = MemoryCatalog(tmp_path, 'desktop')
    assert set(catalog.entries) == {newest}
    assert old not in catalog.entries
    assert PROCEDURE not in json.dumps(catalog.search('保存文档'), ensure_ascii=False)
    assert catalog.load(newest)['model_input'] == PROCEDURE
    assert not MemoryCatalog(tmp_path, 'browser').entries
    assert not MemoryCatalog(tmp_path, 'android', 'com.example.game').entries


def test_android_catalog_requires_same_application(tmp_path):
    store, _, _ = seed(tmp_path)
    identifier = compile_draft(tmp_path, store)['experience_draft_id']
    # Drafts are never searchable before explicit publication.
    assert not MemoryCatalog(tmp_path, 'android', 'com.example.game').entries
    save_draft(tmp_path, identifier, draft_sha256=read_draft(tmp_path, identifier)['draft_sha256'], confirmed=True)
    assert set(MemoryCatalog(tmp_path, 'android', 'com.example.game').entries) == {identifier}
    assert not MemoryCatalog(tmp_path, 'android', 'com.other.app').entries
    assert not MemoryCatalog(tmp_path, 'desktop').entries


@pytest.mark.parametrize('change', ['content', 'metadata', 'trash'])
def test_load_rechecks_selected_version(tmp_path, change):
    identifier = published(tmp_path)
    catalog = MemoryCatalog(tmp_path, 'selected_windows')
    directory = tmp_path / 'trace-library' / identifier
    if change == 'content':
        (directory / 'model-input.txt').write_text('changed', encoding='utf-8')
    elif change == 'metadata':
        path = directory / 'header.json'
        header = json.loads(path.read_text(encoding='utf-8'))
        header['trace_name'] = 'changed'
        path.write_text(json.dumps(header), encoding='utf-8')
    else:
        (directory / '.trace2task-deleted.json').write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError):
        catalog.load(identifier)


@pytest.mark.parametrize('provider', ['codex', 'api'])
def test_agent_searches_then_loads_and_reobserves_before_action(tmp_path, provider):
    identifier = published(tmp_path)
    result, session, driver = run(tmp_path, [request('search_memory', query='保存文档'),
        request('load_memory', memory_id=identifier),
        _reply([{'skill': 'click', 'args': {'x': .5, 'y': .5, 'button': 'left'}}])],
        max_actions=1, api_config=SimpleNamespace() if provider == 'api' else None)
    assert result['stop_reason'] == 'action_limit'
    assert result['actions'] == 1 and len(driver.calls) == 1
    assert result['use_experience'] is True
    assert result['memory_selection']['selected']['id'] == identifier
    assert result['memory_selection']['status'] == 'loaded'
    assert [r['purpose'] for r in result['model_io']] == ['memory', 'memory', 'plan']
    assert all('execution' not in r for r in result['model_io'][:2])
    assert PROCEDURE not in session.requests[0]['prompt']
    assert PROCEDURE not in session.requests[1]['prompt']
    assert PROCEDURE in session.requests[2]['prompt']
    assert session.requests[0]['image_path'] != session.requests[2]['image_path']
    assert driver.observations[:2] == [0, 2]
    root = Path(result['trace_path']).parent
    assert (root / 'memory/model-input.txt').read_text(encoding='utf-8') == PROCEDURE
    assert json.loads((root / 'memory/selection.json').read_text(encoding='utf-8')) == result['memory_selection']
    catalog = json.loads((root / 'memory/catalog.json').read_text(encoding='utf-8'))
    selected = result['memory_selection']['selected']
    assert catalog['entries'] == [selected] and selected['sha256'] and selected['metadata_sha256']
    assert catalog['target_context']['current_window'] == Driver.target
    prompt = session.requests[1]['prompt']
    assert prompt_field(prompt, 'Search results (metadata only)') == [
        {key: selected[key] for key in ('id', 'name', 'description', 'outcome')}]
    assert prompt_field(prompt, 'Authorized scope')['windows'] == [
        {'title': 'Temporary Test', 'app_name': 'fake.exe', 'current': True}]
    assert selected['sha256'] not in prompt and 'window_id' not in prompt and '"pid"' not in prompt
    assert result['model_io'][1]['prompt'] == prompt
    assert result['memory_selection']['operations'][0]['reason'] == 'chosen for this task'


def test_second_search_keeps_first_result_loadable_without_repeating_audit(tmp_path, monkeypatch):
    identifier = published(tmp_path)
    original_search = MemoryCatalog.search
    monkeypatch.setattr(MemoryCatalog, 'search',
                        lambda self, query: original_search(self, query) if query == '保存' else [])
    result, session, driver = run(tmp_path, [request('search_memory', query='保存'),
        request('search_memory', query='第二次查询'), request('load_memory', memory_id=identifier),
        _reply([{'skill': 'click', 'args': {'x': .5, 'y': .5, 'button': 'left'}}])], max_actions=1)
    assert result['memory_selection']['status'] == 'loaded' and len(driver.calls) == 1
    prompt = session.requests[2]['prompt']
    assert 'Searches remaining: 0.' in prompt
    assert prompt_field(prompt, 'Search results (metadata only)') == []
    assert prompt_field(prompt, 'Previous searches') == [
        {'query': '保存', 'returned_ids': [identifier]}, {'query': '第二次查询', 'returned_ids': []}]
    assert '"memory_id":""' not in prompt and 'chosen for this task' not in prompt


@pytest.mark.parametrize('empty', [True, False])
def test_no_candidates_or_explicit_skip_runs_without_memory(tmp_path, empty):
    if not empty:
        published(tmp_path)
    replies = ([] if empty else [request('skip_memory', reason='不适用')]) + [_reply(complete=True),
        json.dumps({'verdict': 'complete', 'evidence': 'visible', 'missing': ''})]
    result, session, driver = run(tmp_path, replies)
    assert result['memory_selection']['status'] == 'skipped'
    assert result['use_experience'] is False and not driver.calls
    assert len(session.requests) == (2 if empty else 3)
    assert result['stop_reason'] == 'visual_completion_reviewed'


@pytest.mark.parametrize('replies', [
    [request('load_memory', memory_id='../../secret')],
    ['{"tool":"shell","command":"write"}'],
    [request('search_memory', query='test')] * 3,
])
def test_invalid_tool_or_search_budget_never_dispatches_input(tmp_path, replies):
    published(tmp_path)
    result, _, driver = run(tmp_path, replies)
    assert result['stop_reason'] == 'error' and result['actions'] == 0
    assert result['memory_selection']['status'] == 'error'
    assert not driver.calls


def test_stop_after_search_never_loads_or_dispatches(tmp_path):
    from trace2task.windows_runner import EmergencyStopRequested

    published(tmp_path)

    class Cancel(Stop):
        cancelled = False

        def raise_if_requested(self):
            if self.cancelled:
                raise EmergencyStopRequested()

    stop = Cancel()

    def cancel_after_search(selection):
        if selection['operations']:
            stop.cancelled = True

    result, session, driver = run(tmp_path, [request('search_memory', query='保存')],
        emergency_stop=stop, on_memory_selection=cancel_after_search)
    assert result['stop_reason'] == 'emergency_stop'
    assert result['actions'] == 0 and not driver.calls
    assert len(session.requests) == 1


def test_controller_freezes_auto_policy_and_persists_actual_choice(tmp_path, monkeypatch):
    controller = WebConsoleController(tmp_path, rsi_client=object())
    calls = []
    monkeypatch.setattr(controller, '_run_job', lambda *args: calls.append(args))
    reply = controller.start_job(task_path='', instruction='test', execute=True,
        execution_scope='desktop', memory_mode='auto')
    assert reply['memory_mode'] == reply['run_facts']['memory_mode'] == 'auto'
    assert reply['use_experience'] is False
    assert reply['selection_mode'] == 'agent_memory'
    assert reply['run_facts']['experience_id'] == ''
    choice = {'mode': 'auto', 'status': 'loaded', 'selected': {'id': 'chosen', 'sha256': 'digest'}}

    def runner(**kwargs):
        assert kwargs['memory_mode'] == 'auto' and kwargs['memory_root'] == tmp_path
        assert kwargs['task_path'] is None and kwargs['use_experience'] is False
        kwargs['on_memory_selection'](choice)
        return {'stop_reason': 'visual_completion_reviewed', 'use_experience': True, 'memory_selection': choice,
                'experience_task_path': 'trace-library/chosen/model-input.txt', 'actions': 1}

    monkeypatch.setattr('trace2task.chat_agent_runner.run_chat_agent', runner)
    job = controller._jobs[reply['job_id']]
    WebConsoleController._run_job(controller, job, None, True)
    assert job.result['use_experience'] is True
    saved = controller.run_store.get(job.job_id)['session']
    assert saved['memory_selection'] == choice
    assert saved['run_facts']['memory_mode'] == 'auto'
    assert saved['run_facts']['experience_id'] == ''  # Start policy is not rewritten as a manual choice.


@pytest.mark.parametrize('options', [
    {'provider': 'trained_d'}, {'mobileforge': 'not-allowed'}, {'task_path': 'manual-path'},
    {'use_experience': True}, {'execute': False}, {'orchestration': 'langgraph'},
])
def test_controller_rejects_unsupported_or_ambiguous_auto_mode(tmp_path, options):
    controller = WebConsoleController(tmp_path, rsi_client=object())
    args = {'task_path': '', 'instruction': 'test', 'execute': True, 'execution_scope': 'desktop', 'memory_mode': 'auto'}
    with pytest.raises(ValueError, match='经验'):
        controller.start_job(**{**args, **options})
