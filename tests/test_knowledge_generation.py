import json
import threading
from typing import ClassVar

import pytest

from trace2task import knowledge_generation as generation


def test_research_draft_is_durable_but_not_published(tmp_path, monkeypatch):
    class Session:
        last_web_searches: ClassVar[list] = [{'type': 'webSearch', 'action': {'query': 'official UI docs'}}]
        def __init__(self, *args, **kwargs):
            assert kwargs['thread_config']['web_search'] == 'live'
        def run_turn(self, **kwargs):
            return json.dumps({'content': '窗口菜单提供文件操作。[1]', 'sources': [
                {'title': '官方帮助', 'url': 'https://example.org/docs', 'supports': '文件菜单',
                 'version': '待验证'}], 'open_questions': ['实际运行核对菜单位置']})
        def close(self):
            pass
    monkeypatch.setattr(generation, 'CodexAppServerSession', Session)
    monkeypatch.setattr(generation, 'resolve_codex_binary', lambda _: 'codex')
    request = generation.prepare_request({'scope': 'desktop', 'application': 'Editor'})
    result = generation.generate(tmp_path, 'test', request, model='test', effort='low',
        stop_event=threading.Event(), on_round=lambda entry: None)
    assert generation.list_drafts(tmp_path) == [result]
    assert not (tmp_path / 'application-knowledge').exists()
    assert (tmp_path / 'knowledge-drafts/test/searches.json').is_file()
    Session.last_web_searches = []
    with pytest.raises(ValueError, match='网络检索'):
        generation.generate(tmp_path, 'no-search', request, model='test', effort='low',
            stop_event=threading.Event(), on_round=lambda entry: None)
    assert len(generation.list_drafts(tmp_path)) == 1


def test_reject_invalid_source_and_application():
    with pytest.raises(ValueError):
        generation.prepare_request({'scope': 'android', 'application': 'not a package'})
    with pytest.raises(ValueError):
        generation.validate_draft({'content': 'test', 'open_questions': [], 'sources': [
            {'title': 'bad', 'url': 'javascript:alert(1)', 'supports': 'x', 'version': ''}]})


def test_long_knowledge_can_be_saved_and_loaded(tmp_path):
    from trace2task.application_knowledge import read_knowledge, save_knowledge
    content = '界面状态与操作条件。' * 1000
    saved = save_knowledge(tmp_path, scope='desktop', application='Editor',
                           content=content, expected_sha256='')
    assert read_knowledge(tmp_path, saved['id'])['content'] == content
