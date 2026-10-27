"""Compact phase-method evidence; the complete immutable audit remains separate."""
import math
import re
from copy import deepcopy

from trace2task.compiler_projection import _model_output, compiler_evidence
from trace2task.model_text import model_json

SCHEMA = 'trace2task.method-evidence.v2'
INSTRUCTIONS = """
rounds 是逐轮简表，轮次从 1 起；当时判断/回答保留原文，是旧模型自述，不是事实。
计划只保留操作语义，普通坐标、机械时长与重复回执在本地完整归档。输入方向指画面方向，
不是角色实际运动方向。交付链中的计划序号指向本轮动作，不证明动作生效。
送达只证明发出了输入；未另列效果时没有可验证效果。导航接近目标和相机拖动结束都不证明任务完成。
images 是实际附件顺序，同一图片可关联多个轮次；无图片的轮次不得补造画面。
人工指令的状态只表示排队、提交或收到回复；标注图是用户说明，不是界面控件或精确手势。
人工 submitted_round/responded_round 同样从 1 起。只有接管前后图时不能推断中间动作。
"""

_GEOMETRY = {'x', 'y', 'start_x', 'start_y', 'end_x', 'end_y', 'from_x', 'from_y', 'to_x', 'to_y',
             'map_region', 'marker', 'goal', 'stop_radius', 'camera_heading_deg'}
_VISUAL_SKILLS = {'click', 'double_click', 'right_click', 'move', 'move_pointer', 'drag', 'swipe',
                  'tap', 'long_press', 'scroll', 'joystick_move', 'navigate_to', 'camera_drag'}
_ROUTINE_REASONS = {
    'Bounded navigation ended; discard the remaining batch and re-observe. Proximity is not proof of task completion.',
    'Camera swipe ended; discard the remaining batch and re-observe. Verify the actual view change and re-estimate camera heading before movement. Input delivery is not task completion.',
}
_SKILLS = {'click': '点击', 'double_click': '双击', 'right_click': '右键点击', 'tap': '点击',
           'joystick_move': '摇杆移动', 'navigate_to': '朝地图目标移动', 'camera_drag': '拖动视角',
           'wait': '等待', 'scroll': '滚动', 'drag': '拖动', 'swipe': '滑动', 'long_press': '长按'}


def _direction(dx, dy):
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (dx, dy)) or (dx == dy == 0):
        return None
    return ('右', '右下', '下', '左下', '左', '左上', '上', '右上')[round(math.atan2(dy, dx) / (math.pi / 4)) % 8]


def semantic_action(action):
    if not isinstance(action, dict) or not isinstance(action.get('args'), dict):
        return deepcopy(action)
    skill, args = action.get('skill'), action['args']
    kept = deepcopy(args)
    if skill in _VISUAL_SKILLS:
        for key in _GEOMETRY:
            kept.pop(key, None)
        if (skill in {'drag', 'swipe', 'joystick_move', 'camera_drag'}
                and all(isinstance(args.get(k), (int, float)) for k in ('start_x', 'start_y', 'end_x', 'end_y'))):
            direction = _direction(args['end_x'] - args['start_x'], args['end_y'] - args['start_y'])
            if direction:
                kept['input_direction'] = direction
        if (skill == 'navigate_to' and isinstance(args.get('goal'), dict) and isinstance(args.get('marker'), dict)
                and all(isinstance(args[k].get(axis), (int, float)) for k in ('goal', 'marker') for axis in ('x', 'y'))):
            direction = _direction(args['goal']['x'] - args['marker']['x'], args['goal']['y'] - args['marker']['y'])
            if direction:
                kept['map_target_direction'] = direction
        if kept.get('button') == 'left':
            kept.pop('button')
    # Hold durations encode an actual gesture; routine moves and polling waits do not.
    if skill in (_VISUAL_SKILLS - {'long_press'}) | {'wait'}:
        kept.pop('duration_ms', None)
    return {**{k: deepcopy(v) for k, v in action.items() if k != 'args'}, **({'args': kept} if kept else {})}


def method_body(text):
    """Strip known archive-only sections/citations, never summarize or invent a procedure."""
    if not isinstance(text, str):
        return text
    text = re.sub(r'^## (?:反馈修订说明|人工干预说明|来源与证据|证据索引)\s*\n.*?(?=^## |\Z)', '', text, flags=re.MULTILINE | re.DOTALL)
    return re.sub(r'\[(?:第\s*\d+\s*轮|父方法|人工指令\s*\d+|人工接管\s*\d+)\]', '', text)


def deduplicate_images(images):
    unique, aliases, seen = [], {}, {}
    for picture in images:
        key = picture.get('sha256') or picture['id']
        if key not in seen:
            item = {**picture, 'id': f'image-{len(unique) + 1:02d}', 'source_ids': []}
            seen[key] = item
            unique.append(item)
        item = seen[key]
        sources = picture.get('source_ids') or [picture['id']]
        for identifier in sources:
            aliases[identifier] = item['id']
            if identifier not in item['source_ids']:
                item['source_ids'].append(identifier)
        aliases[picture['id']] = item['id']
    return unique, aliases


def _action_text(action):
    if not isinstance(action, dict) or not isinstance(action.get('skill'), str):
        return model_json(action)
    skill = action.get('skill')
    label = _SKILLS.get(skill, skill or '未知动作')
    args = action.get('args') or {}
    if not isinstance(args, dict):
        return model_json(action)
    if set(args) == {'input_direction'}:
        return label + '（输入方向：' + args['input_direction'] + '）'
    if set(args) == {'map_target_direction'}:
        return label + '（地图目标位于：' + args['map_target_direction'] + '）'
    extra = {k: v for k, v in action.items() if k != 'skill'}
    return label + (' ' + model_json(extra) if extra else '')


def _delivery_text(receipt):
    parts = ['已送达' if receipt.get('executed') is True else '未送达' if receipt.get('executed') is False else '送达情况未记录']
    status = receipt.get('status')
    if status and not (status == 'delivered' and receipt.get('executed') is True):
        parts.append({'reobserve': '需重新观察', 'partial': '部分执行', 'intervened': '人工干预使计划失效'}.get(status, status))
    parts.extend(f'{key}：{receipt[key]}' for key in ('effect', 'reason', 'error') if key in receipt)
    return '；'.join(parts)


def _action_reference(reference):
    match = re.fullmatch(r'(model_output|prediction)\.actions\[(\d+)\]', reference)
    return ('计划' if match[1] == 'model_output' else '解析动作') + str(int(match[2]) + 1) if match else reference


def _round_record(entry):
    parts = [str(entry['step_index'] + 1)]
    output = entry.get('model_output') or {}
    if isinstance(output.get('actions'), list):
        parts.append('计划：' + '、'.join(_action_text(a) for a in output['actions']))
    prediction = entry.get('prediction')
    if prediction:
        if isinstance(prediction, dict) and set(prediction) == {'actions'} and not output:
            parts.append('计划：' + '、'.join(_action_text(a) for a in prediction['actions']))
        else:
            parts.append('解析差异：' + model_json(prediction))
    if 'execution' in entry:
        execution = entry['execution']
        parts.append(_delivery_text(execution))
        if 'action' in execution:
            parts.append('实际动作：' + _action_text(execution['action']))
        elif 'action_ref' in execution and ('steps' in execution or prediction or len(output.get('actions', [])) > 1):
            parts.append('交付动作：' + _action_reference(execution['action_ref']))
        for i, step in enumerate(execution.get('steps') or []):
            action = _action_text(step['action']) if 'action' in step else _action_reference(step.get('action_ref', '动作未记录'))
            parts.append(f'交付链{i + 1}（{action}）：' + _delivery_text(step.get('receipt') or {}))
    if 'reason' in output:
        parts.append('当时判断：' + str(output['reason']))
    other = {k: v for k, v in output.items() if k not in {'actions', 'reason'}}
    if other:
        parts.append('模型其他声明：' + model_json(other))
    if 'raw_output' in entry:
        raw = entry['raw_output']
        parts.append('当时回答：' + (raw if isinstance(raw, str) else model_json(raw)))
    for key in ('status', 'purpose', 'error', 'verification'):
        if key in entry:
            parts.append(key + '：' + model_json(entry[key]))
    if entry.get('image_id'):
        parts.append('图：' + entry['image_id'])
    elif entry.get('image_missing'):
        parts.append('选中图片缺失')
    return '｜'.join(parts)


def method_evidence(evidence, images):
    unique, aliases = deduplicate_images(images)
    compact = deepcopy(evidence)
    for entry in compact['rounds']:
        # Project before reference deduplication, so mechanically different defaults disappear.
        output = _model_output(entry.get('raw_output'))
        if output is not None:
            if isinstance(output.get('actions'), list):
                output['actions'] = [semantic_action(a) for a in output['actions']]
            entry['raw_output'] = model_json(output)
        prediction = entry.get('prediction')
        if isinstance(prediction, dict):
            prediction.pop('coordinate_space', None)
            if isinstance(prediction.get('actions'), list):
                prediction['actions'] = [semantic_action(a) for a in prediction['actions']]
        execution = entry.get('execution') or {}
        for receipt in [execution, *(s.get('receipt') or {} for s in execution.get('steps') or [])]:
            if receipt.get('effect') == 'unverifiable':
                receipt.pop('effect')
            if receipt.get('reason') in _ROUTINE_REASONS:
                receipt.pop('reason')
        if 'action' in execution:
            execution['action'] = semantic_action(execution['action'])
        for step in execution.get('steps') or []:
            if 'action' in step:
                step['action'] = semantic_action(step['action'])
        entry.pop('intervention_revision', None)
        entry.pop('intervention_command_ids', None)
    result = compiler_evidence(compact, unique)
    result['schema'] = SCHEMA
    for entry in result['rounds']:
        if entry.get('status') == 'predicted':
            entry.pop('status')
        if entry.get('purpose') == 'plan':
            entry.pop('purpose')
        for key in ('model_output', 'prediction'):
            value = entry.get(key)
            if isinstance(value, dict) and value.get('task_complete') is False:
                value.pop('task_complete')
        if entry.get('prediction') == {}:
            entry.pop('prediction')
    result['parent_procedure'] = method_body(result.get('parent_procedure'))
    human = result.get('human_interventions')
    if human:
        for command in human['commands']:
            command.pop('id', None)
            command.pop('revision', None)
            for key in ('submitted_round', 'responded_round'):
                if type(command.get(key)) is int:
                    command[key] += 1
            if command.get('visual'):
                command['visual'].pop('strokes', None)
                command['visual'].pop('coordinate_space', None)
        # A resume command and its takeover note often contain exactly the same user text.
        notes = {c.get('text'): c['index'] for c in human['commands'] if c.get('text')}
        for takeover in human['takeovers']:
            if takeover.get('note') in notes:
                takeover['note_command_index'] = notes[takeover.pop('note')]

    def remap(value):
        if isinstance(value, dict):
            return {k: aliases.get(v, v) if k in {'image_id', 'id'} and isinstance(v, str) else remap(v)
                    for k, v in value.items()}
        if isinstance(value, list):
            return [remap(v) for v in value]
        return value
    result = remap(result)
    result['rounds'] = [_round_record(entry) for entry in result['rounds']]
    return result
