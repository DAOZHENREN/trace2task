"""Unit tests for resident GUI service observability and cancellation primitives.

These intentionally use no CUDA model: GPU loading is covered by the manual
local acceptance run, while request cancellation must remain deterministic.
"""
import base64
import contextlib
import importlib.util
import json
import sys
import threading
import types
import weakref
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def service_module():
    path = Path(__file__).parents[1] / "scripts" / "local_gui" / "server.py"
    spec = importlib.util.spec_from_file_location("trace2task_local_gui_service", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cancel_before_claim_is_preserved(service_module, tmp_path):
    engine = service_module.Engine(tmp_path, tmp_path / "output")
    request_id = "request-before-predict"
    receipt = engine.cancel(request_id)
    assert receipt == {"status": "cancel_requested", "request_id": request_id, "active": False}
    assert engine.claim_cancellation(request_id).is_set()


def test_active_cancel_sets_same_event(service_module, tmp_path):
    engine = service_module.Engine(tmp_path, tmp_path / "output")
    request_id = "request-active-predict"
    event = engine.claim_cancellation(request_id)
    assert not event.is_set()
    assert engine.cancel(request_id)["active"] is True
    assert event.is_set()
    engine.finish_cancellation(request_id)
    assert engine._cancellations[request_id]["active"] is False


def test_active_request_is_not_pruned_while_generation_is_slow(service_module, tmp_path, monkeypatch):
    engine = service_module.Engine(tmp_path, tmp_path / "output")
    request_id = "request-slow-generation"
    event = engine.claim_cancellation(request_id)
    monkeypatch.setattr(service_module.time, "monotonic", lambda: 10_000_000)
    engine._prune_cancellations()
    assert engine._cancellations[request_id]["event"] is event
    assert engine.cancel(request_id)["active"] is True
    assert event.is_set()


def test_request_id_is_strict(service_module, tmp_path):
    engine = service_module.Engine(tmp_path, tmp_path / "output")
    assert engine._request_id("safe_request-123") == "safe_request-123"
    for value in ("short", "contains space", "../unsafe", 7):
        with pytest.raises(ValueError):
            engine._request_id(value)


def test_generation_stopper_reads_event(service_module):
    event = threading.Event()
    stopper = service_module._CancelGeneration(event)
    assert stopper(None, None) is False
    event.set()
    assert stopper(None, None) is True


def test_memory_snapshot_has_peak_fields(service_module):
    class FakeCuda:
        @staticmethod
        def is_available(): return True
        @staticmethod
        def mem_get_info(): return (9, 12)
        @staticmethod
        def memory_allocated(): return 2
        @staticmethod
        def memory_reserved(): return 3
        @staticmethod
        def max_memory_allocated(): return 4
        @staticmethod
        def max_memory_reserved(): return 5
    class FakeTorch:
        cuda = FakeCuda()
    assert service_module.Engine._memory(FakeTorch(), peak=True) == {
        "available": True, "allocated_bytes": 2, "reserved_bytes": 3,
        "free_bytes": 9, "total_bytes": 12,
        "peak_allocated_bytes": 4, "peak_reserved_bytes": 5,
    }


class _FakeTensor:
    def __init__(self, values):
        self.values = values
        self.shape = (1, len(values))

    def tolist(self):
        return list(self.values)

    def __len__(self):
        return len(self.values)

    def __getitem__(self, value):
        if isinstance(value, tuple):
            return _FakeTensor(self.values[value[1]])
        return self.values[value]


class _FakeInputs(dict):
    def __init__(self):
        self.input_ids = _FakeTensor([1, 2, 3])
        self.attention_mask = _FakeTensor([1, 1, 1])
        self.image_grid_thw = _FakeTensor([1, 1, 1])
        super().__init__(input_ids=self.input_ids, attention_mask=self.attention_mask,
                         image_grid_thw=self.image_grid_thw)

    def to(self, device):
        assert device == "cuda"
        return self


class _FakeImage:
    width, height = 4, 3
    size = (4, 3)

    def convert(self, mode):
        assert mode == "RGB"
        return self

    def save(self, path):
        Path(path).write_bytes(b"fake-image")

    def copy(self):
        return _FakeImage()


class _FakeCuda:
    @staticmethod
    def is_available(): return False
    @staticmethod
    def synchronize(): pass
    @staticmethod
    def reset_peak_memory_stats(): pass
    @staticmethod
    def empty_cache(): pass


class _FakeTorch:
    cuda = _FakeCuda()

    @staticmethod
    def inference_mode():
        return contextlib.nullcontext()


class _FakeProcessor:
    def apply_chat_template(self, messages, **kwargs):
        return "formatted prompt"

    def __call__(self, **kwargs):
        return _FakeInputs()

    def decode(self, ids, **kwargs):
        return '{"actions":[{"skill":"wait","args":{"duration_ms":100}}]}'


class _FakeDraw:
    def ellipse(self, *args, **kwargs): pass
    def text(self, *args, **kwargs): pass
    def line(self, *args, **kwargs): pass


def _install_predict_fakes(monkeypatch, cuda=None):
    from trace2task import local_gui_memory
    from trace2task.local_gui_prefix_cache import TextPrefixCache
    # These tests cover orchestration; the GPU cache has its own integration check.
    monkeypatch.setattr(TextPrefixCache, 'prepare', lambda self, model, inputs:
                        (None, {'status': 'test_stub', 'reused_tokens': 0}))
    monkeypatch.setattr(local_gui_memory, 'memory_admission', lambda *a, **kw:
                        {'fits': True, 'basis': 'test_memory_stub'})
    cuda = cuda or _FakeCuda()
    torch = types.ModuleType("torch")
    torch.cuda = cuda
    torch.OutOfMemoryError = MemoryError
    torch.inference_mode = _FakeTorch.inference_mode
    transformers = types.ModuleType("transformers")
    transformers.StoppingCriteriaList = list
    pil = types.ModuleType("PIL")
    pil.Image = types.SimpleNamespace(open=lambda stream: _FakeImage())
    pil.ImageDraw = types.SimpleNamespace(Draw=lambda image: _FakeDraw())
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    monkeypatch.setitem(sys.modules, "PIL", pil)


def _request(request_id):
    return {
        "request_id": request_id,
        "model": "qwen3-vl-2b",
        "task": "wait briefly",
        "history": [],
        "image": base64.b64encode(b"not-a-real-image").decode(),
    }


def _experience_context():
    return {
        "task_id": "Notepad draft",
        "goal": "Type the requested text.",
        "completion": {"mode": "state", "success_condition": "text visible"},
        "state_index": [{"id": "editing", "name": "Editing"}],
        "candidate_state": {"id": "editing", "name": "Editing"},
        "candidate_is_unverified": True,
        "nearby_states": [], "outgoing_transitions": [], "candidate_terminals": [],
        "human_guidance": {"revision": 2, "rules": []},
    }


def test_only_current_image_is_accepted_per_turn(service_module, tmp_path, monkeypatch):
    _install_predict_fakes(monkeypatch)
    engine = _engine_with_model(service_module, tmp_path, lambda **kw: _FakeTensor([1, 2, 3, 99]))
    observed = {}
    def processor(**kwargs):
        observed.update(kwargs)
        return _FakeInputs()
    class Processor(_FakeProcessor):
        def __call__(self, **kwargs):
            return processor(**kwargs)
    engine.processor = Processor()
    request = _request('two-images')
    request.update(previous_image=request['image'], cua_context={})
    with pytest.raises(ValueError, match='Only the current'):
        engine.predict(request)
    assert observed == {}


def test_custom_system_and_turn_prompt_are_actual_archived_model_input(service_module, tmp_path, monkeypatch):
    from trace2task.local_gui_protocol import TURN_TEMPLATE

    _install_predict_fakes(monkeypatch)
    engine = _engine_with_model(service_module, tmp_path, lambda **kw: _FakeTensor([1, 2, 3, 99]))
    request = _request('custom-prompt-001')
    request['prompt_profile'] = {'system_prompt': 'Custom system rules',
                                 'turn_template': 'CUSTOM\n' + TURN_TEMPLATE}
    result = engine.predict(request)
    archived = json.loads((Path(result['output_directory'])/'input.json').read_text(encoding='utf-8'))
    assert archived['messages'][0]['content'] == 'Custom system rules'
    assert archived['messages'][1]['content'][-1]['text'].startswith('CUSTOM\n')
    assert 'wait briefly' in archived['messages'][1]['content'][-1]['text']

    request['request_id'] = 'custom-review-001'
    request['cua_context'] = {'purpose': 'verify_completion'}
    engine.processor.decode = lambda *_args, **_kwargs: json.dumps({
        'verdict': 'incomplete', 'evidence': 'still open', 'missing': 'not finished'})
    reviewed = engine.predict(request)
    review_input = json.loads((Path(reviewed['output_directory'])/'input.json').read_text(encoding='utf-8'))
    assert 'NOT an operator' in review_input['messages'][0]['content']


def _engine_with_model(service_module, tmp_path, generate):
    model = types.SimpleNamespace(
        generation_config=types.SimpleNamespace(eos_token_id=99, cache_implementation=None),
        generate=generate,
    )
    engine = service_module.Engine(tmp_path, tmp_path / "out")
    engine.model = model
    engine.processor = _FakeProcessor()
    engine.key = "qwen3-vl-2b"
    engine.load = lambda key: None
    return engine


def test_task_conversation_reuses_full_experience_and_prior_answer(service_module, tmp_path, monkeypatch):
    import hashlib

    _install_predict_fakes(monkeypatch)
    engine = _engine_with_model(service_module, tmp_path, lambda **kw: _FakeTensor([1, 2, 3, 99]))
    text = 'Full experience\r\n{"actions": []}'
    request = _request('conversation-first')
    request.update(conversation_id='task-session-001', conversation_start=True,
                   experience_context={'kind': 'trace_sequence', 'task_id': 'test',
                       'model_input': text, 'sha256': hashlib.sha256(text.encode()).hexdigest()})
    first = engine.predict(request)
    assert first['status'] == 'predicted'
    first_saved = json.loads((Path(first['output_directory'])/'input.json').read_text(encoding='utf-8'))
    assert first_saved['new_messages'] == [first_saved['messages'][1]]
    assert text in first_saved['new_messages'][0]['content'][0]['text']
    request.update(request_id='conversation-second', conversation_start=False)
    request.pop('experience_context')
    second = engine.predict(request)
    saved = json.loads((Path(second['output_directory'])/'input.json').read_text(encoding='utf-8'))
    assert second['conversation_id'] == 'task-session-001'
    assert [m['role'] for m in saved['messages']] == ['system', 'user', 'assistant', 'user']
    assert json.dumps(saved['messages']).count('Full experience') == 1
    assert text in saved['messages'][1]['content'][0]['text']
    assert text not in saved['messages'][0]['content']
    assert '【任务经验开始】' in saved['messages'][1]['content'][0]['text']
    assert '【任务经验结束】' in saved['messages'][1]['content'][0]['text']
    assert saved['messages'][-1]['content'] == [
        {'type': 'text', 'text': 'Instruction: wait briefly'}, {'type': 'image'}]
    assert saved['new_messages'] == [saved['messages'][-1]]
    assert saved['configuration']['image_count'] == 2
    engine.conversation = None
    with pytest.raises(ValueError, match='lost'):
        engine.predict({**request, 'request_id': 'conversation-third'})


def test_vram_pressure_discards_old_image_and_reports_exact_token_delta(service_module, tmp_path, monkeypatch):
    from trace2task import local_gui_memory

    _install_predict_fakes(monkeypatch)
    monkeypatch.setattr(local_gui_memory, 'memory_admission', lambda model, inputs, *a, **kw:
                        {'fits': inputs.input_ids.shape[-1] == 3, 'basis': 'simulated_vram_pressure'})
    engine = _engine_with_model(service_module, tmp_path, lambda **kw: _FakeTensor([1, 2, 3, 99]))
    class SizedProcessor(_FakeProcessor):
        def __call__(self, **kwargs):
            inputs = _FakeInputs()
            if len(kwargs['images']) > 1:
                inputs.input_ids = _FakeTensor([1] * 6000)
            return inputs
    engine.processor = SizedProcessor()
    request = _request('discard-first')
    request.update(conversation_id='discard-session', conversation_start=True)
    assert engine.predict(request)['status'] == 'predicted'
    request.update(request_id='discard-second', conversation_start=False)
    second = engine.predict(request)
    assert second['status'] == 'predicted'
    assert second['dropped_history_images'] == 1
    saved = json.loads((Path(second['output_directory'])/'input.json').read_text(encoding='utf-8'))
    assert [m['role'] for m in saved['messages']] == ['system', 'user', 'assistant', 'user']
    assert saved['messages'][1]['content'][-1]['text']
    assert saved['messages'][2]['content']
    assert saved['configuration']['image_count'] == 1
    report = saved['context_management']
    assert report['evictions'][0]['reason'] == 'vram_pressure'
    assert report['removed_tokens'] == 5997
    assert report['removed_text_messages'] == 0
    assert report['input_tokens_before_cleanup'] == 6000
    assert report['input_tokens_after_cleanup'] == 3
    assert second['context_management'] == report


def test_no_application_token_cap_when_vram_is_available(service_module, tmp_path, monkeypatch):
    _install_predict_fakes(monkeypatch)
    engine = _engine_with_model(service_module, tmp_path,
                                lambda **kw: _FakeTensor([1] * 7000 + [99]))
    class LongInputProcessor(_FakeProcessor):
        def __call__(self, **kwargs):
            inputs = _FakeInputs()
            inputs.input_ids = inputs['input_ids'] = _FakeTensor([1] * 7000)
            return inputs
    engine.processor = LongInputProcessor()
    result = engine.predict(_request('long-vram-admitted'))
    assert result['status'] == 'predicted'
    assert result['metrics']['tokens']['input_tokens'] == 7000
    assert result['context_management']['removed_images'] == 0
    saved = json.loads((Path(result['output_directory'])/'input.json').read_text(encoding='utf-8'))
    assert 'max_input_tokens' not in saved['configuration']


def test_memory_pressure_without_old_images_fails_without_deleting_text(service_module, tmp_path, monkeypatch):
    from trace2task import local_gui_memory

    _install_predict_fakes(monkeypatch)
    monkeypatch.setattr(local_gui_memory, 'memory_admission', lambda *a, **kw: {'fits': False})
    engine = _engine_with_model(service_module, tmp_path,
                                lambda **kw: pytest.fail('Do not infer when even the current input cannot fit'))
    request = _request('no-evictable-image')
    request.update(conversation_id='no-eviction-session', conversation_start=True)
    result = engine.predict(request)
    assert result['status'] == 'error'
    assert '全部文本' in result['error']
    assert result['context_management']['removed_text_messages'] == 0
    assert result['context_management']['inference_attempts'] == []


def test_real_model_context_boundary_is_not_ignored(service_module, tmp_path, monkeypatch):
    _install_predict_fakes(monkeypatch)
    engine = _engine_with_model(service_module, tmp_path, lambda **kw: pytest.fail('Must not generate'))
    engine.model.config = types.SimpleNamespace(text_config=types.SimpleNamespace(max_position_embeddings=512))
    result = engine.predict(_request('model-window-boundary'))
    assert result['status'] == 'error'
    assert 'model_context_window' in result['error']
    assert result['context_management']['removed_text_messages'] == 0


def test_cuda_oom_retries_inference_after_image_only_eviction(service_module, tmp_path, monkeypatch):
    _install_predict_fakes(monkeypatch)
    calls = []
    def generate(**kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            raise MemoryError('simulated CUDA OOM before any action')
        return _FakeTensor([1, 2, 3, 99])
    engine = _engine_with_model(service_module, tmp_path, generate)
    request = _request('oom-first-round')
    request.update(conversation_id='oom-test-session', conversation_start=True)
    assert engine.predict(request)['status'] == 'predicted'
    request.update(request_id='oom-second-round', conversation_start=False)
    result = engine.predict(request)
    assert result['status'] == 'predicted'
    assert len(calls) == 3
    report = result['context_management']
    assert report['evictions'][0]['reason'] == 'cuda_out_of_memory'
    assert [a['status'] for a in report['inference_attempts']] == ['cuda_out_of_memory_no_output', 'generated']
    root = Path(result['output_directory'])
    failed = json.loads((root/'attempt-00-input.json').read_text(encoding='utf-8'))
    retried = json.loads((root/'attempt-01-input.json').read_text(encoding='utf-8'))
    assert len(failed['image_order']) == 2 and len(retried['image_order']) == 1
    assert set(failed['image_order']).isdisjoint(retried['image_order'])
    assert [m['role'] for m in retried['messages']] == ['system', 'user', 'assistant', 'user']
    assert report['removed_text_messages'] == 0


def test_owl_window_hover_is_preserved_for_execution_adapter(
        service_module, tmp_path, monkeypatch):
    _install_predict_fakes(monkeypatch)
    engine = _engine_with_model(service_module, tmp_path,
                                lambda **kw: _FakeTensor([1, 2, 3, 99]))
    engine.processor.decode = lambda *_args, **_kwargs: (
        '<tool_call>{"name":"computer_use","arguments":'
        '{"action":"mouse_move","coordinate":[200,300]}}</tool_call>')
    request = _request('owl-hover-rejection')
    request.update(model='gui-owl-2b', cua_context={'current_window': {'pid': 1, 'window_id': 2}})
    output = engine.predict(request)
    assert output['status'] == 'predicted'
    assert output['execution_status'] == 'not_executed_by_model_service'
    assert output['prediction']['actions'] == [
        {'skill': 'move_cursor', 'args': {'x': .2, 'y': .3}}]
    archived = json.loads((Path(output['output_directory']) / 'input.json').read_text(encoding='utf-8'))
    assert 'mouse_move' in archived['messages'][0]['content']


@pytest.mark.parametrize('context_field', ['cua_context', 'execution_context'])
def test_desktop_capability_context_does_not_switch_model_to_cua_mode(
        service_module, tmp_path, monkeypatch, context_field):
    _install_predict_fakes(monkeypatch)
    engine = _engine_with_model(service_module, tmp_path,
                                lambda **kw: _FakeTensor([1, 2, 3, 99]))
    engine.processor.decode = lambda *_args, **_kwargs: (
        '<tool_call>{"name":"computer_use","arguments":'
        '{"action":"wait","time":1}}</tool_call>')
    request = _request('desktop-capabilities')
    request.update(model='gui-owl-2b', **{context_field: {
        'execution_scope': 'win32_desktop',
        'capabilities': {'available_skills': ['click', 'move_cursor']},
    }})
    output = engine.predict(request)
    assert output['status'] == 'predicted'
    archived = json.loads((Path(output['output_directory']) / 'input.json').read_text(encoding='utf-8'))
    from trace2task.local_gui_owl_prompt import OWL_PROMPT
    assert archived['messages'][0]['content'] == OWL_PROMPT
    assert 'Win32 desktop' not in archived['messages'][0]['content']
    assert 'Cua window' not in archived['messages'][0]['content']
    assert 'win32_desktop' not in archived['messages'][1]['content'][-1]['text']
    assert 'available_skills' not in archived['messages'][1]['content'][-1]['text']

    window_request = _request('window-capabilities-' + context_field)
    window_request.update(model='gui-owl-2b', **{context_field: {
        'execution_scope': 'cua_window',
        'capabilities': {'available_skills': ['click']},
    }})
    window_output = engine.predict(window_request)
    window_input = json.loads((Path(window_output['output_directory']) / 'input.json').read_text(encoding='utf-8'))
    assert window_input['messages'][0]['content'] == archived['messages'][0]['content']
    assert window_input['messages'][1]['content'][-1]['text'] == archived['messages'][1]['content'][-1]['text']


def test_execution_context_rejects_ambiguous_legacy_and_generic_fields(
        service_module, tmp_path, monkeypatch):
    _install_predict_fakes(monkeypatch)
    engine = _engine_with_model(service_module, tmp_path,
                                lambda **kw: _FakeTensor([1, 2, 3, 99]))
    request = _request('ambiguous-execution-context')
    request.update(cua_context={}, execution_context={})
    with pytest.raises(ValueError, match='Ambiguous execution context'):
        engine.predict(request)


def test_malformed_model_action_is_recoverable_only_before_input(
        service_module, tmp_path, monkeypatch):
    _install_predict_fakes(monkeypatch)
    engine = _engine_with_model(service_module, tmp_path,
                                lambda **kw: _FakeTensor([1, 2, 3, 99]))
    engine.processor.decode = lambda *_args, **_kwargs: '<tool_call>{broken JSON}</tool_call>'
    request = _request('owl-malformed-reply')
    request['model'] = 'gui-owl-2b'
    output = engine.predict(request)
    assert output['status'] == 'format_rejected'
    assert output['execution_status'] == 'no_input_sent'
    assert 'prediction' not in output
    assert '{broken JSON}' in output['raw_output']
    archived = json.loads((Path(output['output_directory']) / 'outcome.json').read_text(encoding='utf-8'))
    assert archived['status'] == 'format_rejected'


@pytest.mark.parametrize('cua', [False, True])
def test_format_feedback_stays_out_of_actual_model_input(
        service_module, tmp_path, monkeypatch, cua):
    _install_predict_fakes(monkeypatch)
    engine = _engine_with_model(service_module, tmp_path,
                                lambda **kw: _FakeTensor([1, 2, 3, 99]))
    request = _request(f'format-feedback-{cua}')
    feedback = {'status': 'format_rejected', 'executed': False,
                'reason': 'Expected exactly one tool_call'}
    if cua:
        request['cua_context'] = {'execution_feedback': feedback}
    else:
        request['execution_feedback'] = feedback
    output = engine.predict(request)
    assert output['status'] == 'predicted'
    archived = json.loads((Path(output['output_directory']) / 'input.json').read_text(encoding='utf-8'))
    turn = archived['messages'][1]['content'][-1]['text']
    assert 'Input not sent:' not in turn
    assert 'Expected exactly one tool_call' not in turn


def test_owl_control_message_is_not_dispatched_as_action(service_module, tmp_path, monkeypatch):
    _install_predict_fakes(monkeypatch)
    engine = _engine_with_model(service_module, tmp_path,
                                lambda **kw: _FakeTensor([1, 2, 3, 99]))
    engine.processor.decode = lambda *_args, **_kwargs: (
        '<tool_call>{"name":"computer_use","arguments":'
        '{"action":"answer","text":"Need clarification"}}</tool_call>')
    request = _request('owl-answer-control')
    request['model'] = 'gui-owl-2b'
    output = engine.predict(request)
    assert output['status'] == 'model_control'
    assert output['control'] == 'answer'
    assert output['execution_status'] == 'no_input_sent'
    assert 'prediction' not in output


def test_predict_persists_complete_input_output_metrics_and_releases_generated(
        service_module, tmp_path, monkeypatch):
    _install_predict_fakes(monkeypatch)
    observed = {}

    def generate(**kwargs):
        value = _FakeTensor([1, 2, 3, 42, 99])
        observed["generated"] = weakref.ref(value)
        return value

    result = _engine_with_model(service_module, tmp_path, generate).predict(_request("predict-success-001"))
    assert result["status"] == "predicted"
    assert result["metrics"]["phase_ms"]["total_ms"] >= 0
    assert result['phase'] == 'prediction'
    assert result['execution_status'] == 'not_executed_by_model_service'
    assert 'executed' not in result and 'task_success_verified' not in result
    assert result["metrics"]["tokens"] == {"input_tokens": 3, "output_tokens": 2}
    assert result["elapsed_seconds"] == result["metrics"]["phase_ms"]["total_ms"] / 1000
    folder = Path(result["output_directory"])
    assert json.loads((folder / "input.json").read_text())["request_id"] == "predict-success-001"
    assert "wait briefly" in json.loads((folder / "input.json").read_text())["messages"][1]["content"][1]["text"]
    assert (folder / "formatted-prompt.txt").read_text() == "formatted prompt"
    assert json.loads((folder / "raw-output.json").read_text())["input_tokens"] == 3
    assert json.loads((folder / "outcome.json").read_text())["status"] == "predicted"
    assert observed["generated"]() is None


@pytest.mark.parametrize("model", ["qwen3-vl-2b", "gui-owl-2b", "mai-ui-2b"])
def test_experience_context_is_bounded_audited_and_visible_to_each_gui_model(
        service_module, tmp_path, monkeypatch, model):
    _install_predict_fakes(monkeypatch)
    monkeypatch.setattr(service_module, "decode", lambda *_args, **_kwargs: {
        "actions": [{"skill": "wait", "args": {"duration_ms": 100}}]
    })
    engine = _engine_with_model(
        service_module, tmp_path, lambda **kwargs: _FakeTensor([1, 2, 3, 99])
    )
    request = _request(f"experience-{model}")
    request.update(model=model, experience_context=_experience_context())
    result = engine.predict(request)
    assert result["status"] == "predicted"
    archived = json.loads(
        (Path(result["output_directory"]) / "input.json").read_text(encoding="utf-8")
    )
    assert archived["experience_context"] == _experience_context()
    prompt_text = archived["messages"][1]["content"][-1]["text"]
    assert "Confirmed Trace-derived experience context" in prompt_text
    assert '"task_id":"Notepad draft"' in prompt_text
    assert "not an action script" in prompt_text


@pytest.mark.parametrize("context", [
    {"task_id": "x"},
    {
        "task_id": "x", "source_scope": "window",
        "applicability": {"process": "notepad.exe", "title": None},
        "semantic": {}, "human_guidance": None,
    },
])
def test_experience_context_invalid_format_is_rejected_before_model_loading(
        service_module, tmp_path, context):
    engine = service_module.Engine(tmp_path, tmp_path / "out")
    request = _request("invalid-experience")
    request["experience_context"] = context
    with pytest.raises(ValueError, match="经验上下文"):
        engine.predict(request)


def test_a_transformers_input_contains_frozen_evidence_and_live_image(service_module, tmp_path, monkeypatch):
    import PIL
    from test_task_conversations import image_experience

    real_pil = PIL
    context = image_experience()
    _install_predict_fakes(monkeypatch)
    monkeypatch.setitem(sys.modules, 'PIL', real_pil)
    engine = _engine_with_model(service_module, tmp_path, lambda **kwargs: _FakeTensor([1, 2, 3, 99]))
    observed = {}

    class Processor(_FakeProcessor):
        def __call__(self, **kwargs):
            observed.update(kwargs)
            return _FakeInputs()

    engine.processor = Processor()
    request = _request('a-transformers-001')
    request.update(image=context['images'][0]['base64'], experience_context=context,
                   conversation_id='a-transformers-conversation', conversation_start=True)
    result = engine.predict(request)
    assert result['status'] == 'predicted'
    assert len(observed['images']) == 3
    assert [picture.getpixel((0, 0)) for picture in observed['images']] == [(0, 0, 255), (0, 128, 0), (0, 0, 255)]
    archived = json.loads((Path(result['output_directory']) / 'input.json').read_text(encoding='utf-8'))
    assert [source['kind'] for source in archived['image_sources']] == ['experience', 'experience', 'current']
    assert archived['messages'][1]['content'][0]['text'].count(context['model_input']) == 1
    assert archived['client_submission']['new_image_count'] == 3


def test_completion_review_is_read_only_not_action_decoding(service_module, tmp_path, monkeypatch):
    _install_predict_fakes(monkeypatch)
    engine = _engine_with_model(service_module, tmp_path,
        lambda **kwargs: _FakeTensor([1, 2, 3, 99]))
    engine.processor.decode = lambda *args, **kwargs: json.dumps({
        'verdict': 'incomplete', 'evidence': 'input empty', 'missing': 'text not entered'})
    request = _request('completion-review-001')
    request['cua_context'] = {'purpose': 'verify_completion'}
    result = engine.predict(request)
    assert result['status'] == 'reviewed'
    assert 'prediction' not in result
    assert result['execution_status'] == 'read_only_no_actions'
    archived = json.loads((Path(result['output_directory'])/'input.json').read_text(encoding='utf-8'))
    assert 'NOT an operator' in archived['messages'][0]['content']
    assert 'Original task: wait briefly' in archived['messages'][1]['content'][1]['text']


def test_completion_review_never_accepts_operator_tool_call(service_module, tmp_path, monkeypatch):
    _install_predict_fakes(monkeypatch)
    engine = _engine_with_model(service_module, tmp_path,
        lambda **kwargs: _FakeTensor([1, 2, 3, 99]))
    engine.processor.decode = lambda *args, **kwargs: '<tool_call>{"action":"click"}</tool_call>'
    request = _request('completion-review-002')
    request['cua_context'] = {'purpose': 'verify_completion'}
    result = engine.predict(request)
    assert result['status'] == 'error'
    assert 'prediction' not in result


def test_predict_cancelled_during_generate_keeps_partial_output_and_metrics(
        service_module, tmp_path, monkeypatch):
    _install_predict_fakes(monkeypatch)
    engine = None

    def generate(**kwargs):
        engine.cancel("predict-cancel-001")
        assert kwargs["stopping_criteria"][0](None, None)
        return _FakeTensor([1, 2, 3, 99])

    engine = _engine_with_model(service_module, tmp_path, generate)
    result = engine.predict(_request("predict-cancel-001"))
    assert result["status"] == "cancelled"
    assert result["reason"] == "cancelled_during_generation"
    folder = Path(result["output_directory"])
    assert json.loads((folder / "raw-output.json").read_text())["cancel_requested"] is True
    assert json.loads((folder / "outcome.json").read_text())["metrics"]["phase_ms"]["generate_ms"] >= 0


@pytest.mark.parametrize('eos', [True, False])
def test_missing_tool_close_recovers_only_after_eos(service_module, tmp_path, monkeypatch, eos):
    _install_predict_fakes(monkeypatch)
    engine = _engine_with_model(service_module, tmp_path,
        lambda **kwargs: _FakeTensor([1, 2, 3, 99 if eos else 42]))
    raw = '<tool_call>{"name":"mobile_use","arguments":{"action":"type","text":"测试","coordinate":[500,800]}}'
    engine.processor.decode = lambda *args, **kwargs: raw
    request = _request('missing-close-check')
    request.update(model='mai-ui-2b', cua_context={})
    result = engine.predict(request)
    assert result['status'] == ('predicted' if eos else 'error')
    if eos:
        assert result['raw_output'] == raw
        assert result['protocol_normalizations'] == ['complete_json_missing_tool_call_close']
    else:
        assert 'truncated' in result['error']


@pytest.mark.parametrize('model', ['qwen3-vl-2b', 'gui-owl-2b', 'mai-ui-2b'])
def test_native_history_has_no_unverified_effect_boilerplate(
        service_module, tmp_path, monkeypatch, model):
    _install_predict_fakes(monkeypatch)
    # Decoders are covered separately; here inspect the exact archived model input.
    monkeypatch.setattr(service_module, 'decode', lambda *args, **kwargs: {
        'actions': [{'skill': 'wait', 'args': {'duration_ms': 100}}]})
    engine = _engine_with_model(service_module, tmp_path, lambda **kwargs: _FakeTensor([1, 2, 3, 99]))
    request = _request('predict-pending-effect')
    request['model'] = model
    raw = '<tool_call>{"name":"computer_use","arguments":{"action":"right_click","coordinate":[144,220]}}</tool_call>'
    request['history'] = [{'step_index': 0, 'model_output': raw, 'input_call_status': 'returned'}]
    result = engine.predict(request)
    assert result['status'] == 'predicted'
    data = json.loads((Path(result['output_directory']) / 'input.json').read_text(encoding='utf-8'))
    text = data['messages'][1]['content'][1]['text']
    assert json.dumps(raw) in text
    assert 'input_call_status' not in text
    assert 'Execution feedback:' not in text
    assert 'do not repeat' not in text
    assert 'unverifiable' not in text
    assert 'delivery-path warning' not in text


def test_predict_error_keeps_auditable_input_and_metrics(service_module, tmp_path, monkeypatch):
    _install_predict_fakes(monkeypatch)

    def generate(**kwargs):
        raise RuntimeError("simulated generation failure")

    result = _engine_with_model(service_module, tmp_path, generate).predict(_request("predict-error-001"))
    assert result["status"] == "error"
    assert "simulated generation failure" in result["error"]
    assert result["metrics"]["phase_ms"]["generate_ms"] >= 0
    folder = Path(result["output_directory"])
    assert (folder / "input.json").is_file()
    assert (folder / "error.log").is_file()
    assert json.loads((folder / "outcome.json").read_text())["status"] == "error"


def test_unknown_click_prompt_keeps_native_action_without_driver_diagnostics(
        service_module, tmp_path, monkeypatch):
    _install_predict_fakes(monkeypatch)
    monkeypatch.setattr(service_module, 'decode', lambda *args, **kwargs: {
        'actions': [{'skill': 'wait', 'args': {'duration_ms': 100}}]})
    engine = _engine_with_model(service_module, tmp_path, lambda **kwargs: _FakeTensor([1, 2, 3, 99]))
    request = _request('unknown-click')
    raw = '<tool_call>{"name":"computer_use","arguments":{"action":"right_click","coordinate":[144,220]}}</tool_call>'
    request.update(model='gui-owl-2b', history=[{
        'step_index': 1, 'model_output': raw, 'input_call_status': 'unknown'}],
        execution_feedback={'status': 'reobserve', 'executed': None,
                            'reason': 'Cua returned a non-JSON error: exact target HWND 0x150b94',
                            'action': {'skill': 'click', 'args': {'x': .144, 'y': .22}},
                            'instruction': 'do not blindly repeat the click'})
    result = engine.predict(request)
    data = json.loads((Path(result['output_directory']) / 'input.json').read_text(encoding='utf-8'))
    text = data['messages'][1]['content'][1]['text']
    assert json.dumps(raw) in text
    assert 'input_call_status' not in text
    assert 'input outcome unknown' not in text
    assert '0x150b94' not in text and 'non-JSON' not in text
    assert 'do not blindly repeat' not in text and '0.144' not in text


@pytest.mark.parametrize('model', ['gui-owl-2b', 'mai-ui-2b'])
def test_backend_capabilities_stay_out_of_native_model_input(service_module, tmp_path, monkeypatch, model):
    _install_predict_fakes(monkeypatch)
    monkeypatch.setattr(service_module, 'decode', lambda *args, **kwargs: {
        'actions': [{'skill': 'wait', 'args': {'duration_ms': 100}}]})
    engine = _engine_with_model(service_module, tmp_path, lambda **kwargs: _FakeTensor([1, 2, 3, 99]))
    request = _request('capability-feedback')
    request.update(model=model, cua_context={
        'execution_scope': 'cua_window',
        'capabilities': {'available_skills': ['click'], 'unavailable_skills': {'type_text': 'no editable control'}},
        'execution_feedback': {'status': 'rejected', 'executed': False}})
    result = engine.predict(request)
    data = json.loads((Path(result['output_directory']) / 'input.json').read_text(encoding='utf-8'))
    text = data['messages'][1]['content'][1]['text']
    assert 'no editable control' not in text
    assert 'available_skills' not in text
    assert 'Input not sent:' not in text
    assert 'Cua window' not in text


def test_win32_rejection_stays_out_of_model_prompt(
        service_module, tmp_path, monkeypatch):
    _install_predict_fakes(monkeypatch)
    monkeypatch.setattr(service_module, 'decode', lambda *args, **kwargs: {
        'actions': [{'skill': 'wait', 'args': {'duration_ms': 100}}]})
    engine = _engine_with_model(service_module, tmp_path, lambda **kwargs: _FakeTensor([1, 2, 3, 99]))
    request = _request('win32-feedback')
    request.update(model='gui-owl-2b', cua_context=None, execution_feedback={
        'status': 'completion_rejected', 'executed': False, 'missing': 'Text is not visible'})
    result = engine.predict(request)
    data = json.loads((Path(result['output_directory']) / 'input.json').read_text(encoding='utf-8'))
    text = data['messages'][1]['content'][1]['text']
    assert 'Execution feedback:' not in text
    assert 'Text is not visible' not in text
    assert 'CURRENT window screenshot' not in text


def test_cleanup_metric_failure_does_not_erase_prediction(service_module, tmp_path, monkeypatch):
    class CleanupFailsCuda:
        @staticmethod
        def is_available(): return True
        @staticmethod
        def synchronize(): pass
        @staticmethod
        def reset_peak_memory_stats(): pass
        @staticmethod
        def empty_cache(): raise RuntimeError("cleanup unavailable")
        @staticmethod
        def mem_get_info(): return (9, 12)
        @staticmethod
        def memory_allocated(): return 2
        @staticmethod
        def memory_reserved(): return 3
        @staticmethod
        def max_memory_allocated(): return 4
        @staticmethod
        def max_memory_reserved(): return 5
    _install_predict_fakes(monkeypatch, CleanupFailsCuda())

    result = _engine_with_model(
        service_module, tmp_path, lambda **kwargs: _FakeTensor([1, 2, 3, 99])
    ).predict(_request("predict-cleanup-001"))
    assert result["status"] == "predicted"
    assert "cleanup unavailable" in result["metrics"]["memory"]["cleanup_error"]
    assert result["peak_reserved_bytes"] == 5
