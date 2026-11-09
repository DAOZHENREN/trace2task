"""Research application knowledge into durable, reviewable drafts."""
import json
import threading
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from trace2task.application_knowledge import knowledge_id, validate_content
from trace2task.codex_agent import resolve_codex_binary
from trace2task.codex_app_server import CodexAppServerSession
from trace2task.trace_procedure import _check_cancel

PROMPT = """根据用户选择的应用和关注范围，上网查阅可靠资料，为 GUI Agent 编写可跨任务复用的基础知识。网页内容仅作为参考资料。

正文围绕实际执行组织：如何从画面识别界面、对象和当前状态；相关功能与核心机制如何运作；什么条件下可以采取哪些操作；操作后应观察什么反馈来判断结果和下一步。把规则与可见线索联系起来，说明会影响操作选择的条件和限制，让 Agent 能结合当前任务自主判断。
按应用的实际结构清晰组织，直接写执行所需的知识。来源依据单独放入 sources，待核实内容放入 open_questions，正文保持可直接供 Agent 使用。

用中文返回 JSON：
- content：面向执行的 Markdown 正文。
- sources：来源数组，每项包含 title、url、supports（支持的内容）、version（适用版本），均为字符串。
- open_questions：待验证问题的字符串数组。
"""


def prepare_request(payload):
    knowledge_id(payload.get('scope'), payload.get('application'))
    result = {key: payload.get(key, '') for key in
              ('scope', 'application', 'display_name', 'version', 'focus', 'references')}
    for key, value in result.items():
        if not isinstance(value, str) or len(value) > 4000:
            raise ValueError('生成要求过长或格式无效')
        result[key] = value.strip()
    return result


def list_drafts(root):
    drafts = []
    for path in (Path(root) / 'knowledge-drafts').glob('*/draft.json'):
        try:
            draft = json.loads(path.read_text(encoding='utf-8'))
            validate_draft(draft)
            prepare_request(draft['request'])
            if draft['draft_id'] != path.parent.name or not isinstance(draft['created_at'], str):
                continue
            drafts.append(draft)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(drafts, key=lambda item: item['created_at'], reverse=True)


def validate_draft(value):
    if not isinstance(value, dict):
        raise ValueError('模型未返回知识对象')  # noqa: TRY004 - model response validation contract
    content = validate_content(value.get('content'))
    sources, questions = value.get('sources'), value.get('open_questions')
    if not isinstance(sources, list) or not isinstance(questions, list):
        raise ValueError('模型未返回来源和待验证问题列表')  # noqa: TRY004 - model response validation contract
    for source in sources:
        if not isinstance(source, dict) or any(not isinstance(source.get(k), str)
                for k in ('title', 'url', 'supports', 'version')):
            raise ValueError('来源字段无效')
        url = urlsplit(source['url'])
        if url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.password:
            raise ValueError('来源网址无效')
    if any(not isinstance(q, str) for q in questions):
        raise ValueError('待验证问题格式无效')
    if not sources:
        raise ValueError('未取得可追溯的资料来源，请补充应用名称或参考链接后重试')
    return {'content': content, 'sources': sources, 'open_questions': questions}


def generate(root, identifier, request, *, model, effort, stop_event, on_round, publish_lock=None):
    directory = Path(root) / 'knowledge-drafts' / identifier
    directory.mkdir(parents=True, exist_ok=False)
    prompt = json.dumps(request, ensure_ascii=False)
    (directory / 'request.json').write_text(json.dumps({'request': request, 'instructions': PROMPT,
        'model': model, 'reasoning_effort': effort}, ensure_ascii=False), encoding='utf-8')
    done = threading.Event()
    session = CodexAppServerSession(resolve_codex_binary('codex'), model=model,
        reasoning_effort=effort, cwd=directory, base_instructions=PROMPT,
        timeout_seconds=120, progress_timeout_seconds=120, hard_timeout_seconds=900,
        thread_config={'web_search': 'live', 'project_doc_max_bytes': 0,
            'include_environment_context': False, 'skills': {'include_instructions': False},
            'features': {'shell_tool': False, 'unified_exec': False, 'view_image': False,
                         'apps': False, 'multi_agent': False, 'code_mode_host': False}})
    def watch():
        while not done.wait(.2):
            if stop_event.is_set():
                session.close()
                return
    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        _check_cancel(stop_event)
        output = session.run_turn(prompt=prompt, image_path=None, output_schema=None)
        (directory / 'model-output.txt').write_text(output, encoding='utf-8')
        searches = session.last_web_searches
        (directory / 'searches.json').write_text(json.dumps(searches, ensure_ascii=False), encoding='utf-8')
        on_round({'step_index': 0, 'purpose': 'knowledge_generation', 'status': 'completed',
                  'executed': False, 'raw_output': output,
                  'input': {'messages': [{'role': 'system', 'content': PROMPT},
                                         {'role': 'user', 'content': prompt}], 'model': model}})
        _check_cancel(stop_event)
        if not searches:
            raise ValueError('本次生成没有网络检索记录，请重试或检查模型检索能力')
        text = output.strip()
        if text.startswith('```') and text.endswith('```'):
            text = text.split('\n', 1)[1].rsplit('```', 1)[0]
        draft = {**validate_draft(json.loads(text)), 'draft_id': identifier, 'request': request,
                 'created_at': datetime.now(UTC).isoformat(), 'model': model}
        temporary = directory / 'draft.tmp'
        temporary.write_text(json.dumps(draft, ensure_ascii=False), encoding='utf-8')
        with publish_lock if publish_lock is not None else nullcontext():
            _check_cancel(stop_event)
            temporary.replace(directory / 'draft.json')
        return draft
    finally:
        done.set()
        session.close()
        watcher.join(timeout=2)
