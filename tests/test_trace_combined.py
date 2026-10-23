"""C reuses B and frozen A without another compiler call or desktop input."""
import base64
import json

import pytest
from test_trace_evidence import recording
from test_trace_procedure import PROCEDURE, Session, compile_fake

from trace2task.chat_model_adapter import ChatModelAdapter
from trace2task.local_gui_client import validate_experience_context
from trace2task.procedure_prompt import PROCEDURE_PROMPT
from trace2task.trace_combined import generate_c
from trace2task.trace_library import (
    execution_context,
    generate_a,
    list_traces,
    read_asset,
    read_body,
    read_model_input,
)
from trace2task.web_console import WebConsoleController


def test_c_keeps_exact_b_and_a_bytes_and_selected_images_without_model_calls(tmp_path, monkeypatch):
    trace, _ = recording(tmp_path)
    session = Session()
    b = compile_fake(tmp_path, trace, session)['id']
    a = generate_a(tmp_path, trace)['id']
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}

    def forbidden(*args, **kwargs):
        pytest.fail('C must not invoke a compiler')

    monkeypatch.setattr('trace2task.trace_procedure.compile_b', forbidden)
    c = generate_c(tmp_path, b)['id']
    assert len(session.calls) == 1
    assert read_asset(tmp_path, c, 'procedure.md') == PROCEDURE.encode()
    a_input = read_model_input(tmp_path, a)
    assert read_asset(tmp_path, c, 'evidence/model-input.txt') == a_input['content'].encode()
    model = read_model_input(tmp_path, c)
    assert PROCEDURE in model['content'] and a_input['content'] in model['content']
    assert PROCEDURE_PROMPT not in model['content']
    assert model['format'] == 'procedure-with-evidence-v1'
    assert [(i['id'], i['sha256']) for i in model['images']] == [(i['id'], i['sha256']) for i in a_input['images']]
    assert len(model['images']) == 8
    for image, original in zip(model['images'], a_input['images'], strict=True):
        assert read_asset(tmp_path, c, image['file']) == read_asset(tmp_path, a, original['file'])
    assert read_body(tmp_path, c)['source_b_id'] == b
    assert all(p.read_bytes() == data for p, data in before.items())
    context = execution_context(tmp_path / 'trace-library' / c / 'model-input.txt')
    assert validate_experience_context(context) == context
    assert context['model_input'] == model['content']
    assert len(context['images']) == 8


@pytest.mark.parametrize('confirmed', [False, None, 'true', 1])
def test_duplicate_is_bound_to_explicit_b_version_and_needs_strict_confirmation(tmp_path, confirmed):
    trace, _ = recording(tmp_path, 3)
    b = compile_fake(tmp_path, trace)['id']
    c = generate_c(tmp_path, b)['id']
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    reply = generate_c(tmp_path, b, confirm_duplicate=confirmed)
    assert reply['confirmation_required'] and reply['existing_versions'][0]['id'] == c
    assert {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before
    assert generate_c(tmp_path, b, confirm_duplicate=True)['id'] != c
    b2 = compile_fake(tmp_path, trace, Session(PROCEDURE.replace('保存文档', '另一个方法')), confirm_duplicate=True)['id']
    c2 = generate_c(tmp_path, b2)['id']
    assert '另一个方法' in read_model_input(tmp_path, c2)['content']
    assert read_body(tmp_path, c2)['source_b_id'] == b2


def test_c_survives_recording_and_b_removal_and_its_own_trash_restore(tmp_path):
    trace, _ = recording(tmp_path, 3)
    b = compile_fake(tmp_path, trace)['id']
    trace.parent.rename(trace.parent.with_name('retained-elsewhere'))
    controller = WebConsoleController(tmp_path)
    c = controller.generate_trace_c(b)['id']
    controller.delete_trace_experience(b)
    path = tmp_path / 'trace-library' / c / 'model-input.txt'
    before = execution_context(path)
    deleted = controller.delete_trace_experience(c)
    with pytest.raises(ValueError):
        execution_context(path)
    controller.restore_trashed_asset(deleted['trash_path'])
    assert execution_context(path) == before


@pytest.mark.parametrize('filename', ['model-input.txt', 'compiler-input.txt', 'evidence/model-input.txt', 'evidence/manifest.json'])
def test_tampered_b_cannot_publish_c(tmp_path, filename):
    trace, _ = recording(tmp_path, 3)
    b = compile_fake(tmp_path, trace)['id']
    (tmp_path / 'trace-library' / b / filename).write_text('tampered')
    with pytest.raises(ValueError):
        generate_c(tmp_path, b)
    assert all(t['representation'] != 'C' for t in list_traces(tmp_path))


@pytest.mark.parametrize('filename', ['manifest.json', 'model-input.txt', 'evidence/images/0000.png'])
def test_c_tampering_rejected_before_execution(tmp_path, filename):
    trace, _ = recording(tmp_path, 3)
    c = generate_c(tmp_path, compile_fake(tmp_path, trace)['id'])['id']
    if filename.endswith('.png'):
        filename = read_model_input(tmp_path, c)['images'][0]['file']
    (tmp_path / 'trace-library' / c / filename).write_bytes(b'tampered')
    with pytest.raises(ValueError):
        execution_context(tmp_path / 'trace-library' / c / 'model-input.txt')


@pytest.mark.parametrize('provider', ['codex', 'api'])
def test_c_execution_sends_full_method_evidence_and_original_images(tmp_path, provider):
    trace, _ = recording(tmp_path, 3)
    c = generate_c(tmp_path, compile_fake(tmp_path, trace)['id'])['id']
    context = execution_context(tmp_path / 'trace-library' / c / 'model-input.txt')
    session = Session(json.dumps({'task_complete': True, 'reason': 'visible', 'actions': []}))
    adapter = ChatModelAdapter(session, selected_windows=False, provider=provider, evidence_directory=tmp_path / 'run-evidence')
    current = tmp_path / 'current.png'
    adapter.predict('本次任务', screenshot=current, history=[], experience_context=context)
    call = session.calls[0]
    assert context['model_input'] in call['prompt']
    assert PROCEDURE_PROMPT not in call['prompt']
    assert call['image_path'] == current
    assert [p.read_bytes() for p in call['additional_image_paths']] == [base64.b64decode(i['base64']) for i in context['images']]


def test_non_b_and_path_escape_do_not_generate_c(tmp_path):
    trace, _ = recording(tmp_path, 3)
    a = generate_a(tmp_path, trace)['id']
    for identifier in (a, '../outside', ''):
        with pytest.raises(ValueError):
            generate_c(tmp_path, identifier)
    assert len(list_traces(tmp_path)) == 1


def test_c_accepts_frozen_missing_frames_without_fabricating_images(tmp_path):
    trace, derived = recording(tmp_path, 3)
    path = derived / 'manifest.json'
    manifest = json.loads(path.read_text(encoding='utf-8'))
    for frame in manifest['frames'].values():
        frame.update(status='missing_prestate', path=None)
    path.write_text(json.dumps(manifest), encoding='utf-8')
    c = generate_c(tmp_path, compile_fake(tmp_path, trace)['id'])['id']
    assert read_model_input(tmp_path, c)['images'] == []
    assert execution_context(tmp_path / 'trace-library' / c / 'model-input.txt')['images'] == []


def test_controller_requires_idle_before_combining(tmp_path):
    from types import SimpleNamespace

    trace, _ = recording(tmp_path, 3)
    b = compile_fake(tmp_path, trace)['id']
    controller = WebConsoleController(tmp_path)
    controller._active_job_id = 'active'
    controller._jobs['active'] = SimpleNamespace(status='running')
    with pytest.raises(RuntimeError):
        controller.generate_trace_c(b)
    assert [t['representation'] for t in list_traces(tmp_path)] == ['B']


def test_http_c_is_synchronous_has_duplicate_gate_and_no_model_job(tmp_path):
    import re
    import threading
    from urllib.request import ProxyHandler, Request, build_opener

    from trace2task.web_console import create_web_server

    trace, _ = recording(tmp_path, 3)
    b = compile_fake(tmp_path, trace)['id']
    controller = WebConsoleController(tmp_path)
    server = create_web_server(tmp_path, port=0, controller=controller)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(base + '/', timeout=5) as response:
            token = re.search(r'name="trace2task-csrf" content="([^"]+)"', response.read().decode()).group(1)

        def post(**extra):
            with opener.open(Request(base + '/api/traces/generate-c', data=json.dumps({'b_id': b, **extra}).encode(),
                headers={'Content-Type': 'application/json', 'Origin': base, 'X-Trace2Task-CSRF': token}), timeout=5) as response:
                assert response.status == 200
                return json.load(response)

        first = post()
        assert first['representation'] == 'C'
        assert post()['existing_versions'][0]['id'] == first['id']
        assert post(confirm_duplicate='true')['confirmation_required']
        assert post(confirm_duplicate=True)['id'] != first['id']
        assert controller.workbench_runs()['runs'] == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
