"""Visual guidance travels through real adapters with fake device/model transports."""
import base64
import copy
import hashlib
import io
import json
from pathlib import Path

import pytest
from PIL import Image
from test_android_execution import action
from test_android_execution import phone as phone  # noqa: PLC0414
from test_chat_agent_bridge import Session, _reply
from test_run_interventions import HookSession, inputs, run_phone

from trace2task.chat_model_adapter import ChatModelAdapter
from trace2task.intervention_images import MAX_UPLOAD_BYTES, save_visual, validate_visual
from trace2task.run_interventions import RunInterventions

STROKES = [{'tool': 'circle', 'points': [[.1, .2], [.4, .6]]},
           {'tool': 'cross', 'points': [[.7, .1], [.9, .3]]},
           {'tool': 'pen', 'points': [[.1, .8], [.4, .7], [.6, .8]]}]


def upload(size=(200, 100)):
    image = Image.new('RGB', size, '#406070')
    data = io.BytesIO()
    image.save(data, format='PNG')
    return {'upload_data_url': 'data:image/png;base64,' + base64.b64encode(data.getvalue()).decode(),
            'strokes': copy.deepcopy(STROKES)}


@pytest.mark.parametrize('provider', ['codex', 'api'])
@pytest.mark.parametrize('source', ['capture', 'upload'])
def test_visual_correction_discards_old_plan_and_sends_original_marks_and_fresh_image(phone, tmp_path, provider, source):
    _, data = phone
    control = RunInterventions()
    frozen = []
    def changed(state):
        if state['phase'] == 'paused':
            segment = state['takeovers'][-1]
            visual = upload() if source == 'upload' else {
                'takeover_id': segment['id'], 'source_sha256': segment['before']['sha256'],
                'strokes': copy.deepcopy(STROKES)}
            request = {'request_id': 'visual-resume-01', 'kind': 'resume', 'text': '点圈内，不要点叉号', 'visual': visual}
            first = control.request(**request)
            assert control.request(**request) == first
            with pytest.raises(ValueError, match='不同内容'):
                control.request(**{**request, 'visual': {**visual, 'strokes': []}})
            frozen.append(first['commands'][-1]['visual'])
            data.update(rotation=0, size=(720, 1280))
    control.on_change = changed
    session = HookSession([_reply([action('click', x=.1, y=.1).to_payload()]),
                           _reply([action('click', x=.8, y=.8).to_payload()])],
        lambda n: control.request(request_id='pause-001', kind='pause') if n == 1 else None)
    result = run_phone(tmp_path, control, session, max_actions=1,
                       **({'api_config': object()} if provider == 'api' else {}))
    assert result['actions'] == 1 and inputs(data) == [['tap', '576', '1024']]
    assert session.peak == 1 and len(session.requests) == 2
    request = session.requests[1]
    assert len(request['additional_image_paths']) == (4 if source == 'upload' else 3)
    assert request['image_path'] not in request['additional_image_paths']
    assert request['prompt'].count('点圈内，不要点叉号') == 1
    assert 'not GUI elements' in request['prompt']
    assert 'Only image 1 is the current observation' in request['prompt']
    assert 'upload_data_url' not in request['prompt'] and 'visual_digest' not in request['prompt']
    reference = frozen[0]
    assert reference['origin'] == ('user_upload' if source == 'upload' else 'takeover_snapshot')
    assert reference['source']['sha256'] != reference['image']['sha256']
    for picture in (reference['source'], reference['image']):
        assert hashlib.sha256(Path(picture['path']).read_bytes()).hexdigest() == picture['sha256']
    image = Image.open(reference['image']['path'])
    assert any(r > 180 and g < 70 and b < 100 for r, g, b in zip(*[iter(image.tobytes())] * 3, strict=True))
    assert result['interventions']['commands'][-1]['status'] == 'responded'
    assert result['interventions']['commands'][-1]['visual']['strokes'] == STROKES


def handoff_context(tmp_path):
    directory = tmp_path / 'interventions'
    directory.mkdir()
    note = '不要做可选任务，圈内是必做任务，叉号是可选路线。'
    segment = {'id': 'takeover-1', 'status': 'returned', 'note': note,
               'resume_command_id': '96929f25-5c1e-4cd7-ac56-f7662dfcda7a',
               'started_at': '2026-10-26T13:16:59+00:00'}
    for phase, color in [('before', 'blue'), ('after', 'green')]:
        path = directory / f'takeover-1-{phase}.png'
        Image.new('RGB', (200, 100), color).save(path)
        segment[phase] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                          'width': 200, 'height': 100, 'rotation': 1, 'package': 'com.example.game'}
    visual = save_visual({'takeover_id': segment['id'], 'source_sha256': segment['before']['sha256'],
                          'strokes': copy.deepcopy(STROKES)},
                         segment=segment, directory=tmp_path, request_id='visual-01')
    commands = [{'id': 'earlier-1', 'kind': 'instruction', 'text': '先处理可选任务'},
                {'id': 'pause-001', 'kind': 'pause', 'text': ''},
                {'id': segment['resume_command_id'], 'kind': 'resume', 'text': note,
                 'visual': visual, 'visual_digest': 'private-digest'},
                {'id': 'later-01', 'kind': 'instruction', 'text': '领取奖励后再确认完成'}]
    return {'human_intervention': {'assisted_by_user': True, 'revision': 4,
                                  'commands': commands, 'takeovers': [segment],
                                  'limits': 'full audit limits'}}


@pytest.mark.parametrize('provider', ['codex', 'api'])
@pytest.mark.parametrize('review', [False, True])
def test_compact_guidance_keeps_order_and_image_mapping_without_changing_audit(tmp_path, provider, review):
    context = handoff_context(tmp_path)
    if review:
        context['purpose'] = 'verify_completion'
    original = copy.deepcopy(context)
    handoff = context['human_intervention']
    before, after = (Path(handoff['takeovers'][0][phase]['path']) for phase in ('before', 'after'))
    annotated = Path(handoff['commands'][2]['visual']['image']['path'])
    # A historical demonstration occupies image 2; intervention indices must shift.
    data = before.read_bytes()
    experience = {'kind': 'trace_sequence', 'model_input': 'old demonstration',
                  'images': [{'id': '1', 'sha256': hashlib.sha256(data).hexdigest(),
                              'base64': base64.b64encode(data).decode()}]}
    answer = (json.dumps({'verdict': 'unknown', 'evidence': 'no reward visible', 'missing': 'reward'})
              if review else _reply([action('wait', duration_ms=1).to_payload()]))
    session = Session([answer, _reply([action('wait', duration_ms=1).to_payload()])])
    adapter = ChatModelAdapter(session, selected_windows=False, android=True, provider=provider,
                               evidence_directory=tmp_path / 'evidence')
    result = adapter.predict('test', screenshot=before, history=[], context=context,
                             experience_context=experience)
    prompt = result['prompt']
    compact = prompt[prompt.index('User update:'):]
    assert len(compact) < 1000
    instructions = [c['text'] for c in handoff['commands'] if c['text']]
    assert all(compact.count(text) == 1 for text in instructions)
    assert [compact.index(text) for text in instructions] == sorted(compact.index(text) for text in instructions)
    assert 'later corrections override earlier guidance' in compact and 'completion review' in compact
    assert 'before=image 3, after=image 4' in compact
    assert 'original=image 3, annotated=image 5' in compact
    assert all(value not in compact for value in ('sha256', 'strokes', 'rotation', 'package',
               'coordinate_space', 'private-digest', '2026-10-26', 'full audit limits',
               handoff['commands'][2]['id'], str(tmp_path)))
    assert context == original
    attached = session.requests[0]['additional_image_paths']
    assert attached[1:] == (before, after, annotated)
    assert result['context_management']['intervention_image_count'] == 3
    assert result['context_management']['new_image_count'] == 5
    assert [part['path'] for part in result['input_messages'][0]['content'][1:]] == [
        str(path.resolve()) for path in (before, *attached)]
    # Even byte-identical historical images must never replace the fresh observation.
    assert session.requests[0]['image_path'] == before
    # Removing old images retains the actual user instructions through review/continuation.
    session.context_near_limit = True
    empty = {'human_intervention': {'assisted_by_user': True, 'revision': 4, 'commands': [], 'takeovers': []}}
    replay = adapter.predict('test', screenshot=after, history=[], context=empty)
    assert all(text in replay['prompt'] for text in instructions)
    assert 'User update:' not in replay['prompt'].split('Current user message:\n')[-1]


def test_duplicate_image_alias_is_still_validated_before_request(tmp_path):
    context = handoff_context(tmp_path)
    source = context['human_intervention']['commands'][2]['visual']['source']
    alias = tmp_path / 'changed-alias.png'
    alias.write_bytes(b'changed bytes with the original recorded hash')
    source['path'] = str(alias)
    session, submitted = Session([]), []
    adapter = ChatModelAdapter(session, selected_windows=False, android=True)
    with pytest.raises(ValueError, match='人工标注截图已变化'):
        adapter.predict('test', screenshot=tmp_path / 'current.png', history=[], context=context,
                        on_request=lambda: submitted.append(True))
    assert not session.requests and not submitted


@pytest.mark.parametrize('role', ['source', 'image'])
def test_tampered_reference_is_never_submitted(phone, tmp_path, role):
    control = RunInterventions()
    def changed(state):
        if state['phase'] == 'paused':
            control.request(request_id='visual-resume-01', kind='resume', visual=upload())
        if state['phase'] == 'running' and state['commands']:
            Path(state['commands'][-1]['visual'][role]['path']).write_bytes(b'tampered')
    control.on_change = changed
    session = HookSession([_reply([action('click', x=.1, y=.1).to_payload()])],
        lambda _: control.request(request_id='pause-001', kind='pause'))
    result = run_phone(tmp_path, control, session, max_actions=1)
    assert result['actions'] == 0 and result['stop_reason'] == 'error'
    assert len(session.requests) == 1 and 'submitted_round' not in result['interventions']['commands'][-1]


@pytest.mark.parametrize('bad', [
    {'path': 'C:/private.png', 'strokes': []},
    {'upload_data_url': 'x', 'strokes': [{'tool': 'pen', 'points': [[0, 0], [float('nan'), 1]]}]},
    {'upload_data_url': 'x', 'strokes': [{'tool': 'cross', 'points': [[True, 0], [1, 1]]}]},
    {'upload_data_url': 'x', 'strokes': [{'tool': 'circle', 'points': [[-1, 0], [1, 1]]}]},
    {'upload_data_url': 'x', 'strokes': [STROKES[0]] * 65},
    {'upload_data_url': 'x', 'strokes': [{'tool': 'pen', 'points': [[0, 0]] * 501}] * 4},
    {'upload_data_url': 'x' * (MAX_UPLOAD_BYTES * 2), 'strokes': []},
])
def test_invalid_inputs_are_bounded_without_writing(tmp_path, bad):
    with pytest.raises(ValueError):
        validate_visual(bad)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('value', [
    {'upload_data_url': 'data:image/png;base64,!!!', 'strokes': []},
    {'upload_data_url': 'data:image/svg+xml;base64,PHN2Zz4=', 'strokes': []},
    {'upload_data_url': 'data:image/png;base64,dGV4dA==', 'strokes': []},
    upload((4097, 16)),
])
def test_invalid_images_are_rejected_before_creating_files(tmp_path, value):
    validate_visual(value)
    with pytest.raises(ValueError):
        save_visual(value, segment={'id': 'takeover-1'}, directory=tmp_path, request_id='visual-01')
    assert not list(tmp_path.iterdir())


def test_visual_cannot_bypass_explicit_pause(tmp_path):
    control = RunInterventions()
    control.activate(lambda *a, **k: None, directory=tmp_path)
    with pytest.raises(RuntimeError, match='确认暂停'):
        control.request(request_id='visual-01', kind='resume', visual=upload())
    with pytest.raises(ValueError, match='先暂停'):
        control.request(request_id='visual-02', kind='instruction', text='try', visual=upload())
    assert not control.snapshot()['commands'] and not list(tmp_path.iterdir())


@pytest.mark.parametrize('provider', ['codex', 'api'])
def test_live_capture_uses_device_thread_and_multiple_references_reach_model(phone, tmp_path, provider, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    _, data = phone
    control = RunInterventions()
    work = []
    main_thread = threading.get_ident()
    from trace2task.android_execution import AndroidObservation
    original = AndroidObservation.intervention_snapshot
    def capture(observer, identifier, phase):
        assert threading.get_ident() == main_thread
        return original(observer, identifier, phase)
    monkeypatch.setattr(AndroidObservation, 'intervention_snapshot', capture)
    with ThreadPoolExecutor(max_workers=1) as pool:
        def return_with_capture(segment):
            assert threading.get_ident() != main_thread
            data.update(rotation=0, size=(720, 1280))
            result = control.capture_reference(segment['id'])
            assert control.snapshot()['phase'] == 'paused'
            assert not inputs(data)
            picture = result['picture']
            assert (picture['width'], picture['height']) == (720, 1280)
            assert picture['sha256'] != segment['before']['sha256']
            visual = {**upload(), 'extras': [
                {'takeover_id': segment['id'], 'capture_id': result['capture_id'],
                 'source_sha256': picture['sha256'], 'strokes': []},
                {**upload((100, 200)), 'strokes': []}]}
            return control.request(request_id='live-resume-01', kind='resume', visual=visual)

        def changed(state):
            if state['phase'] == 'paused' and not work:
                work.append(pool.submit(return_with_capture, state['takeovers'][-1]))
        control.on_change = changed
        session = HookSession([_reply([action('click', x=.1, y=.1).to_payload()]),
                               _reply([action('click', x=.8, y=.8).to_payload()])],
            lambda n: control.request(request_id='pause-001', kind='pause') if n == 1 else None)
        result = run_phone(tmp_path, control, session, max_actions=1,
                           **({'api_config': object()} if provider == 'api' else {}))
        state = work[0].result(timeout=2)
    assert result['actions'] == 1 and len(session.requests) == 2
    visual = state['commands'][-1]['visual']
    assert visual['extras'][0]['origin'] == 'live_snapshot'
    assert visual['extras'][0]['strokes'] == []
    assert 'additional 1=image' in session.requests[1]['prompt']
    assert 'additional 2=image' in session.requests[1]['prompt']
    assert Path(visual['extras'][1]['source']['path']) in session.requests[1]['additional_image_paths']
    assert 'upload_data_url' not in json.dumps(state)


@pytest.mark.parametrize('extras', [[{**upload(), 'strokes': STROKES}],
    [{**upload(), 'strokes': [], 'extras': []}], [{**upload(), 'strokes': []}] * 8])
def test_multiple_images_reject_nested_marks_and_excess_images(extras):
    with pytest.raises(ValueError):
        validate_visual({**upload(), 'extras': extras})


def test_failed_extra_leaves_no_partial_files(tmp_path):
    value = {**upload(), 'extras': [{'upload_data_url': 'data:image/png;base64,!!!', 'strokes': []}]}
    with pytest.raises(ValueError):
        save_visual(value, segment={'id': 'takeover-1'}, directory=tmp_path, request_id='rollback-001')
    assert not list(tmp_path.rglob('*.png'))


def test_live_reference_must_belong_to_current_takeover(tmp_path):
    value = {'takeover_id': 'takeover-1', 'capture_id': 'a' * 32, 'source_sha256': 'b' * 64, 'strokes': []}
    with pytest.raises(ValueError, match='不匹配'):
        save_visual(value, segment={'id': 'takeover-2'}, directory=tmp_path, request_id='capture-001')


def test_capture_phase_timeout_and_close(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    control = RunInterventions()
    control.activate(lambda *a, **k: None, directory=tmp_path)
    with pytest.raises(RuntimeError, match='暂停'):
        control.capture_reference('takeover-1')
    control.request(request_id='pause-001', kind='pause')
    control.state['phase'] = 'paused'
    with pytest.raises(RuntimeError, match='超时'):
        control.capture_reference('takeover-1', timeout=.01)
    assert control.pending_capture is None
    queued = threading.Event()
    class SignalEvent:
        def wait(self, timeout):
            queued.set()
            return done.wait(timeout)
        def set(self):
            done.set()
    done = threading.Event()
    from unittest.mock import patch
    with ThreadPoolExecutor(max_workers=1) as pool:
        # Only replace the event created by capture_reference, not executor internals.
        def request():
            with patch('trace2task.run_interventions.threading.Event', return_value=SignalEvent()):
                return control.capture_reference('takeover-1')
        future = pool.submit(request)
        assert queued.wait(2)
        with pytest.raises(RuntimeError, match='等待截图'):
            control.request(request_id='resume-01', kind='resume')
        control.close('stopped')
        with pytest.raises(RuntimeError, match='已结束'):
            future.result(timeout=2)


def test_live_capture_failure_retains_takeover_and_does_not_resume(tmp_path):
    import threading

    from test_chat_agent_bridge import Stop

    from trace2task.android_execution import AndroidAppNotFocused

    control = RunInterventions()
    control.activate(lambda *a, **k: None, directory=tmp_path)
    control.request(request_id='pause-001', kind='pause')
    control.state['phase'] = 'paused'
    segment = control.state['takeovers'][-1]
    original = {'path': 'original.png', 'sha256': 'unchanged'}
    segment['before'] = original
    request = {'id': 'a' * 32, 'segment': segment, 'done': threading.Event()}
    control.pending_capture = request
    def fail(*args):
        raise AndroidAppNotFocused('test app is not focused')
    control._capture_reference(fail, Stop())
    assert request['done'].is_set() and 'not focused' in request['error']
    assert control.pending_capture is None
    assert control.state['phase'] == 'paused' and segment['before'] == original
    assert not segment.get('captures')
