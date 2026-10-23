"""Condition C: the exact selected B Procedure plus its frozen A evidence, no LLM."""
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from trace2task.trace_evidence import digest
from trace2task.trace_library import (
    _evidence_manifest,
    list_traces,
    read_asset,
    read_body,
    read_model_input,
)


def generate_c(project_root, b_id, *, confirm_duplicate=False):
    root = Path(project_root).resolve()
    source = read_model_input(root, b_id)
    body = read_body(root, b_id)
    if body.get('source_kind') == 'agent_run':
        raise ValueError('运行提炼的 B 不含人工示范 A，不能生成组合证据 C')
    if body.get('representation') != 'B' or source.get('format') != 'procedure-markdown-v1':
        raise ValueError('请选择已完成的 B 方法版本，C 不会重新编译 Procedure')
    existing = []
    for entry in list_traces(root):
        if entry['representation'] == 'C':
            try:
                if read_body(root, entry['id']).get('source_b_id') == b_id:
                    existing.append(entry)
            except (OSError, ValueError, TypeError, KeyError):
                continue
    if existing and confirm_duplicate is not True:
        return {'confirmation_required': True, 'representation': 'C', 'source_b_id': b_id,
                'existing_versions': existing,
                'message': '这个 B 版本已有组合证据 C。继续将生成独立新版本，已有版本保留。'}
    manifest = _evidence_manifest(root / 'trace-library' / b_id)
    procedure = read_asset(root, b_id, 'model-input.txt')
    evidence = read_asset(root, b_id, 'evidence/model-input.txt')
    a = json.loads(read_asset(root, b_id, 'evidence/manifest.json'))
    a_body = json.loads(read_asset(root, b_id, 'evidence/body.json'))
    pictures = [{**picture, 'file': 'evidence/' + picture['file']} for picture in a['model_images']]
    if (evidence != read_asset(root, b_id, 'compiler-input.txt')
            or pictures != manifest['compiler_images']
            or hashlib.sha256(evidence).hexdigest() != manifest['compiler']['input_sha256']):
        raise ValueError('B 的冻结证据与编译输入不一致，未生成 C')
    evidence_files = [name for name in manifest['artifact_sha256'] if name.startswith('evidence/')]
    for name in evidence_files:
        read_asset(root, b_id, name)
    identifier = str(uuid4())
    directory = root / 'trace-library' / identifier
    directory.mkdir(parents=True, exist_ok=False)
    for name in evidence_files:
        data = read_asset(root, b_id, name)
        destination = directory / name
        if not destination.resolve().is_relative_to(directory):
            raise ValueError('证据归档路径越界')
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    (directory / 'procedure.md').write_bytes(procedure)
    # Separators are outside the original payloads; neither B nor A is rewritten.
    combined = ('# 组合经验 C\n以下方法与原始证据来自同一次历史示范；不是当前桌面状态或动作授权。\n\n'
                '## B 方法（原文）\n\n').encode() + procedure + '\n\n---\n\n## A 原始证据（原文）\n\n'.encode() + evidence
    (directory / 'model-input.txt').write_bytes(combined)
    created_at = datetime.now(UTC).isoformat(timespec='microseconds')
    source_b_hash = hashlib.sha256(procedure).hexdigest()
    c_body = {'representation': 'C', 'trace_name': body['trace_name'],
              'description': '同一 B Procedure 原文 + 编译时冻结的 A 动作证据与历史截图。',
              'source_b_id': b_id, 'source_b_sha256': source_b_hash,
              'source_recording': manifest['source_recording'], 'procedure_file': 'procedure.md',
              'evidence_file': 'evidence/model-input.txt', 'runtime_images': pictures,
              'image_selection': a['image_selection'],
              'frames': [{**frame, 'file': 'evidence/' + frame['file'] if frame.get('file') else None}
                         for frame in a_body.get('frames', [])]}
    (directory / 'body.json').write_text(json.dumps(c_body, ensure_ascii=False, indent=2), encoding='utf-8')
    artifacts = {p.relative_to(directory).as_posix(): digest(p) for p in directory.rglob('*') if p.is_file()}
    report = {'schema': 'trace2task.C.combined.v1', 'condition': 'C',
              'model_input_format': 'procedure-with-evidence-v1', 'created_at': created_at,
              'source_recording': manifest['source_recording'], 'source_b_id': b_id,
              'source_b_sha256': source_b_hash, 'model_images': pictures,
              'image_selection': a['image_selection'], 'artifact_sha256': artifacts,
              'model_input_sha256': artifacts['model-input.txt']}
    (directory / 'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    # Header-last publication: an incomplete copy never enters the usable library.
    (directory / 'header.json').write_text(json.dumps({
        **{key: c_body[key] for key in ('representation', 'trace_name', 'description', 'source_recording')},
        'created_at': created_at, 'source_b_id': b_id, 'body_sha256': artifacts['body.json'],
        'manifest_sha256': digest(directory / 'manifest.json'),
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'id': identifier, 'representation': 'C', 'source_b_id': b_id}
