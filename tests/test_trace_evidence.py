import base64
import hashlib
import json

import pytest
from PIL import Image

from trace2task.trace_evidence import SELECTION_RULE, choose_images, image_bytes
from trace2task.trace_library import (
    compiled_versions,
    execution_context,
    generate_a,
    generate_d,
    list_traces,
    read_asset,
    read_body,
    read_model_input,
)


def recording(root, count=12):
    run = root / 'runs' / 'raw-demo'
    derived = run / 'derived' / 'official'
    (derived / 'frames').mkdir(parents=True)
    (run / 'trace2task.json').write_text(json.dumps({
        'source': 'opencua_native', 'capture_status': 'finished', 'task_id': '原始示范',
        'derivation': {'status': 'completed', 'directory': str(derived)},
    }), encoding='utf-8')
    (run / 'metadata.json').write_text(json.dumps({'screen_width': 16, 'screen_height': 12}), encoding='utf-8')
    events = [{'event_idx': i, 'time_stamp': i + .1, 'action': 'click', 'unchanged': '中文原始值'} for i in range(count - 1)]
    raw = '\r\n'.join(json.dumps(event, ensure_ascii=False) for event in events) + '\r\n'
    (run / 'events.jsonl').write_bytes(raw.encode('utf-8'))
    actions = [{'id': i, 'action': 'click', 'start_time': i + .1, 'end_time': i + .2,
                'description': 'Original click', 'coordinate': {'x': i, 'y': 2}, 'hidden_detail': [i, '原值']}
               for i in range(count - 1)]
    for name in ('reduced_events_complete.jsonl', 'reduced_events_vis.jsonl'):
        (derived / name).write_text('\n'.join(json.dumps(a, ensure_ascii=False) for a in actions), encoding='utf-8')
    frames = {}
    for i in range(count):
        key = f'{i:04d}'
        Image.new('RGB', (16, 12), color=(i * 10, 20, 30)).save(derived / 'frames' / f'{key}.png')
        frames[key] = {'status': 'available', 'path': f'frames/{key}.png', 'frame_index': i * 10}
    (derived / 'manifest.json').write_text(json.dumps({
        'status': 'completed', 'frames': frames,
        'action_frames': {str(i): {'before': f'{i:04d}', 'after': f'{i+1:04d}'} for i in range(count - 1)},
        'alignment': 'obs_cts_approximate', 'warnings': ['不是严格前态或成功判定'],
    }), encoding='utf-8')
    return run / 'events.jsonl', derived


def test_a_sends_d_actions_and_selected_images_but_keeps_raw_evidence_audit_only(tmp_path):
    trace, derived = recording(tmp_path)
    before = {path: path.read_bytes() for path in trace.parent.rglob('*') if path.is_file()}
    identity = generate_a(tmp_path, trace)['id']
    directory = tmp_path / 'trace-library' / identity
    preview = read_model_input(tmp_path, identity)
    body = read_body(tmp_path, identity)
    assert list_traces(tmp_path)[0]['representation'] == 'A'
    assert preview['format'] == 'actions-with-images-v2'
    assert preview['content'] == (directory / 'model-input.txt').read_bytes().decode('utf-8')
    for path in (trace, derived / 'reduced_events_complete.jsonl', derived / 'reduced_events_vis.jsonl'):
        assert path.read_bytes().decode('utf-8') not in preview['content']
        assert (directory / 'source' / path.name).read_bytes() == before[path]
    selected = ['0000', '0001', '0003', '0004', '0006', '0007', '0009', '0011']
    assert [item['id'] for item in preview['images']] == selected
    assert body['image_selection']['selected_ids'] == selected
    assert preview['image_selection']['rule'] == SELECTION_RULE
    assert len(body['frames']) == 12
    assert sum(item['selected'] for item in body['frames']) == 8
    payload = json.loads(preview['content'].split('\n\n', 1)[1])
    d_id = generate_d(tmp_path, trace)['id']
    d_payload = json.loads(read_model_input(tmp_path, d_id)['content'].split('\n\n', 1)[1])
    assert payload['source_screen'] == d_payload['source_screen']
    assert payload['image_ids'] == selected
    assert len(payload['actions']) == 11
    assert [{k: v for k, v in action.items() if k not in ('before_image', 'after_image')}
            for action in payload['actions']] == d_payload['actions']
    for action in payload['actions']:
        pair = body['action_frames'][str(action['id'])]
        for role in ('before', 'after'):
            assert action.get(f'{role}_image') == (pair[role] if pair[role] in selected else None)
    for forbidden in ('source_files', 'record_counts', 'action_frames', 'frame_index', 'video_pts_seconds',
                      'cts_ns', 'alignment', 'sha256', 'images/', 'source/', 'events.jsonl', 'selected',
                      'hidden_detail', 'time_stamp', 'start_time', 'end_time', '中文原始值'):
        assert forbidden not in preview['content']
    assert body['frames'][5]['source']['frame_index'] == 50
    assert 'sha256' in body['frames'][5]
    context = execution_context(directory / 'model-input.txt')
    assert context['model_input'] == preview['content']
    for item, data in zip(context['images'], image_bytes(context['images']), strict=True):
        assert data == before[derived / 'frames' / f"{item['id']}.png"]
        assert item['sha256'] == hashlib.sha256(data).hexdigest()
    assert all(path.read_bytes() == data for path, data in before.items())
    assert '不保证严格前态或操作成功' in preview['content']
    assert '不是严格前态或成功判定' in body['warnings']


def test_a_preserves_d_nested_actions_and_verbatim_typing(tmp_path):
    trace, derived = recording(tmp_path, 3)
    click = {'action': 'click', 'description': 'Single left Click', 'start_time': .5, 'end_time': .6}
    visual = [{'id': 0, 'action': 'long_press', 'description': 'ctrl + clicks',
               'start_time': 0, 'end_time': 1, 'children': [click]},
              {'id': 1, 'action': 'type', 'description': 'jiaofu$space$', 'start_time': 2, 'end_time': 3}]
    full = json.loads(json.dumps(visual))
    full[0]['children'].insert(0, {'action': 'release', 'start_time': 0, 'end_time': 0, 'vis': False})
    full[0]['children'][1]['coordinate'] = {'x': 5, 'y': 7}
    for suffix, rows in (('vis', visual), ('complete', full)):
        (derived / f'reduced_events_{suffix}.jsonl').write_text('\n'.join(json.dumps(row) for row in rows), encoding='utf-8')
    identity = generate_a(tmp_path, trace)['id']
    payload = json.loads(read_model_input(tmp_path, identity)['content'].split('\n\n', 1)[1])
    assert payload['actions'] == [
        {'id': 0, 'action': 'ctrl + clicks', 'duration_seconds': 1,
         'children': [{'action': 'Single left Click', 'duration_seconds': .1, 'coordinate': {'x': 5, 'y': 7}}],
         'before_image': '0000', 'after_image': '0001'},
        {'id': 1, 'action': 'jiaofu$space$', 'duration_seconds': 1, 'before_image': '0001', 'after_image': '0002'}]


def test_legacy_a_input_remains_frozen_and_is_labelled_for_regeneration(tmp_path):
    trace, _ = recording(tmp_path, 3)
    identity = generate_a(tmp_path, trace)['id']
    directory = tmp_path / 'trace-library' / identity
    path = directory / 'manifest.json'
    report = json.loads(path.read_text(encoding='utf-8'))
    legacy_text = '旧版完整原始输入\r\n' + trace.read_bytes().decode('utf-8')
    (directory / 'model-input.txt').write_bytes(legacy_text.encode('utf-8'))
    report['schema'] = 'trace2task.A.raw-evidence.v1'
    report.pop('model_input_format')
    report['artifact_sha256']['model-input.txt'] = hashlib.sha256(legacy_text.encode('utf-8')).hexdigest()
    report['model_input_sha256'] = report['artifact_sha256']['model-input.txt']
    path.write_text(json.dumps(report), encoding='utf-8')
    before = {p: p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    preview = read_model_input(tmp_path, identity)
    assert preview['format'] == 'raw-evidence-v1'
    assert preview['content'] == legacy_text
    assert execution_context(directory / 'model-input.txt')['model_input'] == legacy_text
    new = generate_a(tmp_path, trace, confirm_duplicate=True)['id']
    assert read_model_input(tmp_path, new)['format'] == 'actions-with-images-v2'
    assert all(p.read_bytes() == data for p, data in before.items())


def test_a_rejects_missing_action_image_associations(tmp_path):
    trace, derived = recording(tmp_path, 3)
    manifest = derived / 'manifest.json'
    value = json.loads(manifest.read_text(encoding='utf-8'))
    del value['action_frames']['0']
    manifest.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(ValueError, match='动作缺少截图关联'):
        generate_a(tmp_path, trace)
    assert not (tmp_path / 'trace-library').exists()


@pytest.mark.parametrize('count', [0, 1, 3, 8, 9, 30])
def test_temporal_selection_is_bounded_and_keeps_first_and_last(count):
    frames = [{'id': str(i), 'file': f'{i}.png'} for i in range(count)]
    selected = choose_images([{'id': 'missing-before', 'file': None}, *frames, {'id': 'missing-after', 'file': None}])
    assert len(selected) == min(count, 8)
    if count:
        assert selected[0] == frames[0] and selected[-1] == frames[-1]
        assert len({frame['id'] for frame in selected}) == len(selected)


def test_a_and_d_are_separate_versions_with_strict_duplicate_confirmation(tmp_path):
    trace, _ = recording(tmp_path, 3)
    a = generate_a(tmp_path, trace)['id']
    d = generate_d(tmp_path, trace)['id']
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    for value in (False, None, 1, 'true'):
        pending = generate_a(tmp_path, trace, confirm_duplicate=value)
        assert pending['representation'] == 'A'
        assert pending['confirmation_required'] is True
        assert [item['id'] for item in pending['existing_versions']] == [a]
        assert {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before
    new = generate_a(tmp_path, trace, confirm_duplicate=True)['id']
    assert new not in (a, d)
    assert len(compiled_versions(tmp_path, trace, 'A')) == 2
    assert len(compiled_versions(tmp_path, trace, 'D')) == 1
    assert all(p.read_bytes() == data for p, data in before.items())


def test_a_survives_source_removal_and_bundle_trash_restore(tmp_path):
    from trace2task.local_trash import LocalTrash

    trace, _ = recording(tmp_path, 3)
    identity = generate_a(tmp_path, trace)['id']
    directory = tmp_path / 'trace-library' / identity
    preview = read_model_input(tmp_path, identity)
    context = execution_context(directory / 'model-input.txt')
    trash = LocalTrash(tmp_path)
    trash.move(trace.parent, 'recording')
    assert read_model_input(tmp_path, identity) == preview
    assert execution_context(directory / 'model-input.txt') == context
    saved = trash.move(directory, 'trace_experience')
    assert list_traces(tmp_path) == []
    with pytest.raises((ValueError, FileNotFoundError)):
        read_asset(tmp_path, identity, preview['images'][0]['file'])
    trash.restore(saved['trash_path'])
    assert execution_context(directory / 'model-input.txt') == context


@pytest.mark.parametrize('filename', ['model-input.txt', 'images/0000.png'])
def test_a_refuses_changed_model_evidence(tmp_path, filename):
    trace, _ = recording(tmp_path, 3)
    identity = generate_a(tmp_path, trace)['id']
    directory = tmp_path / 'trace-library' / identity
    (directory / filename).write_bytes(b'changed')
    with pytest.raises(ValueError, match='证据已变化'):
        read_asset(tmp_path, identity, filename)
    with pytest.raises(ValueError, match='证据已变化'):
        execution_context(directory / 'model-input.txt')


def test_a_rejects_outside_evidence_and_arbitrary_asset_reads(tmp_path):
    trace, derived = recording(tmp_path, 3)
    manifest = derived / 'manifest.json'
    value = json.loads(manifest.read_text(encoding='utf-8'))
    original = manifest.read_bytes()
    value['frames']['0000']['path'] = '../outside.png'
    manifest.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(ValueError, match='PNG'):
        generate_a(tmp_path, trace)
    assert not (tmp_path / 'trace-library').exists()
    manifest.write_bytes(original)
    identity = generate_a(tmp_path, trace)['id']
    for filename in ('../outside.txt', 'header.json', 'manifest.json', str(trace)):
        with pytest.raises(ValueError):
            read_asset(tmp_path, identity, filename)


def test_missing_frame_is_explicit_not_fabricated(tmp_path):
    trace, derived = recording(tmp_path, 3)
    path = derived / 'manifest.json'
    value = json.loads(path.read_text(encoding='utf-8'))
    value['frames']['0000'] = {'status': 'missing_prestate', 'path': None}
    path.write_text(json.dumps(value), encoding='utf-8')
    identity = generate_a(tmp_path, trace)['id']
    preview = read_model_input(tmp_path, identity)
    assert [item['id'] for item in preview['images']] == ['0001', '0002']
    assert '0000' not in preview['content']
    assert read_body(tmp_path, identity)['frames'][0]['file'] is None


def test_a_wire_validation_checks_bytes_count_and_does_not_accept_local_paths(tmp_path):
    from trace2task.local_gui_client import validate_experience_context

    trace, _ = recording(tmp_path, 3)
    identity = generate_a(tmp_path, trace)['id']
    context = execution_context(tmp_path / 'trace-library' / identity / 'model-input.txt')
    assert validate_experience_context(context) == context
    image = context['images'][0]
    for invalid in ([{**image, 'sha256': '0' * 64}], [image] * 9, [{**image, 'path': str(trace)}],
                    [{**image, 'base64': base64.b64encode(b'not a png').decode()}]):
        with pytest.raises(ValueError):
            validate_experience_context({**context, 'images': invalid})


def test_http_a_generation_previews_and_integrity(tmp_path):
    import re
    import threading
    from urllib.error import HTTPError
    from urllib.parse import urlencode
    from urllib.request import ProxyHandler, Request, build_opener

    from trace2task.web_console import WebConsoleController, create_web_server

    trace, _ = recording(tmp_path, 3)
    server = create_web_server(tmp_path, port=0, controller=WebConsoleController(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_address[1]}'
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(base + '/') as response:
            token = re.search(r'name="trace2task-csrf" content="([^"]+)"', response.read().decode()).group(1)

        def post(endpoint, body):
            request = Request(base + endpoint, data=json.dumps(body).encode(), headers={
                'Content-Type': 'application/json', 'Origin': base, 'X-Trace2Task-CSRF': token})
            with opener.open(request, timeout=5) as response:
                return json.load(response)

        result = post('/api/traces/generate-a', {'trace_path': str(trace)})
        preview = post('/api/traces/model-input', result)
        assert len(preview['images']) == 3
        asset = preview['images'][0]['file']
        url = base + '/api/traces/asset?' + urlencode({'id': result['id'], 'file': asset})
        with opener.open(url, timeout=5) as response:
            assert response.headers['Content-Type'] == 'image/png'
            assert response.read() == read_asset(tmp_path, result['id'], asset)
        assert post('/api/traces/generate-a', {'trace_path': str(trace)})['confirmation_required']
        assert len(list_traces(tmp_path)) == 1
        (tmp_path / 'trace-library' / result['id'] / asset).write_bytes(b'tampered')
        with pytest.raises(HTTPError) as error:
            opener.open(url, timeout=5)
        assert error.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
