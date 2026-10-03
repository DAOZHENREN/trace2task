import threading
from contextlib import contextmanager
from copy import deepcopy

import pytest

from trace2task.gui_conversation import GuiConversation
from trace2task.gui_summarization import compact_conversation, projected_context_tokens
from trace2task.local_gui_llama import GenerationCancelled


def conversation():
    value = GuiConversation('test-conversation', 'gui-owl-2b', 'Original goal', {'model_input': 'Full experience'})
    for index in range(10):
        value.commit([{'type': 'image'}],
                     f'proposed-action-{index}', [f'picture-{index}'], index)
    value.last_total_tokens = 26000
    return value


class Runtime:
    def __init__(self, event, finish='stop', text='Files: report.txt. Delivery observed; success unverified.'):
        self.event, self.finish, self.text = event, finish, text
        self.calls = []
        self.reloaded = False

    @contextmanager
    def summary_worker(self, cancelled):
        try:
            yield
        finally:
            self.reloaded = True

    def complete(self, payload, cancelled):
        self.calls.append(deepcopy(payload))
        return {'choices': [{'message': {'content': self.text}, 'finish_reason': self.finish}],
                'usage': {'prompt_tokens': 500, 'completion_tokens': 40}}


def run(value, runtime, projected=27000):
    archive = {}
    event = compact_conversation(value, runtime, runtime.event, projected,
                                 lambda name, data: archive.update({name: deepcopy(data)}))
    return event, archive


def test_real_langchain_middleware_replaces_older_turns_transactionally():
    value = conversation()
    value.initial_user_text = '原始要求：只复制 PNG，不删除原文件。\r\n{{keep braces}}\nFull experience'
    pinned = value.initial_user_text
    original = deepcopy(value.turns)
    runtime = Runtime(threading.Event())
    event, archive = run(value, runtime)
    assert event['status'] == 'completed'
    assert runtime.reloaded
    assert value.turns == original[-4:]
    assert value.step_indices == [6, 7, 8, 9]
    assert value.task == 'Original goal' and value.experience == {'model_input': 'Full experience'}
    assert event['summarized_steps'] == list(range(6))
    assert event['removed_images'] == 6 and value.dropped_images == 6
    assert 'report.txt' in value.summary
    assert value.summary_version == 1
    summary_input = archive['summary-request.json']['messages'][0]['content']
    assert 'proposed-action-0' in summary_input
    assert 'proposed-action-5' in summary_input
    assert 'proposed-action-6' not in summary_input
    assert value.task not in summary_input
    assert 'Full experience' not in summary_input
    assert '原始要求' not in summary_input
    assert 'Never describe a proposal as an executed action.' in summary_input
    assert '## SESSION INTENT' not in summary_input
    messages, images = value.compose('PINNED TASK AND EXPERIENCE', [{'type': 'image'}], ['CURRENT'])
    assert messages[0]['content'] == 'PINNED TASK AND EXPERIENCE'
    assert messages[1] == {'role': 'user', 'content': [{'type': 'text', 'text': pinned}]}
    assert value.initial_user_text == pinned
    assert 'executor receipts' not in value.summary
    assert images == ['picture-6', 'picture-7', 'picture-8', 'picture-9', 'CURRENT']


def test_no_trigger_does_not_call_model_or_create_compaction_artifact():
    value = conversation()
    runtime = Runtime(threading.Event())
    event, archive = run(value, runtime, 12000)
    assert event is None and not archive and not runtime.calls
    assert len(value.turns) == 10


def test_per_turn_task_reminders_are_not_compressed_and_current_reminder_survives():
    value = conversation()
    reminder = {'type': 'text', 'text': 'Instruction: ' + value.task}
    for user, _reply, _images in value.turns:
        user['content'].insert(0, deepcopy(reminder))
    runtime = Runtime(threading.Event())
    _, archive = run(value, runtime)
    assert value.task not in archive['summary-request.json']['messages'][0]['content']
    messages, _ = value.compose('system', [reminder, {'type': 'image'}], ['current'])
    assert messages[1]['content'][0]['text'] == value.task
    assert messages[-1]['content'][0] == reminder
    assert len(value.turns) == 4  # Existing retention policy is unchanged.


@pytest.mark.parametrize('finish,text', [('length', 'partial'), ('stop', ''), ('error', 'bad')])
def test_failed_summary_leaves_original_history(finish, text):
    value = conversation()
    original = deepcopy(value.__dict__)
    runtime = Runtime(threading.Event(), finish, text)
    with pytest.raises(ValueError, match='empty/truncated'):
        run(value, runtime)
    assert value.__dict__ == original
    assert len(runtime.calls) == 1  # No expensive retries or cancellation delay.


def test_cancelled_summary_never_commits():
    value = conversation()
    original = deepcopy(value.__dict__)
    runtime = Runtime(threading.Event())
    normal = runtime.complete
    def cancel_after_response(payload, event):
        result = normal(payload, event)
        event.set()
        return result
    runtime.complete = cancel_after_response
    with pytest.raises(GenerationCancelled):
        run(value, runtime)
    assert value.__dict__ == original
    assert len(runtime.calls) == 1


def test_repeated_compaction_includes_previous_summary():
    value = conversation()
    runtime = Runtime(threading.Event())
    run(value, runtime)
    for index in range(10, 16):
        value.commit([{'type': 'text', 'text': f'new-{index}'}], 'wait', [], index)
    event, archive = run(value, runtime)
    assert 'report.txt' in archive['summary-request.json']['messages'][0]['content']
    assert value.summary_version == 2
    assert value.step_indices == [12, 13, 14, 15]
    assert event['summarized_steps'] == [6, 7, 8, 9, 10, 11]
    messages, _ = value.compose('fixed system', [{'type': 'image'}], ['current'])
    assert messages[1]['content'][0]['text'] == 'Original goal'
    assert sum('Original goal' in str(message) for message in messages) == 1


def test_projection_uses_prior_engine_usage_and_current_turn_estimate():
    value = conversation()
    assert projected_context_tokens(value, [{'type': 'text', 'text': 'new receipt'}]) > 27000
    value.last_total_tokens = None
    assert projected_context_tokens(value, []) == 0
