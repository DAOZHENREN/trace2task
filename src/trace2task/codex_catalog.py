"""Picker metadata from the same installed Codex backend used for inference."""
from __future__ import annotations

import re
import threading
import time
from copy import deepcopy
from pathlib import Path

from trace2task.codex_app_server import CODEX_REASONING_EFFORTS, CodexAppServerSession


def validate_codex_choice(model, reasoning_effort):
    """Validate wire values; model availability is owned by Codex, not a local list."""
    if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}', model):
        raise ValueError('无效的 Codex 模型 ID')
    if reasoning_effort not in CODEX_REASONING_EFFORTS:
        raise ValueError(f'不支持的思考强度：{reasoning_effort}')


def read_codex_models(root):
    from trace2task.codex_agent import resolve_codex_binary
    session = CodexAppServerSession(resolve_codex_binary(), model=None, cwd=Path(root), timeout_seconds=12)
    try:
        return session.list_models()
    finally:
        session.close()


def normalize_models(rows):
    models, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError('Codex 模型目录条目格式无效')
        model = row.get('model')
        if row.get('hidden') or not isinstance(model, str) or not model or model in seen:
            continue
        efforts = [item['reasoningEffort'] for item in row.get('supportedReasoningEfforts', [])
                   if isinstance(item, dict) and isinstance(item.get('reasoningEffort'), str)]
        efforts = list(dict.fromkeys(efforts))
        default = row.get('defaultReasoningEffort')
        if default not in efforts:
            default = efforts[0] if efforts else ''
        models.append({'model': model, 'display_name': row.get('displayName') or model,
                       'description': row.get('description') or '', 'reasoning_efforts': efforts,
                       'default_reasoning_effort': default, 'is_default': row.get('isDefault') is True,
                       'input_modalities': row.get('inputModalities') if isinstance(row.get('inputModalities'), list)
                                           else ['text', 'image']})
        seen.add(model)
    if not models:
        raise ValueError('Codex 未返回可显示的模型，请检查登录状态和客户端版本。')
    return models


class CodexModelCatalog:
    def __init__(self, root, *, loader=None, ttl=300):
        self.root, self.loader, self.ttl = root, loader or read_codex_models, ttl
        self._lock = threading.Lock()
        self._models, self._error, self._expires = [], '', 0

    def options(self, *, refresh=False):
        with self._lock:
            if refresh or time.monotonic() >= self._expires:
                try:
                    self._models = normalize_models(self.loader(self.root))
                    self._error = ''
                except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
                    # Do not substitute an invented list or expose transport stderr/credentials.
                    self._error = f'无法读取 Codex 模型目录（{type(error).__name__}），请检查 Codex 登录和连接后刷新。'
                self._expires = time.monotonic() + (15 if self._error else self.ttl)
            models = deepcopy(self._models)
            usable = [m for m in models if 'image' in m['input_modalities'] and m['reasoning_efforts']]
            default = next((m for m in usable if m['is_default']), usable[0] if usable else None)
            choice = {'model': default['model'] if default else '',
                      'reasoning_effort': default['default_reasoning_effort'] if default else ''}
            return {'models': [m['model'] for m in models], 'model_details': models,
                    'reasoning_efforts': list(dict.fromkeys(e for m in models for e in m['reasoning_efforts'])),
                    'defaults': choice, 'compiler_defaults': dict(choice), 'revision_defaults': dict(choice),
                    'task_model_revision_defaults': dict(choice),
                    'catalog_status': 'stale' if self._error and models else 'unavailable' if self._error else 'ready',
                    'catalog_error': self._error}

    def validate(self, model, effort):
        validate_codex_choice(model, effort)
        options = self.options()
        if options['catalog_error']:
            raise ValueError(options['catalog_error'])
        item = next((m for m in options['model_details'] if m['model'] == model), None)
        if item is None:
            raise ValueError(f'不支持的模型：{model}；当前 Codex 目录不包含该模型，请刷新后重新选择。')
        if 'image' not in item['input_modalities']:
            raise ValueError(f'{model} 不支持本任务需要的截图输入。')
        if effort not in item['reasoning_efforts']:
            raise ValueError(f'{model} 不支持思考强度 {effort}；可选：' + ', '.join(item['reasoning_efforts']))
