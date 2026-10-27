"""Explicit Trace representations; header listing and on-demand body."""
import base64
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from trace2task.trace_evidence import export_a
from trace2task.trace_projection import export


def generate_d(project_root, trace_path, *, confirm_duplicate=False):
    return generate_representation(project_root, trace_path, 'D', confirm_duplicate=confirm_duplicate)


def generate_a(project_root, trace_path, *, confirm_duplicate=False):
    return generate_representation(project_root, trace_path, 'A', confirm_duplicate=confirm_duplicate)


def generate_representation(project_root, trace_path, representation, *, confirm_duplicate=False):
    if representation not in {'A', 'D'}:
        raise ValueError('尚未实现这种经验表示')
    root, trace, derivation = recording_source(project_root, trace_path)
    confirmation = duplicate_confirmation(root, trace, representation)
    if confirmation and confirm_duplicate is not True:
        return confirmation
    identifier = str(uuid4())
    exporter = export_a if representation == 'A' else export
    exporter(trace.parent, derivation["directory"], root / "trace-library" / identifier)
    return {"id": identifier}


def recording_source(project_root, trace_path):
    """Resolve a completed native recording without creating an output or calling a model."""
    root = Path(project_root).resolve()
    trace = (root / trace_path).resolve()
    if trace.name != "events.jsonl" or trace.parent.parent != root / "runs" or not trace.is_file():
        raise ValueError("请选择本项目的 OpenCUA 录制")
    if trace.with_name('.trace2task-deleted.json').exists():
        raise ValueError('录制已移入回收站，请先恢复')
    sidecar = json.loads((trace.parent / "trace2task.json").read_text(encoding="utf-8"))
    if sidecar.get("source") != "opencua_native":
        raise ValueError("A / B / D 表示仅支持 OpenCUA 录制")
    derivation = sidecar.get("derivation") or {}
    if derivation.get("status") != "completed" or not derivation.get("directory"):
        raise ValueError("请先完成官方动作整理")
    return root, trace, derivation


def duplicate_confirmation(root, trace_path, representation):
    existing = compiled_versions(root, trace_path, representation)
    label = {'A': '原始证据 A', 'B': 'Agent 方法 B', 'D': '精简序列 D'}[representation]
    if existing:
        return {"confirmation_required": True, "representation": representation,
                "trace_path": (Path(root) / trace_path).resolve().relative_to(root).as_posix(), "existing_versions": existing,
                "message": f"同一原始录制已有{label}。继续将生成一个新版本，已有版本保留。"}
    return None


def version_time(header, path):
    """Old exports have no generation timestamp; label the file-time fallback."""
    try:
        value = datetime.fromisoformat(header["created_at"])
        if value.tzinfo is not None:
            return value.astimezone(UTC).isoformat(timespec="microseconds"), "metadata"
    except (KeyError, TypeError, ValueError):
        pass
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="microseconds"), "file_mtime"


def list_traces(project_root):
    root = Path(project_root).resolve() / "trace-library"
    result = []
    for path in sorted(root.glob("*/header.json")):
        if (not path.resolve().is_relative_to(root) or path.parent.name == '.trash'
                or path.with_name('.trace2task-deleted.json').exists()):
            continue
        try:
            header = json.loads(path.read_text(encoding="utf-8"))
            created_at, time_source = version_time(header, path)
            review = {}
            if header['representation'] == 'R':
                from trace2task.procedure_evolution import trial_review
                review = {'review_status': trial_review(path.parent)['decision']}
            result.append({"id": path.parent.name, "trace_name": header["trace_name"],
                           "description": header["description"], "representation": header["representation"],
                           "created_at": created_at, "created_at_source": time_source,
                           **{k: header[k] for k in ('source_kind', 'source_run_id', 'source_draft_id', 'source_recording', 'source_b_id',
                                                    'source_scope', 'source_package', 'lineage', 'condition',
                                                    'revision_changed', 'assisted_by_user', 'execution_condition', 'versioning') if k in header}, **review})
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(result, key=lambda item: (item["created_at"], item["id"]), reverse=True)


def compiled_versions(project_root, trace_path, representation):
    """Match recording identity, not its display name. Read old provenance on demand."""
    root = Path(project_root).resolve()
    recording = (root / trace_path).resolve().parent
    matches = []
    for entry in list_traces(root):
        if entry['representation'] != representation:
            continue
        directory = root / 'trace-library' / entry['id']
        try:
            header = json.loads((directory / 'header.json').read_text(encoding='utf-8'))
            source = header.get('source_recording')
            if source is None:
                manifest = directory / 'manifest.json'
                if not manifest.resolve().is_relative_to(directory):
                    continue
                report = json.loads(manifest.read_text(encoding='utf-8'))
                source = report.get('source_recording')
                if source is None:
                    # Pre-timestamp D exports recorded source paths in their hash map.
                    sources = {str(Path(path).parent) for path in report.get('source_sha256', {})
                               if Path(path).name == 'trace2task.json'}
                    source = next(iter(sources)) if len(sources) == 1 else None
            if isinstance(source, str) and (root / source).resolve() == recording:
                matches.append(entry)
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
    return matches


def read_body(project_root, identifier):
    root = Path(project_root).resolve() / "trace-library"
    directory = (root / identifier).resolve()
    if directory.parent != root or not identifier:
        raise ValueError("无效的 Trace 标识")
    if identifier == '.trash' or (directory / '.trace2task-deleted.json').exists():
        raise ValueError('经验已移入回收站，请先恢复')
    header_path, body_path = directory / "header.json", directory / "body.json"
    if any(not p.resolve().is_relative_to(directory) for p in (header_path, body_path)):
        raise ValueError("Trace 文件不能指向外部路径")
    header = json.loads(header_path.read_text(encoding="utf-8"))
    data = body_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != header["body_sha256"]:
        raise ValueError("Trace 正文已变化，请重新生成")
    return json.loads(data)


def _input_layout(content, runtime):
    """Keep the saved original and identify a lossless model-facing layout separately."""
    if runtime == content:
        return {'content': content}
    source_bytes, runtime_bytes = content.encode('utf-8'), runtime.encode('utf-8')
    return {'content': content, 'runtime_content': runtime,
            'formatting': {'kind': 'compact-json-v1',
                          'source_sha256': hashlib.sha256(source_bytes).hexdigest(),
                          'runtime_sha256': hashlib.sha256(runtime_bytes).hexdigest(),
                          'source_bytes': len(source_bytes), 'runtime_bytes': len(runtime_bytes)}}


def read_model_input(project_root, identifier):
    """Keep saved text verbatim; optionally expose a lossless compact runtime view."""
    from trace2task.model_text import compact_json_suffix

    root = Path(project_root).resolve() / "trace-library"
    directory = (root / identifier).resolve()
    if not identifier or directory.parent != root:
        raise ValueError("无效的 Trace 标识")
    if identifier == '.trash' or (directory / '.trace2task-deleted.json').exists():
        raise ValueError('经验已移入回收站，请先恢复')
    path = directory / "model-input.txt"
    if not path.resolve().is_relative_to(directory):
        raise ValueError("Trace 文件不能指向外部路径")
    header = directory / 'header.json'
    if not header.resolve().is_relative_to(directory):
        raise ValueError('Trace 文件不能指向外部路径')
    representation = json.loads(header.read_text(encoding='utf-8')).get('representation')
    if representation in {'B', 'R'}:
        report = _evidence_manifest(directory)
        extra = {}
        if representation == 'R':
            from trace2task.procedure_evolution import trial_review
            extra = {'evolution': report['evolution'], 'review': trial_review(directory),
                     'parent_content': read_asset(project_root, identifier, 'parent-procedure.md').decode('utf-8')}
        elif report.get('lineage'):
            files = report['artifact_sha256']
            parent_file = 'parent-procedure.md' if 'parent-procedure.md' in files else 'parent-experience.txt'
            extra = {'lineage': report['lineage'], 'revision_changed': report.get('revision_changed'),
                     'parent_content': read_asset(project_root, identifier, parent_file).decode('utf-8'),
                     'revision_diff': (read_asset(project_root, identifier, 'revision.diff').decode('utf-8')
                                       if 'revision.diff' in files else None)}
        return {'filename': 'model-input.txt', 'content': read_asset(project_root, identifier, 'model-input.txt').decode('utf-8'),
                'format': report['model_input_format'], 'images': [], **extra,
                'compiler': {**report['compiler'], 'images': report['compiler_images'],
                             'image_selection': report['image_selection'],
                             'instructions': read_asset(project_root, identifier, 'compiler-instructions.txt').decode('utf-8'),
                             'input': read_asset(project_root, identifier, 'compiler-input.txt').decode('utf-8')}}
    if representation in {'A', 'C'}:
        report = _evidence_manifest(directory)
        content = read_asset(project_root, identifier, 'model-input.txt').decode('utf-8')
        runtime = content
        if representation == 'A' and report.get('model_input_format') == 'actions-with-images-v2':
            runtime = compact_json_suffix(content)
        elif representation == 'C':
            # Never scan the B Markdown for JSON; only the verified A suffix can change layout.
            evidence = read_asset(project_root, identifier, 'evidence/model-input.txt').decode('utf-8')
            if not evidence or not content.endswith(evidence):
                raise ValueError('组合经验与冻结的 A 证据不一致')
            runtime = content[:-len(evidence)] + compact_json_suffix(evidence)
        return {'filename': 'model-input.txt', **_input_layout(content, runtime),
                'format': report.get('model_input_format', 'raw-evidence-v1'),
                'images': report['model_images'], 'image_selection': report['image_selection']}
    content = path.read_bytes().decode('utf-8')
    return {'filename': 'model-input.txt', **_input_layout(content,
            compact_json_suffix(content) if representation == 'D' else content)}


def _evidence_manifest(directory):
    path = directory / 'manifest.json'
    if not path.resolve().is_relative_to(directory):
        raise ValueError('证据索引不能指向外部路径')
    data = path.read_bytes()
    report = json.loads(data)
    if report.get('schema') in {'trace2task.B.procedure.v1', 'trace2task.C.combined.v1',
                              'trace2task.R.procedure-evolution.v1'}:
        header = directory / 'header.json'
        if not header.resolve().is_relative_to(directory):
            raise ValueError('经验索引不能指向外部路径')
        if hashlib.sha256(data).hexdigest() != json.loads(header.read_text(encoding='utf-8')).get('manifest_sha256'):
            raise ValueError('Agent 编译清单已变化，请重新生成并审查')
    elif report.get('schema') not in {'trace2task.A.raw-evidence.v1', 'trace2task.A.action-evidence.v2'}:
        raise ValueError('不是可用的 A / B / C / R 编译版本')
    return report


def read_asset(project_root, identifier, filename):
    """Only hashed A/B/C bundle members may be previewed; never follow source paths."""
    root = (Path(project_root) / 'trace-library').resolve()
    directory = (root / identifier).resolve()
    if (not identifier or directory.parent != root or identifier == '.trash'
            or (directory / '.trace2task-deleted.json').exists()):
        raise ValueError('无效或已移除的经验版本')
    report = _evidence_manifest(directory)
    path = (directory / filename).resolve()
    expected = report['artifact_sha256'].get(filename)
    if not expected or not path.is_relative_to(directory):
        raise ValueError('文件不属于这个编译版本的证据清单')
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('编译证据已变化，请重新生成并审查')
    return data


def execution_context(path):
    """Snapshot selected input; R additionally requires a digest-bound trial review."""
    path = Path(path).resolve()
    if path.name != "model-input.txt" or path.parent.parent.name != "trace-library":
        raise ValueError("无效的经验输入路径")
    root, identifier = path.parent.parent.parent, path.parent.name
    header = next((item for item in list_traces(root) if item["id"] == identifier), None)
    if header is None or header["representation"] not in {'A', 'B', 'C', 'D', 'R'}:
        raise ValueError("找不到可用的经验版本")
    model = read_model_input(root, identifier)
    if header['representation'] == 'R' and model['review']['decision'] != 'approved_for_trial':
        raise ValueError('改进候选尚未获准试用；请先审查，不能直接执行')
    content = model.get('runtime_content', model['content'])
    context = {"kind": "trace_sequence", "task_id": header["trace_name"],
            "model_input": content,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()}
    if header['representation'] in {'A', 'C'}:
        # Freeze bytes at task start. Network services receive bytes, never local paths to open.
        context['images'] = [{'id': image['id'], 'sha256': image['sha256'],
                              'base64': base64.b64encode(read_asset(root, identifier, image['file'])).decode('ascii')}
                             for image in model['images']]
    return context
