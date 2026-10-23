"""Task evidence for compilers, separate from immutable local audit metadata."""
import json

from trace2task.model_text import compact_json_suffix, model_json

SCHEMA = 'trace2task.compiler-evidence.v1'
INSTRUCTIONS = """
输入是任务证据视图：来源哈希、路径与完整回执在本地核验和归档，不交给你判断。
model_output 是可无损转为对象的旧模型 JSON；其他回答保留 raw_output 原文。
prediction 只在解析结果与 model_output 不同或有额外信息时出现。
execution.action_ref / steps[].action_ref 指向本轮 model_output.actions[N] 或 prediction.actions[N]，
仅替代完全相同的动作；是否送达、是否生效仍以执行回执为准，引用动作不等于执行成功。
仅有一个子步骤且其动作及回执已完整体现在 execution 中时，省去重复的 steps。
images 按实际附件顺序列出图片 ID。无 image_id 的轮次没有提供截图；image_missing 表示选中图片缺失。
所有轮次保留，重复轮次没有合并。不要推断缺失画面，也不要将未执行的计划当作已发生的操作。
"""


def _fields(value, keys):
    # Only explicit metadata fields are pruned. Never prune user/model text or action arguments.
    return {key: value[key] for key in keys if key in value and value[key] not in (None, '', [], {})}


def _same(left, right):
    # Unlike Python ==, distinguish JSON false/0, true/1 and differing number encodings.
    return model_json(left, sort_keys=True) == model_json(right, sort_keys=True)


def _model_output(raw):
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            return None
        # Do not silently round old numbers or normalize escapes/duplicate keys.
        compact = compact_json_suffix('\n\n' + raw)[2:].strip(' \t\r\n')
        return value if model_json(value) == compact else None
    except (ValueError, RecursionError):
        return None


def _action(value, planned, source, preferred_index=None):
    if preferred_index is not None and preferred_index < len(planned) and _same(planned[preferred_index], value):
        return {'action_ref': f'{source}.actions[{preferred_index}]'}
    for index, action in enumerate(planned):
        if _same(action, value):
            return {'action_ref': f'{source}.actions[{index}]'}
    return {'action': value}


def _execution(value, planned, source):
    result = _fields(value, ('status', 'effect', 'reason', 'error'))
    if 'executed' in value:
        result['executed'] = value['executed']  # None means unknown, not false.
    if value.get('action') is not None:
        result.update(_action(value['action'], planned, source))
    steps = []
    for index, step in enumerate(value.get('steps') or []):
        receipt = _fields(step.get('receipt') or {}, ('executed', 'status', 'effect', 'reason', 'error'))
        projected = _action(step['action'], planned, source, index) if step.get('action') is not None else {}
        if receipt:
            projected['receipt'] = receipt
        steps.append(projected)
    if steps:
        first = steps[0]
        action = {k: v for k, v in first.items() if k != 'receipt'}
        redundant = (len(steps) == 1 and bool(action)
                     and all(k in result and _same(v, result[k]) for k, v in action.items())
                     and all(k in result and _same(v, result[k]) for k, v in first.get('receipt', {}).items()))
        if not redundant:
            result['steps'] = steps
    return result


def compiler_round(entry):
    result = _fields(entry, ('step_index', 'status', 'purpose', 'error', 'verification',
                             'intervention_revision', 'intervention_command_ids'))
    output = _model_output(entry.get('raw_output'))
    if output is not None:
        result['model_output'] = output
    elif 'raw_output' in entry:
        result['raw_output'] = entry['raw_output']
    prediction = entry.get('prediction')
    planned, source = [], 'model_output'
    if isinstance(output, dict) and isinstance(output.get('actions'), list):
        planned = output['actions']
    if isinstance(prediction, dict):
        normalized = {k: v for k, v in prediction.items() if k not in {'protocol_version', 'coordinate_space'}}
        if prediction.get('coordinate_space'):
            result['coordinate_space'] = prediction['coordinate_space']
        if isinstance(normalized.get('actions'), list):
            if 'model_output' in result and _same(normalized['actions'], planned):
                normalized.pop('actions')
            else:
                planned, source = normalized['actions'], 'prediction'
        if normalized:
            result['prediction'] = normalized
    elif prediction is not None:
        result['prediction'] = prediction  # Unknown legacy formats remain visible.
    if entry.get('execution') is not None:
        result['execution'] = _execution(entry['execution'], planned, source)
    picture = entry.get('image') or {}
    if picture.get('available') and picture.get('id'):
        result['image_id'] = picture['id']
    elif picture.get('selected'):
        result['image_missing'] = True
    return result


def _human(value):
    if not value:
        return None
    result = _fields(value, ('condition', 'precise_actions_recorded'))
    result['commands'] = [_fields(command, ('id', 'index', 'kind', 'text', 'status', 'revision',
                           'submitted_round', 'responded_round', 'takeover_id')) for command in value['commands']]
    result['takeovers'] = []
    for segment in value['takeovers']:
        item = _fields(segment, ('id', 'status', 'note', 'before_error', 'return_error'))
        for phase in ('before', 'after'):
            picture = segment.get(phase)
            item[phase] = {'id': picture['id']} if picture else None
        result['takeovers'].append(item)
    return result


def compiler_evidence(evidence, images):
    """No writes, hashes or authority changes. Call only after source validation."""
    original = evidence['run']
    run = _fields(original, ('instruction', 'status', 'error', 'outcome', 'stop_reason',
                              'scope', 'package', 'execution_condition'))
    # Fixed-updater archives have a raw session instead of a run-experience summary.
    if isinstance(original.get('result'), dict):
        outcome = _fields(original['result'], ('stop_reason', 'error', 'task_complete', 'verification'))
        if outcome:
            run['result'] = outcome
    if 'scope' not in run and (original.get('run_facts') or {}).get('operation_scope'):
        run['scope'] = original['run_facts']['operation_scope']
    used = evidence.get('used_experience')
    return {'schema': SCHEMA, 'run': run,
            'used_experience': _fields(used, ('name', 'representation', 'historical_images_included')) if used else None,
            'parent_procedure': evidence.get('parent_procedure'),
            'reference_experience_text': evidence.get('reference_experience_text'),
            'rounds': [compiler_round(entry) for entry in evidence['rounds']],
            'human_interventions': _human(evidence.get('human_interventions')),
            'images': [picture['id'] for picture in images],
            'image_selection': evidence['image_selection'], 'limits': evidence['limits']}
