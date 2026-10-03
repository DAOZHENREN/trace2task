"""No-GPU regression coverage for the optional llama-server backend."""
import base64
import hashlib
import importlib.util
import io
import json
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from PIL import Image

from trace2task import local_gui_llama as llama


@pytest.fixture
def engine(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('llama_test_server',
        Path(__file__).parents[1] / 'scripts/local_gui/server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    value = module.Engine(tmp_path, tmp_path / 'output', backend='llama-server')
    monkeypatch.setattr(value.llama, 'load', lambda *args: None)
    return value


def request(identity, **extra):
    buffer = io.BytesIO()
    Image.new('RGB', (32, 32), 'red').save(buffer, format='PNG')
    return {'request_id': identity, 'model': 'gui-owl-2b', 'task': 'wait',
            'image': base64.b64encode(buffer.getvalue()).decode(), **extra}


def reply(finish='stop'):
    return {'choices': [{'message': {'content': 'Action: Wait.\n<tool_call>\n'
        '{"name":"computer_use","arguments":{"action":"wait","time":1}}\n</tool_call>'},
        'finish_reason': finish}], 'usage': {'prompt_tokens': 2048, 'completion_tokens': 30,
        'prompt_tokens_details': {'cached_tokens': 1000}}, 'timings': {'cache_n': 1000}}


def test_wire_images_are_in_order_and_original_messages_unchanged():
    messages = [{'role': 'user', 'content': [{'type': 'image'}, {'type': 'text', 'text': 'retain'}]}]
    output = llama.wire_messages(messages, [Image.new('RGB', (2, 2))])
    assert output[0]['content'][0]['image_url']['url'].startswith('data:image/png;base64,')
    assert messages[0]['content'][0] == {'type': 'image'}
    assert output[0]['content'][1]['text'] == 'retain'
    with pytest.raises(ValueError, match='Missing'):
        llama.wire_messages(messages, [])
    with pytest.raises(ValueError, match='Extra'):
        llama.wire_messages([], [Image.new('RGB', (2, 2))])


def test_registered_model_and_unknown_backend_fail_closed(tmp_path):
    assert llama.runtime_paths(tmp_path, 'qwen3-vl-2b')[1].parent.name == 'qwen3-vl-2b'
    with pytest.raises(ValueError, match='不支持'):
        llama.runtime_paths(tmp_path, 'mai-ui-2b')
    (tmp_path / 'backend.json').write_text('{"backend":"other"}')
    with pytest.raises(ValueError, match='Unknown'):
        llama.read_backend(tmp_path)


def test_gguf_checksums_are_required_and_verified(tmp_path):
    paths = [tmp_path / 'llama-server.exe', tmp_path / 'model-BF16.gguf', tmp_path / 'mmproj-F16.gguf']
    report = {'model': 'mPLUG/GUI-Owl-1.5-2B-Instruct',
              'revision': '528ceaec795bbfbe6103bd79e03db849feadfb24', 'files': []}
    for path in paths[1:]:
        path.write_bytes(b'GGUF test fixture')
        report['files'].append({'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    (tmp_path / 'verified-gguf.json').write_text(json.dumps(report))
    llama.verify_weights(paths)
    paths[2].write_bytes(b'changed')
    with pytest.raises(ValueError, match='checksum mismatch'):
        llama.verify_weights(paths)


def test_manifest_cannot_be_used_for_a_different_model(tmp_path):
    report = {'model': 'mPLUG/GUI-Owl-1.5-2B-Instruct',
              'revision': '528ceaec795bbfbe6103bd79e03db849feadfb24', 'files': []}
    (tmp_path / 'verified-gguf.json').write_text(json.dumps(report))
    with pytest.raises(ValueError, match='provenance'):
        llama.verify_weights([tmp_path / 'server', tmp_path / 'model', tmp_path / 'vision'], 'qwen3-vl-2b')


@pytest.mark.parametrize('key', ['gui-owl-2b', 'qwen3-vl-2b', 'qwen3-vl-8b-instruct'])
def test_summary_restores_the_original_model(tmp_path, monkeypatch, key):
    runtime = llama.LlamaRuntime(tmp_path, tmp_path)
    runtime.process_key = key
    calls = []
    path = tmp_path / 'summary.gguf'
    path.touch()
    monkeypatch.setattr(llama, 'summary_model_path', lambda root: path)
    monkeypatch.setattr(runtime, 'stop', lambda: calls.append('stop'))
    monkeypatch.setattr(runtime, 'load', lambda key, cancelled: calls.append(key))
    with runtime.summary_worker(threading.Event()):
        calls.append('summary')
    assert calls == ['stop', 'summary-qwen8b', 'summary', 'stop', key]


@pytest.mark.parametrize('key', ['qwen3-vl-2b', 'qwen3-vl-8b-instruct'])
def test_qwen_llama_uses_its_own_alias_and_decoder(engine, monkeypatch, key):
    seen = []
    def complete(payload, cancelled):
        seen.append(payload['model'])
        return {'choices': [{'message': {'content': '{"actions":[{"skill":"wait","args":{"duration_ms":1000}}]}'},
                             'finish_reason': 'stop'}]}
    monkeypatch.setattr(engine.llama, 'complete', complete)
    result = engine.predict(request('qwen-native-001', model=key))
    assert seen == ['trace2task-' + key]
    assert result['status'] == 'predicted'
    assert result['prediction']['actions'][0]['skill'] == 'wait'
    configuration = json.loads((Path(result['output_directory']) / 'input.json').read_text())['configuration']
    assert configuration['dtype'] == ('Q4_K_M text / F16 vision' if '8b' in key else 'BF16 text / F16 vision')
    assert configuration['kv_cache_type'] == ('q8_0' if '8b' in key else 'f16')


def test_prebuilt_8b_reuses_verified_files_without_a_conversion_manifest(tmp_path, monkeypatch):
    from trace2task.model_registry import MODEL_PROFILES, profile_for

    key = 'qwen3-vl-8b-instruct'
    profile = profile_for(key, 'llama-server')
    existing = tmp_path / 'existing'
    existing.mkdir()
    files = []
    for index, (name, _) in enumerate(profile.prebuilt_gguf.files):
        path = existing / name
        path.write_bytes(b'GGUF test fixture ' + str(index).encode())
        files.append((name, hashlib.sha256(path.read_bytes()).hexdigest()))
    preset = replace(profile.prebuilt_gguf, directory=str(existing), files=tuple(files))
    monkeypatch.setitem(MODEL_PROFILES, key, replace(profile, prebuilt_gguf=preset))
    monkeypatch.delenv(preset.directory_env, raising=False)
    root = tmp_path / 'managed'
    paths = llama.runtime_paths(root, key)
    assert paths[1].parent == existing
    assert llama.required_runtime_paths(root, key) == paths  # No HF source checkpoint required.
    assert llama.summary_model_path(root) == paths[1]
    llama.verify_weights(paths, key)
    paths[2].write_bytes(b'changed')
    # A forged local manifest cannot weaken the pinned publisher hashes.
    (existing / 'verified-gguf.json').write_text(json.dumps({'files': [
        {'file': paths[2].name, 'sha256': hashlib.sha256(b'changed').hexdigest()}]}))
    with pytest.raises(ValueError, match='checksum mismatch'):
        llama.verify_weights(paths, key)
    explicit = tmp_path / 'explicit-missing'
    monkeypatch.setenv(preset.directory_env, str(explicit))
    assert llama.runtime_paths(root, key)[1].parent == explicit  # Never silently fall back.
    with pytest.raises(ValueError, match='不支持'):
        profile_for(key, 'transformers')


def test_8b_native_format_and_capabilities_match_the_qwen_adapter():
    from trace2task.local_gui_protocol import MODEL_ACTION_SKILLS, decode, prompt

    key = 'qwen3-vl-8b-instruct'
    assert prompt(key) == prompt('qwen3-vl-2b')
    assert MODEL_ACTION_SKILLS[key] == MODEL_ACTION_SKILLS['qwen3-vl-2b']
    assert decode(key, '{"actions":[{"done":true}]}')['actions'][0]['done'] is True
    for invalid in ('{"actions":[]}', '{"actions":[{"skill":"exec","args":{}}]}',
                    '{"actions":[{"skill":"click","args":{"x":2,"y":0.5}}]}'):
        with pytest.raises(ValueError):
            decode(key, invalid)


def test_request_engine_mismatch_fails_before_image_or_conversation(engine):
    with pytest.raises(ValueError, match='differs'):
        engine.predict({'model': 'qwen3-vl-2b', 'expected_backend': 'transformers'})
    assert engine.conversation is None


def test_context_error_name_in_message_is_not_treated_as_native_error(tmp_path):
    runtime = llama.LlamaRuntime(tmp_path, tmp_path)
    def fail(*args, **kwargs):
        raise HTTPError('http://127.0.0.1', 400, 'bad', {}, io.BytesIO(
            b'{"error":{"type":"invalid_request_error","message":"exceed_context_size_error"}}'))
    runtime.opener = SimpleNamespace(open=fail)
    with pytest.raises(RuntimeError) as raised:
        runtime.request('/v1/chat/completions', {})
    assert not isinstance(raised.value, llama.ContextFull)


def test_native_redirect_is_rejected():
    with pytest.raises(RuntimeError, match='redirects are forbidden'):
        llama.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.org')


def test_stop_waits_for_owned_process_exit_before_forgetting_it(tmp_path, monkeypatch):
    runtime = llama.LlamaRuntime(tmp_path, tmp_path)
    order = []
    runtime.process = SimpleNamespace(wait=lambda **kwargs: order.append('exited'))
    runtime.process_key = 'gui-owl-2b'
    monkeypatch.setattr(llama, 'stop_started_process', lambda process: order.append('termination_requested'))
    runtime.stop()
    assert order == ['termination_requested', 'exited']
    assert runtime.process is None and runtime.process_key is None


def test_completion_shares_parser_and_archives_actual_wire(engine, monkeypatch):
    monkeypatch.setattr(engine.llama, 'complete', lambda *args: reply())
    result = engine.predict(request('llama-normal-001'))
    assert result['status'] == 'predicted'
    assert result['execution_status'] == 'not_executed_by_model_service'
    assert result['metrics']['tokens']['cached_tokens'] == 1000
    assert result['peak_allocated_bytes'] is None
    archive = Path(result['output_directory'])
    wire = json.loads((archive / 'attempt-00-input.json').read_text())
    assert wire['stream'] is False and wire['cache_prompt'] is True
    assert 'Authorization' not in wire
    assert wire['messages'][-1]['content'][0]['type'] == 'image_url'


@pytest.mark.parametrize('conversation', [False, True])
def test_a_evidence_reaches_llama_with_current_image_last_and_auditable_sources(engine, monkeypatch, conversation):
    from test_task_conversations import image_experience

    context = image_experience()
    calls = []
    monkeypatch.setattr(engine.llama, 'complete', lambda payload, cancelled: (calls.append(payload), reply())[1])
    extra = {'conversation_id': 'a-evidence-conversation', 'conversation_start': True} if conversation else {}
    result = engine.predict(request('a-evidence-001', experience_context=context, **extra))
    assert result['status'] == 'predicted'
    parts = [part for msg in calls[0]['messages'] if isinstance(msg['content'], list) for part in msg['content']]
    pictures = [Image.open(io.BytesIO(base64.b64decode(part['image_url']['url'].split(',', 1)[1])))
                for part in parts if part['type'] == 'image_url']
    assert [picture.getpixel((0, 0)) for picture in pictures] == [(0, 0, 255), (0, 128, 0), (255, 0, 0)]
    assert any(context['model_input'] in part['text'] for part in parts if part['type'] == 'text')
    archived = json.loads((Path(result['output_directory']) / 'input.json').read_text(encoding='utf-8'))
    assert [source['kind'] for source in archived['image_sources']] == ['experience', 'experience', 'current']
    assert [source['evidence_id'] for source in archived['image_sources'][:2]] == ['0000', '0001']
    if conversation:
        second = engine.predict(request('a-evidence-002', conversation_id='a-evidence-conversation'))
        assert second['status'] == 'predicted'
        assert second['context_management']['new_image_count'] == 1
        parts = [p for m in calls[-1]['messages'] if isinstance(m['content'], list) for p in m['content']]
        assert sum(p['type'] == 'image_url' for p in parts) == 4  # 2 frozen + previous/current observations.


def test_a_overflow_evicts_observations_but_never_drops_selected_evidence(engine, monkeypatch):
    from test_task_conversations import image_experience

    calls = []

    def complete(payload, cancelled):
        calls.append(payload)
        if len(calls) == 2:
            raise llama.ContextFull('test context pressure')
        return reply()

    monkeypatch.setattr(engine.llama, 'complete', complete)
    first = engine.predict(request('a-context-001', conversation_id='a-context-conversation', conversation_start=True,
                                   experience_context=image_experience()))
    second = engine.predict(request('a-context-002', conversation_id='a-context-conversation'))
    assert first['status'] == second['status'] == 'predicted'
    assert second['context_management']['removed_images'] == 1
    sources = json.loads((Path(second['output_directory']) / 'input.json').read_text(encoding='utf-8'))['image_sources']
    assert [source['kind'] for source in sources] == ['experience', 'experience', 'current']

    monkeypatch.setattr(engine.llama, 'complete', lambda *args: (_ for _ in ()).throw(llama.ContextFull('full')))
    full = engine.predict(request('a-context-003', conversation_id='a-context-new', conversation_start=True,
                                  experience_context=image_experience()))
    assert full['status'] == 'error'
    assert full['context_management']['removed_images'] == 0
    assert len(engine.conversation.evidence_images) == 2


@pytest.mark.parametrize('finish', ['length', 'error', None])
def test_truncated_output_never_commits_or_dispatches(engine, monkeypatch, finish):
    monkeypatch.setattr(engine.llama, 'complete', lambda *args: reply(finish))
    result = engine.predict(request('llama-truncate-001', conversation_id='conversation-truncate', conversation_start=True))
    assert result['status'] == 'error' and 'truncated' in result['error']
    assert not engine.conversation.turns


def test_pre_cancel_never_loads_or_generates(engine, monkeypatch):
    monkeypatch.setattr(engine.llama, 'load', lambda *args: pytest.fail('must not load'))
    engine.cancel('llama-cancel-001')
    result = engine.predict(request('llama-cancel-001'))
    assert result['status'] == 'cancelled'
    assert not engine._cancellations['llama-cancel-001']['active']


def test_late_completed_reply_after_cancellation_never_commits(engine, monkeypatch):
    def complete(payload, cancelled):
        cancelled.set()
        return reply()
    monkeypatch.setattr(engine.llama, 'complete', complete)
    result = engine.predict(request('llama-late-001', conversation_id='llama-late-conversation', conversation_start=True))
    assert result['status'] == 'cancelled'
    assert not engine.conversation.turns


def test_context_eviction_keeps_text_and_current_image(engine, monkeypatch):
    calls = []

    def complete(payload, cancelled):
        calls.append(payload)
        if len(calls) == 2:
            raise llama.ContextFull('engine context exhausted')
        return reply()

    monkeypatch.setattr(engine.llama, 'complete', complete)
    first = engine.predict(request('llama-context-001', conversation_id='llama-conversation', conversation_start=True))
    second = engine.predict(request('llama-context-002', conversation_id='llama-conversation'))
    assert first['status'] == second['status'] == 'predicted'
    assert second['context_management']['removed_images'] == 1
    assert second['context_management']['removed_text_messages'] == 0
    assert calls[1]['messages'][1]['content'][1]['type'] == 'image_url'
    assert calls[2]['messages'][1]['content'][0]['type'] == 'text'
    assert calls[1]['messages'][1]['content'][0] == calls[2]['messages'][1]['content'][0]
    assert calls[2]['messages'][-1]['content'][1]['type'] == 'image_url'


def test_context_full_without_old_images_does_not_truncate(engine, monkeypatch):
    def complete(*args):
        raise llama.ContextFull('full')
    monkeypatch.setattr(engine.llama, 'complete', complete)
    result = engine.predict(request('llama-full-001'))
    assert result['status'] == 'error'
    assert result['context_management']['removed_images'] == 0


def test_three_turn_wire_repeats_exact_original_task_with_current_image(engine, monkeypatch):
    from copy import deepcopy
    calls, outputs = [], []
    def complete(payload, cancelled):
        calls.append(deepcopy(payload))
        return reply()
    monkeypatch.setattr(engine.llama, 'complete', complete)
    task = '只复制 PNG，不删除原文件。\r\nKeep {{literal braces}} and exact paths.'
    for index in range(3):
        result = engine.predict(request(f'images-only-{index}', task=task,
            conversation_id='images-only-conversation', conversation_start=index == 0,
            history=[{'executed': True, 'effect': 'unverifiable', 'input_call_status': 'unknown'}],
            execution_feedback={'status': 'reobserve', 'executed': None, 'reason': 'HWND SECRET_ERROR'},
            execution_context={'current_window': {'title': 'HOST_CONTEXT'}}))
        assert result['status'] == 'predicted'
        outputs.append(result['raw_output'])
    messages = calls[-1]['messages']
    assert [m['role'] for m in messages] == ['system', 'user', 'assistant', 'user', 'assistant', 'user']
    assert task not in messages[0]['content']
    assert task in messages[1]['content'][0]['text']
    assert messages[2]['content'] == outputs[0] and messages[4]['content'] == outputs[1]
    assert [part['type'] for part in messages[1]['content']] == ['text', 'image_url']
    assert [part['type'] for part in messages[3]['content']] == ['text', 'image_url']
    assert [part['type'] for part in messages[5]['content']] == ['text', 'image_url']
    assert messages[3]['content'][0]['text'] == 'Instruction: ' + task
    assert messages[5]['content'][0]['text'] == 'Instruction: ' + task
    serialized = json.dumps(messages, ensure_ascii=False)
    for forbidden in ('SECRET_ERROR', 'HOST_CONTEXT', 'input_call_status', 'unverifiable', 'Continue the original task.'):
        assert forbidden not in serialized
    assert calls[0]['messages'] == messages[:2]


def test_cancel_inflight_stops_only_owned_child_and_discards_reply(tmp_path, monkeypatch):
    runtime = llama.LlamaRuntime(tmp_path, tmp_path)
    cancelled, release = threading.Event(), threading.Event()
    stops = []
    def complete(*args):
        cancelled.set()
        release.wait(3)
        return reply()
    def stop():
        stops.append(True)
        release.set()
    monkeypatch.setattr(runtime, 'request', complete)
    monkeypatch.setattr(runtime, 'stop', stop)
    with pytest.raises(llama.GenerationCancelled):
        runtime.complete({}, cancelled)
    assert stops == [True]


@pytest.mark.parametrize('error_type,expected', [('exceed_context_size_error', llama.ContextFull),
                                               ('invalid_request_error', RuntimeError)])
def test_only_engine_context_error_allows_retry(tmp_path, error_type, expected):
    runtime = llama.LlamaRuntime(tmp_path, tmp_path)
    def fail(*args, **kwargs):
        raise HTTPError('http://127.0.0.1', 400, 'bad', {},
                        io.BytesIO(json.dumps({'error': {'type': error_type}}).encode()))
    runtime.opener = SimpleNamespace(open=fail)
    with pytest.raises(expected):
        runtime.request('/v1/chat/completions', {})


def test_owned_gui_llama_is_not_classified_as_independent_8b():
    from trace2task.local_model_service import is_model_process, process_service
    process = {'Name': 'llama-server.exe', 'CommandLine': 'llama-server.exe -m '
        'D:/Models/Trace2Task-GUI/gguf/gui-owl-2b/model-BF16.gguf --alias trace2task-gui-owl-2b'}
    assert is_model_process(process)
    assert process_service(process) == 'gui'
    process['CommandLine'] = 'llama-server.exe -m D:/other/model.gguf'
    assert not is_model_process(process)
