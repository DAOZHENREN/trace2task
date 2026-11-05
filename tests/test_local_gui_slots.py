"""Slot adapter, isolated resident process and task-boundary contracts."""
import base64
import importlib.util
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from trace2task import local_gui_slots as slots
from trace2task.execution_protocol import ActionUnavailable
from trace2task.local_gui_protocol import adapt_capabilities, decode, default_turn_template, prompt
from trace2task.model_registry import profile_for


@pytest.fixture
def engine(tmp_path):
    path = Path(__file__).parents[1] / 'scripts/local_gui/server.py'
    spec = importlib.util.spec_from_file_location('slot_service_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.Engine(tmp_path, tmp_path / 'output', backend='slotread')
    worker = SimpleNamespace(ready=False, loads=0, requests=[], on_predict=None)

    def load():
        if not worker.ready:
            worker.loads += 1
            worker.ready = True

    def predict(image, instruction, previous, request_id):
        worker.requests.append({'image': image, 'instruction': instruction, 'previous': previous})
        if worker.on_predict:
            worker.on_predict(request_id)
        return {'request_id': request_id, 'step': {'action': 'click', 'x': .3, 'y': .4},
                'worker_pid': 123, 'model_loads': worker.loads, 'inference_ms': 1,
                'peak_allocated_bytes': 40, 'peak_reserved_bytes': 80,
                'messages': [{'role': 'system', 'content': slots.SYSTEM},
                             {'role': 'user', 'content': instruction + previous}]}

    worker.load, worker.predict = load, predict
    result.slots = worker
    return result


def request(identity, index=0, task='open settings', **extra):
    stream = io.BytesIO()
    Image.new('RGB', (20, 20), 'white').save(stream, format='PNG')
    return {'model': slots.MODEL, 'image': base64.b64encode(stream.getvalue()).decode(),
            'task': task, 'expected_backend': 'slotread', 'conversation_id': identity,
            'conversation_start': index == 0, 'step_index': index,
            'request_id': f'{identity}-{index}', **extra}


def test_two_tasks_reuse_model_but_not_history(engine):
    first = engine.predict(request('conversation-one'))
    second = engine.predict(request('conversation-one', 1))
    third = engine.predict(request('conversation-two', task='click cancel'))
    assert {r['worker_pid'] for r in (first, second, third)} == {123}
    assert engine.slots.loads == 1
    assert [r['status'] for r in (first, second, third)] == ['predicted'] * 3
    assert engine.slots.requests[0]['previous'] == engine.slots.requests[2]['previous'] == 'None'
    assert engine.slots.requests[1]['previous'] == '1. click(x=0.300, y=0.400)'
    prior = json.loads((Path(second['output_directory']) / 'input.json').read_text(encoding='utf-8'))
    assert prior['history_outputs'] == [first['raw_output']]
    assert 'open settings' not in engine.slots.requests[2]['instruction']
    assert engine.conversation.total_turns == 1 and engine.conversation.history_image_count == 0
    assert engine.runtime_status()['model'] == slots.MODEL
    record = json.loads((Path(third['output_directory']) / 'input.json').read_text(encoding='utf-8'))
    assert record['configuration']['backend'] == 'slotread'
    assert record['input_stage'] == 'inferred'


def test_native_history_keeps_all_steps_without_unused_slots_or_timings():
    outputs = [json.dumps(value) for value in [
        {'action': 'click', 'x': .12345, 'y': .98765, 'key': 'enter', 'x2': .7, 'ms': 200},
        {'action': 'select', 'x': .2, 'y': .3},
        {'action': 'swipe', 'x': .1, 'y': .2, 'x2': .8, 'y2': .9},
        {'action': 'scroll', 'sdir': 'down', 'samt': 0},
        {'action': 'press', 'key': 'enter'}, {'action': 'wait'},
    ]]
    assert slots.native_history(outputs) == (
        '1. click(x=0.123, y=0.988)\n2. click(x=0.200, y=0.300)\n'
        '3. swipe(from=(0.10,0.20), to=(0.80,0.90))\n'
        "4. scroll(direction='down', amount=0)\n5. press(keys='enter')\n6. wait()")
    assert slots.native_history([]) == 'None'


def test_native_history_preserves_unknown_and_rejected_decisions():
    outputs = ['unrecognized output', '{"action":"click","x":2,"y":0.3}',
               '{"action":"type"}', '{"action":"back"}']
    assert slots.native_history(outputs) == '\n'.join(f'{i}. {raw}' for i, raw in enumerate(outputs, 1))


def test_stateless_slot_history_uses_same_adapter(engine):
    output = '{"action":"click","x":0.3,"y":0.4,"x2":0.9,"key":"enter","ms":99}'
    payload = request('stateless-history', history=[{'model_output': output}])
    payload.pop('conversation_id')
    payload.pop('conversation_start')
    result = engine.predict(payload)
    assert result['status'] == 'predicted'
    assert engine.slots.requests[-1]['previous'] == '1. click(x=0.300, y=0.400)'
    record = json.loads((Path(result['output_directory']) / 'input.json').read_text(encoding='utf-8'))
    assert record['history_outputs'] == [output]


def test_cancelled_response_is_drained_without_action_or_reload(engine):
    engine.slots.on_predict = engine.cancel
    cancelled = engine.predict(request('conversation-cancel'))
    assert cancelled['status'] == 'cancelled' and 'prediction' not in cancelled
    assert engine.conversation.total_turns == 0
    assert not engine._cancellations[cancelled['request_id']]['active']
    engine.slots.on_predict = None
    assert engine.predict(request('conversation-next'))['status'] == 'predicted'
    assert engine.slots.loads == 1


def test_precancel_and_review_do_not_load_or_claim_completion(engine):
    engine.cancel('conversation-pre-0')
    assert engine.predict(request('conversation-pre'))['status'] == 'cancelled'
    review = engine.predict(request('conversation-review', execution_context={'purpose': 'verify_completion'}))
    assert review['verification']['verdict'] == 'unknown'
    assert 'prediction' not in review and engine.slots.loads == 0


def test_native_prompt_contract(engine):
    native = {'system_prompt': prompt(slots.MODEL), 'turn_template': default_turn_template(slots.MODEL)}
    assert engine.predict(request('conversation-native', prompt_profile=native))['status'] == 'predicted'
    with pytest.raises(ValueError, match='固定'):
        engine.predict(request('conversation-custom', prompt_profile={**native, 'system_prompt': 'custom'}))
    with pytest.raises(ValueError, match='differs'):
        engine.predict(request('conversation-backend', expected_backend='transformers'))


def test_native_decoder_capabilities_and_model_identity():
    assert profile_for(slots.MODEL, 'slotread').repository == 'infinitylogesh/GUI-Decisions-2B'
    with pytest.raises(ValueError):
        profile_for(slots.MODEL, 'transformers')
    result = adapt_capabilities(slots.MODEL, {'available_skills': ['click', 'type_text', 'press_key', 'launch_app']})
    assert result['available_skills'] == ['click', 'press_key']
    plan = decode(slots.MODEL, '{"action":"select","x":0.1,"y":0.9,"x2":"unused"}')
    assert plan['actions'][0] == {'skill': 'click', 'args': {'x': .1, 'y': .9, 'button': 'left'}}


@pytest.mark.parametrize('value,skill', [
    ({'action': 'swipe', 'x': .1, 'y': .2, 'x2': .7, 'y2': .8}, 'drag'),
    ({'action': 'press', 'key': 'enter'}, 'press_key'),
    ({'action': 'wait'}, 'wait'),
    ({'action': 'scroll', 'sdir': 'down', 'samt': 9}, 'scroll'),
])
def test_supported_slot_actions(value, skill):
    result = slots.decode_slots(value)['actions'][0]
    assert result['skill'] == skill
    if skill == 'scroll':
        assert result['args'] == {'direction': 'down', 'amount': 50, 'by': 'line'}


@pytest.mark.parametrize('action', ['type', 'answer', 'open'])
def test_missing_text_requests_human_without_done_or_empty_text(action):
    result = slots.decode_slots({'action': action, 'text': 'untrusted extra text'})
    assert result['control'] == 'interact' and 'actions' not in result


@pytest.mark.parametrize('value', [
    {'action': 'click', 'x': True, 'y': .5}, {'action': 'click', 'x': float('nan'), 'y': .5},
    {'action': 'click', 'x': 1.5, 'y': .5}, {'action': 'swipe', 'x': .2, 'y': .1},
    {'action': 'scroll', 'samt': True, 'sdir': 'down'}, {'action': 'scroll', 'samt': 10, 'sdir': 'down'},
])
def test_malformed_slots_are_not_actions(value):
    with pytest.raises(ValueError):
        slots.decode_slots(value)


@pytest.mark.parametrize('value', [{'action': 'back'}, {'action': 'home'}, {'action': 'press', 'key': 'none'}])
def test_ambiguous_desktop_actions_are_rejected(value):
    with pytest.raises(ActionUnavailable):
        slots.decode_slots(value)


def test_real_pipe_process_is_reused_and_closed(tmp_path, monkeypatch):
    # Exercise real pipes, reader thread, PID reuse, and owned tree termination;
    # substitute only GPU inference, which has separate on-device acceptance.
    worker = tmp_path / 'fake_worker.py'
    worker.write_text('''import json,os,sys
print(json.dumps({'status':'ready','model':'gui-decisions-2b','worker_pid':os.getpid(),'model_loads':1}),flush=True)
for line in sys.stdin:
    q=json.loads(line)
    print(json.dumps({'request_id':q['request_id'],'worker_pid':os.getpid(),'model_loads':1}),flush=True)
''', encoding='utf-8')
    root = tmp_path / 'deployment'
    monkeypatch.setattr(slots, 'deployment_root', lambda: root)
    monkeypatch.setattr(slots, 'required_paths', list)
    monkeypatch.setattr(slots, 'verify_model', lambda root: None)
    monkeypatch.setattr(slots, 'worker_script', lambda: worker)
    popen = slots.subprocess.Popen

    def start(args, **kwargs):
        if args[0] == str(root / '.venv/Scripts/python.exe'):
            args[0] = sys.executable
        return popen(args, **kwargs)

    monkeypatch.setattr(slots.subprocess, 'Popen', start)
    runtime = slots.SlotRuntime(tmp_path / 'output')
    try:
        runtime.load()
        process = runtime.process
        first = runtime.predict(tmp_path / 'image.png', 'a', 'None', 'request-first')
        runtime.load()
        second = runtime.predict(tmp_path / 'image.png', 'b', 'None', 'request-second')
        assert runtime.process is process
        assert first['worker_pid'] == second['worker_pid']
        assert first['model_loads'] == second['model_loads'] == 1
    finally:
        runtime.close()
    assert process.poll() is not None and not runtime.ready


def test_model_checksum_is_checked_before_loading(tmp_path, monkeypatch):
    (tmp_path / 'model').mkdir()
    (tmp_path / 'model/config.json').write_bytes(b'wrong')
    monkeypatch.setattr(slots, 'HASHES', {'config.json': '0' * 64})
    with pytest.raises(ValueError, match='checksum mismatch'):
        slots.verify_model(tmp_path)


def test_missing_slot_runtime_preserves_existing_service(tmp_path, monkeypatch):
    from trace2task import local_model_service as service
    monkeypatch.setattr(slots, 'required_paths', lambda: [tmp_path / 'missing-worker'])
    monkeypatch.setattr(service, 'managed_processes', lambda: pytest.fail('validate before stopping'))
    with pytest.raises(RuntimeError, match='运行环境不存在'):
        service.control_service('start', slots.MODEL, tmp_path, backend='slotread')


def test_run_facts_describe_slot_context(tmp_path):
    from trace2task.run_session import RunSession
    path = tmp_path / 'runs/local-gui/backend.json'
    path.parent.mkdir(parents=True)
    path.write_text('{"backend":"slotread"}')
    job = SimpleNamespace(job_id='a' * 32, provider='trained_d', model=slots.MODEL,
        executor_backend='cua', execution_scope='desktop', cua_target={'kind': 'desktop'},
        task_path='', background=False)
    facts = RunSession.for_job(job, tmp_path, requested_backend='slotread')
    assert facts.inference_backend == 'slotread'
    assert facts.context_policy == 'current_image_text_history' and facts.data_flow == 'local'


def test_prompt_api_exposes_and_enforces_native_template(tmp_path):
    from trace2task.web_console import WebConsoleController
    controller = WebConsoleController(tmp_path)
    profile = controller.local_prompt_profile(slots.MODEL, 'cua')
    assert profile['effective'] == {'system_prompt': slots.SYSTEM, 'turn_template': slots.TURN}
    with pytest.raises(ValueError, match='固定官方'):
        controller.save_local_prompt_profile(slots.MODEL, 'cua',
            {**profile['effective'], 'system_prompt': 'edited system'})
    assert not (tmp_path / 'runs/local-gui/prompt-profiles.json').exists()


def test_generic_installer_does_not_claim_to_install_slot_environment(tmp_path):
    from scripts.local_gui.download import REPOS
    from trace2task.components import ComponentManager
    with pytest.raises(ValueError, match='独立官方部署'):
        ComponentManager(tmp_path).start('model', model=slots.MODEL)
    assert slots.MODEL not in REPOS


def test_changing_slot_inputs_release_cached_workspace_but_keep_model():
    from scripts.local_gui.slot_worker import predict_step

    class Cuda:
        def __init__(self):
            self.weights, self.reserved, self.peak, self.clears = 100, 100, 100, 0
        def reset_peak_memory_stats(self): self.peak = self.reserved
        def max_memory_allocated(self): return self.peak
        def max_memory_reserved(self): return self.reserved
        def memory_allocated(self): return self.weights
        def memory_reserved(self): return self.reserved
        def empty_cache(self):
            self.clears += 1
            self.reserved = self.weights

    cuda = Cuda()
    seen = []
    def decide(image, instruction, previous):
        seen.append((image, instruction, previous))
        cuda.reserved += len(previous)
        cuda.peak = cuda.reserved
        return {'action': 'click', 'x': .1, 'y': .2}
    model = SimpleNamespace(decide=decide)
    for previous in ('a', 'ab', 'abc', 'abcd'):
        step, metrics = predict_step(model, 'same-image', 'unchanged task', previous, cuda)
        assert step == {'action': 'click', 'x': .1, 'y': .2}
        assert metrics['peak_reserved_bytes'] == 100 + len(previous)
        assert metrics['reserved_after_bytes'] == metrics['allocated_after_bytes'] == 100
        assert metrics['cache_release_ms'] >= 0
    assert seen == [('same-image', 'unchanged task', text) for text in ('a', 'ab', 'abc', 'abcd')]
    assert cuda.clears == 4


def test_slot_failure_also_releases_unused_cache():
    from scripts.local_gui.slot_worker import predict_step

    released = []
    cuda = SimpleNamespace(reset_peak_memory_stats=lambda: None, empty_cache=lambda: released.append(True))
    def fail(*args, **kwargs):
        raise RuntimeError('forward failed')
    with pytest.raises(RuntimeError, match='forward failed'):
        predict_step(SimpleNamespace(decide=fail), None, 'task', 'history', cuda)
    assert released == [True]
