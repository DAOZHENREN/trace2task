"""Persistent current-version selection, separate from immutable published bundles."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile

from trace2task.trace_library import list_traces, read_body, read_model_input

_LOCK = threading.RLock()


def _path(root, name):
    library = Path(root).resolve() / 'trace-library'
    path = library / name
    if path.resolve().parent != library:
        raise ValueError('版本记录不能指向经验库外部')
    return path


@contextmanager
def _locked(root):
    # A second workbench process must not overwrite a concurrent publication/rollback.
    with _LOCK:
        path = _path(root, '.versions.lock')
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a+b') as handle:
            if handle.tell() == 0:
                handle.write(b'0')
                handle.flush()
            deadline = time.monotonic() + 5
            while True:
                try:
                    handle.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise ValueError('版本记录正在更新，请稍后重试') from None
                    time.sleep(.05)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read(root):
    path = _path(root, '.versions.json')
    if not path.exists():
        return {'schema': 1, 'revision': 0, 'versions': {}}
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
        if (state['schema'] != 1 or type(state['revision']) is not int or state['revision'] < 0
                or not isinstance(state['versions'], dict)):
            raise ValueError('Invalid version index')
        for identifier, item in state['versions'].items():
            if (not isinstance(identifier, str) or not identifier
                    or not isinstance(item['keys'], list) or not item['keys']
                    or not all(isinstance(k, str) for k in item['keys'])
                    or type(item['registered']) is not int or not 0 < item['registered'] <= state['revision']
                    or type(item['selected']) is not int or not 0 <= item['selected'] <= state['revision']):
                raise ValueError('Invalid version record')
        return state
    except (KeyError, TypeError, ValueError):
        raise ValueError('当前版本记录损坏；已停止自动选择，请恢复版本记录后重试') from None


def _write(root, state):
    path = _path(root, '.versions.json')
    pending = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                prefix='.versions-', suffix='.tmp', delete=False) as handle:
            pending = Path(handle.name)
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(pending, path)
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


def _keys(root, entry):
    # Do not merge by display name. Scope and representation remain independent.
    metadata = dict(entry)
    if not metadata.get('source_recording') and not metadata.get('source_run_id'):
        manifest = Path(root) / 'trace-library' / entry['id'] / 'manifest.json'
        if manifest.resolve().parent == manifest.parent:
            try:
                report = json.loads(manifest.read_text(encoding='utf-8'))
                metadata = {**report, **metadata}
                if not metadata.get('source_recording'):
                    sources = {str(Path(p).parent) for p in report.get('source_sha256', {})
                               if Path(p).name == 'trace2task.json'}
                    if len(sources) == 1:
                        metadata['source_recording'] = sources.pop()
            except (OSError, ValueError, TypeError, AttributeError):
                pass
    scope = metadata.get('source_scope', 'desktop')
    if not isinstance(scope, str):
        scope = 'unknown'
    if scope in {'desktop', 'selected_windows', 'desktop_recording'}:
        scope = 'desktop'
    prefix = json.dumps([entry['representation'], scope, metadata.get('source_package', '')])
    keys = ['id:' + entry['id']]
    versioning = metadata.get('versioning')
    if versioning is not None:
        # Publication membership is distinct from provenance. An independent copy
        # keeps its source lineage, but neither that lineage nor source joins families.
        if not isinstance(versioning, dict) or versioning.get('mode') not in {'independent', 'revision'}:
            raise ValueError('经验的保存方式记录无效')
        if versioning['mode'] == 'revision':
            parent = versioning.get('parent_id')
            if not isinstance(parent, str) or not parent:
                raise ValueError('经验新版本缺少所属版本')
            keys.append('id:' + parent)
        return [prefix + key for key in keys]
    # R candidates retain their separate trial/review lifecycle.
    if entry['representation'] != 'R':
        if isinstance(metadata.get('source_run_id'), str) and metadata['source_run_id']:
            keys.append('run:' + metadata['source_run_id'])
        elif isinstance(metadata.get('source_recording'), str) and metadata['source_recording']:
            keys.append('recording:' + os.path.normcase(str((Path(root) / metadata['source_recording']).resolve())))
        lineage = metadata.get('lineage') or {}
        if isinstance(lineage, dict):
            keys.extend('id:' + lineage[k] for k in ('root_id', 'parent_id') if isinstance(lineage.get(k), str))
    return [prefix + key for key in keys]


def _snapshot(root, *, restored_ids=()):
    state, entries = _read(root), list_traces(root)
    changed = False
    # Header-last publication is the commit boundary. Drafts never enter this list.
    for entry in reversed(entries):
        if entry['id'] not in state['versions']:
            state['revision'] += 1
            state['versions'][entry['id']] = {'keys': _keys(root, entry),
                'registered': state['revision'], 'selected': 0 if entry['id'] in restored_ids else state['revision']}
            changed = True
    if changed:
        _write(root, state)

    # Retain membership even when a version is manually moved to the recycle bin.
    # Removing the current version must never silently resurrect an old one.
    parents, owners = {identifier: identifier for identifier in state['versions']}, {}

    def find(identifier):
        while parents[identifier] != identifier:
            parents[identifier] = parents[parents[identifier]]
            identifier = parents[identifier]
        return identifier

    for identifier, version in state['versions'].items():
        for key in version['keys']:
            if key in owners:
                parents[find(identifier)] = find(owners[key])
            owners[key] = identifier
    groups = {}
    for identifier in parents:
        groups.setdefault(find(identifier), []).append(identifier)
    available = {entry['id'] for entry in entries}
    info = {}
    for members in groups.values():
        family = min(members, key=lambda i: state['versions'][i]['registered'])
        current = max(members, key=lambda i: state['versions'][i]['selected'])
        revision = state['versions'][current]['selected']
        count = len(available.intersection(members))
        for identifier in members:
            info[identifier] = {'family_id': family, 'current_version_id': current if revision else '',
                'is_current': bool(revision and identifier == current), 'version_count': count,
                'version_revision': revision}
    return state, [{**entry, **info[entry['id']]} for entry in entries]


def list_versions(root):
    """List current and historical versions for review; immutable listing stays intact."""
    with _locked(root):
        return _snapshot(root)[1]


def current_version_ids(root):
    return {entry['id'] for entry in list_versions(root) if entry['is_current']}


@contextmanager
def version_publication(root):
    """Bind a publication decision to the current family while writing header last."""
    with _locked(root):
        yield _snapshot(root)[1]
        _snapshot(root)


def restore_version(root, restore):
    """A pre-index trash restore is history, not a newly published revision."""
    with _locked(root):
        _snapshot(root)
        result = restore()
        identifier = Path(result['original_path']).name
        _snapshot(root, restored_ids={identifier})
        return result


def rollback_version(root, identifier, *, expected_revision, model_input_sha256):
    """Select the exact reviewed historical version without editing its artifacts."""
    with _locked(root):
        state, entries = _snapshot(root)
        target = next((entry for entry in entries if entry['id'] == identifier), None)
        if target is None:
            raise ValueError('请选择仍在经验库中的历史版本')
        if type(expected_revision) is not int or expected_revision != target['version_revision']:
            raise ValueError('当前版本已变化，请刷新版本历史并重新确认')
        if target['is_current']:
            raise ValueError('此版本已经是当前版本')
        if target['representation'] not in {'A', 'B', 'C', 'D'}:
            raise ValueError('实验候选不通过版本回滚启用，请使用候选审查流程')
        read_body(root, identifier)
        source = read_model_input(root, identifier)
        actual = hashlib.sha256(source['content'].encode('utf-8')).hexdigest()
        if not model_input_sha256 or model_input_sha256 != actual:
            raise ValueError('历史版本内容已变化，请重新查看原文后确认回滚')
        state['revision'] += 1
        state['versions'][identifier]['selected'] = state['revision']
        _write(root, state)
        return {'id': identifier, 'previous_id': target['current_version_id'],
                'version_revision': state['revision']}
