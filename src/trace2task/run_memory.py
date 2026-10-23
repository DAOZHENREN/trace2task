"""Identify and freeze the experience actually supplied to an execution run."""
import hashlib
import re
from pathlib import Path

from trace2task.trace_library import list_traces, read_asset, read_body, read_model_input


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def freeze_experience(root, context):
    """Keep exact text for later feedback without changing any model's wire schema."""
    if not context or context.get('kind') != 'trace_sequence':
        return None
    data = context['model_input'].encode('utf-8')
    if _sha(data) != context.get('sha256'):
        raise ValueError('实际经验正文与哈希不一致，未发送给模型')
    root = Path(root).resolve()
    path = root / 'experience' / 'model-input.txt'
    if not path.resolve().is_relative_to(root):
        raise ValueError('经验快照不能指向运行目录外部')
    path.parent.mkdir(exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError('同一次运行的经验快照发生变化')
    else:
        path.write_bytes(data)
    return {'path': str(path), 'sha256': context['sha256']}


def used_experience(root, run, rounds):
    """Resolve auto/manual selection and require matching delivered-round hashes.

    Older runs can use the exact library version only if its digest still matches;
    never guess from a name, latest version, prompt substring, or a search result.
    """
    root = Path(root).resolve()
    facts, result = run.get('run_facts') or {}, run.get('result') or {}
    delivered = [r for r in rounds if (r.get('execution') or {}).get('executed') is True]
    selection = run.get('memory_selection') or result.get('memory_selection') or {}
    automatic = (run.get('memory_mode') == 'auto' or facts.get('memory_mode') == 'auto'
                 or result.get('memory_mode') == 'auto' or selection.get('mode') == 'auto')
    if automatic:
        chosen = selection.get('selected') if selection.get('status') == 'loaded' else None
        identifier = (chosen or {}).get('id')
        expected = (chosen or {}).get('sha256')
        representation = (chosen or {}).get('representation')
    else:
        identifier, expected = facts.get('experience_id'), facts.get('experience_sha256')
        representation = facts.get('experience_representation')
    has_receipt = any(r.get('experience_sha256') for r in delivered)
    if not identifier and not has_receipt and not (run.get('use_experience') or result.get('use_experience')):
        return None
    if (not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', identifier)
            or not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected)):
        raise ValueError('运行没有冻结可核对的经验身份；不能静默按无经验重新提炼')
    body = read_body(root, identifier)
    header = next((item for item in list_traces(root) if item['id'] == identifier), None)
    model = read_model_input(root, identifier)
    original = model['content']
    source_sha = _sha(original.encode('utf-8'))
    expected_source = facts.get('experience_source_sha256')
    if expected_source and expected_source != source_sha:
        raise ValueError('运行冻结的经验归档原文哈希不一致，不能合并反馈')
    content = original
    if source_sha != expected:
        runtime = model.get('runtime_content')
        if runtime is None or _sha(runtime.encode('utf-8')) != expected:
            raise ValueError('运行实际使用的经验与父版本不一致，不能合并反馈')
        if not expected_source:
            raise ValueError('紧凑经验输入缺少归档原文哈希，不能确认父版本')
        content = runtime
    data = content.encode('utf-8')
    if (_sha(data) != expected or (header or {}).get('representation') != representation
            or body.get('representation', representation) != representation):
        raise ValueError('运行实际使用的经验与父版本不一致，不能合并反馈')
    if representation == 'R' and model['review']['decision'] != 'approved_for_trial':
        raise ValueError('父候选未获准试用，不能作为反馈来源')
    if not delivered or any(r.get('experience_sha256') != expected for r in delivered):
        raise ValueError('执行回合缺少匹配的实际经验哈希，不能确认反馈来源')
    snapshots = [r['experience_snapshot'] for r in delivered if r.get('experience_snapshot')]
    if result.get('experience_snapshot'):
        snapshots.append(result['experience_snapshot'])
    for snapshot in snapshots:
        path = (root / snapshot['path']).resolve()
        if (not path.is_relative_to(root / 'runs') or path.name != 'model-input.txt'
                or path.parent.name != 'experience' or snapshot.get('sha256') != expected):
            raise ValueError('实际经验快照不属于本次运行证据')
        if result.get('trace_path') and path.parent.parent != (root / result['trace_path']).resolve().parent:
            raise ValueError('经验快照与源运行目录不一致')
        if path.read_bytes() != data:
            raise ValueError('实际经验快照已变化，不能据此修订方法')
    procedure = content if representation in {'B', 'R'} else None
    if representation == 'C':
        procedure = read_asset(root, identifier, 'procedure.md').decode('utf-8')
        if _sha(procedure.encode('utf-8')) != body.get('source_b_sha256'):
            raise ValueError('组合经验的父方法哈希不一致')
    previous = body.get('lineage') or body.get('evolution') or {}
    generation = previous.get('generation', 0)
    if type(generation) is not int or generation < 0:
        raise ValueError('父方法的版本谱系无效')
    return {'id': identifier, 'name': body['trace_name'], 'representation': representation,
            'sha256': expected, 'selection_mode': 'auto' if automatic else 'manual',
            'model_input': content, 'procedure': procedure,
            'source_sha256': source_sha,
            'input_formatting': 'compact-json-v1' if content != original else 'verbatim',
            'root_id': previous.get('root_id', previous.get('root_b_id', body.get('source_b_id', identifier))),
            'generation': generation,
            'text_source': 'run_snapshot' if snapshots else 'digest_matched_library',
            'historical_images_included': False}
