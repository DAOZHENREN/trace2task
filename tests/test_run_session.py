"""Small contract suite; no GPU, user credentials, or desktop input."""
import json
from types import SimpleNamespace

import pytest

from trace2task.model_registry import MODEL_PROFILES, profile_for
from trace2task.run_session import RunSession, RunStore, operation_scope


def job(**extra):
    return SimpleNamespace(job_id='a' * 32, provider='codex', model='gpt-test', task_path='',
        background=False, execution_scope='desktop', **extra)


def snapshot(**extra):
    return dict(job_id='a' * 32, updated_at='2026-10-02', status='running',
        instruction='Test only', model_io=[{'input': 'huge history'}], logs=['not polled'], **extra)


@pytest.mark.parametrize('key,backend', [(p.id, b) for p in MODEL_PROFILES.values() for b in p.engines])
def test_registered_combinations_have_native_adapter(key, backend):
    assert profile_for(key, backend).adapter


def test_scope_is_not_inferred_from_driver_name(tmp_path):
    desktop = {'kind': 'desktop', 'display_id': 'primary'}
    facts = RunSession.for_job(job(executor_backend='cua', cua_target=desktop), tmp_path).public()
    assert facts['operation_scope'] == 'desktop'
    assert facts['input_mode'] == 'foreground'
    assert facts['data_flow'] == 'remote'
    assert operation_scope('cua', 'desktop', {'targets': []}) == 'selected_windows'


def test_runtime_choice_does_not_silently_fall_back(tmp_path):
    value = job(executor_backend='win32', cua_target=None)
    value.provider, value.model = 'trained_d', 'qwen3-vl-2b'
    with pytest.raises(ValueError, match='服务配置不同'):
        RunSession.for_job(value, tmp_path, requested_backend='transformers')


def test_llama_is_preferred_without_overwriting_explicit_existing_settings(tmp_path):
    from trace2task.local_gui_llama import read_backend

    assert read_backend(tmp_path) == 'llama-server'
    assert not (tmp_path / 'backend.json').exists()
    for profile in MODEL_PROFILES.values():
        if 'llama-server' in profile.engines:
            assert profile.engines[0] == 'llama-server'
    (tmp_path / 'backend.json').write_text('{"backend":"transformers"}')
    assert read_backend(tmp_path) == 'transformers'
    assert MODEL_PROFILES['mai-ui-2b'].engines == ('transformers',)


def test_8b_run_uses_shared_llama_policy_not_the_old_api_route(tmp_path):
    value = job(executor_backend='win32', cua_target=None)
    value.provider, value.model = 'trained_d', 'qwen3-vl-8b-instruct'
    config = tmp_path / 'runs/local-gui/backend.json'
    config.parent.mkdir(parents=True)
    config.write_text('{"backend":"llama-server"}')
    facts = RunSession.for_job(value, tmp_path, requested_backend='llama-server')
    assert facts.profile == value.model
    assert facts.inference_backend == 'llama-server'
    assert facts.context_policy == 'langchain_summary_then_native_image_eviction'
    assert facts.data_flow == 'local'
    assert '32K' in facts.context_description and '24K' in facts.context_description


def test_incremental_events_raw_rounds_and_restart_are_independent(tmp_path):
    store = RunStore(tmp_path)
    store.save(snapshot(run_facts={'model': 'test'}), log='Started')
    entry = {'step_index': 0, 'status': 'pending', 'input': 'FULL_HISTORY' * 1000}
    store.round('a' * 32, entry)
    first = store.get('a' * 32)
    assert 'FULL_HISTORY' not in json.dumps(first)
    assert 'model_io' not in first['session'] and 'logs' not in first['session']
    assert first['events'][-1]['name'] == 'model.round'
    assert store.get('a' * 32, first['cursor'])['events'] == []
    assert store.read_round('a' * 32, 0)['input'] == entry['input']
    entry.update(status='predicted', execution={'executed': True, 'status': 'returned'})
    store.round('a' * 32, entry)
    assert len(store.get('a' * 32, first['cursor'])['events']) == 1
    store.round('a' * 32, entry)  # Duplicate callbacks are idempotent.
    assert len(store.get('a' * 32, first['cursor'])['events']) == 1
    reopened = RunStore(tmp_path)
    assert reopened.get('a' * 32)['session']['status'] == 'interrupted'
    assert reopened.read_round('a' * 32, 0)['execution']['executed'] is True
    with pytest.raises(ValueError):
        store.get('../outside')


def test_controller_journal_survives_restart_and_is_served_over_http(tmp_path, monkeypatch):
    """Real controller/SQLite/HTTP path; no model or desktop action is invoked."""
    import threading
    from urllib.request import ProxyHandler, build_opener

    from trace2task.web_console import WebConsoleController, create_web_server

    controller = WebConsoleController(tmp_path)
    monkeypatch.setattr(controller, '_run_job', lambda *args: None)
    started = controller.start_job(task_path='', instruction='Journal integration fixture',
        execute=False, execution_scope='desktop', use_experience=False)
    key = started['job_id']
    assert controller.run_store.get(key)['session']['status'] == 'queued'
    job = controller._jobs[key]
    controller._update(job, status='running', log='Planning started')
    controller._model_round_update(job, {'step_index': 0, 'status': 'predicted',
        'input': {'messages': [{'text': 'FULL_REQUEST_ONLY_IN_ROUND'}]},
        'raw_output': 'READ_ONLY_TEST', 'execution': {'executed': False}})
    controller._update(job, status='completed', log='No desktop input sent',
        result={'status': 'completed', 'model_io': ['FULL_REQUEST_ONLY_IN_ROUND']})
    controller.run_store.db.close()

    reopened = WebConsoleController(tmp_path)
    assert not reopened._jobs  # Nothing was automatically resumed.
    server = create_web_server(tmp_path, port=0, controller=reopened)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = build_opener(ProxyHandler({}))
    base = f'http://127.0.0.1:{server.server_port}'

    def get(path):
        with opener.open(base + path, timeout=5) as response:
            return json.load(response)

    try:
        assert get('/api/workbench/runs')['runs'][0]['job_id'] == key
        timeline = get(f'/api/workbench/runs/{key}?after=0')
        assert timeline['session']['status'] == 'completed'
        assert 'FULL_REQUEST_ONLY_IN_ROUND' not in json.dumps(timeline)
        assert any(e['name'] == 'model.round' for e in timeline['events'])
        assert any(e['attributes'].get('message') == 'No desktop input sent' for e in timeline['events'])
        assert get(f'/api/workbench/runs/{key}?after={timeline["cursor"]}')['events'] == []
        assert get(f'/api/workbench/runs/{key}/rounds/0')['raw_output'] == 'READ_ONLY_TEST'
        # Recording requests are journaled before the worker, with no page poll.
        monkeypatch.setattr(reopened, '_run_opencua_recording', lambda *args: None)
        recording = reopened.start_recording(handle=0, task_id='Recording fixture',
            execution_scope='desktop', recording_backend='opencua')
        assert get(f'/api/workbench/runs/{recording["job_id"]}')['session']['kind'] == 'recording'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        reopened.run_store.db.close()
