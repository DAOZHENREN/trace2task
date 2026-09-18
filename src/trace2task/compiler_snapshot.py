"""Immutable, byte-bound automatic drafts. No review status is inferred here."""

from __future__ import annotations

import hashlib
import json
import shutil
import stat
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MARKER = ".automatic-compiler-snapshot.json"


def _reject_symlink_ancestors(path: Path) -> None:
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise RuntimeError("Snapshot paths must not contain symlinks")


def tree_digest(root: Path, *, exclude_marker: bool = False) -> tuple[str, int, int]:
    """Preserve the historical relative-path/size/SHA-256 digest algorithm."""
    _reject_symlink_ancestors(root)
    if not root.is_dir():
        raise RuntimeError("Snapshot tree must be a real directory")
    digest = hashlib.sha256()
    count = size = 0
    for path in sorted(root.rglob("*")):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise RuntimeError(f"Unsafe snapshot entry: {path}")
        if stat.S_ISDIR(mode) or (exclude_marker and path == root / MARKER):
            continue
        data = path.read_bytes()
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0" + str(len(data)).encode("ascii") + b"\0")
        digest.update(hashlib.sha256(data).hexdigest().encode("ascii") + b"\n")
        count += 1
        size += len(data)
    return digest.hexdigest(), count, size


def validate_snapshot(task_path: Path) -> dict[str, Any]:
    _reject_symlink_ancestors(task_path)
    manifest = task_path.parent.parent / "snapshot.json"
    if not manifest.is_file():
        raise RuntimeError("--allow-automatic-compiler-draft requires a frozen Compiler snapshot")
    if manifest.is_symlink() or task_path.is_symlink():
        raise RuntimeError("Snapshot paths must not be symlinks")
    if not task_path.is_file():
        raise RuntimeError("Compiler snapshot task file is missing")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("kind") != "automatic_compiler_output":
        raise RuntimeError("Compiler snapshot manifest is invalid")
    if payload.get("schema_version") not in {"0.1", "0.2"}:
        raise RuntimeError("Compiler snapshot schema version is invalid")
    if payload.get("task_path") != str(task_path.resolve()):
        raise RuntimeError("Compiler snapshot manifest does not match --task")
    if payload.get("taskpack_root") != str(task_path.parent.resolve()):
        raise RuntimeError("Compiler snapshot root is invalid")
    actual = tree_digest(task_path.parent)
    if actual != (payload.get("tree_sha256"), payload.get("file_count"), payload.get("size_bytes")):
        raise RuntimeError("Compiler snapshot tree digest mismatch")
    # 0.1 manifests use their original source path; 0.2 keeps a portable local copy.
    source = Path(str(payload.get("source_trace") or ""))
    if payload.get("schema_version") == "0.2":
        source = manifest.parent / "provenance" / "source-trace.jsonl"
        previous = manifest.parent / "provenance" / "previous-automatic-compiler-snapshot.json"
        _reject_symlink_ancestors(previous)
        previous_hash = (
            hashlib.sha256(previous.read_bytes()).hexdigest() if previous.is_file() else None
        )
        if previous_hash != payload.get("previous_marker_sha256"):
            raise RuntimeError("Compiler snapshot previous marker hash mismatch")
    _reject_symlink_ancestors(source)
    if source.is_symlink() or not source.is_file():
        raise RuntimeError("Compiler snapshot source trace is unverifiable")
    if hashlib.sha256(source.read_bytes()).hexdigest() != payload.get("source_trace_sha256"):
        raise RuntimeError("Compiler snapshot source trace hash mismatch")
    reference = task_path.parent / "reference" / "trace.jsonl"
    if reference.is_file() and hashlib.sha256(reference.read_bytes()).hexdigest() != payload.get(
        "source_trace_sha256"
    ):
        raise RuntimeError("Compiler snapshot reference trace does not match source trace")
    return payload


def freeze_snapshot(
    task_path: Path,
    *,
    output_root: Path,
    source_trace: Path,
    variant: str,
    model: str,
    reasoning_effort: str,
    fresh_taskpack: bool,
) -> dict[str, Any]:
    """Always create a new version. Preserve old marker bytes outside active tree.

    Caller owns source-marker updates. Failed partial snapshots remain as evidence,
    without a valid snapshot.json completion manifest.
    """
    _reject_symlink_ancestors(task_path)
    _reject_symlink_ancestors(source_trace)
    _reject_symlink_ancestors(output_root)
    task_path = task_path.resolve(strict=True)
    source_trace = source_trace.resolve(strict=True)
    before = tree_digest(task_path.parent, exclude_marker=True)
    source_data = source_trace.read_bytes()
    reference = task_path.parent / "reference" / "trace.jsonl"
    if reference.is_file() and reference.read_bytes() != source_data:
        raise RuntimeError("TaskPack reference trace does not match source trace")
    snapshot_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f") + "-" + uuid.uuid4().hex[:8]
    root = output_root.resolve() / snapshot_id
    if root.is_relative_to(task_path.parent):
        raise RuntimeError("Snapshot destination must be outside source TaskPack")
    root.mkdir(parents=True, exist_ok=False)
    provenance = root / "provenance"
    provenance.mkdir()
    (provenance / "source-trace.jsonl").write_bytes(source_data)
    marker = task_path.parent / MARKER
    marker_data = marker.read_bytes() if marker.is_file() else None
    if marker_data is not None:
        (provenance / "previous-automatic-compiler-snapshot.json").write_bytes(marker_data)
    target = root / "taskpack"
    shutil.copytree(
        task_path.parent,
        target,
        symlinks=True,
        ignore=lambda directory, names: [MARKER] if Path(directory) == task_path.parent else [],
    )
    after = tree_digest(task_path.parent, exclude_marker=True)
    if before != after or tree_digest(target) != before or source_trace.read_bytes() != source_data:
        raise RuntimeError("Compiler snapshot source changed during freezing")
    if marker_data != (marker.read_bytes() if marker.is_file() else None):
        raise RuntimeError("Compiler snapshot marker changed during freezing")
    payload = {
        "schema_version": "0.2",
        "kind": "automatic_compiler_output",
        "snapshot_id": snapshot_id,
        "created_at": datetime.now(UTC).isoformat(),
        "variant": variant,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "fresh_taskpack": fresh_taskpack,
        "source_task_path": str(task_path),
        "source_trace": str(source_trace),
        "source_trace_sha256": hashlib.sha256(source_data).hexdigest(),
        "previous_marker_sha256": hashlib.sha256(marker_data).hexdigest()
        if marker_data is not None
        else None,
        "task_path": str(target / task_path.name),
        "taskpack_root": str(target),
        "tree_sha256": before[0],
        "source_tree_sha256": before[0],
        "file_count": before[1],
        "size_bytes": before[2],
        "review_policy": "Unreviewed automatic Compiler output; requires explicit --allow-automatic-compiler-draft.",
    }
    manifest = root / "snapshot.json"
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        validate_snapshot(target / task_path.name)
    except Exception:
        manifest.rename(root / "invalid-snapshot.json")
        raise
    return payload
