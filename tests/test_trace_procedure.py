"""Procedure B contract, with a fake compiler: no remote calls or desktop input."""
import hashlib
import json
import re
import threading
import time
from copy import deepcopy
from urllib.request import ProxyHandler, Request, build_opener

import pytest
from test_trace_evidence import recording

from trace2task import trace_procedure
from trace2task.chat_model_adapter import ChatModelAdapter
from trace2task.local_gui_client import validate_experience_context
from trace2task.procedure_prompt import PROCEDURE_PROMPT, PROMPT_VERSION
from trace2task.trace_library import (
    execution_context,
    generate_a,
    list_traces,
    read_asset,
    read_body,
    read_model_input,
)
from trace2task.trace_procedure import ProcedureCompileCancelled, compile_b
from trace2task.web_console import WebConsoleController, create_web_server

PROCEDURE = '# 保存文档\n将编辑好的文档保存到指定位置。\n\n输入：{文件名}，由当前任务提供。\n\n## 步骤\n1. 在编辑器中选择保存。\n2. 输入 {文件名} 并确认保存。\n3. 检查标题中的文件名与任务一致；已提供截图不足以确认落盘结果，执行时还需核对目标文件。\n'
OPTIONS = {'model': 'gpt-6.1-sol', 'reasoning_effort': 'high'}


class Session:
    def __init__(self, output=PROCEDURE):
        self.output = output
        self.closed = False
        self.calls = []
        self.options = None

    def factory(self, executable, **options):
        assert executable == 'fake-codex'
        self.options = options
        return self

    def run_turn(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.output, Exception):
            raise self.output
        return self.output

    def close(self):
        self.closed = True


def compile_fake(root, trace, session=None, **kwargs):
    session = session or Session()
    return compile_b(root, trace, **OPTIONS, session_factory=session.factory,
                     binary_resolver=lambda _: 'fake-codex', **kwargs)


def test_b_freezes_markdown_and_exact_compiler_evidence_but_executes_text_only(tmp_path):
    trace, derived = recording(tmp_path)
    originals = {p: p.read_bytes() for p in trace.parent.rglob('*') if p.is_file()}
    session, rounds = Session(), []
    result = compile_fake(tmp_path, trace, session, on_round=lambda entry: rounds.append(deepcopy(entry)))
    identity = result['id']
    folder = tmp_path / 'trace-library' / identity
    assert session.closed and len(session.calls) == 1
    assert session.options['base_instructions'] == PROCEDURE_PROMPT
    assert session.options['thread_config']['web_search'] == 'disabled'
    assert session.options['thread_config']['features']['shell_tool'] is False
    assert session.options['cwd'] == folder / 'evidence'
    call = session.calls[0]
    assert call['output_schema'] is None
    paths = (call['image_path'], *call['additional_image_paths'])
    assert len(paths) == 8
    chosen = ['0000', '0001', '0003', '0004', '0006', '0007', '0009', '0011']
    assert [p.stem for p in paths] == chosen
    assert all(p.read_bytes() == originals[derived / 'frames' / f'{p.stem}.png'] for p in paths)
    preview = read_model_input(tmp_path, identity)
    assert preview['content'] == PROCEDURE and preview['images'] == []
    assert preview['format'] == 'procedure-markdown-v1'
    assert preview['compiler']['instructions'] == PROCEDURE_PROMPT
    assert preview['compiler']['input'] == call['prompt']
    assert preview['compiler']['prompt_version'] == PROMPT_VERSION
    assert [i['id'] for i in preview['compiler']['images']] == chosen
    assert read_body(tmp_path, identity)['runtime_images'] == []
    assert [t['representation'] for t in list_traces(tmp_path)] == ['B']
    assert list_traces(tmp_path)[0]['trace_name'] == '保存文档'
    context = execution_context(folder / 'model-input.txt')
    assert set(context) == {'kind', 'task_id', 'model_input', 'sha256'}
    assert context['model_input'] == PROCEDURE
    assert validate_experience_context(context) == context
    assert context['sha256'] == hashlib.sha256(PROCEDURE.encode()).hexdigest()
    assert 'compiler' not in context and 'images' not in context
    assert rounds[0]['status'] == 'pending' and rounds[-1]['status'] == 'compiled'
    assert rounds[-1]['raw_output'] == PROCEDURE and rounds[-1]['executed'] is False
    assert rounds[0]['input']['messages'][0]['content'] == PROCEDURE_PROMPT
    assert all(path.read_bytes() == data for path, data in originals.items())
    # A uses exactly the same evidence text and images, but it remains an independent version.
    a_id = generate_a(tmp_path, trace)['id']
    a = read_model_input(tmp_path, a_id)
    assert a['content'] == call['prompt']
    assert [(i['id'], i['sha256']) for i in a['images']] == [(i['id'], i['sha256']) for i in preview['compiler']['images']]


@pytest.mark.parametrize('confirmed', [False, None, 1, 'true'])
def test_duplicate_confirmation_happens_before_any_model_or_file_write(tmp_path, confirmed):
    trace, _ = recording(tmp_path, 3)
    first = compile_fake(tmp_path, trace)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    session = Session()
    result = compile_fake(tmp_path, trace, session, confirm_duplicate=confirmed)
    assert result['confirmation_required'] and result['representation'] == 'B'
    assert result['existing_versions'][0]['id'] == first['id']
    assert session.options is None and not session.calls
    assert {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before
    second = compile_fake(tmp_path, trace, confirm_duplicate=True)
    assert second['id'] != first['id']
    assert len(list_traces(tmp_path)) == 2
    assert all(p.read_bytes() == data for p, data in before.items())


@pytest.mark.parametrize('output', ['{"status":"ok"}', '```markdown\n' + PROCEDURE + '```',
    '缺少关键截图，无法形成方法。', '# 名称\n## 步骤\n1. 点击。', '', OSError('compiler offline')])
def test_failed_or_incomplete_output_is_retained_but_never_published(tmp_path, output):
    trace, _ = recording(tmp_path, 3)
    session, rounds = Session(output), []
    with pytest.raises((ValueError, OSError)):
        compile_fake(tmp_path, trace, session, on_round=lambda entry: rounds.append(deepcopy(entry)))
    assert session.closed and not list_traces(tmp_path)
    assert rounds[-1]['status'] == 'error'
    assert trace.is_file()
    if isinstance(output, str):
        assert next((tmp_path / 'trace-library').glob('*/model-output.md')).read_bytes().decode() == output
        assert rounds[-1]['raw_output'] == output


def test_cancel_before_start_does_not_call_agent_or_create_version(tmp_path):
    trace, _ = recording(tmp_path, 3)
    stop = threading.Event()
    stop.set()
    session = Session()
    with pytest.raises(ProcedureCompileCancelled):
        compile_fake(tmp_path, trace, session, stop_event=stop)
    assert session.options is None and not (tmp_path / 'trace-library').exists()


def test_cancel_during_agent_turn_closes_own_session_and_never_publishes(tmp_path):
    trace, _ = recording(tmp_path, 3)
    stop, closed = threading.Event(), threading.Event()
    session = Session()

    def turn(**kwargs):
        stop.set()
        assert closed.wait(3)
        return PROCEDURE

    session.run_turn = turn
    session.close = closed.set
    with pytest.raises(ProcedureCompileCancelled):
        compile_fake(tmp_path, trace, session, stop_event=stop)
    assert not list_traces(tmp_path)


def test_mutated_frozen_inputs_prevent_publication(tmp_path):
    trace, _ = recording(tmp_path, 3)
    session = Session()

    def turn(**kwargs):
        kwargs['image_path'].write_bytes(b'changed')
        return PROCEDURE

    session.run_turn = turn
    with pytest.raises(ValueError, match='冻结证据发生变化'):
        compile_fake(tmp_path, trace, session)
    assert not list_traces(tmp_path)


@pytest.mark.parametrize('filename', ['model-input.txt', 'manifest.json'])
def test_b_rejects_tampered_procedure_or_manifest(tmp_path, filename):
    trace, _ = recording(tmp_path, 3)
    identity = compile_fake(tmp_path, trace)['id']
    folder = tmp_path / 'trace-library' / identity
    path = folder / filename
    if filename == 'manifest.json':
        payload = json.loads(path.read_text(encoding='utf-8'))
        payload['compiler']['model'] = 'changed'
        path.write_text(json.dumps(payload))
    else:
        path.write_text(PROCEDURE + 'changed', encoding='utf-8')
    with pytest.raises(ValueError, match='已变化'):
        execution_context(folder / 'model-input.txt')


def test_b_survives_source_removal_and_trash_restore(tmp_path):
    trace, _ = recording(tmp_path, 3)
    identity = compile_fake(tmp_path, trace)['id']
    folder = tmp_path / 'trace-library' / identity
    trace.parent.rename(trace.parent.with_name('original-kept-elsewhere'))
    assert execution_context(folder / 'model-input.txt')['model_input'] == PROCEDURE
    preview = read_model_input(tmp_path, identity)
    assert read_asset(tmp_path, identity, preview['compiler']['images'][0]['file']).startswith(b'\x89PNG')
    controller = WebConsoleController(tmp_path)
    deleted = controller.delete_trace_experience(identity)
    assert list_traces(tmp_path) == []
    controller.restore_trashed_asset(deleted['trash_path'])
    assert execution_context(folder / 'model-input.txt')['model_input'] == PROCEDURE


@pytest.mark.parametrize('provider', ['codex', 'api'])
def test_b_execution_adapter_sends_procedure_and_current_image_only(tmp_path, provider):
    trace, _ = recording(tmp_path, 3)
    identity = compile_fake(tmp_path, trace)['id']
    context = execution_context(tmp_path / 'trace-library' / identity / 'model-input.txt')
    session = Session(json.dumps({'task_complete': True, 'reason': 'visible result', 'actions': []}))
    adapter = ChatModelAdapter(session, selected_windows=False, provider=provider)
    current = tmp_path / 'current.png'
    result = adapter.predict('本次任务与参数', screenshot=current, history=[], experience_context=context)
    assert result['status'] == 'predicted'
    call = session.calls[0]
    assert PROCEDURE in call['prompt']
    assert PROCEDURE_PROMPT not in call['prompt']
    assert 'sha256' not in call['prompt'] and 'compiler-input' not in call['prompt']
    assert call['image_path'] == current and call['additional_image_paths'] == ()


def test_missing_all_frames_does_not_fabricate_compiler_images(tmp_path):
    trace, derived = recording(tmp_path, 3)
    path = derived / 'manifest.json'
    payload = json.loads(path.read_text(encoding='utf-8'))
    for frame in payload['frames'].values():
        frame.update(status='missing_prestate', path=None)
    path.write_text(json.dumps(payload), encoding='utf-8')
    session = Session()
    compile_fake(tmp_path, trace, session)
    assert session.calls[0]['image_path'] is None and session.calls[0]['additional_image_paths'] == ()


def test_unsupported_model_does_not_prepare_evidence_or_call_agent(tmp_path):
    trace, _ = recording(tmp_path, 3)
    with pytest.raises(ValueError, match='不支持'):
        compile_b(tmp_path, trace, model='unsupported', reasoning_effort='high')
    assert not (tmp_path / 'trace-library').exists()


def wait_job(controller, identity):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = controller.get_job(identity)
        if job['status'] not in {'queued', 'running', 'stopping'}:
            return job
        time.sleep(.01)
    pytest.fail('Compiler job did not finish')


def test_http_agent_compile_job_and_duplicate_gate(monkeypatch, tmp_path):
    trace, _ = recording(tmp_path, 3)
    session = Session()
    monkeypatch.setattr(trace_procedure, 'CodexAppServerSession', session.factory)
    monkeypatch.setattr(trace_procedure, 'resolve_codex_binary', lambda _: 'fake-codex')
    controller = WebConsoleController(tmp_path)
    server = create_web_server(tmp_path, port=0, controller=controller)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(base + '/') as response:
            csrf = re.search(r'name="trace2task-csrf" content="([^"]+)"', response.read().decode()).group(1)

        def post(**extra):
            body = json.dumps({'trace_path': str(trace), **OPTIONS, **extra}).encode()
            with opener.open(Request(base + '/api/traces/generate-b', data=body,
                headers={'Content-Type': 'application/json', 'Origin': base, 'X-Trace2Task-CSRF': csrf}), timeout=5) as response:
                return response.status, json.load(response)

        code, started = post()
        assert code == 202
        final = wait_job(controller, started['job_id'])
        assert final['status'] == 'completed'
        identity = final['result']['compiled_trace_id']
        saved = controller.run_store.get(started['job_id'])['session']
        assert saved['result']['compiled_trace_id'] == identity
        assert saved['run_facts']['execution_backend'] == 'none'
        assert controller.run_store.read_round(started['job_id'], 0)['raw_output'] == PROCEDURE
        code, pending = post()
        assert code == 200 and pending['confirmation_required']
        assert len(session.calls) == 1 and len(controller.run_store.list()) == 1
        code, restarted = post(confirm_duplicate=True)
        assert code == 202 and restarted['job_id'] != started['job_id']
        assert wait_job(controller, restarted['job_id'])['status'] == 'completed'
        assert len(session.calls) == 2 and len(list_traces(tmp_path)) == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
