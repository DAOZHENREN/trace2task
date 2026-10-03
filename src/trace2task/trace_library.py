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
    root = Path(project_root).resolve()
    trace = (root / trace_path).resolve()
    if trace.name != "events.jsonl" or trace.parent.parent != root / "runs" or not trace.is_file():
        raise ValueError("请选择本项目的 OpenCUA 录制")
    if trace.with_name('.trace2task-deleted.json').exists():
        raise ValueError('录制已移入回收站，请先恢复')
    sidecar = json.loads((trace.parent / "trace2task.json").read_text(encoding="utf-8"))
    if sidecar.get("source") != "opencua_native":
        raise ValueError("A / D 表示仅支持 OpenCUA 录制")
    derivation = sidecar.get("derivation") or {}
    if derivation.get("status") != "completed" or not derivation.get("directory"):
        raise ValueError("请先完成官方动作整理")
    existing = compiled_versions(root, trace_path, representation)
    label = '原始证据 A' if representation == 'A' else '精简序列 D'
    if existing and confirm_duplicate is not True:
        return {"confirmation_required": True, "representation": representation,
                "trace_path": trace.relative_to(root).as_posix(), "existing_versions": existing,
                "message": f"同一原始录制已有{label}。继续将生成一个新版本，已有版本保留。"}
    identifier = str(uuid4())
    exporter = export_a if representation == 'A' else export
    exporter(trace.parent, derivation["directory"], root / "trace-library" / identifier)
    return {"id": identifier}


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
            result.append({"id": path.parent.name, "trace_name": header["trace_name"],
                           "description": header["description"], "representation": header["representation"],
                           "created_at": created_at, "created_at_source": time_source})
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


def read_model_input(project_root, identifier):
    """Return the saved model input verbatim, including whitespace and preamble."""
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
    if json.loads(header.read_text(encoding='utf-8')).get('representation') == 'A':
        report = _evidence_manifest(directory)
        return {'filename': 'model-input.txt', 'content': read_asset(project_root, identifier, 'model-input.txt').decode('utf-8'),
                'format': report.get('model_input_format', 'raw-evidence-v1'),
                'images': report['model_images'], 'image_selection': report['image_selection']}
    return {"filename": "model-input.txt", "content": path.read_bytes().decode("utf-8")}


def _evidence_manifest(directory):
    path = directory / 'manifest.json'
    if not path.resolve().is_relative_to(directory):
        raise ValueError('证据索引不能指向外部路径')
    report = json.loads(path.read_text(encoding='utf-8'))
    if report.get('schema') not in {'trace2task.A.raw-evidence.v1', 'trace2task.A.action-evidence.v2'}:
        raise ValueError('不是可用的原始证据 A 版本')
    return report


def read_asset(project_root, identifier, filename):
    """Only hashed A bundle members may be previewed; never follow source paths."""
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
    """Snapshot the selected A/D input, not a graph or an action grant."""
    path = Path(path).resolve()
    if path.name != "model-input.txt" or path.parent.parent.name != "trace-library":
        raise ValueError("无效的经验输入路径")
    root, identifier = path.parent.parent.parent, path.parent.name
    header = next((item for item in list_traces(root) if item["id"] == identifier), None)
    if header is None or header["representation"] not in {'A', 'D'}:
        raise ValueError("找不到 A 原始证据或 D 精简序列")
    model = read_model_input(root, identifier)
    content = model['content']
    context = {"kind": "trace_sequence", "task_id": header["trace_name"],
            "model_input": content,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()}
    if header['representation'] == 'A':
        # Freeze bytes at task start. Network services receive bytes, never local paths to open.
        context['images'] = [{'id': image['id'], 'sha256': image['sha256'],
                              'base64': base64.b64encode(read_asset(root, identifier, image['file'])).decode('ascii')}
                             for image in model['images']]
    return context
