import json
from types import SimpleNamespace

import pytest
from PIL import Image

from trace2task.mobileforge_adapter import UPSTREAM_COMMIT, decode_mobileforge
from trace2task.mobileforge_pipeline import (
    evaluate_run,
    evidence_digest,
    export_attempt,
    load_trajectory,
    require_local_endpoint,
    validate_decision,
)


@pytest.mark.parametrize('url', ['https://api.openai.com/v1', 'http://4090:8000/v1', 'https://127.0.0.1.evil.test'])
def test_external_model_forbidden(url):
    with pytest.raises(ValueError):
        require_local_endpoint(url)


def test_loopback():
    require_local_endpoint('http://127.0.0.1:8098/v1')
    require_local_endpoint('http://[::1]:8098/v1')


def decision():
    return {'evidence_sufficient': True, 'decision': 1, 'reason': 'fixture only', 'task_feasible': True,
            'reasonable_steps': [1], 'unreasonable_steps': [], 'step_analysis': {
                '1': {'reasonableness': 'reasonable', 'explanation': 'fixture', 'impact': 'positive'}}}


def test_unknown_not_negative():
    assert not validate_decision({'evidence_sufficient': False}, 1)
    bad = decision()
    bad['unreasonable_steps'] = [1]
    with pytest.raises(ValueError, match='disjoint'):
        validate_decision(bad, 1)


@pytest.fixture
def trajectory(tmp_path):
    root = tmp_path / 'run'
    root.mkdir()
    before, after = root / 'before.png', root / 'after.png'
    Image.new('RGB', (32, 32), 'white').save(before)
    Image.new('RGB', (32, 32), 'black').save(after)
    raw = '<tool_call>{"name":"mobile_use","arguments":{"action":"click","coordinate":[500,500]}}</tool_call>'
    step = {'raw_output': raw, 'prediction': decode_mobileforge(raw)['prediction'],
            'execution': {'executed': True, 'status': 'delivered'}, 'screenshot': str(before),
            'input': {'messages': [{'role': 'system', 'content': 'Synthetic fixture; not a real trajectory'},
                                  {'role': 'user', 'content': [{'type': 'text', 'text': 'fixture'},
                                                            {'type': 'localImage', 'path': str(before)}]}]}}
    result = {'executor_backend': 'android', 'model_adapter': {'commit': UPSTREAM_COMMIT},
              'stop_reason': 'action_limit', 'model_io': [step], 'actions': 1,
              'model': 'SYNTHETIC_TEST_ONLY', 'final_screenshot': str(after),
              'instruction': 'Synthetic conversion fixture only'}
    (root / 'result.json').write_text(json.dumps(result), encoding='utf-8')
    feedback = {'source_sha256': evidence_digest(root, result, [step]), 'decision': decision(),
                'training_eligible': True, 'task': 'Synthetic conversion fixture only',
                'descriptions': [{'action_description': 'fixture click', 'ui_description': 'fixture screen'}],
                'synthetic_test_only': True}
    path = tmp_path / 'feedback.json'
    path.write_text(json.dumps(feedback), encoding='utf-8')
    return SimpleNamespace(root=root, result=result, feedback=path, out=tmp_path / 'export')


def test_export_embeds_local_images_and_preserves_labels(trajectory):
    t = trajectory
    target = export_attempt(t.root, t.feedback, t.out, 'fixture', 1)
    logs = json.loads((target / 'detailed_model_logs.json').read_text())
    assert logs[0]['input_messages'][1]['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
    assert logs[0]['parsed_action']['action_type'] == 'click'
    with pytest.raises(FileExistsError):
        export_attempt(t.root, t.feedback, t.out, 'fixture', 1)


def test_changed_image_invalidates_feedback(trajectory):
    t = trajectory
    Image.new('RGB', (32, 32), 'red').save(t.root / 'before.png')
    with pytest.raises(ValueError, match='different run'):
        export_attempt(t.root, t.feedback, t.out, 'fixture', 1)


def test_unknown_delivery_is_quarantined(trajectory):
    t = trajectory
    t.result['model_io'][0]['execution']['executed'] = None
    (t.root / 'result.json').write_text(json.dumps(t.result))
    with pytest.raises(ValueError, match='fully delivered'):
        load_trajectory(t.root)


@pytest.mark.parametrize('sufficient', [True, False])
def test_critic_local_only_and_unknown_quarantine(trajectory, sufficient):
    t = trajectory
    calls = []
    verdict = decision() if sufficient else {'evidence_sufficient': False, 'reason': 'unclear'}
    replies = iter([{'action_description': 'fixture click', 'ui_description': 'fixture screen'}, verdict])
    def complete(messages, **kwargs):
        calls.append(messages)
        return json.dumps(next(replies))
    session = SimpleNamespace(config=SimpleNamespace(base_url='http://127.0.0.1:8098/v1'),
                              model='fixture-critic', complete=complete)
    prompts = SimpleNamespace(critic=SimpleNamespace(
        get_describe_final_step_prompt=lambda *args: ('system', 'describe'),
        get_final_decision_prompt=lambda *args: ('system', 'decide')))
    feedback = evaluate_run(prompts, session, t.root, t.result['instruction'], t.root / 'critic.json')
    assert feedback['training_eligible'] is sufficient
    assert feedback['label_source'] == 'model_judgment_not_ground_truth'
    assert len(calls) == 2
    if not sufficient:
        with pytest.raises(ValueError, match='Unknown'):
            export_attempt(t.root, t.root / 'critic.json', t.out, 'fixture', 1)


def test_task_mismatch_before_model_call(trajectory):
    with pytest.raises(ValueError, match='match the recorded'):
        evaluate_run(None, None, trajectory.root, 'different task', trajectory.root / 'feedback.json')
