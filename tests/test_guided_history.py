import base64
import io

import pytest
from PIL import Image

from trace2task.guided_history import guided_conversation


def picture():
    stream = io.BytesIO()
    Image.new('RGB', (4, 3)).save(stream, 'PNG')
    return base64.b64encode(stream.getvalue()).decode()


def test_replay_preserves_all_native_text_and_exactly_two_images():
    history = [{'raw_output': f'action {i}', **({'image': picture()} if i >= 3 else {})}
               for i in range(5)]
    conversation = guided_conversation('test', 'gui-owl-2b', 'Current subgoal', history)
    messages, images = conversation.compose('native system', [{'type': 'image'}], [object()])
    assert [m['content'] for m in messages if m['role'] == 'assistant'] == [f'action {i}' for i in range(5)]
    assert len(images) == 3 and conversation.total_turns == 5
    assert conversation.history_image_count == 2
    assert conversation.discard_oldest_image() is None
    assert conversation.history_image_count == 2


def test_slotread_retains_action_text_but_does_not_claim_historical_images():
    conversation = guided_conversation('test', 'gui-decisions-2b', 'Current',
        [{'raw_output': 'native', 'image': picture()}], images_supported=False)
    assert conversation.total_turns == 1 and conversation.history_image_count == 0
    assert conversation.turns[0][1] == 'native'


@pytest.mark.parametrize('history', [None, [{'raw_output': ''}], [{'raw_output': 'x', 'image': 'bad'}],
    [{'raw_output': 'x', 'unexpected': True}], [{'raw_output': 'x'}] * 121,
    [{'raw_output': 'x', 'image': picture()}, {'raw_output': 'y'}, {'raw_output': 'z'}]])
def test_reject_invalid_or_unbounded_history(history):
    with pytest.raises((ValueError, TypeError)):
        guided_conversation('test', 'gui-owl-2b', 'Current', history)
