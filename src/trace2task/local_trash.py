"""Recoverable local assets, with explicit restore and no automatic emptying.

The original asset is never merged into a conflicting destination. Windows copy
fallbacks are checked before removing originals; pending cleanup is allowed only
when the surviving source files still match the retained copy.
"""
import hashlib
import json
import re
import shutil
import stat
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

META = '.trace2task-trash.json'
MARKER = '.trace2task-deleted.json'
KINDS = {
    'recording': ('runs/.trash/recordings', 'runs'),
    'trace_experience': ('trace-library/.trash', 'trace-library'),
    'taskpack': ('taskpacks/.trash', 'taskpacks'),
    'candidate': ('runs/.trash/candidates', 'runs/candidates'),
    'human_guidance': ('taskpacks/.trash/guidance', 'taskpacks'),
}


def _now():
    return datetime.now(UTC).isoformat()


def _linked(path):
    info = path.lstat()
    return path.is_symlink() or bool(getattr(info, 'st_file_attributes', 0) &
                                    getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0))


def _files(directory):
    """Reject link/junction traversal before copy, restore, cleanup, or purge."""
    if _linked(directory):
        raise ValueError('资产目录不能是符号链接或目录联接')
    result = []
    for path in directory.rglob('*'):
        if _linked(path) or not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError('资产包含外部链接，未移动或删除任何内容')
        if path.is_file() and not (path.parent == directory and path.name in {META, MARKER}):
            result.append(path)
    return result


def _hashes(directory):
    result = {}
    for path in _files(directory):
        with path.open('rb') as stream:
            result[path.relative_to(directory).as_posix()] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return result


def _write(path, value):
    temporary = path.with_name('.trash-write-' + uuid4().hex)
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _read(path):
    if _linked(path) or path.resolve() != path:
        raise ValueError('回收站元数据不能指向外部文件')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise TypeError('回收站元数据必须为对象')
    return value


def _asset_name(directory, kind, fallback):
    # Read small display metadata only, never full traces or image payloads.
    candidates = {'recording': [('trace2task.json', 'task_id'), ('metadata.json', 'task_id')],
                  'trace_experience': [('header.json', 'trace_name')]}
    for filename, key in candidates.get(kind, []):
        path = directory / filename
        try:
            if path.stat().st_size > 1024 * 1024:
                continue
            name = _read(path).get(key)
            if isinstance(name, str) and name.strip():
                return name.strip()[:512]
        except (OSError, ValueError, TypeError):
            continue
    return fallback


class LocalTrash:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def _path(self, raw):
        if not isinstance(raw, str) or not raw or Path(raw).is_absolute() or '..' in Path(raw).parts:
            raise ValueError('无效的回收站路径')
        path = self.root / raw
        if path.resolve() != path or not path.resolve().is_relative_to(self.root):
            raise ValueError('回收站路径不能包含外部链接')
        return path

    def _entry_path(self, raw):
        path = self._path(raw)
        kind = next((kind for kind, (trash, _) in KINDS.items()
                     if path.parent == self.root / trash), None)
        if not kind or not path.is_dir() or path.name == 'guidance':
            raise ValueError('请选择回收站中的单条资产，不能选择整个目录')
        return path, kind

    def _original(self, raw, kind):
        path = self._path(raw)
        allowed = self.root / KINDS[kind][1]
        if path == allowed or not path.is_relative_to(allowed) or '.trash' in path.parts:
            raise ValueError('原位置不在对应资产目录中')
        if kind in {'recording', 'trace_experience', 'candidate'} and path.parent != allowed:
            raise ValueError('原位置必须是对应资产目录的直接子项')
        return path

    def describe(self, raw):
        directory, kind = self._entry_path(raw)
        metadata_path = directory / META
        legacy = not metadata_path.is_file()
        metadata = _read(metadata_path) if not legacy else {}
        if not isinstance(metadata, dict) or (metadata and metadata.get('kind') != kind):
            raise ValueError('回收站记录类型与目录不一致')
        original = metadata.get('original_path', '')
        reason = ''
        if legacy:
            match = re.fullmatch(r'(\d{8}-\d{6}-\d{6})-(.+)', directory.name)
            if kind == 'human_guidance':
                restore = _read(directory / 'restore.json')
                original = restore.get('task_path', '')
                reason = '旧人工反馈归档未记录任务版本，请打开归档人工核对后恢复'
            elif match and kind != 'taskpack':
                original = f'{KINDS[kind][1]}/{match.group(2)}'
            else:
                reason = '旧归档未记录完整原位置，请打开归档人工恢复'
        source = self._original(original, kind) if original else None
        pending = False
        if source and source.exists():
            marker = source / MARKER
            if marker.is_file():
                pending = _read(marker).get('trash_path') == raw
            if not pending and kind != 'human_guidance':
                reason = '原位置已有内容；为避免覆盖，请先处理同名资产'
        if kind == 'human_guidance' and not legacy:
            restore = _read(directory / 'restore.json')
            task = self._original(restore.get('task_path', ''), kind)
            if task != source:
                raise ValueError('人工反馈归档中的原任务位置不一致')
            if not task.is_file():
                reason = '原任务不存在，请先恢复任务包'
            elif hashlib.sha256(task.read_bytes()).hexdigest() != restore.get('task_without_guidance_sha256'):
                reason = '原任务在移除反馈后已变化，不能自动启用旧反馈'
        name = metadata.get('name') or _asset_name(directory, kind, source.name if source else directory.name)
        delete_reason = ('原位置仍有待清理副本，请先关闭占用程序并恢复或重启工作台' if pending else
                         '旧归档的原位置未知，请人工核对后处理' if not source else '')
        return {'path': raw, 'kind': kind, 'name': name, 'original_path': original,
                'deleted_at': metadata.get('deleted_at'), 'legacy': legacy,
                'file_count': metadata.get('file_count'), 'bytes': metadata.get('bytes'),
                'pending_cleanup': pending, 'can_restore': bool(source) and not reason,
                'restore_blocked_reason': reason, 'can_delete': not delete_reason,
                'delete_blocked_reason': delete_reason}

    def list(self):
        entries = []
        for trash, _ in KINDS.values():
            try:
                directory = self._path(trash)
                paths = sorted(directory.iterdir(), reverse=True) if directory.is_dir() else []
            except (OSError, ValueError):
                entries.append({'path': trash, 'name': trash, 'kind': 'unknown',
                    'can_restore': False, 'can_delete': False, 'can_open': False,
                    'restore_blocked_reason': '回收站目录不可读或包含链接；内容保留，请人工检查'})
                continue
            for path in paths:
                if not path.is_dir() or path.name == 'guidance':
                    continue
                raw = path.relative_to(self.root).as_posix()
                try:
                    entries.append(self.describe(raw))
                except (OSError, ValueError, TypeError, KeyError):
                    entries.append({'path': raw, 'name': path.name, 'kind': 'unknown',
                        'can_restore': False, 'can_delete': False, 'can_open': False,
                        'restore_blocked_reason': '归档元数据不完整或路径异常；内容保留，请人工检查'})
        return sorted(entries, key=lambda item: item.get('deleted_at') or item['path'].rsplit('/', 1)[-1], reverse=True)

    def move(self, target, kind):
        target = self._original(Path(target).relative_to(self.root).as_posix(), kind)
        if not target.is_dir() or (target / MARKER).exists() or (target / META).exists():
            raise ValueError('资产不存在或已移入回收站')
        files = _files(target)
        trash = self._path(KINDS[kind][0])
        trash.mkdir(parents=True, exist_ok=True)
        destination = trash / (datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f') + '-' + target.name)
        raw = destination.relative_to(self.root).as_posix()
        metadata = {'schema_version': 1, 'kind': kind, 'name': _asset_name(target, kind, target.name),
                    'original_path': target.relative_to(self.root).as_posix(), 'deleted_at': _now(),
                    'file_count': len(files), 'bytes': sum(p.stat().st_size for p in files)}
        try:
            target.replace(destination)
        except PermissionError:
            try:
                shutil.copytree(target, destination)
                hashes = _hashes(target)
                if _hashes(destination) != hashes:
                    raise OSError('回收站副本校验失败，原始数据未删除')
                metadata['copy_sha256'] = hashes
                _write(destination / META, metadata)
                _write(target / MARKER, {'trash_path': raw, 'created_at': _now()})
            except Exception:
                if destination.exists():
                    shutil.rmtree(destination, ignore_errors=True)
                raise
            pending = not self._cleanup_source(target, destination)
        else:
            try:
                _write(destination / META, metadata)
            except Exception:
                destination.replace(target)
                raise
            pending = False
        return {'deleted': True, 'kind': kind, 'trash_path': raw, 'recoverable': True,
                'pending_cleanup': pending}

    def _cleanup_source(self, source, archive):
        """Never trust a marker alone as authorization to discard source data."""
        raw = archive.relative_to(self.root).as_posix()
        entry = self.describe(raw)
        if source != self._original(entry['original_path'], entry['kind']) or not entry['pending_cleanup']:
            return False
        try:
            archived = _hashes(archive)
            metadata = _read(archive / META) if (archive / META).is_file() else {}
            if metadata.get('copy_sha256', archived) != archived:
                return False
            if any(archived.get(name) != digest for name, digest in _hashes(source).items()):
                return False
            shutil.rmtree(source)
            return True
        except (OSError, ValueError, TypeError):
            if source.is_dir():
                _write(source / MARKER, {'trash_path': raw, 'created_at': _now()})
            return False

    def cleanup_pending(self):
        for original in dict.fromkeys(original for _, original in KINDS.values()):
            try:
                directory = self._path(original)
                # Materialize before removals, including nested task directories.
                markers = list(directory.rglob(MARKER)) if directory.is_dir() else []
            except (OSError, ValueError):
                continue
            for marker in markers:
                if '.trash' in marker.parts or not marker.is_file():
                    continue
                try:
                    raw = _read(marker).get('trash_path')
                    archive, _ = self._entry_path(raw)
                    self._cleanup_source(marker.parent, archive)
                except (OSError, ValueError, TypeError, KeyError):
                    continue

    def restore(self, raw):
        archive, kind = self._entry_path(raw)
        entry = self.describe(raw)
        if not entry['can_restore']:
            raise ValueError(entry['restore_blocked_reason'])
        if kind == 'human_guidance':
            return self._restore_guidance(archive, entry)
        source = self._original(entry['original_path'], kind)
        _files(archive)
        if entry['pending_cleanup'] and not self._cleanup_source(source, archive):
            raise RuntimeError('原位置仍被占用或内容已变化；两个副本均保留，未覆盖任何文件')
        if source.exists():
            raise FileExistsError('原位置已有内容，未覆盖')
        source.parent.mkdir(parents=True, exist_ok=True)
        archive.replace(source)
        (source / META).unlink(missing_ok=True)
        (source / MARKER).unlink(missing_ok=True)
        return {'restored': True, 'kind': kind, 'original_path': entry['original_path']}

    def register_guidance(self, directory, task_path):
        files = _files(directory)
        _write(directory / META, {'schema_version': 1, 'kind': 'human_guidance',
            'name': task_path.parent.name + ' · 人工反馈', 'deleted_at': _now(),
            'original_path': task_path.relative_to(self.root).as_posix(),
            'file_count': len(files), 'bytes': sum(p.stat().st_size for p in files)})

    def _restore_guidance(self, archive, entry):
        import yaml

        from trace2task.windows_task import load_windows_task

        restore = _read(archive / 'restore.json')
        task_path = self._original(restore['task_path'], 'human_guidance')
        before = task_path.read_bytes()
        if hashlib.sha256(before).hexdigest() != restore['task_without_guidance_sha256']:
            raise ValueError('任务已变化，不能自动启用旧反馈')
        task = yaml.safe_load(before)
        if not isinstance(task, dict) or task.get('human_guidance') is not None:
            raise ValueError('原任务已有人工反馈，未覆盖')
        pointer = restore['guidance_pointer']
        if not isinstance(pointer, dict):
            raise TypeError('人工反馈指针无效')
        raw_guidance = pointer.get('path')
        if not isinstance(raw_guidance, str) or not raw_guidance or Path(raw_guidance).is_absolute():
            raise ValueError('人工反馈恢复路径无效')
        target = self._path((task_path.parent / raw_guidance).relative_to(self.root).as_posix())
        if not target.is_relative_to(task_path.parent):
            raise ValueError('人工反馈恢复路径超出任务目录')
        files = _files(archive)
        if any(p.relative_to(archive).parts[0] not in {target.name, 'guidance-revisions', 'restore.json'} for p in files):
            raise ValueError('人工反馈归档包含额外文件，请人工核对；所有文件保留')
        pairs = [(archive / target.name, target)]
        if (archive / 'guidance-revisions').is_dir():
            pairs.append((archive / 'guidance-revisions', task_path.parent / 'guidance-revisions'))
        if any(destination.exists() for _, destination in pairs):
            raise FileExistsError('原位置已有反馈文件，未覆盖')
        moved = []
        temporary = task_path.with_name('.trash-restore-' + uuid4().hex)
        try:
            for source, destination in pairs:
                destination.parent.mkdir(parents=True, exist_ok=True)
                source.replace(destination)
                moved.append((source, destination))
            task['human_guidance'] = pointer
            temporary.write_bytes(yaml.safe_dump(task, sort_keys=False, allow_unicode=True, width=100).encode('utf-8'))
            temporary.replace(task_path)
            load_windows_task(task_path)
        except Exception:
            temporary.write_bytes(before)
            temporary.replace(task_path)
            for source, destination in reversed(moved):
                destination.replace(source)
            raise
        finally:
            temporary.unlink(missing_ok=True)
        shutil.rmtree(archive)
        return {'restored': True, 'kind': 'human_guidance', 'original_path': entry['original_path']}

    def purge(self, raw, *, confirmed=False):
        if confirmed is not True:
            raise ValueError('永久删除必须再次明确确认')
        archive, kind = self._entry_path(raw)
        entry = self.describe(raw)
        if not entry['can_delete']:
            raise ValueError(entry['delete_blocked_reason'])
        _files(archive)
        shutil.rmtree(archive)
        return {'deleted': True, 'recoverable': False, 'kind': kind, 'trash_path': raw}
