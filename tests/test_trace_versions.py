"""Version history is persistent selection metadata; published evidence stays intact."""
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest
from test_run_experience import OPTIONS, compile_draft, seed
from test_run_experience import Session as RunSession
from test_run_feedback import REVISED, feedback_run, publish
from test_trace_evidence import recording
from test_trace_procedure import PROCEDURE, Session, compile_fake
from test_web_console import _csrf_headers

from trace2task.agent_memory import MemoryCatalog
from trace2task.run_experience import (
    compile_run_draft,
    prepare_run_experience,
    read_draft,
    save_draft,
)
from trace2task.trace_library import generate_a, generate_d, list_traces, read_model_input
from trace2task.trace_versions import current_version_ids, list_versions, rollback_version
from trace2task.web_console import WebConsoleController, create_web_server


def pair(root):
    trace, _ = recording(root)
    old = compile_fake(root, trace)['id']
    new = compile_fake(root, trace, Session(PROCEDURE + '\n再次核对保存结果。'), confirm_duplicate=True)['id']
    return trace, old, new


def rollback_request(root, identifier):
    version = next(v for v in list_versions(root) if v['id'] == identifier)
    content = read_model_input(root, identifier)['content']
    return {'expected_revision': version['version_revision'],
            'model_input_sha256': hashlib.sha256(content.encode('utf-8')).hexdigest()}


def artifacts(root):
    return {p.relative_to(root): p.read_bytes() for p in (root / 'trace-library').glob('*/*')
            if p.is_file()}


def test_migration_rollback_persists_and_only_current_enters_auto_selection(tmp_path):
    trace, old, new = pair(tmp_path)
    before = artifacts(tmp_path)
    entries = list_versions(tmp_path)
    assert len(entries) == 2 and {e['family_id'] for e in entries} == {old}
    assert current_version_ids(tmp_path) == {new}
    assert all(e['version_count'] == 2 for e in entries)
    catalog = MemoryCatalog(tmp_path, 'desktop')
    assert set(catalog.entries) == {new}
    request = rollback_request(tmp_path, old)
    rollback_version(tmp_path, old, **request)
    assert current_version_ids(tmp_path) == {old}
    assert catalog.search('保存') == []
    with pytest.raises(ValueError, match='current version'):
        catalog.load(new)
    reopened = MemoryCatalog(tmp_path, 'desktop')
    assert set(reopened.entries) == {old} and reopened.load(old)['model_input'] == PROCEDURE
    assert len(list_traces(tmp_path)) == 2 and artifacts(tmp_path) == before
    with pytest.raises(ValueError, match='已变化'):
        rollback_version(tmp_path, new, **request)
    # A new completed publication supersedes a rollback, without removing either version.
    newest = compile_fake(tmp_path, trace, confirm_duplicate=True)['id']
    assert current_version_ids(tmp_path) == {newest}
    assert len(list_versions(tmp_path)) == 3


def test_draft_does_not_promote_and_old_run_feedback_keeps_its_actual_parent(tmp_path):
    store, source, _ = seed(tmp_path)
    parent = publish(tmp_path, compile_draft(tmp_path, store))
    run, _ = feedback_run(tmp_path, store, source, parent_id=parent)
    prepared = prepare_run_experience(tmp_path, store, run['job_id'])
    result = compile_run_draft(tmp_path, prepared, **OPTIONS,
        session_factory=RunSession(REVISED).factory, binary_resolver=lambda _: 'fake')
    assert current_version_ids(tmp_path) == {parent}
    child = publish(tmp_path, result)
    assert current_version_ids(tmp_path) == {child}
    assert len({v['family_id'] for v in list_versions(tmp_path)}) == 1
    # Inspecting or compiling an old run still reads its old, archived parent.
    again = prepare_run_experience(tmp_path, store, run['job_id'])
    assert again['lineage']['parent_id'] == parent
    assert json.loads(again['input'])['parent_procedure'] == read_model_input(tmp_path, parent)['content']
    rollback_version(tmp_path, parent, **rollback_request(tmp_path, parent))
    assert current_version_ids(tmp_path) == {parent}
    # Repeating an already completed save must not undo the user's rollback.
    draft = read_draft(tmp_path, child)
    assert save_draft(tmp_path, child, draft_sha256=draft['draft_sha256'], confirmed=True)['already_saved']
    assert current_version_ids(tmp_path) == {parent}


def test_representations_and_same_named_unrelated_sources_remain_separate(tmp_path):
    trace, old, new = pair(tmp_path)
    a = generate_a(tmp_path, trace)['id']
    d = generate_d(tmp_path, trace)['id']
    other = tmp_path / 'trace-library' / 'unrelated'
    other.mkdir()
    (other / 'header.json').write_text(json.dumps({'representation': 'B', 'trace_name': '原始示范',
        'description': 'different source', 'source_recording': 'runs/other',
        'created_at': '2026-10-27T00:00:00+00:00'}), encoding='utf-8')
    assert current_version_ids(tmp_path) == {new, a, d, 'unrelated'}
    assert next(v for v in list_versions(tmp_path) if v['id'] == old)['current_version_id'] == new


def test_family_graph_joins_source_siblings_and_descendants_but_not_other_scope(tmp_path):
    def header(identifier, **extra):
        directory = tmp_path / 'trace-library' / identifier
        directory.mkdir(parents=True)
        (directory / 'header.json').write_text(json.dumps({'representation': 'B', 'trace_name': 'same',
            'description': 'same', 'created_at': '2026-10-27T00:00:00+00:00', **extra}), encoding='utf-8')
    header('first', source_run_id='run', source_scope='android', source_package='one')
    header('sibling', source_run_id='run', source_scope='android', source_package='one')
    list_versions(tmp_path)
    header('child', source_run_id='feedback', source_scope='android', source_package='one',
           lineage={'parent_id': 'first', 'root_id': 'first'})
    header('another-app', source_run_id='run', source_scope='android', source_package='two')
    header('browser', source_run_id='run', source_scope='browser')
    assert current_version_ids(tmp_path) == {'child', 'another-app', 'browser'}
    family = {e['family_id'] for e in list_versions(tmp_path) if e['id'] in {'first', 'sibling', 'child'}}
    assert len(family) == 1


@pytest.mark.parametrize('damage', ['model-input.txt', 'header.json'])
def test_corrupt_current_never_falls_back_to_history(tmp_path, damage):
    _, old, new = pair(tmp_path)
    list_versions(tmp_path)
    (tmp_path / 'trace-library' / new / damage).write_text('corrupted', encoding='utf-8')
    catalog = MemoryCatalog(tmp_path, 'desktop')
    assert old not in catalog.entries
    if new in catalog.entries:
        with pytest.raises(ValueError):
            catalog.load(new)
    else:
        assert not catalog.entries
    assert not next(e for e in list_versions(tmp_path) if e['id'] == old)['is_current']


def test_deleted_current_does_not_resurrect_history_and_explicit_rollback_can(tmp_path):
    _, old, new = pair(tmp_path)
    controller = WebConsoleController(tmp_path)
    controller.delete_trace_experience(new)  # Also initializes selection before a first listing.
    assert not current_version_ids(tmp_path)
    assert not MemoryCatalog(tmp_path, 'desktop').entries
    rollback_version(tmp_path, old, **rollback_request(tmp_path, old))
    assert current_version_ids(tmp_path) == {old}


def test_restoring_a_pre_index_archive_does_not_promote_it(tmp_path):
    _, old, new = pair(tmp_path)
    controller = WebConsoleController(tmp_path)
    archived = controller.trash.move(tmp_path / 'trace-library' / old, 'trace_experience')
    assert current_version_ids(tmp_path) == {new}
    controller.restore_trashed_asset(archived['trash_path'])
    assert current_version_ids(tmp_path) == {new}
    assert len(list_versions(tmp_path)) == 2
    rollback_version(tmp_path, old, **rollback_request(tmp_path, old))
    assert current_version_ids(tmp_path) == {old}


def test_concurrent_rollback_rejects_stale_confirmation_and_tampered_content(tmp_path):
    trace, old, new = pair(tmp_path)
    newest = compile_fake(tmp_path, trace, confirm_duplicate=True)['id']
    requests = {identifier: rollback_request(tmp_path, identifier) for identifier in (old, new)}
    bad = {**requests[old], 'model_input_sha256': '0' * 64}
    with pytest.raises(ValueError, match='内容已变化'):
        rollback_version(tmp_path, old, **bad)
    assert current_version_ids(tmp_path) == {newest}
    def attempt(identifier):
        try:
            rollback_version(tmp_path, identifier, **requests[identifier])
            return 'changed'
        except ValueError:
            return 'stale'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, (old, new))) == ['changed', 'stale']


def test_bad_index_is_not_silently_reset(tmp_path):
    pair(tmp_path)
    list_versions(tmp_path)
    index = tmp_path / 'trace-library/.versions.json'
    index.write_text('{broken', encoding='utf-8')
    with pytest.raises(ValueError, match='版本记录损坏'):
        MemoryCatalog(tmp_path, 'desktop')
    assert index.read_text(encoding='utf-8') == '{broken'


def test_http_state_rollback_and_busy_guard(tmp_path, monkeypatch):
    _, old, new = pair(tmp_path)
    controller = WebConsoleController(tmp_path)
    server = create_web_server(tmp_path, port=0, controller=controller)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_address[1]}'
    opener = build_opener(ProxyHandler({}))
    try:
        headers = {'Content-Type': 'application/json', **_csrf_headers(opener, base)}
        with opener.open(base + '/api/state?compact=1') as response:
            state = json.load(response)
        assert {e['id'] for e in state['trace_representations'] if e['is_current']} == {new}
        payload = {'id': old, **rollback_request(tmp_path, old)}
        request = Request(base + '/api/traces/rollback', data=json.dumps(payload).encode(), headers=headers)
        idle = controller._require_idle
        def busy():
            raise ValueError('任务运行中')
        monkeypatch.setattr(controller, '_require_idle', busy)
        with pytest.raises(HTTPError) as error:
            opener.open(request)
        assert error.value.code == 400 and current_version_ids(tmp_path) == {new}
        monkeypatch.setattr(controller, '_require_idle', idle)
        with opener.open(request) as response:
            assert json.load(response)['previous_id'] == new
        assert current_version_ids(tmp_path) == {old}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
