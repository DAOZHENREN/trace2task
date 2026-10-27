"""Human evidence remains distinct and digest-bound through draft and library save."""
import hashlib
import json
from pathlib import Path

import pytest
from test_intervention_images import upload
from test_run_experience import METHOD, OPTIONS, Session, compile_draft, seed
from test_run_feedback import REVISED, feedback_run, publish

from trace2task.intervention_images import save_visual
from trace2task.run_experience import compile_run_draft, prepare_run_experience, read_draft
from trace2task.trace_library import list_traces, read_body, read_model_input

HUMAN = '\n## 人工干预说明\n用户补充了目标。[人工指令 1] 用户说明已调整界面，只有前后截图，没有精确动作记录。[人工接管 1] 不能将人工完成部分归为模型自主能力。\n'


def assisted(root, store, run):
    directory = Path(run.get('result', {}).get('trace_path') or root / 'runs/source/trace.jsonl').parent
    paths = directory / 'interventions'
    paths.mkdir()
    segment = {'id': 'takeover-1', 'status': 'returned', 'note': '我已选好技能，继续',
               'serial': 'SECRET_DEVICE'}
    for phase in ('before', 'after'):
        path = paths / f'takeover-1-{phase}.png'
        path.write_bytes(('image-' + phase).encode())
        segment[phase] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                          'package': 'com.example.game', 'serial': 'SECRET_DEVICE'}
    commands = [{'id': 'correct-1', 'index': 1, 'kind': 'instruction', 'text': '优先支援任务',
                 'status': 'responded', 'submitted_round': 1, 'responded_round': 1},
                {'id': 'pause-001', 'index': 2, 'kind': 'pause', 'text': '', 'status': 'responded'},
                {'id': 'resume-01', 'index': 3, 'kind': 'resume', 'text': segment['note'], 'status': 'responded'}]
    run['interventions'] = {'supported': True, 'phase': 'closed', 'assisted_by_user': True,
                            'commands': commands, 'takeovers': [segment]}
    store.save(run)
    return run


def test_human_preview_and_saved_provenance_include_all_handoff_images(tmp_path):
    store, run, _ = seed(tmp_path, count=12)
    assisted(tmp_path, store, run)
    prepared = prepare_run_experience(tmp_path, store, run['job_id'])
    evidence = json.loads(prepared['input'])
    assert prepared['summary']['assisted_by_user'] is True
    assert prepared['summary']['execution_condition'] == 'human-assisted'
    assert len(prepared['images']) == 3  # Deduplicated observation plus both manual images.
    human = evidence['human_interventions']
    assert human['precise_actions_recorded'] is False and len(human['commands']) == 3
    assert human['takeovers'][0]['note_command_index'] == 3
    assert 'SECRET_DEVICE' not in prepared['input'] and 'PRIVATE_DEVICE' not in prepared['input']
    assert str(tmp_path) not in prepared['input']
    session = Session(METHOD)
    child = publish(tmp_path, compile_run_draft(tmp_path, prepared, **OPTIONS,
        session_factory=session.factory, binary_resolver=lambda _: 'fake'))
    assert '人工辅助运行' in session.options['base_instructions']
    assert read_body(tmp_path, child)['execution_condition'] == 'human-assisted'
    assert list_traces(tmp_path)[0]['assisted_by_user'] is True
    assert len(read_model_input(tmp_path, child)['compiler']['images']) == 3


@pytest.mark.parametrize('tamper', [False, True])
def test_visual_reference_survives_experience_projection_and_is_digest_checked(tmp_path, tamper):
    store, run, _ = seed(tmp_path)
    assisted(tmp_path, store, run)
    run['result']['trace_path'] = str(tmp_path / 'runs/source/trace.jsonl')
    command = run['interventions']['commands'][-1]
    visual = save_visual(upload(), segment=run['interventions']['takeovers'][0],
                         directory=Path(run['result']['trace_path']).parent, request_id=command['id'])
    command['visual'] = visual
    store.save(run)
    if tamper:
        Path(visual['image']['path']).write_bytes(b'tampered')
        with pytest.raises(ValueError, match='标注截图已变化'):
            prepare_run_experience(tmp_path, store, run['job_id'])
        return
    prepared = prepare_run_experience(tmp_path, store, run['job_id'])
    reference = json.loads(prepared['input'])['human_interventions']['commands'][-1]['visual']
    assert reference['origin'] == 'user_upload' and 'strokes' not in reference
    assert reference['source']['id'] in [i['id'] for i in prepared['images']]
    assert reference['image']['id'] in [i['id'] for i in prepared['images']]
    assert str(tmp_path) not in prepared['input'] and 'upload_data_url' not in prepared['input']
    session = Session(METHOD)
    child = publish(tmp_path, compile_run_draft(tmp_path, prepared, **OPTIONS,
        session_factory=session.factory, binary_resolver=lambda _: 'fake'))
    assert any(p['id'] == reference['image']['id'] for p in read_model_input(tmp_path, child)['compiler']['images'])


@pytest.mark.parametrize('change', ['hash', 'external', 'other_run', 'package', 'missing'])
def test_manual_image_tamper_and_scope_mismatch_rejected_before_model(tmp_path, change):
    store, run, _ = seed(tmp_path)
    assisted(tmp_path, store, run)
    picture = run['interventions']['takeovers'][0]['after']
    if change == 'hash':
        picture['sha256'] = '0' * 64
    elif change == 'external':
        picture['path'] = str(tmp_path.parent / 'external.png')
    elif change == 'other_run':
        run['result']['trace_path'] = str(tmp_path / 'runs/another/trace.jsonl')
    elif change == 'package':
        picture['package'] = 'com.other.app'
    else:
        Path(picture['path']).unlink()
    store.save(run)
    with pytest.raises(ValueError):
        prepare_run_experience(tmp_path, store, run['job_id'])


@pytest.mark.parametrize('output', [METHOD + HUMAN, METHOD + HUMAN.replace('[人工指令 1]', '[人工指令 2]'),
                                  METHOD + HUMAN.replace('[人工接管 1]', '[人工接管 2]')])
def test_compiler_keeps_human_reports_out_of_standalone_method(tmp_path, output):
    store, run, _ = seed(tmp_path)
    assisted(tmp_path, store, run)
    with pytest.raises(ValueError, match='人工干预'):
        compile_draft(tmp_path, store, session=Session(output))
    assert not list_traces(tmp_path)


@pytest.mark.parametrize('unchanged', [False, True])
def test_human_feedback_preserves_actual_parent_without_claiming_improvement(tmp_path, unchanged):
    store, source, _ = seed(tmp_path)
    parent = publish(tmp_path, compile_draft(tmp_path, store))
    run, _ = feedback_run(tmp_path, store, source, parent_id=parent)
    assisted(tmp_path, store, run)
    prepared = prepare_run_experience(tmp_path, store, run['job_id'])
    assert json.loads(prepared['input'])['parent_procedure'] == METHOD
    session = Session(METHOD if unchanged else REVISED)
    result = compile_run_draft(tmp_path, prepared, **OPTIONS,
        session_factory=session.factory, binary_resolver=lambda _: 'fake')
    draft = read_draft(tmp_path, result['experience_draft_id'])
    assert draft['revision_changed'] is not unchanged
    child = publish(tmp_path, result)
    body = read_body(tmp_path, child)
    assert body['assisted_by_user'] is True and body['execution_condition'] == 'human-assisted'
    assert body['lineage']['parent_id'] == parent and body['lineage']['condition'] == 'trace-compile+run-feedback'
    assert read_model_input(tmp_path, parent)['content'] == METHOD
