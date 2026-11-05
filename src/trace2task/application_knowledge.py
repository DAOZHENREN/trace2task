"""Reviewed application facts, stored separately from task experience."""
import hashlib
import json
import os
import re
from pathlib import Path
from uuid import uuid4

KNOWLEDGE_LIMIT = 6000
COMPILER_RULE = (
    '底层知识用于同一应用的多个任务，例如界面区域、功能和稳定机制。'
    '任务经验记录特定目标的操作流程、分支和完成标准。'
    '经验中按名称引用相关底层机制，只保留本任务如何使用它，避免复述整套规则。'
    '以当前画面、用户提供的信息和有证据的运行记录为准，不猜测隐藏规则。'
)


def knowledge_id(scope, application):
    if scope not in {'android', 'desktop'} or not isinstance(application, str):
        raise ValueError('请选择 Android 或 Windows 应用并填写应用标识')
    key = application.strip()
    if (not key or not key.isprintable() or len(key) > 120
            or (scope == 'android' and not re.fullmatch(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+', key))):
        raise ValueError('应用标识无效，Android 需要包名')
    canonical = key if scope == 'android' else key.casefold()
    return hashlib.sha256(f'{scope}:{canonical}'.encode()).hexdigest()


def knowledge_path(root, identifier):
    if not isinstance(identifier, str) or not re.fullmatch('[0-9a-f]{64}', identifier):
        raise ValueError('底层知识标识无效')
    root = Path(root).resolve()
    path = root / 'application-knowledge' / f'{identifier}.json'
    if path.resolve() != path:
        raise ValueError('底层知识路径无效')
    return path


def read_knowledge(root, identifier):
    path = knowledge_path(root, identifier)
    if not path.is_file():
        return None
    value = json.loads(path.read_bytes())
    if (not isinstance(value, dict) or set(value) != {'id', 'scope', 'application', 'content', 'sha256', 'revision'}
            or value.get('id') != identifier
            or knowledge_id(value.get('scope'), value.get('application')) != identifier
            or type(value.get('revision')) is not int or value['revision'] < 1
            or validate_content(value['content']) != value['content']
            or hashlib.sha256(value['content'].encode()).hexdigest() != value['sha256']):
        raise ValueError('底层知识记录与校验值不一致')
    return value


def knowledge_header(value):
    return {key: val for key, val in value.items() if key != 'content'}


def list_knowledge(root):
    profiles = []
    for path in (Path(root) / 'application-knowledge').glob('*.json'):
        try:
            value = read_knowledge(root, path.stem)
        except (OSError, ValueError):
            continue
        if value is not None:
            profiles.append(knowledge_header(value))
    return sorted(profiles, key=lambda item: item['application'].casefold())


def validate_content(content):
    if not isinstance(content, str) or not 1 <= len(content.strip()) <= KNOWLEDGE_LIMIT:
        raise ValueError(f'底层知识正文须为 1–{KNOWLEDGE_LIMIT} 字')
    return content.strip()


def save_knowledge(root, *, scope, application, content, expected_sha256):
    identifier = knowledge_id(scope, application)
    path = knowledge_path(root, identifier)
    content = validate_content(content)
    previous = read_knowledge(root, identifier)
    if previous and previous['content'] == content:
        return previous
    if expected_sha256 != (previous or {}).get('sha256', ''):
        raise ValueError('这份底层知识已更新，请重新打开并核对当前内容')
    value = {'id': identifier, 'scope': scope, 'application': application.strip(), 'content': content,
             'sha256': hashlib.sha256(content.encode()).hexdigest(), 'revision': (previous or {}).get('revision', 0) + 1}
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(f'.{uuid4().hex}.tmp')
    try:
        pending.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)
    return value


def knowledge_prompt(value):
    if not value:
        return ''
    return ('\n\nApplication knowledge for ' + value['application'] +
            ' (shared UI and mechanics; use only where relevant to the task and current screen):\n'
            + value['content'])
