import json
from copy import deepcopy

import pytest
from test_compiler_projection import round_entry
from test_run_experience import OPTIONS, Session, seed

from trace2task.method_projection import (
    deduplicate_images,
    method_body,
    method_evidence,
    semantic_action,
)
from trace2task.run_experience import (
    compile_run_draft,
    prepare_run_experience,
    read_draft,
    save_draft,
)
from trace2task.trace_library import execution_context


def test_semantics_keep_direction_buttons_text_and_significant_holds():
    assert semantic_action({'skill': 'camera_drag', 'args': {'start_x': .9, 'start_y': .4,
        'end_x': .3, 'end_y': .4, 'duration_ms': 333}}) == {'skill': 'camera_drag', 'args': {'input_direction': '左'}}
    assert semantic_action({'skill': 'click', 'args': {'x': .9, 'y': .4, 'button': 'right'}}) == {
        'skill': 'click', 'args': {'button': 'right'}}
    assert semantic_action({'skill': 'long_press', 'args': {'x': .9, 'y': .4, 'duration_ms': 2000}}) == {
        'skill': 'long_press', 'args': {'duration_ms': 2000}}
    action = {'skill': 'type', 'args': {'text': '保留  原文\n定时 2 秒，SHA256 也是任务内容'}}
    assert semantic_action(action) == action


def test_compact_rounds_preserve_judgments_partial_delivery_and_invalidated_plans():
    entry = round_entry()
    entry['prediction']['actions'][0]['args']['button'] = 'left'
    invalid = {**deepcopy(entry), 'step_index': 1, 'status': 'stale',
               'execution': {'executed': False, 'status': 'intervened'}}
    entry['execution']['steps'].append({'action': {'skill': 'wait', 'args': {'duration_ms': 100}},
                                      'receipt': {'executed': False, 'status': 'rejected', 'reason': 'scope denied'}})
    source = {'run': {'instruction': '任务', 'status': 'stopped'}, 'rounds': [entry, invalid],
              'image_selection': {}, 'limits': '未独立验证'}
    before = deepcopy(source)
    view = method_evidence(source, [])
    assert source == before
    first, second = view['rounds']
    assert json.loads(entry['raw_output'])['reason'] in first
    assert '解析差异' not in first and 'coordinate_space' not in first
    assert '交付链2（实际' not in first and '未送达' in first
    assert 'scope denied' in first
    assert 'stale' in second and '未送达' in second
    assert 'duration_ms' not in json.dumps(view) and '"x"' not in json.dumps(view)


def test_deduplicated_image_aliases_survive_reprojection_without_uuid_or_strokes():
    pictures = [{'id': 'observation', 'sha256': 'same', 'file': 'one.png'},
                {'id': 'human-UUID-source', 'sha256': 'same', 'file': 'two.png'},
                {'id': 'annotation', 'sha256': 'different', 'file': 'three.png'}]
    source = {'run': {}, 'rounds': [{**round_entry(), 'image': {'available': True, 'id': 'observation'}}],
              'human_interventions': {'condition': 'human-assisted', 'precise_actions_recorded': False,
                'commands': [{'id': 'UUID', 'index': 1, 'kind': 'instruction', 'text': '使用圈出的入口',
                    'visual': {'strokes': [{'points': [[.3, .2]]}], 'origin': 'user_upload',
                        'source': {'id': 'human-UUID-source'}, 'image': {'id': 'annotation'}}}], 'takeovers': []},
              'image_selection': {}, 'limits': ''}
    view = method_evidence(source, pictures)
    assert view['images'] == ['image-01', 'image-02']
    assert '图：image-01' in view['rounds'][0]
    assert view['human_interventions']['commands'][0]['visual']['source']['id'] == 'image-01'
    assert all(word not in json.dumps(view) for word in ('UUID', 'strokes', 'sha256'))
    unique, _ = deduplicate_images(pictures)
    assert method_evidence(source, unique) == view


@pytest.mark.parametrize('numbered', [False, True])
def test_phase_method_saved_without_fixed_sections_or_reports(tmp_path, numbered):
    store, _, _ = seed(tmp_path)
    method = '# 可见目标导航\n根据当前任务找到目标并核对到达状态。\n## 定位目标\n目标可见时朝可通行方向移动；出现交互入口后核对目标文字。\n## 交互与核对\n交互后读取当前任务目标；仅在结果符合要求时结束。'
    if numbered:
        method = method.replace('## 定位目标\n', '1. ').replace('## 交互与核对\n', '2. ')
    session = Session(method)
    result = compile_run_draft(tmp_path, prepare_run_experience(tmp_path, store, 'source-run'), **OPTIONS,
                               session_factory=session.factory, binary_resolver=lambda _: 'fake')
    draft = read_draft(tmp_path, result['experience_draft_id'])
    assert draft['content'] == method
    assert '不固定章节名称或步骤数量' in session.options['base_instructions']
    save_draft(tmp_path, draft['draft_id'], draft_sha256=draft['draft_sha256'], confirmed=True)
    context = execution_context(tmp_path / 'trace-library' / draft['draft_id'] / 'model-input.txt')
    assert context['model_input'] == method


def test_parent_cleanup_keeps_conditional_methods_and_removes_only_known_reports():
    parent = '# 方法\n用于当前任务。\n## 操作\n遇到前情提要时，从入口跳过。[父方法]\n## 反馈修订说明\n来自某次运行。[第 2 轮]\n## 室内路径\n需要下楼时沿楼梯开口进入大厅。\n'
    body = method_body(parent)
    assert '遇到前情提要时，从入口跳过。' in body and '沿楼梯开口进入大厅' in body
    assert '反馈修订说明' not in body and '父方法' not in body and '第 2 轮' not in body


def test_pending_and_unparseable_outputs_do_not_become_executed_actions():
    source = {'run': {}, 'rounds': [
        {'step_index': 0, 'raw_output': None, 'status': 'error', 'execution': {'executed': False}},
        {'step_index': 1, 'raw_output': '未完成的 JSON {', 'prediction': {'actions': [{'skill': 42, 'args': []}]},
         'execution': {'executed': False}}], 'image_selection': {}, 'limits': ''}
    records = method_evidence(source, [])['rounds']
    assert '当时回答：null' in records[0] and '未送达' in records[0]
    assert '未完成的 JSON {' in records[1] and '未送达' in records[1]


@pytest.mark.parametrize('reference', ['沿用旧方法。', '参照父方法。', '本次未覆盖。', '按之前的做法。'])
def test_ambiguous_history_references_cannot_be_published(tmp_path, reference):
    store, _, _ = seed(tmp_path)
    session = Session('# 方法\n完成当前任务。\n## 处理\n' + reference)
    with pytest.raises(ValueError, match='独立可用'):
        compile_run_draft(tmp_path, prepare_run_experience(tmp_path, store, 'source-run'), **OPTIONS,
                          session_factory=session.factory, binary_resolver=lambda _: 'fake')
    assert not list((tmp_path / 'runs/experience-drafts').glob('*/draft.json'))
