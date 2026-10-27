"""Publication choice controls membership without changing historical evidence."""
import json
import threading
from urllib.request import ProxyHandler, Request, build_opener

import pytest
from test_run_experience import METHOD, OPTIONS, Session, compile_draft, seed
from test_run_feedback import REVISED, feedback_run, publish
from test_web_console import _csrf_headers

from trace2task.agent_memory import MemoryCatalog
from trace2task.run_experience import (
    compile_run_draft,
    edit_draft,
    encoded,
    prepare_run_experience,
    read_draft,
    save_draft,
)
from trace2task.trace_library import read_body, read_model_input
from trace2task.trace_versions import current_version_ids, list_versions
from trace2task.web_console import WebConsoleController, create_web_server


def feedback_draft(root):
    store, source, _ = seed(root)
    parent = publish(root, compile_draft(root, store))
    run, _ = feedback_run(root, store, source, parent_id=parent)
    result = compile_run_draft(root, prepare_run_experience(root, store, run['job_id']), **OPTIONS,
                              session_factory=Session(REVISED).factory, binary_resolver=lambda _: 'fake')
    return store, source, parent, read_draft(root, result['experience_draft_id'])


def payload(draft, mode, target=None):
    result = {'draft_sha256': draft['draft_sha256'], 'confirmed': True, 'save_mode': mode}
    if mode == 'revision':
        options = draft['save_options']
        identifier = target or options['default_parent_id']
        chosen = next(t for t in options['revision_targets'] if t['id'] == identifier)
        result.update(revision_parent_id=identifier, expected_revision=chosen['version_revision'])
    return result


@pytest.mark.parametrize('mode', ['revision', 'independent'])
def test_save_modes_control_current_versions_but_preserve_provenance(tmp_path, mode):
    _, _, parent, draft = feedback_draft(tmp_path)
    assert draft['save_options']['default_mode'] == 'revision'
    assert draft['save_options']['default_parent_id'] == parent
    before = {p: p.read_bytes() for p in (tmp_path / 'trace-library' / parent).rglob('*') if p.is_file()}
    result = save_draft(tmp_path, draft['draft_id'], **payload(draft, mode))
    child = result['id']
    assert current_version_ids(tmp_path) == ({child} if mode == 'revision' else {parent, child})
    assert set(MemoryCatalog(tmp_path, 'android', 'com.example.game').entries) == current_version_ids(tmp_path)
    assert not MemoryCatalog(tmp_path, 'android', 'com.other.app').entries
    versions = {v['id']: v for v in list_versions(tmp_path)}
    assert (versions[child]['family_id'] == versions[parent]['family_id']) == (mode == 'revision')
    assert read_body(tmp_path, child)['lineage']['parent_id'] == parent
    assert read_model_input(tmp_path, child)['parent_content'] == METHOD
    assert all(p.read_bytes() == value for p, value in before.items())
    assert read_draft(tmp_path, child)['saved_versioning']['mode'] == mode


def test_independent_family_stays_separate_through_further_feedback_and_edits(tmp_path):
    store, source, original, draft = feedback_draft(tmp_path)
    fork = save_draft(tmp_path, draft['draft_id'], **payload(draft, 'independent'))['id']
    fork_draft = read_draft(tmp_path, fork)
    edited = edit_draft(tmp_path, fork, draft_sha256=fork_draft['draft_sha256'],
                        content=REVISED.replace('核对结算画面。', '核对结算画面和最终奖励。'))
    assert edited['save_options']['default_parent_id'] == fork
    edited = edit_draft(tmp_path, edited['draft_id'], draft_sha256=edited['draft_sha256'],
                        content=edited['content'].replace('精灵对战方法', '独立的对战方法'))
    assert edited['save_options']['default_parent_id'] == fork
    saved_edit = save_draft(tmp_path, edited['draft_id'], **payload(edited, 'revision'))['id']
    assert current_version_ids(tmp_path) == {original, saved_edit}
    run, _ = feedback_run(tmp_path, store, source, parent_id=saved_edit, run_id='fork-feedback')
    result = compile_run_draft(tmp_path, prepare_run_experience(tmp_path, store, run['job_id']), **OPTIONS,
                              session_factory=Session(REVISED).factory, binary_resolver=lambda _: 'fake')
    next_draft = read_draft(tmp_path, result['experience_draft_id'])
    next_id = save_draft(tmp_path, next_draft['draft_id'], **payload(next_draft, 'revision'))['id']
    assert current_version_ids(tmp_path) == {original, next_id}
    family = {v['id']: v['family_id'] for v in list_versions(tmp_path)}
    assert family[fork] == family[saved_edit] == family[next_id] != family[original]
    # The original derivation remains auditable; it must not merge these families.
    assert read_body(tmp_path, next_id)['lineage']['root_id'] == original


def test_same_run_can_create_independent_experiences_or_explicitly_update_one(tmp_path):
    store, _, _ = seed(tmp_path)
    first = publish(tmp_path, compile_draft(tmp_path, store))
    second_draft = read_draft(tmp_path, compile_draft(tmp_path, store)['experience_draft_id'])
    assert second_draft['save_options']['default_parent_id'] == first
    second = save_draft(tmp_path, second_draft['draft_id'], **payload(second_draft, 'independent'))['id']
    assert current_version_ids(tmp_path) == {first, second}
    third_draft = read_draft(tmp_path, compile_draft(tmp_path, store)['experience_draft_id'])
    assert third_draft['save_options']['default_mode'] == 'independent'  # Two candidates, no guessed parent.
    assert third_draft['save_options']['default_parent_id'] == ''
    third = save_draft(tmp_path, third_draft['draft_id'], **payload(third_draft, 'revision', target=second))['id']
    assert current_version_ids(tmp_path) == {first, third}


def test_stale_version_choice_and_repeat_save_cannot_change_membership(tmp_path):
    _, _, parent, draft = feedback_draft(tmp_path)
    stale = payload(draft, 'revision')
    changed = edit_draft(tmp_path, draft['draft_id'], draft_sha256=draft['draft_sha256'],
                         content=REVISED.replace('精灵对战方法', '更新的精灵对战方法'))
    current = save_draft(tmp_path, changed['draft_id'], **payload(changed, 'revision'))['id']
    with pytest.raises(ValueError, match='原经验已变化'):
        save_draft(tmp_path, draft['draft_id'], **stale)
    assert not (tmp_path / 'trace-library' / draft['draft_id']).exists()
    independent = payload(draft, 'independent')
    fork = save_draft(tmp_path, draft['draft_id'], **independent)['id']
    assert save_draft(tmp_path, draft['draft_id'], **independent)['already_saved']
    with pytest.raises(ValueError, match='另一种归属'):
        save_draft(tmp_path, draft['draft_id'], **stale)
    assert current_version_ids(tmp_path) == {current, fork}
    assert read_body(tmp_path, parent)['versioning']['mode'] == 'independent'


def test_revision_targets_cannot_cross_application_scope(tmp_path):
    store, _, _ = seed(tmp_path)
    parent = publish(tmp_path, compile_draft(tmp_path, store))
    entry = store.read_round('source-run', 0)
    entry['execution']['target']['package'] = 'com.other.app'
    store.round('source-run', entry)
    draft = read_draft(tmp_path, compile_draft(tmp_path, store)['experience_draft_id'])
    assert draft['save_options']['revision_targets'] == []
    assert draft['save_options']['default_mode'] == 'independent'
    version = next(v for v in list_versions(tmp_path) if v['id'] == parent)
    with pytest.raises(ValueError, match='范围匹配'):
        save_draft(tmp_path, draft['draft_id'], draft_sha256=draft['draft_sha256'], confirmed=True,
                   save_mode='revision', revision_parent_id=parent, expected_revision=version['version_revision'])
    assert current_version_ids(tmp_path) == {parent}
    assert not (tmp_path / 'trace-library' / draft['draft_id']).exists()


@pytest.mark.parametrize('mode', ['revision', 'independent'])
def test_save_choice_http_contract(tmp_path, mode):
    _, _, parent, draft = feedback_draft(tmp_path)
    server = create_web_server(tmp_path, port=0, controller=WebConsoleController(tmp_path))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f'http://127.0.0.1:{server.server_port}'
    opener = build_opener(ProxyHandler({}))
    try:
        headers = {**_csrf_headers(opener, base), 'Content-Type': 'application/json'}
        request = Request(base + '/api/run-experience/save', headers=headers,
            data=encoded({'draft_id': draft['draft_id'], **payload(draft, mode)}))
        with opener.open(request, timeout=5) as response:
            saved = json.load(response)
        assert saved['versioning']['mode'] == mode
        assert current_version_ids(tmp_path) == ({saved['id']} if mode == 'revision' else {parent, saved['id']})
    finally:
        server.shutdown()
        server.server_close()
        worker.join(3)
