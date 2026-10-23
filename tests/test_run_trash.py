"""Database recycle lifecycle; all fixtures live under temporary roots."""
from types import SimpleNamespace

import pytest

from trace2task.run_session import RunStore
from trace2task.web_console import WebConsoleController


def saved(store, key='run-1', status='completed'):
    store.save({'job_id': key, 'status': status, 'updated_at': '2026-10-08', 'instruction': key})
    store.round(key, {'step_index': 0, 'status': 'completed', 'input': 'full original input'})


def test_recycle_restart_restore_preserves_every_round_and_event(tmp_path):
    store = RunStore(tmp_path)
    saved(store)
    before = store.get('run-1')
    store.trash_action('run-1', 'move')
    assert store.list() == []
    assert store.trash_list()[0]['kind'] == 'run'
    store.db.close()
    reopened = RunStore(tmp_path)
    assert reopened.list() == []
    assert reopened.get('run-1') == before
    assert reopened.read_round('run-1', 0)['input'] == 'full original input'
    reopened.trash_action('run-1', 'restore')
    assert reopened.list()[0]['job_id'] == 'run-1'
    assert reopened.trash_list() == []


@pytest.mark.parametrize('status', ['queued', 'running', 'stopping', 'awaiting_recording_start', 'awaiting_narration'])
def test_active_records_are_protected(tmp_path, status):
    store = RunStore(tmp_path)
    saved(store, status=status)
    with pytest.raises(ValueError, match='不能移入'):
        store.trash_action('run-1', 'move')
    assert not store.trash_list()


def test_purge_requires_trashed_record_and_strict_confirmation(tmp_path):
    store = RunStore(tmp_path)
    saved(store)
    saved(store, 'run-2')
    linked = tmp_path / 'runs' / 'source.txt'
    linked.write_text('untouched', encoding='utf-8')
    with pytest.raises(ValueError, match='不在回收站'):
        store.trash_action('run-1', 'purge', confirmed=True)
    store.trash_action('run-1', 'move')
    with pytest.raises(ValueError, match='已在回收站'):
        store.trash_action('run-1', 'move')
    for confirmation in (False, 'true', 1):
        with pytest.raises(ValueError, match='明确确认'):
            store.trash_action('run-1', 'purge', confirmed=confirmation)
    store.trash_action('run-1', 'purge', confirmed=True)
    assert not store.trash_list()
    for table, key in [('events', 'run_id'), ('rounds', 'run_id'), ('sessions', 'id')]:
        assert not store.db.execute(f'SELECT 1 FROM {table} WHERE {key}=?', ('run-1',)).fetchall()
    assert store.read_round('run-2', 0)['input'] == 'full original input'
    assert linked.read_text() == 'untouched'
    with pytest.raises(ValueError):
        store.trash_action('../outside', 'move')


def test_controller_recycle_and_purge_clear_completed_active_pointer(tmp_path):
    controller = WebConsoleController(tmp_path)
    saved(controller.run_store)
    controller._active_job_id = 'run-1'
    controller._jobs['run-1'] = SimpleNamespace(status='completed')
    controller.delete_run_record('run-1')
    controller.restore_trashed_asset('workbench-run/run-1')
    controller.delete_run_record('run-1')
    controller.purge_trashed_asset('workbench-run/run-1', confirmed=True)
    assert controller.active_job() is None
    assert controller.workbench_runs()['runs'] == []


def test_http_run_delete_is_listed_in_global_trash_and_restored(tmp_path):
    import json
    import re
    import threading
    from urllib.request import ProxyHandler, Request, build_opener

    from trace2task.web_console import create_web_server

    controller = WebConsoleController(tmp_path)
    saved(controller.run_store)
    server = create_web_server(tmp_path, port=0, controller=controller)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(base + '/', timeout=5) as response:
            token = re.search(r'name="trace2task-csrf" content="([^"]+)"', response.read().decode()).group(1)

        def post(path, body):
            with opener.open(Request(base + path, data=json.dumps(body).encode(), headers={
                'Content-Type': 'application/json', 'Origin': base, 'X-Trace2Task-CSRF': token}), timeout=5) as response:
                return json.load(response)

        post('/api/workbench/runs/delete', {'id': 'run-1'})
        with opener.open(base + '/api/state?compact=1', timeout=5) as response:
            assert json.load(response)['trash'][0]['path'] == 'workbench-run/run-1'
        with opener.open(base + '/api/workbench/runs', timeout=5) as response:
            assert json.load(response)['runs'] == []
        post('/api/trash/restore', {'path': 'workbench-run/run-1'})
        assert controller.run_store.list()[0]['job_id'] == 'run-1'
        assert controller.run_store.read_round('run-1', 0)['input'] == 'full original input'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
