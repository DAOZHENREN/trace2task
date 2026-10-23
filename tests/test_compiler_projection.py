"""Audit-free compiler payloads preserve decision/execution differences and all rounds."""
import json
from copy import deepcopy

import pytest

from trace2task.compiler_projection import compiler_evidence, compiler_round
from trace2task.model_text import model_json


def round_entry():
    action = {'skill': 'click', 'args': {'x': .5, 'y': .3, 'text': '保留  原文\n下一行'}}
    return {'step_index': 0, 'status': 'predicted', 'purpose': 'plan',
            'raw_output': model_json({'task_complete': False, 'reason': '查看  走廊\n不要猜测', 'actions': [action]}),
            'prediction': {'protocol_version': '1', 'coordinate_space': 'observation_normalized_0_1', 'actions': [action]},
            'experience_sha256': 'a' * 64, 'full_round_sha256': 'b' * 64,
            'verification': None, 'intervention_command_ids': [],
            'execution': {'executed': True, 'status': 'delivered', 'effect': 'unverifiable', 'action': action,
                'target': {'serial': 'PRIVATE_DEVICE'}, 'steps': [{'action': action, 'receipt': {'effect': 'unverifiable'}}]},
            'image': {'selected': False, 'available': False}}


def resolve_action(entry, receipt):
    if 'action' in receipt:
        return receipt['action']
    source, index = receipt['action_ref'].split('.actions[')
    return entry[source]['actions'][int(index[:-1])]


def test_identical_actions_appear_once_but_delivery_is_never_success():
    original = round_entry()
    before = deepcopy(original)
    projected = compiler_round(original)
    assert original == before
    assert 'sha256' not in model_json(projected) and 'PRIVATE_DEVICE' not in model_json(projected)
    assert 'raw_output' not in projected and 'prediction' not in projected
    assert 'steps' not in projected['execution']
    assert 'image_id' not in projected and 'image_missing' not in projected
    assert projected['model_output'] == json.loads(original['raw_output'])
    assert projected['model_output']['task_complete'] is False
    assert projected['execution']['executed'] is True and projected['execution']['effect'] == 'unverifiable'
    assert resolve_action(projected, projected['execution']) == original['execution']['action']
    assert model_json(projected).count('"skill":"click"') == 1


def test_changed_normalization_and_actual_action_are_not_collapsed():
    original = round_entry()
    normalized = {'skill': 'click', 'args': {'x': 500, 'y': 300}}
    actual = {'skill': 'click', 'args': {'x': 501, 'y': 300}}
    original['prediction']['actions'] = [normalized]
    original['prediction']['coordinate_space'] = 'pixels'
    original['execution'].update(action=actual, steps=[{'action': actual, 'receipt': {'effect': 'changed'}}])
    projected = compiler_round(original)
    assert projected['prediction']['actions'] == [normalized]
    assert projected['model_output']['actions'] != projected['prediction']['actions']
    assert projected['execution']['action'] == actual
    assert projected['execution']['steps'][0]['receipt']['effect'] == 'changed'


def test_multi_action_partial_failure_preserves_order_and_each_receipt():
    original = round_entry()
    actions = [original['execution']['action'], {'skill': 'wait', 'args': {'duration_ms': 100}}]
    original['raw_output'] = model_json({'reason': '批量操作', 'actions': actions})
    original['prediction']['actions'] = actions
    original['execution'].update(status='partial', steps=[
        {'action': actions[0], 'receipt': {'executed': True, 'effect': 'unverifiable'}},
        {'action': actions[1], 'receipt': {'executed': False, 'status': 'rejected', 'reason': 'scope denied'}}])
    projected = compiler_round(original)
    steps = projected['execution']['steps']
    assert [resolve_action(projected, step) for step in steps] == actions
    assert steps[1]['receipt']['executed'] is False
    assert steps[1]['receipt']['reason'] == 'scope denied'


def test_repeated_action_steps_still_have_separate_receipts_and_occurrences():
    original = round_entry()
    actions = [original['execution']['action']] * 2
    original['raw_output'] = model_json({'actions': actions})
    original['prediction']['actions'] = actions
    original['execution']['steps'] *= 2
    projected = compiler_round(original)
    assert [step['action_ref'] for step in projected['execution']['steps']] == [
        'model_output.actions[0]', 'model_output.actions[1]']


@pytest.mark.parametrize('raw', ['不完整 JSON { "actions":', '模型拒绝；无动作\n保留  空格',
    '{"actions": [], "x": 1, "x": 2}', '{"actions": [], "x": 1.23000000000000000001}',
    r'{"actions": [], "text": "\u4e2d"}', '{"actions": [], "x": NaN}', '{"actions": [], "x": -0}'])
def test_noncanonical_or_unknown_model_text_stays_exact(raw):
    original = round_entry()
    original['raw_output'] = raw
    projected = compiler_round(original)
    assert projected['raw_output'] == raw and 'model_output' not in projected
    assert resolve_action(projected, projected['execution']) == original['execution']['action']


def test_false_zero_unknown_execution_and_falsy_arguments_are_preserved():
    original = round_entry()
    action = {'skill': 'click', 'args': {'x': 0, 'y': 0, 'toggle': False, 'text': '', 'value': None}}
    original.update(raw_output=model_json({'task_complete': False, 'actions': [action]}), verification=False,
                    intervention_revision=0)
    original['prediction']['actions'] = [action]
    original['execution'] = {'executed': None, 'status': 'interrupted_or_unknown'}
    projected = compiler_round(original)
    assert projected['step_index'] == projected['intervention_revision'] == 0
    assert projected['verification'] is False and projected['execution']['executed'] is None
    assert projected['model_output']['actions'] == [action]
    assert 'action_ref' not in projected['execution']


def test_only_audit_fields_are_removed_not_user_text_or_sha_action_arguments():
    entry = round_entry()
    action = {'skill': 'type', 'args': {'text': 'sha256 abc', 'sha256': 'TASK_DATA'}}
    entry.update(raw_output='用户要求比较 SHA-256，不应删除任务文本。', prediction={'actions': [action]},
                 execution={'executed': False, 'action': action, 'reason': 'not sent'})
    view = compiler_round(entry)
    assert view['raw_output'] == entry['raw_output']
    assert resolve_action(view, view['execution']) == action
    assert view['execution']['executed'] is False


def test_model_envelope_has_ordered_images_human_context_but_no_audit_metadata():
    entry = round_entry()
    entry['image'] = {'selected': True, 'available': True, 'id': 'round-0000', 'sha256': 'b' * 64}
    human = {'condition': 'human-assisted', 'precise_actions_recorded': False,
             'commands': [{'id': 'c1', 'index': 1, 'kind': 'instruction', 'text': '只做支线', 'status': 'queued'}],
             'takeovers': [{'id': 'takeover-1', 'status': 'not_returned', 'note': '人工操作  未结束',
                 'before': {'id': 'human-before', 'file': 'PRIVATE_PATH', 'sha256': 'c' * 64}, 'after': None}]}
    evidence = {'run': {'instruction': '任务', 'status': 'stopped', 'run_id': 'AUDIT_ONLY',
                        'parent_experience': {'sha256': 'a' * 64}},
                'used_experience': {'name': '原方法', 'representation': 'B', 'sha256': 'a' * 64,
                                    'historical_images_included': False}, 'parent_procedure': '# 原方法\n保留  全文\n',
                'rounds': [entry, {**entry, 'step_index': 1, 'image': {'selected': True, 'available': False}}],
                'human_interventions': human, 'lineage': {'parent_sha256': 'a' * 64},
                'image_selection': {'rule': 'only listed images'}, 'limits': 'no independent verification'}
    before = deepcopy(evidence)
    result = compiler_evidence(evidence, [{'id': 'round-0000'}, {'id': 'human-before'}])
    assert evidence == before
    assert result['images'] == ['round-0000', 'human-before']
    assert result['rounds'][0]['image_id'] == 'round-0000'
    assert result['rounds'][1]['image_missing'] is True
    assert result['parent_procedure'] == evidence['parent_procedure']
    assert result['human_interventions']['precise_actions_recorded'] is False
    assert result['human_interventions']['commands'][0]['status'] == 'queued'
    assert result['human_interventions']['takeovers'][0]['after'] is None
    assert result['human_interventions']['takeovers'][0]['before'] == {'id': 'human-before'}
    text = model_json(result)
    assert all(value not in text for value in ('sha256', 'AUDIT_ONLY', 'PRIVATE_PATH'))
