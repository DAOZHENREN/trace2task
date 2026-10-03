"""Trash lifecycle and destructive-operation boundaries, using only temp assets."""
import json
from pathlib import Path

import pytest

from trace2task import local_trash
from trace2task.local_trash import KINDS, MARKER, META, LocalTrash


def asset(root, relative='runs/demo'):
    directory = root / relative
    directory.mkdir(parents=True)
    (directory / 'evidence').mkdir()
    (directory / 'data.json').write_bytes(b'original task data')
    (directory / 'evidence/frame.png').write_bytes(b'original screenshot')
    return directory


def lock_source(monkeypatch, source, *, partially_remove=False):
    replace, rmtree = Path.replace, local_trash.shutil.rmtree

    def locked_replace(path, destination):
        if path == source:
            raise PermissionError('test directory lock')
        return replace(path, destination)

    def locked_rmtree(path, *args, **kwargs):
        if Path(path) == source:
            if partially_remove:
                (source / 'data.json').unlink(missing_ok=True)
                (source / MARKER).unlink(missing_ok=True)
            raise PermissionError('test directory lock')
        return rmtree(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'replace', locked_replace)
    monkeypatch.setattr(local_trash.shutil, 'rmtree', locked_rmtree)


@pytest.mark.parametrize('kind,relative', [
    ('recording', 'runs/demo'), ('trace_experience', 'trace-library/trace-D'),
    ('taskpack', 'taskpacks/nested/task'), ('candidate', 'runs/candidates/feedback'),
])
def test_move_restore_and_explicit_permanent_delete(tmp_path, kind, relative):
    source = asset(tmp_path, relative)
    trash = LocalTrash(tmp_path)
    result = trash.move(source, kind)
    assert not source.exists()
    entry, = trash.list()
    assert entry['path'] == result['trash_path']
    assert entry['original_path'] == relative
    assert entry['file_count'] == 2
    assert entry['bytes'] == len(b'original task dataoriginal screenshot')
    assert entry['can_restore'] and entry['can_delete']
    with pytest.raises(ValueError, match='确认'):
        trash.purge(entry['path'], confirmed=1)
    assert trash.restore(entry['path'])['restored']
    assert (source / 'data.json').read_bytes() == b'original task data'
    assert (source / 'evidence/frame.png').read_bytes() == b'original screenshot'
    assert not (source / META).exists()
    assert trash.list() == []
    moved = trash.move(source, kind)
    assert not trash.purge(moved['trash_path'], confirmed=True)['recoverable']
    assert trash.list() == []


def test_restore_conflict_does_not_merge_or_overwrite(tmp_path):
    source = asset(tmp_path)
    trash = LocalTrash(tmp_path)
    raw = trash.move(source, 'recording')['trash_path']
    source.mkdir()
    (source / 'data.json').write_bytes(b'newer task data')
    assert not trash.describe(raw)['can_restore']
    with pytest.raises(ValueError, match='已有内容'):
        trash.restore(raw)
    assert (source / 'data.json').read_bytes() == b'newer task data'
    assert (tmp_path / raw / 'data.json').read_bytes() == b'original task data'


def test_recordings_keep_display_name_and_original_id_including_legacy_archives(tmp_path):
    source = asset(tmp_path)
    (source / 'trace2task.json').write_text(json.dumps({'task_id': 'Named recording'}))
    trash = LocalTrash(tmp_path)
    raw = trash.move(source, 'recording')['trash_path']
    assert trash.describe(raw)['name'] == 'Named recording'
    (tmp_path / raw / META).unlink()  # Simulate a pre-metadata archive.
    assert trash.describe(raw)['name'] == 'Named recording'
    assert trash.restore(raw)['original_path'] == 'runs/demo'


@pytest.mark.parametrize('raw', ['', '.', '..', 'runs', 'runs/.trash/recordings',
    'taskpacks/.trash/guidance', 'runs/demo', 'runs/.trash/recordings/../../demo'])
def test_operations_never_accept_roots_active_assets_or_traversal(tmp_path, raw):
    source = asset(tmp_path)
    trash = LocalTrash(tmp_path)
    for action in (trash.restore, lambda value: trash.purge(value, confirmed=True)):
        with pytest.raises(ValueError):
            action(raw)
    assert (source / 'data.json').is_file()


@pytest.mark.parametrize('partial', [False, True])
def test_locked_source_is_hashed_hidden_and_restorable_after_unlock(tmp_path, monkeypatch, partial):
    source = asset(tmp_path)
    trash = LocalTrash(tmp_path)
    with monkeypatch.context() as patch:
        lock_source(patch, source, partially_remove=partial)
        moved = trash.move(source, 'recording')
        assert moved['pending_cleanup']
        assert (source / MARKER).is_file()
        assert not trash.describe(moved['trash_path'])['can_delete']
        with pytest.raises(ValueError, match='待清理'):
            trash.purge(moved['trash_path'], confirmed=True)
        with pytest.raises(RuntimeError, match='两个副本均保留'):
            trash.restore(moved['trash_path'])
    trash.restore(moved['trash_path'])
    assert (source / 'data.json').read_bytes() == b'original task data'
    assert (source / 'evidence/frame.png').read_bytes() == b'original screenshot'
    assert not (source / MARKER).exists()
    assert not trash.list()


@pytest.mark.parametrize('changed', ['source', 'archive', 'new_source_file'])
def test_pending_cleanup_refuses_changed_or_corrupted_data(tmp_path, monkeypatch, changed):
    source = asset(tmp_path)
    trash = LocalTrash(tmp_path)
    with monkeypatch.context() as patch:
        lock_source(patch, source)
        moved = trash.move(source, 'recording')
    archive = tmp_path / moved['trash_path']
    target = archive if changed == 'archive' else source
    (target / ('new.txt' if changed == 'new_source_file' else 'data.json')).write_bytes(b'changed')
    trash.cleanup_pending()
    assert source.is_dir() and archive.is_dir()
    with pytest.raises(RuntimeError, match='两个副本均保留'):
        trash.restore(moved['trash_path'])
    assert (target / ('new.txt' if changed == 'new_source_file' else 'data.json')).read_bytes() == b'changed'


def test_failed_copy_keeps_original_unmarked(tmp_path, monkeypatch):
    source = asset(tmp_path)
    trash = LocalTrash(tmp_path)
    lock_source(monkeypatch, source)
    copytree = local_trash.shutil.copytree

    def corrupt_copy(src, dst, *args, **kwargs):
        result = copytree(src, dst, *args, **kwargs)
        if Path(src) == source:
            (Path(dst) / 'data.json').write_bytes(b'incomplete copy')
        return result

    monkeypatch.setattr(local_trash.shutil, 'copytree', corrupt_copy)
    with pytest.raises(OSError, match='副本校验失败'):
        trash.move(source, 'recording')
    assert (source / 'data.json').read_bytes() == b'original task data'
    assert not (source / MARKER).exists()
    assert not trash.list()


def test_failed_metadata_write_rolls_back_atomic_move(tmp_path, monkeypatch):
    source = asset(tmp_path)

    def fail(*args):
        raise OSError('metadata disk error')

    monkeypatch.setattr(local_trash, '_write', fail)
    with pytest.raises(OSError, match='metadata disk error'):
        LocalTrash(tmp_path).move(source, 'recording')
    assert (source / 'data.json').read_bytes() == b'original task data'
    assert not LocalTrash(tmp_path).list()


@pytest.mark.parametrize('marker', [{'trash_path': '../../outside'}, [], {'trash_path': 'runs/demo'}])
def test_invalid_markers_do_not_authorize_removal(tmp_path, marker):
    source = asset(tmp_path)
    (source / MARKER).write_text(json.dumps(marker))
    LocalTrash(tmp_path).cleanup_pending()
    assert (source / 'data.json').read_bytes() == b'original task data'


def test_legacy_recording_can_restore_but_unknown_task_location_stays_protected(tmp_path):
    legacy = 'runs/.trash/recordings/20261002-180404-251614-old-demo'
    asset(tmp_path, legacy)
    unknown = 'taskpacks/.trash/20261002-180404-251614-old-task'
    asset(tmp_path, unknown)
    trash = LocalTrash(tmp_path)
    assert trash.describe(legacy)['legacy']
    assert trash.restore(legacy)['original_path'] == 'runs/old-demo'
    assert (tmp_path / 'runs/old-demo/data.json').is_file()
    assert not trash.describe(unknown)['can_restore']
    assert not trash.describe(unknown)['can_delete']


def test_malformed_archive_is_visible_but_not_actionable(tmp_path):
    directory = asset(tmp_path, 'trace-library/.trash/broken')
    (directory / META).write_text('[]')
    entry, = LocalTrash(tmp_path).list()
    assert not entry['can_restore'] and not entry['can_delete'] and not entry['can_open']
    assert (directory / 'data.json').is_file()


def test_reparse_points_block_traversal_without_needing_symlink_privilege(tmp_path, monkeypatch):
    source = asset(tmp_path)
    linked = source / 'evidence'
    original = local_trash._linked
    monkeypatch.setattr(local_trash, '_linked', lambda path: path == linked or original(path))
    with pytest.raises(ValueError, match='链接'):
        LocalTrash(tmp_path).move(source, 'recording')
    assert (source / 'data.json').is_file()


def test_symlinks_are_not_followed_for_move_list_restore_or_purge(tmp_path):
    outside = asset(tmp_path, 'protected')
    link = tmp_path / 'runs/link'
    link.parent.mkdir()
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f'Symlink creation unavailable: {error}')
    trash = LocalTrash(tmp_path)
    with pytest.raises(ValueError, match='链接'):
        trash.move(link, 'recording')
    entry = tmp_path / KINDS['recording'][0] / 'linked'
    entry.parent.mkdir(parents=True)
    entry.symlink_to(outside, target_is_directory=True)
    raw = entry.relative_to(tmp_path).as_posix()
    assert not trash.list()[0]['can_delete']
    with pytest.raises(ValueError, match='链接'):
        trash.purge(raw, confirmed=True)
    with pytest.raises(ValueError, match='链接'):
        trash.restore(raw)
    assert (outside / 'data.json').read_bytes() == b'original task data'
