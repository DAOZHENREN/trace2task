"""Compact Android model inputs retain action semantics and execution feedback."""
import copy
import json

import pytest
from test_android_camera import camera
from test_chat_agent_bridge import Session, _reply

from trace2task.chat_model_adapter import ChatModelAdapter
from trace2task.local_gui_protocol import COMPLETION_REVIEW_PROMPT


def target_context():
    # Deliberately include host-only metadata and verbose receipts to detect leakage.
    return {'target': {'kind': 'android', 'serial': 'phone-private', 'package': 'com.example.game',
                       'server_port': 5038, 'transport_id': 7, 'boot_id': 'boot-private'},
            'scope': 'selected Android foreground package, pixels only', 'width': 1280, 'height': 720,
            'capabilities': {'available_skills': ['camera_drag', 'joystick_move'],
                             'camera': {'angle_calibration': 'unknown'}},
            'execution_feedback': {'status': 'camera_reobserve', 'executed': True,
                                   'camera': {'start_px': [960, 288], 'end_px': [768, 288]},
                                   'reason': 'Camera swipe ended; discard the remaining batch and re-observe.'}}


@pytest.mark.parametrize('provider', ['codex', 'api'])
def test_first_and_followup_are_compact_without_losing_scope_or_execution_feedback(tmp_path, provider):
    context = target_context()
    initial_context = copy.deepcopy(context)
    initial_context['execution_feedback'] = None
    history = [{'step_index': 28, 'action': {'actions': [camera()]}, 'executed': True,
                'effect': 'unverifiable', 'delivery_mode_requested': 'remote_android'}]
    original_context, original_history = copy.deepcopy(context), copy.deepcopy(history)
    session = Session([_reply([camera()]), _reply([camera()])])
    adapter = ChatModelAdapter(session, selected_windows=False, android=True, provider=provider)
    adapter.predict('inspect the route', screenshot=tmp_path / 'initial.png', history=[], context=initial_context)
    result = adapter.predict('inspect the route', screenshot=tmp_path / 'next.png', history=history, context=context)
    first, followup = (request['prompt'] for request in session.requests)
    assert len(first) < 2300 and len(followup) < 1600
    assert 'CURRENT screenshot' in first and 'screenshot-normalized 0..1' in first
    assert 'Single touch' in first and 'camera_drag must be alone' in first
    assert 'Execution feedback' not in first and 'null' not in first
    assert followup.count('Execution feedback:') == 1 and 'camera_reobserve' in followup
    assert '"duration_ms":300' in followup and '"effect":"unverifiable"' in followup
    for prompt in (first, followup):
        assert 'navigate_to' not in prompt
        assert 'App: com.example.game' in prompt and 'Screen: 1280x720' in prompt
        assert 'HOLD until duration_ms ends' in prompt and 'No fixed duration cap' in prompt
        assert '6000-12000 ms or longer' in prompt and 'task budget' in prompt
        assert all(key not in prompt for key in ('phone-private', 'boot-private', 'transport_id',
                   'server_port', 'capabilities', 'available_skills', 'start_px', 'camera_region'))
    assert result['input_messages'][0]['content'][0]['text'] == followup
    assert context == original_context and history == original_history  # Do not trim the host audit.


def test_feedback_errors_and_explicit_override_remain_visible(tmp_path):
    context = target_context()
    context['execution_feedback'] = {'status': 'rejected', 'executed': False,
                                     'reason': 'scope changed', 'instruction': 'choose a fresh target'}
    explicit = {'status': 'completion_rejected', 'executed': False,
                'evidence': 'dialog still open', 'missing': 'the requested reward',
                'instruction': 'finish the interaction before claiming success'}
    session = Session([_reply([camera()]), _reply([camera()])])
    adapter = ChatModelAdapter(session, selected_windows=False, android=True)
    adapter.predict('inspect', screenshot=tmp_path / 'first.png', history=[], context=context)
    adapter.predict('inspect', screenshot=tmp_path / 'second.png', history=[], context=context,
                    execution_feedback=explicit)
    first, followup = (request['prompt'] for request in session.requests)
    assert 'scope changed' in first and 'choose a fresh target' in first
    assert 'scope changed' not in followup
    assert all(value in followup for value in ('completion_rejected', 'dialog still open',
                                               'the requested reward', explicit['instruction']))
    assert followup.count('Execution feedback:') == 1


def test_navigation_feedback_retains_outcome_without_frame_and_touch_logs(tmp_path):
    context = target_context()
    navigation = {'outcome': 'target_lost', 'input_sent': True, 'progress_px': 2.3,
                  'corrections': 2, 'release_confirmed': True,
                  'frames': [{'screenshot': 'host-only-frame.png'}],
                  'touch_events': [{'event': 'up'}]}
    context['execution_feedback'] = {'status': 'navigation_reobserve', 'executed': True,
                                     'navigation': navigation, 'reason': 'navigation ended'}
    original = copy.deepcopy(context)
    session = Session([_reply([camera()])])
    adapter = ChatModelAdapter(session, selected_windows=False, android=True)
    result = adapter.predict('inspect', screenshot=tmp_path / 'first.png', history=[], context=context)
    line = next(line for line in result['prompt'].splitlines() if line.startswith('Execution feedback: '))
    feedback = json.loads(line.removeprefix('Execution feedback: '))
    assert feedback['navigation'] == {key: navigation[key] for key in
                                      ('outcome', 'input_sent', 'progress_px', 'corrections', 'release_confirmed')}
    assert 'host-only-frame' not in result['prompt'] and context == original


def test_experience_operator_guidance_and_readonly_review_survive_compaction(tmp_path):
    class LimitedSession(Session):
        context_near_limit = False

    session = LimitedSession([_reply([camera()]), _reply([camera()]),
                              json.dumps({'verdict': 'unknown', 'evidence': 'only the world view is visible',
                                          'missing': 'reward not visible'})])
    adapter = ChatModelAdapter(session, selected_windows=False, android=True,
                               prompt_guidance='Do not touch the red button.')
    experience = {'kind': 'trace_sequence', 'model_input': 'HISTORICAL ROUTE: turn beside the blue sign.'}
    first = adapter.predict('inspect', screenshot=tmp_path / 'first.png', history=[],
                             context=target_context(), experience_context=experience)
    session.context_near_limit = True
    second = adapter.predict('inspect', screenshot=tmp_path / 'second.png', history=[],
                              context=target_context(), experience_context=experience)
    assert 'HISTORICAL ROUTE:' in first['prompt'] and 'Do not touch the red button.' in first['prompt']
    assert 'All prior text messages' in second['prompt']
    assert 'HISTORICAL ROUTE:' in second['prompt'] and 'Do not touch the red button.' in second['prompt']
    session.context_near_limit = False
    review = adapter.predict('inspect', screenshot=tmp_path / 'review.png', history=[],
                              context={'purpose': 'verify_completion'})
    assert review['status'] == 'reviewed' and COMPLETION_REVIEW_PROMPT in review['prompt']
    assert 'joystick_move:' not in review['prompt'] and 'camera_drag:' not in review['prompt']
