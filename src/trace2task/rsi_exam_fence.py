"""Prepare and attest the official host-only ``exam_fence`` cache.

``tools.exam_fence`` intentionally creates ``grader_constants.json`` lazily.
The production practice worker mounts the frozen upstream checkout read-only,
so that lazy write must happen once in a trusted host setup process instead.
This module never exposes the generated constants to the model or the guest.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

FENCE_CACHE_RELATIVE = Path("results/audit/fence/grader_constants.json")
FENCE_RECEIPT_RELATIVE = Path("results/audit/fence/grader_constants.trace2task-receipt.json")
FENCE_RECEIPT_SCHEMA_VERSION = 1


class ExamFenceCacheError(RuntimeError):
    """The host-only official fence cache is absent or cannot be attested."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_child(root: Path, relative: Path) -> Path:
    """Return a non-symlinked child, including every existing parent."""
    root = root.resolve(strict=True)
    if root.is_symlink() or relative.is_absolute() or ".." in relative.parts:
        raise ExamFenceCacheError("Unsafe exam fence cache path")
    child = root.joinpath(relative)
    try:
        child.resolve(strict=False).relative_to(root)
    except ValueError as error:
        raise ExamFenceCacheError("Exam fence cache escapes the upstream checkout") from error
    current = root
    for part in relative.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ExamFenceCacheError("Exam fence cache path contains a symlink")
    return child


def _validate_constants(value: Any) -> None:
    """Check only that the official output is a bounded JSON data object."""
    if (not isinstance(value, dict) or not value
            or any(type(key) is not str or not isinstance(items, list)
                   or any(type(item) is not str for item in items)
                   for key, items in value.items())):
        raise ExamFenceCacheError("Official exam fence output has an invalid schema")


@contextmanager
def _official_exam_fence(upstream: Path, osworld: Path) -> Iterator[Any]:
    """Load precisely the frozen official builder with explicit runtime roots.

    This is only called by the host setup command, before a worker is launched.
    The temporary module loading avoids accidentally reusing another checkout's
    ``config.runtime_paths`` module in a long-lived control process.
    """
    source = upstream / "tools/exam_fence.py"
    if source.is_symlink() or not source.is_file():
        raise ExamFenceCacheError("Frozen official exam_fence source is missing")
    saved_environment = {name: os.environ.get(name) for name in ("RSIAGENT_ROOT", "OSWORLD_ROOT")}
    saved_path = list(sys.path)
    saved_modules = {name: sys.modules.pop(name, None) for name in (
        "config", "config.runtime_paths", "trace2task_official_exam_fence",
    )}
    try:
        os.environ["RSIAGENT_ROOT"] = str(upstream)
        os.environ["OSWORLD_ROOT"] = str(osworld)
        sys.path.insert(0, str(upstream))
        spec = importlib.util.spec_from_file_location("trace2task_official_exam_fence", source)
        if spec is None or spec.loader is None:
            raise ExamFenceCacheError("Cannot load frozen official exam_fence source")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        builder = getattr(module, "build_denylist", None)
        if not callable(builder):
            raise ExamFenceCacheError("Frozen official exam_fence has no build_denylist")
        yield builder
    finally:
        sys.path[:] = saved_path
        for name in ("config", "config.runtime_paths", "trace2task_official_exam_fence"):
            sys.modules.pop(name, None)
            if saved_modules[name] is not None:
                sys.modules[name] = saved_modules[name]
        for name, value in saved_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _receipt(
        *, cache: Path, upstream: Path, upstream_revision: str, osworld_revision: str,
        osworld_task_source_identity: dict[str, int | str],
) -> dict[str, Any]:
    source = upstream / "tools/exam_fence.py"
    return {
        "schema_version": FENCE_RECEIPT_SCHEMA_VERSION,
        "kind": "trace2task_official_exam_fence_cache",
        "status": "completed",
        "generator": "tools.exam_fence.build_denylist",
        "cache": {
            "relative_path": FENCE_CACHE_RELATIVE.as_posix(),
            "sha256": _sha256_file(cache),
            "bytes": cache.stat().st_size,
        },
        "source": {
            "upstream_revision": upstream_revision,
            "exam_fence_sha256": _sha256_file(source),
            "osworld_revision": osworld_revision,
            "osworld_task_source_identity": osworld_task_source_identity,
        },
    }


def verify_exam_fence_cache(
        upstream: Path, *, upstream_revision: str, osworld_revision: str,
        osworld_task_source_identity: dict[str, int | str],
) -> dict[str, Any]:
    """Verify cache bytes and provenance without importing official code or writing."""
    cache = _safe_child(upstream, FENCE_CACHE_RELATIVE)
    receipt_path = _safe_child(upstream, FENCE_RECEIPT_RELATIVE)
    if cache.is_symlink() or receipt_path.is_symlink() or not cache.is_file() or not receipt_path.is_file():
        raise ExamFenceCacheError("Official exam fence cache or receipt is missing")
    try:
        constants = json.loads(cache.read_text(encoding="utf-8"))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExamFenceCacheError("Official exam fence cache or receipt is invalid JSON") from error
    _validate_constants(constants)
    expected = _receipt(
        cache=cache,
        upstream=upstream.resolve(strict=True),
        upstream_revision=upstream_revision,
        osworld_revision=osworld_revision,
        osworld_task_source_identity=osworld_task_source_identity,
    )
    if receipt != expected:
        raise ExamFenceCacheError("Official exam fence cache provenance does not match frozen sources")
    return receipt


def build_exam_fence_cache(
        upstream: Path, osworld: Path, *, upstream_revision: str, osworld_revision: str,
        osworld_task_source_identity: dict[str, int | str],
) -> dict[str, Any]:
    """Use the official builder once and attest its host-only output.

    An existing unattested cache is deliberately refused instead of overwritten:
    an operator must remove it explicitly before rebuilding in a trusted setup
    environment.  This prevents silently blessing arbitrary constants.
    """
    upstream = upstream.resolve(strict=True)
    osworld = osworld.resolve(strict=True)
    cache = _safe_child(upstream, FENCE_CACHE_RELATIVE)
    receipt_path = _safe_child(upstream, FENCE_RECEIPT_RELATIVE)
    if cache.exists() or receipt_path.exists():
        return verify_exam_fence_cache(
            upstream, upstream_revision=upstream_revision, osworld_revision=osworld_revision,
            osworld_task_source_identity=osworld_task_source_identity,
        )
    cache.parent.mkdir(parents=True, exist_ok=True)
    with _official_exam_fence(upstream, osworld) as builder:
        output = builder(str(cache))
    _validate_constants(output)
    if cache.is_symlink() or not cache.is_file():
        raise ExamFenceCacheError("Official exam fence builder did not create a regular cache file")
    try:
        persisted = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExamFenceCacheError("Official exam fence builder wrote invalid JSON") from error
    _validate_constants(persisted)
    if persisted != output:
        raise ExamFenceCacheError("Official exam fence builder output does not match its cache")
    receipt = _receipt(
        cache=cache, upstream=upstream, upstream_revision=upstream_revision,
        osworld_revision=osworld_revision, osworld_task_source_identity=osworld_task_source_identity,
    )
    try:
        with receipt_path.open("x", encoding="utf-8") as handle:
            json.dump(receipt, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError as error:
        raise ExamFenceCacheError("Exam fence cache receipt appeared during setup") from error
    return verify_exam_fence_cache(
        upstream, upstream_revision=upstream_revision, osworld_revision=osworld_revision,
        osworld_task_source_identity=osworld_task_source_identity,
    )
