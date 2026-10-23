"""Parent-linked experience feedback with synthetic runs and a fake compiler."""
import json
from copy import deepcopy

import pytest
from test_run_experience import METHOD, OPTIONS, Session, compile_draft, seed

from trace2task.agent_memory import MemoryCatalog
from trace2task.procedure_evolution import prepare_evolution
from trace2task.run_experience import (
    compile_run_draft,
    prepare_run_experience,
    read_draft,
    save_draft,
)
from trace2task.run_memory import freeze_experience
from trace2task.trace_library import execution_context, list_traces, read_body, read_model_input

REVISED = METHOD.replace('等待可交互状态。[第 1 轮]', '等待可交互状态，核对当前按钮是否可用。[第 1 轮]') + (
    '\n## 反馈修订说明\n- 保留按可见状态判断的步骤。[父方法]\n'
    '- 补充操作前按钮检查。[第 1 轮]\n- 未覆盖其他分支，不认为已经改善。\n')


def publish(root, result):
    identifier = result['experience_draft_id']
    draft = read_draft(root, identifier)
    save_draft(root, identifier, draft_sha256=draft['draft_sha256'], confirmed=True)
    return identifier


def feedback_run(root, store, source, *, parent_id, run_id='feedback-1', auto=True):
    context = execution_context(root / 'trace-library' / parent_id / 'model-input.txt')
    directory = root / 'runs' / run_id
    directory.mkdir()
    snapshot = freeze_experience(directory, context)
    item = MemoryCatalog(root, 'android', 'com.example.game').entries[parent_id]
    run = {**deepcopy(source), 'job_id': run_id, 'use_experience': True,
           'memory_mode': 'auto' if auto else 'manual',
           'run_facts': {'operation_scope': 'android', 'memory_mode': 'auto' if auto else 'manual'},
           'result': {'stop_reason': 'visual_completion_reviewed', 'experience_snapshot': snapshot,
                      'trace_path': str(directory / 'trace.jsonl')}}
    if auto:
        run['memory_selection'] = {'mode': 'auto', 'status': 'loaded', 'selected': item}
    else:
        run['run_facts'].update(experience_id=parent_id, experience_sha256=context['sha256'], experience_representation='B')
    store.save(run)
    entry = store.read_round(source['job_id'], 0)
    entry.update(experience_sha256=context['sha256'], experience_snapshot=snapshot)
    store.round(run_id, entry)
    return run, entry


@pytest.mark.parametrize('auto', [True, False])
def test_compiler_receives_actual_parent_and_feedback_without_wire_data(tmp_path, auto):
    store, source, _ = seed(tmp_path)
    parent = publish(tmp_path, compile_draft(tmp_path, store))
    run, _ = feedback_run(tmp_path, store, source, parent_id=parent, auto=auto)
    prepared = prepare_run_experience(tmp_path, store, run['job_id'])
    session = Session(REVISED)
    result = compile_run_draft(tmp_path, prepared, **OPTIONS, session_factory=session.factory, binary_resolver=lambda _: 'fake')
    data = json.loads(session.calls[0]['prompt'])
    assert data['parent_procedure'] == METHOD
    assert data['used_experience']['representation'] == 'B'
    assert 'lineage' not in data and 'sha256' not in session.calls[0]['prompt']
    audit = json.loads(prepared['files']['evidence/run.json'])
    assert audit['used_experience']['id'] == parent
    assert audit['used_experience']['selection_mode'] == ('auto' if auto else 'manual')
    assert audit['used_experience']['text_source'] == 'run_snapshot'
    assert audit['lineage']['parent_sha256'] == run['result']['experience_snapshot']['sha256']
    assert audit['rounds'][0]['experience_sha256'] == audit['lineage']['parent_sha256']
    assert 'PRIVATE_API_REQUEST' not in session.calls[0]['prompt']
    assert 'PRIVATE_DEVICE_ADDRESS' not in session.calls[0]['prompt']
    assert '保留仍适用' in session.options['base_instructions']
    assert list_traces(tmp_path)[0]['id'] == parent  # A draft is not promoted.
    draft = read_draft(tmp_path, result['experience_draft_id'])
    assert draft['parent_content'] == METHOD and draft['revision_changed'] is True
    assert '核对当前按钮' in draft['revision_diff']
    child = publish(tmp_path, result)
    model = read_model_input(tmp_path, child)
    assert model['parent_content'] == METHOD
    assert model['lineage']['parent_id'] == model['lineage']['root_id'] == parent
    assert model['lineage']['condition'] == 'trace-compile+run-feedback'
    assert model['content'] == REVISED and read_model_input(tmp_path, parent)['content'] == METHOD
    assert read_body(tmp_path, child)['condition'] == 'trace-compile+run-feedback'
    # Only the newest confirmed member of this eligible family enters auto search.
    assert set(MemoryCatalog(tmp_path, 'android', 'com.example.game').entries) == {child}
    assert not MemoryCatalog(tmp_path, 'android', 'com.other.app').entries


def test_next_run_inherits_latest_method_and_preserves_multigeneration_lineage(tmp_path):
    store, source, _ = seed(tmp_path)
    original = publish(tmp_path, compile_draft(tmp_path, store))
    run, _ = feedback_run(tmp_path, store, source, parent_id=original)
    first = publish(tmp_path, compile_run_draft(tmp_path, prepare_run_experience(tmp_path, store, run['job_id']),
        **OPTIONS, session_factory=Session(REVISED).factory, binary_resolver=lambda _: 'fake'))
    second_run, _ = feedback_run(tmp_path, store, source, parent_id=first, run_id='feedback-2')
    prepared = prepare_run_experience(tmp_path, store, second_run['job_id'])
    assert json.loads(prepared['input'])['parent_procedure'] == REVISED
    assert prepared['lineage']['generation'] == 2
    assert prepared['lineage']['parent_id'] == first and prepared['lineage']['root_id'] == original
    # Unchanged output is explicitly recorded, never sold as an improvement.
    result = compile_run_draft(tmp_path, prepared, **OPTIONS, session_factory=Session(REVISED).factory,
                               binary_resolver=lambda _: 'fake')
    draft = read_draft(tmp_path, result['experience_draft_id'])
    assert draft['revision_changed'] is False and draft['revision_diff'] == ''
    second = publish(tmp_path, result)
    assert set(MemoryCatalog(tmp_path, 'android', 'com.example.game').entries) == {second}
    assert len(list_traces(tmp_path)) == 3


@pytest.mark.parametrize('change', ['missing_identity', 'wrong_hash', 'missing_round_hash', 'snapshot'])
def test_missing_or_mismatched_parent_cannot_silently_become_independent_experience(tmp_path, change):
    store, source, _ = seed(tmp_path)
    parent = publish(tmp_path, compile_draft(tmp_path, store))
    run, entry = feedback_run(tmp_path, store, source, parent_id=parent)
    if change == 'missing_identity':
        run['memory_selection'] = {'mode': 'auto', 'status': 'loaded'}
        store.save(run)
    elif change == 'wrong_hash':
        run['memory_selection']['selected']['sha256'] = '0' * 64
        store.save(run)
    elif change == 'missing_round_hash':
        entry.pop('experience_sha256')
        store.round(run['job_id'], entry)
    else:
        from pathlib import Path
        Path(run['result']['experience_snapshot']['path']).write_text('changed', encoding='utf-8')
    with pytest.raises(ValueError):
        prepare_run_experience(tmp_path, store, run['job_id'])


def test_old_digest_bound_manual_runs_work_but_skipped_auto_runs_have_no_parent(tmp_path):
    store, source, _ = seed(tmp_path)
    parent = publish(tmp_path, compile_draft(tmp_path, store))
    run, entry = feedback_run(tmp_path, store, source, parent_id=parent, auto=False)
    run['result'].pop('experience_snapshot')
    entry.pop('experience_snapshot')
    store.save(run)
    store.round(run['job_id'], entry)
    prepared = prepare_run_experience(tmp_path, store, run['job_id'])
    assert json.loads(prepared['files']['evidence/run.json'])['used_experience']['text_source'] == 'digest_matched_library'
    run.update(use_experience=False, memory_mode='auto', memory_selection={'mode': 'auto', 'status': 'skipped'})
    run['run_facts'] = {'operation_scope': 'android', 'memory_mode': 'auto'}
    entry.pop('experience_sha256')
    store.save(run)
    store.round(run['job_id'], entry)
    prepared = prepare_run_experience(tmp_path, store, run['job_id'])
    assert prepared['lineage'] is None and json.loads(prepared['input'])['parent_procedure'] is None


def test_separate_fixed_updater_also_accepts_auto_selected_actual_parent(tmp_path):
    store, source, _ = seed(tmp_path)
    parent = publish(tmp_path, compile_draft(tmp_path, store))
    run, _ = feedback_run(tmp_path, store, source, parent_id=parent)
    prepared = prepare_evolution(tmp_path, store, run['job_id'])
    assert prepared['lineage']['parent_id'] == parent
    assert json.loads(prepared['input'])['parent_procedure'] == METHOD


def test_parent_mutation_during_compilation_never_publishes_a_feedback_draft(tmp_path):
    store, source, _ = seed(tmp_path)
    parent = publish(tmp_path, compile_draft(tmp_path, store))
    run, _ = feedback_run(tmp_path, store, source, parent_id=parent)
    session = Session(REVISED)

    def mutate():
        (session.options['cwd'].parent / 'parent-procedure.md').write_text('changed', encoding='utf-8')

    session.effect = mutate
    with pytest.raises(ValueError, match='证据发生变化'):
        compile_run_draft(tmp_path, prepare_run_experience(tmp_path, store, run['job_id']), **OPTIONS,
                          session_factory=session.factory, binary_resolver=lambda _: 'fake')
    assert len(list_traces(tmp_path)) == 1
