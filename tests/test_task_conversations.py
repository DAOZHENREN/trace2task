import base64
import hashlib
import io
import json

import pytest

from trace2task.chat_model_adapter import ChatModelAdapter
from trace2task.gui_conversation import GuiConversation
from trace2task.model_api import ModelAPIConfig, ModelAPISession


def image_experience():
    from PIL import Image

    value = experience()
    value['images'] = []
    for index, color in enumerate(('blue', 'green')):
        buffer = io.BytesIO()
        Image.new('RGB', (8, 6), color).save(buffer, format='PNG')
        data = buffer.getvalue()
        value['images'].append({'id': f'{index:04d}', 'sha256': hashlib.sha256(data).hexdigest(),
                                'base64': base64.b64encode(data).decode()})
    return value


def test_codex_attaches_a_images_in_audited_order_and_reattaches_after_reset(tmp_path):
    session = Session()
    adapter = ChatModelAdapter(session, selected_windows=False, evidence_directory=tmp_path / 'evidence')
    context = image_experience()
    args = {'screenshot': tmp_path / 'current.png', 'history': [], 'experience_context': context}
    first = adapter.predict('task', **args)
    assert session.requests[0]['image_path'] == args['screenshot']
    snapshots = session.requests[0]['additional_image_paths']
    assert [path.read_bytes() for path in snapshots] == [base64.b64decode(item['base64']) for item in context['images']]
    assert context['model_input'] in session.requests[0]['prompt']
    assert 'image 1 is the CURRENT screenshot' in session.requests[0]['prompt']
    assert first['context_management']['new_image_count'] == 3
    adapter.predict('task', **args)
    assert session.requests[1]['additional_image_paths'] == ()
    session.context_near_limit = True
    third = adapter.predict('task', **args)
    assert session.requests[2]['additional_image_paths'] == snapshots
    assert third['context_management']['removed_images'] == 4
    assert third['context_management']['new_image_count'] == 3


def test_api_a_request_contains_reviewed_text_and_image_bytes(tmp_path):
    requests = []

    def requester(_config, payload):
        requests.append(payload)
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({
            'task_complete': True, 'reason': 'visible', 'actions': []})}}], 'usage': {'total_tokens': 100}}

    current = tmp_path / 'current.png'
    current.write_bytes(b'current-observation-fixture')
    context = image_experience()
    session = ModelAPISession(ModelAPIConfig(api_key='test'), model='test', requester=requester)
    adapter = ChatModelAdapter(session, selected_windows=False, provider='api', evidence_directory=tmp_path / 'evidence')
    adapter.predict('new task', screenshot=current, history=[], experience_context=context)
    parts = requests[0]['messages'][-1]['content']
    assert context['model_input'] in parts[0]['text']
    pictures = [base64.b64decode(p['image_url']['url'].split(',', 1)[1]) for p in parts if p['type'] == 'image_url']
    assert pictures == [current.read_bytes(), *[base64.b64decode(i['base64']) for i in context['images']]]
    assert [path.read_bytes() for path in (tmp_path / 'evidence').glob('*.png')] == pictures[1:]


def test_local_conversation_pins_a_evidence_across_image_eviction_and_summary():
    conversation = GuiConversation('id', 'model', 'task', image_experience())
    conversation.initial_user_text = experience()['model_input']
    conversation.evidence_images = ['evidence-one', 'evidence-two']
    conversation.evidence_image_ids = ['0000', '0001']
    conversation.commit([{'type': 'image'}], 'reply', ['old-current'], step_index=3)
    assert conversation.discard_oldest_image()['removed_images'] == 1
    assert conversation.discard_oldest_image() is None
    for summary in (None, 'Explicit history summary'):
        conversation.summary = summary
        messages, images = conversation.compose('system', [{'type': 'image'}], ['current'])
        assert images == ['evidence-one', 'evidence-two', 'current']
        assert messages[1]['content'][0]['text'] == experience()['model_input']
        assert sum(p['type'] == 'image' for m in messages if isinstance(m['content'], list) for p in m['content']) == 3
        assert conversation.image_steps() == [None, None]


def experience():
    text = 'Full experience\r\n{"actions":[{"action":"all fields retained"}]}\r\n'
    return {"kind": "trace_sequence", "task_id": "demo", "model_input": text,
            "sha256": hashlib.sha256(text.encode()).hexdigest()}


class Session:
    def __init__(self):
        self.requests = []
        self.resets = 0
        self.context_near_limit = False

    def reset_thread(self):
        self.resets += 1
        self.context_near_limit = False

    def run_turn(self, **kwargs):
        self.requests.append(kwargs)
        return json.dumps({"task_complete": False, "reason": "look", "actions": [
            {"skill": "wait", "args": {"duration_ms": 1}}]})


def test_native_session_continues_and_only_restarts_near_limit(tmp_path):
    session = Session()
    adapter = ChatModelAdapter(session, selected_windows=False)
    args = {"screenshot": tmp_path / "image.png", "experience_context": experience()}
    adapter.predict("task", history=[], **args)
    adapter.predict("task", history=[{"receipt": "first"}], **args)
    assert session.resets == 0
    assert experience()["model_input"] in session.requests[0]["prompt"]
    assert "Full experience" not in session.requests[1]["prompt"]
    assert '"first"' in session.requests[1]["prompt"]
    session.context_near_limit = True
    adapter.predict("task", history=[{"receipt": "first"}, {"receipt": "second"}], **args)
    assert session.resets == 1
    replay = session.requests[2]['prompt'].split('Prior text messages with original roles:\n', 1)[1]
    replay = json.loads(replay.split('\nCurrent user message:\n', 1)[0])
    assert experience()["model_input"] in replay[0]['content']
    assert '"first"' in replay[2]['content']
    assert '"second"' in session.requests[2]["prompt"]


def test_stateless_transport_retransmits_dialogue_without_duplicate_experience(tmp_path):
    requests = []
    def requester(_config, payload):
        requests.append(payload)
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
            "task_complete": True, "reason": "visible", "actions": []})}}],
                "usage": {"total_tokens": 100}}
    screenshot = tmp_path / "image.png"
    screenshot.write_bytes(b"fake")
    session = ModelAPISession(ModelAPIConfig(api_key="test-key"), model="test", requester=requester)
    adapter = ChatModelAdapter(session, selected_windows=False, provider="api")
    for _ in range(2):
        adapter.predict("task", screenshot=screenshot, history=[], experience_context=experience())
    assert [m["role"] for m in requests[1]["messages"]] == ["system", "user", "assistant", "user"]
    assert json.dumps(requests[1]).count("Full experience") == 1


def test_conversation_drops_only_old_image_never_text_or_experience():
    data = experience()
    conversation = GuiConversation("id", "model", "task", data)
    conversation.commit([{"type": "image"}, {"type": "text", "text": "old user text"}],
                        "old answer", ["old image"], step_index=7)
    assert conversation.discard_oldest_image() == {
        "turn_index": 0, "step_index": 7, "removed_images": 1, "removed_text_messages": 0}
    messages, images = conversation.compose(data["model_input"], [{"type": "image"}], ["new image"])
    assert messages[0]["content"] == data["model_input"]
    assert images == ["new image"]
    assert messages[1]['content'] == [{"type": "text", "text": "task"},
                                      {"type": "text", "text": "old user text"}]
    assert messages[2]['content'] == "old answer"
    assert not conversation.discard_oldest_image()


def test_old_graph_is_not_an_execution_experience(tmp_path):
    with pytest.raises(ValueError, match="状态图"):
        ChatModelAdapter(Session(), selected_windows=False).predict(
            "task", screenshot=tmp_path / "image.png", history=[], experience_context={"semantic": {}})


def test_chat_completions_eviction_preserves_all_text_and_roles(tmp_path):
    screenshot = tmp_path/'image.png'
    screenshot.write_bytes(b'png')
    requests = []
    def requester(_config, payload):
        requests.append(payload)
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({
            'task_complete': True, 'reason': 'first answer', 'actions': []})}}],
                'usage': {'total_tokens': 30000, 'prompt_tokens': 29900, 'completion_tokens': 100}}
    session = ModelAPISession(ModelAPIConfig(api_key='test-key'), model='test', requester=requester)
    adapter = ChatModelAdapter(session, selected_windows=False, provider='api')
    adapter.predict('original task', screenshot=screenshot, history=[], experience_context=experience())
    output = adapter.predict('original task', screenshot=screenshot, history=[], experience_context=experience())
    messages = requests[-1]['messages']
    assert [m['role'] for m in messages] == ['system', 'user', 'assistant', 'user']
    assert experience()['model_input'] in messages[1]['content'][0]['text']
    assert 'first answer' in messages[2]['content']
    assert sum(part['type'] == 'image_url' for m in messages if isinstance(m['content'], list)
               for part in m['content']) == 1
    assert output['context_management']['removed_text_messages'] == 0
    assert output['context_management']['removed_images'] == 1
    assert output['context_management']['removed_tokens'] is None
