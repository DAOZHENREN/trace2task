"""Lossless model JSON layout with immutable legacy bundles and exact run hashes."""
import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_trace_evidence import recording
from test_trace_procedure import PROCEDURE, Session, compile_fake

from trace2task.local_gui_client import validate_experience_context
from trace2task.model_text import compact_json_suffix, model_json
from trace2task.run_experience import prepare_run_experience
from trace2task.run_memory import freeze_experience, used_experience
from trace2task.run_session import RunSession, RunStore
from trace2task.trace_combined import generate_c
from trace2task.trace_library import execution_context, generate_a, generate_d, read_model_input


def test_json_layout_preserves_strings_number_spellings_and_duplicate_keys():
    document = ('{\n  "value": 1.230000000000000000001, "large": 900719925474099312345678,\n'
                '  "exponent": 1e+003, "negative_zero": -0, "key": 1, "key": 2,\n'
                r'  "text": "空  格\n\t\"引号\"\\路径\u4e2d", "array": [true, null]}' + '\n')
    prefix = '历史记录：不要修改  前言\n\n'
    compact = compact_json_suffix(prefix + document)
    assert compact.startswith(prefix) and compact.endswith('\n')
    assert json.loads(compact[len(prefix):]) == json.loads(document)
    for literal in ('1.230000000000000000001', '900719925474099312345678', '1e+003', '-0',
                    '"key":1,"key":2', r'"空  格\n\t\"引号\"\\路径\u4e2d"'):
        assert literal in compact
    assert '\n  ' not in compact[len(prefix):]
    assert compact_json_suffix(compact) == compact


@pytest.mark.parametrize('text', ['# 方法\n普通正文', '前言\n\n非 JSON 文字', '前言\n\n{invalid}',
                                '前言\n\n{"a":1}\n后记', '前言\n\n123'])
def test_unknown_text_is_never_reformatted(text):
    assert compact_json_suffix(text) == text


def test_new_model_json_is_compact_but_string_whitespace_remains():
    value = {'原文': '两  个空格\n下一行\t', 'actions': [{'x': 1, 'done': False}]}
    encoded = model_json(value)
    assert json.loads(encoded) == value
    assert '\n  ' not in encoded and '": ' not in encoded
    with pytest.raises(ValueError):
        model_json({'invalid': float('nan')})


def legacy_bundle(root, representation, monkeypatch):
    trace, _ = recording(root, 3)
    # Simulate versions generated before compact output became the default.
    with monkeypatch.context() as patch:
        pretty = lambda value: json.dumps(value, ensure_ascii=False, indent=2)
        patch.setattr('trace2task.trace_evidence.model_json', pretty)
        patch.setattr('trace2task.trace_projection.model_json', pretty)
        if representation == 'A':
            return generate_a(root, trace)['id']
        if representation == 'D':
            return generate_d(root, trace)['id']
        # B may contain braces or JSON examples; those must never be minified.
        procedure = PROCEDURE + '\n## 参数示例\n\n{\n  "preserve": "B  原文"\n}\n'
        b = compile_fake(root, trace, Session(procedure))['id']
        return b if representation == 'B' else generate_c(root, b)['id']


@pytest.mark.parametrize('representation', ['A', 'B', 'C', 'D'])
def test_legacy_runtime_input_is_compact_but_archives_and_b_text_remain_exact(tmp_path, monkeypatch, representation):
    identifier = legacy_bundle(tmp_path, representation, monkeypatch)
    directory = tmp_path / 'trace-library' / identifier
    before = {p: p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    preview = read_model_input(tmp_path, identifier)
    original = before[directory / 'model-input.txt'].decode('utf-8')
    assert preview['content'] == original
    context = execution_context(directory / 'model-input.txt')
    assert validate_experience_context(context) == context
    if representation == 'B':
        assert 'runtime_content' not in preview and 'formatting' not in preview
        assert context['model_input'] == original
    else:
        assert context['model_input'] == preview['runtime_content']
        assert len(context['model_input']) < len(original)
        assert preview['formatting']['runtime_sha256'] == context['sha256']
        assert preview['formatting']['source_sha256'] == hashlib.sha256(original.encode()).hexdigest()
        if representation == 'C':
            assert (directory / 'procedure.md').read_text(encoding='utf-8') in context['model_input']
            original = (directory / 'evidence/model-input.txt').read_text(encoding='utf-8')
            assert compact_json_suffix(original) in context['model_input']
        else:
            assert json.loads(original.split('\n\n', 1)[1]) == json.loads(context['model_input'].split('\n\n', 1)[1])
    assert all(p.read_bytes() == data for p, data in before.items())
    job = SimpleNamespace(job_id='source', provider='codex', model='test', use_experience=True,
        task_path=str(directory / 'model-input.txt'), executor_backend='cua', execution_scope='desktop',
        cua_target={'kind': 'desktop'}, background=False)
    facts = RunSession.for_job(job, tmp_path)
    assert facts.experience_sha256 == context['sha256']
    assert facts.experience_source_sha256 == hashlib.sha256(before[directory / 'model-input.txt']).hexdigest()


@pytest.mark.parametrize('representation', ['A', 'C', 'D'])
@pytest.mark.parametrize('old_run', [False, True])
def test_old_and_compact_run_hashes_both_resolve_the_exact_actual_parent(tmp_path, monkeypatch, representation, old_run):
    identifier = legacy_bundle(tmp_path, representation, monkeypatch)
    path = tmp_path / 'trace-library' / identifier / 'model-input.txt'
    original = path.read_bytes()
    context = execution_context(path)
    if old_run:
        context = {**context, 'model_input': original.decode('utf-8'), 'sha256': hashlib.sha256(original).hexdigest()}
    directory = tmp_path / 'runs/source'
    directory.mkdir(parents=True)
    snapshot = freeze_experience(directory, context)
    run = {'job_id': 'source', 'kind': 'agent', 'mode': 'execute', 'status': 'stopped', 'use_experience': True,
           'updated_at': '2026-10-23T12:00:00Z', 'instruction': 'Test source',
           'run_facts': {'operation_scope': 'desktop', 'experience_id': identifier,
                         'experience_representation': representation, 'experience_sha256': context['sha256']},
           'result': {'experience_snapshot': snapshot, 'trace_path': str(directory / 'trace.jsonl')}}
    entry = {'step_index': 0, 'raw_output': 'not independently verified', 'experience_sha256': context['sha256'],
             'experience_snapshot': snapshot, 'execution': {'executed': True, 'status': 'delivered'}}
    if not old_run:
        run['run_facts']['experience_source_sha256'] = hashlib.sha256(original).hexdigest()
    actual = used_experience(tmp_path, run, [entry])
    assert actual['model_input'] == context['model_input']
    assert actual['source_sha256'] == hashlib.sha256(original).hexdigest()
    assert actual['input_formatting'] == ('verbatim' if old_run else 'compact-json-v1')
    store = RunStore(tmp_path)
    store.save(run)
    store.round(run['job_id'], entry)
    prepared = prepare_run_experience(tmp_path, store, run['job_id'])
    assert prepared['files']['parent-experience.txt'] == context['model_input'].encode('utf-8')
    data = json.loads(prepared['files']['evidence/run.json'])
    assert data['used_experience']['sha256'] == context['sha256']
    assert data['used_experience']['source_sha256'] == hashlib.sha256(original).hexdigest()
    assert 'sha256' not in prepared['input']
    assert path.read_bytes() == original
    if not old_run:
        missing = deepcopy(run)
        missing['run_facts'].pop('experience_source_sha256')
        with pytest.raises(ValueError, match='缺少归档原文哈希'):
            used_experience(tmp_path, missing, [entry])
        changed = deepcopy(run)
        changed['run_facts']['experience_source_sha256'] = '0' * 64
        with pytest.raises(ValueError, match='归档原文哈希不一致'):
            used_experience(tmp_path, changed, [entry])
    invalid = deepcopy(entry)
    invalid['experience_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='回合缺少匹配'):
        used_experience(tmp_path, run, [invalid])
    snapshot_path = directory / 'experience/model-input.txt'
    snapshot_path.write_text('changed', encoding='utf-8')
    with pytest.raises(ValueError, match='快照已变化'):
        used_experience(tmp_path, run, [entry])
