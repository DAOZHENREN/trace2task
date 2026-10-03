import json
import os
from datetime import datetime

import pytest

from trace2task.trace_library import compiled_versions, generate_d, list_traces, read_body


def recording(root, name="demo"):
    run = root / "runs" / name
    derived = run / "derived" / "one"
    derived.mkdir(parents=True)
    (run / "events.jsonl").write_text("", encoding="utf-8")
    (run / "trace2task.json").write_text(json.dumps({
        "source": "opencua_native", "capture_status": "finished", "task_id": "演示",
        "derivation": {"status": "completed", "directory": str(derived)},
    }), encoding="utf-8")
    (run / "metadata.json").write_text(json.dumps({"screen_width": 100, "screen_height": 100}), encoding="utf-8")
    row = {"id": 0, "action": "click", "description": "Single left Click", "start_time": 1, "end_time": 2}
    (derived / "reduced_events_vis.jsonl").write_text(json.dumps(row), encoding="utf-8")
    row["coordinate"] = {"x": 5, "y": 6}
    (derived / "reduced_events_complete.jsonl").write_text(json.dumps(row), encoding="utf-8")
    return f"runs/{name}/events.jsonl"


def test_generate_list_and_lazy_read(tmp_path):
    path = recording(tmp_path)
    original = {p: p.read_bytes() for p in (tmp_path / "runs").rglob("*") if p.is_file()}
    identity = generate_d(tmp_path, path)["id"]
    headers = list_traces(tmp_path)
    assert headers[0]["trace_name"] == "演示"
    assert set(headers[0]) == {"id", "trace_name", "description", "representation", "created_at", "created_at_source"}
    assert datetime.fromisoformat(headers[0]['created_at']).tzinfo is not None
    assert headers[0]['created_at_source'] == 'metadata'
    assert read_body(tmp_path, identity)["actions"][0]["action"] == "Single left Click"
    assert all(p.read_bytes() == data for p, data in original.items())
    body = tmp_path / "trace-library" / identity / "body.json"
    body.write_text("{}", encoding="utf-8")
    assert list_traces(tmp_path) == headers  # Does not read body during listing.
    with pytest.raises(ValueError, match="正文已变化"):
        read_body(tmp_path, identity)


@pytest.mark.parametrize('confirmation', [False, None, 1, 'true'])
def test_duplicate_d_requires_explicit_confirmation_without_writing(tmp_path, confirmation):
    trace = recording(tmp_path)
    first = generate_d(tmp_path, trace)['id']
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    pending = generate_d(tmp_path, trace, confirm_duplicate=confirmation)
    assert pending['confirmation_required'] and pending['representation'] == 'D'
    assert pending['existing_versions'][0]['id'] == first
    assert {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before
    second = generate_d(tmp_path, trace, confirm_duplicate=True)['id']
    assert second != first
    assert all(p.read_bytes() == data for p, data in before.items())
    assert len(compiled_versions(tmp_path, trace, 'D')) == 2


def test_matching_is_by_recording_and_representation_not_display_name(tmp_path):
    first_trace, second_trace = recording(tmp_path), recording(tmp_path, 'another-demo')
    first = generate_d(tmp_path, first_trace)['id']
    second = generate_d(tmp_path, second_trace)['id']
    assert first != second  # The two recordings have the same task_id.
    assert [v['id'] for v in compiled_versions(tmp_path, first_trace, 'D')] == [first]
    assert [v['id'] for v in compiled_versions(tmp_path, second_trace, 'D')] == [second]
    header_path = tmp_path / 'trace-library' / first / 'header.json'
    header = json.loads(header_path.read_text(encoding='utf-8'))
    header['representation'] = 'A'  # Metadata fixture, not an A compiler.
    header_path.write_text(json.dumps(header))
    assert compiled_versions(tmp_path, first_trace, 'D') == []
    assert generate_d(tmp_path, first_trace)['id'] not in {first, second}


def test_legacy_source_and_file_time_are_read_without_migrating_files(tmp_path):
    trace = recording(tmp_path)
    identity = generate_d(tmp_path, trace)['id']
    directory = tmp_path / 'trace-library' / identity
    for filename in ('header.json', 'manifest.json'):
        path = directory / filename
        value = json.loads(path.read_text(encoding='utf-8'))
        value.pop('created_at')
        value.pop('source_recording')
        path.write_text(json.dumps(value), encoding='utf-8')
    os.utime(directory / 'header.json', (1700000000, 1700000000))
    before = {p: p.read_bytes() for p in directory.iterdir()}
    entry = list_traces(tmp_path)[0]
    assert entry['created_at_source'] == 'file_mtime'
    assert datetime.fromisoformat(entry['created_at']).timestamp() == 1700000000
    assert compiled_versions(tmp_path, trace, 'D')[0]['id'] == identity
    assert generate_d(tmp_path, trace)['confirmation_required']
    assert {p: p.read_bytes() for p in directory.iterdir()} == before


def test_traces_sort_by_generation_time_not_uuid_and_ignore_trashed_versions(tmp_path):
    from trace2task.local_trash import LocalTrash

    trace = recording(tmp_path)
    ids = [generate_d(tmp_path, trace, confirm_duplicate=True)['id'] for _ in range(2)]
    times = ['2026-10-03T04:00:00+08:00', '2026-10-02T21:00:00+00:00']
    for identity, created_at in zip(ids, times, strict=True):
        path = tmp_path / 'trace-library' / identity / 'header.json'
        header = json.loads(path.read_text(encoding='utf-8'))
        header['created_at'] = created_at
        path.write_text(json.dumps(header))
    assert [t['id'] for t in list_traces(tmp_path)] == ids[::-1]
    for identity in ids:
        LocalTrash(tmp_path).move(tmp_path / 'trace-library' / identity, 'trace_experience')
    assert compiled_versions(tmp_path, trace, 'D') == []
    assert generate_d(tmp_path, trace)['id'] not in ids


def test_controller_serializes_duplicate_generation_checks(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from trace2task.web_console import WebConsoleController

    trace = recording(tmp_path)
    controller = WebConsoleController(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(controller.generate_trace_d, [trace, trace]))
    assert sum('id' in result for result in results) == 1
    assert sum(bool(result.get('confirmation_required')) for result in results) == 1
    assert len(list_traces(tmp_path)) == 1


@pytest.mark.parametrize("identity", ["", "../runs/demo", "a/b"])
def test_body_path_boundary(tmp_path, identity):
    with pytest.raises(ValueError):
        read_body(tmp_path, identity)


def test_source_boundary(tmp_path):
    recording(tmp_path)
    with pytest.raises(ValueError):
        generate_d(tmp_path, "../events.jsonl")


def test_opencua_delete_is_recoverable_and_keeps_compiled_output(tmp_path):
    from trace2task.web_console import WebConsoleController
    path = recording(tmp_path)
    identity = generate_d(tmp_path, path)["id"]
    result = WebConsoleController(tmp_path).delete_recording(path)
    assert result["recoverable"]
    assert (tmp_path / result["trash_path"] / "events.jsonl").is_file()
    assert not (tmp_path / path).exists()
    assert read_body(tmp_path, identity)["trace_name"] == "演示"
    with pytest.raises(ValueError):
        WebConsoleController(tmp_path).delete_recording("../events.jsonl")


def test_existing_compiler_entry_preserved():
    from pathlib import Path
    root = Path(__file__).parents[1]
    js = (root / "src/trace2task/web/app.js").read_text(encoding="utf-8")
    assert '"/api/recordings/compile"' in js
    assert '"/api/traces/generate-d"' in js
    assert "/api/traces/model-input" in js


def test_model_input_verbatim(tmp_path):
    from trace2task.trace_library import read_model_input
    identity = generate_d(tmp_path, recording(tmp_path))["id"]
    path = tmp_path / "trace-library" / identity / "model-input.txt"
    content = '原始说明\r\n\r\n{ "description": "不删字段", "actions": [] }\r\n'
    path.write_bytes(content.encode("utf-8"))
    assert read_model_input(tmp_path, identity)["content"] == content
    with pytest.raises(ValueError):
        read_model_input(tmp_path, "../runs/demo")


def test_delete_trace_experience_keeps_recording(tmp_path):
    from trace2task.web_console import WebConsoleController
    path = recording(tmp_path)
    identity = generate_d(tmp_path, path)["id"]
    result = WebConsoleController(tmp_path).delete_trace_experience(identity)
    assert result["recoverable"]
    assert (tmp_path / result["trash_path"] / "body.json").is_file()
    assert (tmp_path / path).is_file()
    assert list_traces(tmp_path) == []
    with pytest.raises(ValueError):
        WebConsoleController(tmp_path).delete_trace_experience("../runs/demo")


def test_pending_deleted_experience_is_hidden_and_cannot_be_read_or_executed(tmp_path, monkeypatch):
    from pathlib import Path

    from trace2task import local_trash
    from trace2task.trace_library import execution_context, read_model_input
    from trace2task.web_console import WebConsoleController

    trace = recording(tmp_path)
    identity = generate_d(tmp_path, trace)['id']
    source = tmp_path / 'trace-library' / identity
    replace, rmtree = Path.replace, local_trash.shutil.rmtree

    def locked_replace(path, destination):
        if path == source:
            raise PermissionError('test lock')
        return replace(path, destination)

    def locked_rmtree(path, *args, **kwargs):
        if Path(path) == source:
            raise PermissionError('test lock')
        return rmtree(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'replace', locked_replace)
    monkeypatch.setattr(local_trash.shutil, 'rmtree', locked_rmtree)
    controller = WebConsoleController(tmp_path)
    deleted = controller.delete_trace_experience(identity)
    assert deleted['pending_cleanup']
    assert source.is_dir() and list_traces(tmp_path) == []
    for read in (read_body, read_model_input):
        with pytest.raises(ValueError, match='回收站'):
            read(tmp_path, identity)
    with pytest.raises(ValueError):
        execution_context(source / 'model-input.txt')
    with pytest.raises(ValueError):
        controller._resolve_execution_experience(f'trace-library/{identity}/model-input.txt')
    monkeypatch.undo()
    controller.restore_trashed_asset(deleted['trash_path'])
    assert list_traces(tmp_path)[0]['id'] == identity
    assert execution_context(source / 'model-input.txt')['kind'] == 'trace_sequence'


def test_http_generation_and_on_demand_body(tmp_path):
    import re
    import threading
    from urllib.request import ProxyHandler, Request, build_opener

    from trace2task.web_console import WebConsoleController, create_web_server

    trace_path = recording(tmp_path)
    server = create_web_server(tmp_path, port=0, controller=WebConsoleController(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(base + "/", timeout=5) as response:
            page = response.read().decode()
        token = re.search(r'name="trace2task-csrf" content="([^"]+)"', page).group(1)

        def post(path, payload):
            request = Request(base + path, data=json.dumps(payload).encode(), headers={
                "Content-Type": "application/json", "Origin": base, "X-Trace2Task-CSRF": token,
            })
            with opener.open(request, timeout=5) as response:
                return json.load(response)

        result = post("/api/traces/generate-d", {"trace_path": trace_path})
        body = post("/api/traces/body", result)
        assert body["trace_name"] == "演示"
        assert len(body["actions"]) == 1
        pending = post('/api/traces/generate-d', {'trace_path': trace_path})
        assert pending['confirmation_required']
        assert pending['existing_versions'][0]['id'] == result['id']
        assert len(list_traces(tmp_path)) == 1
        second = post('/api/traces/generate-d', {'trace_path': trace_path, 'confirm_duplicate': True})
        assert second['id'] != result['id']
        assert len(list_traces(tmp_path)) == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
