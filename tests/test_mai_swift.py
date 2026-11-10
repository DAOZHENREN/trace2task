"""Mainline native105 seam: action scale, history, cancellation and honest review."""
import base64
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from trace2task.execution_protocol import ActionUnavailable
from trace2task.local_gui_protocol import decode, prompt
from trace2task.model_registry import profile_for


def test_profile_and_action_mapping():
    assert profile_for('mai-swift-2b', 'mai-swift-native105').repository == 'dongjintao/MAI-Swift-2B'
    with pytest.raises(ValueError):
        profile_for('mai-swift-2b', 'transformers')
    plan = decode('mai-swift-2b', '{"action":"tap","coordinate":[250,750]}')
    assert plan['actions'][0]['args'] == {'x': .25, 'y': .75, 'button': 'left'}
    assert decode('mai-swift-2b', '{"action":"failed"}')['control'] == 'terminate_failure'
    assert decode('mai-swift-2b', '{"action":"done"}')['actions'] == [{'done': True}]
    assert '105 masked values' in prompt('mai-swift-2b')
    with pytest.raises(ActionUnavailable):
        decode('mai-swift-2b', '{"action":"scroll_down"}')
    with pytest.raises(ValueError):
        decode('mai-swift-2b', '{"action":"tap","coordinate":[1001,0]}')


@pytest.fixture
def engine(tmp_path):
    path = Path(__file__).parents[1] / 'scripts/local_gui/server.py'
    spec = importlib.util.spec_from_file_location('mai_service_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = module.Engine(tmp_path, tmp_path / 'out', 'mai-swift-native105')
    engine.mai.load = lambda: SimpleNamespace(model=object(), processor=object())
    return engine


def request(**kwargs):
    output = io.BytesIO()
    Image.new('RGB', (32, 32)).save(output, format='PNG')
    return dict(model='mai-swift-2b', expected_backend='mai-swift-native105', task='Tap the visible button',
                image=base64.b64encode(output.getvalue()).decode(), **kwargs)


def test_guided_history_is_confirmed_only_and_archived(engine):
    calls = []
    def predict(messages, images, folder):
        calls.append((messages, images))
        return '{"action":"tap","coordinate":[250,750]}', 500, ['screenshot.png']
    engine.mai.predict = predict
    result = engine.predict(request(conversation_id='guided-test-session', conversation_start=True,
                                    guided_history_offset=0, guided_history=[]))
    assert result['status'] == 'predicted'
    assert engine.conversation.total_turns == 0  # prediction is NOT an executed action
    result = engine.predict(request(conversation_id='guided-test-session', guided_history_offset=0,
                            guided_history=[{'raw_output': result['raw_output'], 'image': request()['image']}]))
    assert result['status'] == 'predicted'
    assert engine.conversation.total_turns == 1
    assert len(calls[1][1]) == 2
    record = json.loads((Path(result['output_directory']) / 'input.json').read_text())
    assert record['configuration']['slots'] == 105
    assert record['input_stage'] == 'inferred'
    assert record['messages'] == calls[1][0]


def test_cancelled_forward_cannot_emit_action(engine):
    identity = 'cancel-native-request'
    def predict(messages, images, folder):
        engine.cancel(identity)
        return '{"action":"tap","coordinate":[250,750]}', 500, []
    engine.mai.predict = predict
    result = engine.predict(request(request_id=identity))
    assert result['status'] == 'cancelled'
    assert 'prediction' not in result
    assert not engine._cancellations[identity]['active']


def test_review_does_not_invent_success_or_run_slots(engine):
    engine.mai.predict = lambda *a: pytest.fail('read-only review must not invoke slots')
    result = engine.predict(request(execution_context={'purpose': 'verify_completion'}))
    assert result['verification']['verdict'] == 'unknown'
    assert 'prediction' not in result
