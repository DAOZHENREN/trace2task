"""Compile frozen A evidence into a separate, text-only Procedure B version."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from contextlib import nullcontext
from datetime import UTC, datetime
from uuid import uuid4

from trace2task.codex_agent import resolve_codex_binary
from trace2task.codex_app_server import CodexAppServerSession
from trace2task.codex_catalog import CodexModelCatalog
from trace2task.procedure_prompt import PROCEDURE_PROMPT, PROMPT_VERSION
from trace2task.trace_evidence import digest, export_a
from trace2task.trace_library import duplicate_confirmation, recording_source

SCHEMA = 'trace2task.B.procedure.v1'


class ProcedureCompileCancelled(RuntimeError):
    pass


def _check_cancel(stop_event):
    if stop_event.is_set():
        raise ProcedureCompileCancelled('Agent 编译已停止，未发布 B 版本')


def validate_procedure(text, *, phase_method=False):
    """Validate format only; semantic correctness still requires human inspection."""
    if not isinstance(text, str):
        raise TypeError('Agent 未返回文本 Procedure')
    value = text.strip()
    title = re.match(r'^# ([^\r\n]+)\r?\n', value)
    steps = re.search(r'^(?:## \S[^\r\n]*\r?\n|\d+[.)] +(?=\S))' if phase_method else r'^## 步骤\s*\r?\n', value, re.MULTILINE)
    if (not title or not steps or re.search(r'^\s*(```|~~~)', value, re.MULTILINE)
            or (not phase_method and not re.search(r'^\s*\d+[.)]\s+\S', value[steps.end():], re.MULTILINE))
            or not value[steps.end():].strip()):
        raise ValueError('Agent 未形成可用的 Markdown Procedure（方法名称、用途、阶段步骤）；原文已保留，未发布 B 版本')
    description = next((line.strip() for line in value[title.end():steps.start()].splitlines()
                        if line.strip() and not line.lstrip().startswith(('#', '输入：', '输入:'))), '')
    if not description:
        raise ValueError('Procedure 缺少方法用途；原文已保留，未发布 B 版本')
    return title.group(1).strip(), description


def compile_b(project_root, trace_path, *, model, reasoning_effort, confirm_duplicate=False,
              stop_event=None, progress=None, on_round=None, publish_lock=None,
              session_factory=None, binary_resolver=None):
    stop_event = stop_event or threading.Event()
    _check_cancel(stop_event)
    CodexModelCatalog(project_root).validate(model, reasoning_effort)
    root, trace, derivation = recording_source(project_root, trace_path)
    confirmation = duplicate_confirmation(root, trace, 'B')
    if confirmation and confirm_duplicate is not True:
        return confirmation
    report = progress or (lambda message: None)
    record_round = on_round or (lambda entry: None)
    _check_cancel(stop_event)
    identifier = str(uuid4())
    directory = root / 'trace-library' / identifier
    directory.mkdir(parents=True, exist_ok=False)
    report('正在冻结动作序列与已选历史截图；不会生成可见的 A 副本。')
    evidence = directory / 'evidence'
    export_a(trace.parent, derivation['directory'], evidence)
    source = json.loads((evidence / 'manifest.json').read_text(encoding='utf-8'))
    input_text = (evidence / 'model-input.txt').read_bytes().decode('utf-8')
    pictures = [{**image, 'file': 'evidence/' + image['file']} for image in source['model_images']]
    paths = tuple(directory / picture['file'] for picture in pictures)
    # The compiler receives only the selected original images, never contact sheets or inferred frames.
    for picture, path in zip(pictures, paths, strict=True):
        if digest(path) != picture['sha256']:
            raise ValueError('冻结的编译截图校验失败')
    _check_cancel(stop_event)
    compiler = {'provider': 'codex', 'model': model, 'reasoning_effort': reasoning_effort,
                'prompt_version': PROMPT_VERSION,
                'instructions_sha256': hashlib.sha256(PROCEDURE_PROMPT.encode('utf-8')).hexdigest(),
                'input_sha256': hashlib.sha256(input_text.encode('utf-8')).hexdigest()}
    (directory / 'compiler-instructions.txt').write_text(PROCEDURE_PROMPT, encoding='utf-8', newline='')
    (directory / 'compiler-input.txt').write_text(input_text, encoding='utf-8', newline='')
    entry = {'step_index': 0, 'purpose': 'compile', 'status': 'pending', 'executed': False,
             'execution': {'executed': False, 'status': 'not_applicable'},
             'input': {'messages': [{'role': 'system', 'content': PROCEDURE_PROMPT},
                                   {'role': 'user', 'content': [{'type': 'text', 'text': input_text},
                                    *[{'type': 'localImage', 'path': str(path)} for path in paths]]}],
                       'image_sources': [{'kind': 'experience', 'evidence_id': image['id']} for image in pictures],
                       'model': model, 'reasoning_effort': reasoning_effort},
             'system_managed_externally': True}
    (directory / 'request.json').write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding='utf-8')
    record_round(entry)
    session = None
    watcher = None
    done = threading.Event()
    started = time.perf_counter()
    try:
        _check_cancel(stop_event)
        factory = session_factory or CodexAppServerSession
        resolver = binary_resolver or resolve_codex_binary
        session = factory(resolver('codex'), model=model, reasoning_effort=reasoning_effort,
                          cwd=evidence, base_instructions=PROCEDURE_PROMPT, timeout_seconds=120,
                          progress_timeout_seconds=90, hard_timeout_seconds=600,
                          thread_config={'web_search': 'disabled', 'project_doc_max_bytes': 0,
                                         'include_environment_context': False,
                                         'skills': {'include_instructions': False},
                                         'features': {'shell_tool': False, 'unified_exec': False, 'view_image': False,
                                                      'apps': False, 'multi_agent': False, 'code_mode_host': False}})

        def watch_stop():
            while not done.wait(.1):
                if stop_event.is_set():
                    session.close()
                    return

        watcher = threading.Thread(target=watch_stop, name='trace2task-procedure-stop', daemon=True)
        watcher.start()
        _check_cancel(stop_event)
        report(f'Agent 正在编译 Procedure B：{model} / {reasoning_effort}，历史截图 {len(paths)} 张。')
        output = session.run_turn(prompt=input_text, image_path=paths[0] if paths else None,
                                  additional_image_paths=paths[1:], output_schema=None)
        if not isinstance(output, str):
            raise TypeError('Agent 未返回文本 Procedure')
        (directory / 'model-output.md').write_text(output, encoding='utf-8', newline='')
        entry['raw_output'] = output
        _check_cancel(stop_event)
        name, description = validate_procedure(output)
        # Detect any mutation of the frozen inputs before publishing the output they support.
        for filename, expected in source['artifact_sha256'].items():
            if digest(evidence / filename) != expected:
                raise ValueError('编译期间冻结证据发生变化，未发布 B 版本')
        (directory / 'model-input.txt').write_text(output, encoding='utf-8', newline='')
        created_at = datetime.now(UTC).isoformat(timespec='microseconds')
        body = {'trace_name': name, 'description': description, 'representation': 'B',
                'source_recording': str(trace.parent), 'compiler': compiler,
                'compiler_images': pictures, 'image_selection': source['image_selection'],
                'procedure_file': 'model-input.txt', 'runtime_images': [],
                'note': '执行时仅发送 Procedure 正文。编译提示词、动作证据、历史截图和审计信息不额外发送。'}
        (directory / 'body.json').write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding='utf-8')
        artifacts = {path.relative_to(directory).as_posix(): digest(path)
                     for path in directory.rglob('*') if path.is_file()}
        manifest = {'schema': SCHEMA, 'condition': 'B', 'model_input_format': 'procedure-markdown-v1',
                    'created_at': created_at, 'source_recording': str(trace.parent),
                    'compiler': compiler, 'compiler_images': pictures, 'image_selection': source['image_selection'],
                    'artifact_sha256': artifacts, 'model_input_sha256': artifacts['model-input.txt']}
        manifest_path = directory / 'manifest.json'
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        with publish_lock if publish_lock is not None else nullcontext():
            _check_cancel(stop_event)
            # Header-last publication: failures and cancelled calls never enter the usable library.
            (directory / 'header.json').write_text(json.dumps({
                'trace_name': name, 'description': description, 'representation': 'B',
                'created_at': created_at, 'source_recording': str(trace.parent),
                'body_sha256': artifacts['body.json'], 'manifest_sha256': digest(manifest_path),
            }, ensure_ascii=False, indent=2), encoding='utf-8')
        entry['status'] = 'compiled'
        return {'id': identifier, 'representation': 'B', 'output_directory': str(directory),
                'trace_name': name, 'compiler': compiler}
    except Exception as error:
        entry.update(status='cancelled' if stop_event.is_set() else 'error', error=str(error))
        _check_cancel(stop_event)
        raise
    finally:
        done.set()
        if watcher is not None:
            watcher.join(timeout=4)
        if session is not None:
            session.close()
        entry['model_roundtrip_ms'] = round((time.perf_counter() - started) * 1000, 2)
        record_round(entry)
