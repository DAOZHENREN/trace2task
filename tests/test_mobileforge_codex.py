import base64
import io

import pytest
from PIL import Image

from trace2task.mobileforge_codex import MobileForgeCodexSession


class FakeSession:
    def __init__(self, executable, **kwargs):
        self.options = kwargs
        self.last_token_usage = {'inputTokens': 12, 'outputTokens': 4, 'totalTokens': 16}
        self.account_type = 'chatgpt'
        self.calls = []

    def reset_thread(self):
        pass

    def start(self):
        pass

    def _request(self, method, params):
        assert method == 'account/read'
        return {'account': {'type': self.account_type}}

    def run_turn(self, **kwargs):
        self.calls.append(kwargs)
        assert kwargs['image_path'].is_file()
        return '{"tasks": []}'

    def close(self):
        pass


def messages():
    data = io.BytesIO()
    Image.new('RGB', (4, 4)).save(data, format='PNG')
    return [{'role': 'system', 'content': 'fixture'}, {'role': 'user', 'content': [
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' +
                                         base64.b64encode(data.getvalue()).decode()}}]}]


def test_subscription_image_bridge(tmp_path):
    session = MobileForgeCodexSession(tmp_path, session_factory=FakeSession, executable='fixture')
    try:
        assert session.complete(messages(), review=True) == '{"tasks": []}'
        assert session.session.options['model'] == 'gpt-6-luna'
        assert not session.session.options['thread_config']['features']['shell_tool']
        assert 'Producing an action prediction is permitted' in session.session.options['base_instructions']
        assert session.last_usage['total_tokens'] == 16
    finally:
        session.close()
    with pytest.raises(RuntimeError, match='closed'):
        session.complete(messages())


def test_api_key_login_cannot_silently_replace_subscription(tmp_path):
    session = MobileForgeCodexSession(tmp_path, session_factory=FakeSession, executable='fixture')
    session.session.account_type = 'apikey'
    try:
        with pytest.raises(ValueError, match='subscription login'):
            session.complete(messages())
        assert session.session.calls == []
    finally:
        session.close()


def test_policy_requests_literal_final_output(tmp_path):
    session = MobileForgeCodexSession(tmp_path, session_factory=FakeSession, executable='fixture')
    try:
        session.complete(messages())
        assert 'FINAL answer as literal text' in session.session.calls[0]['prompt']
    finally:
        session.close()
