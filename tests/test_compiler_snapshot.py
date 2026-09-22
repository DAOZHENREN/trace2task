import json
from pathlib import Path

import pytest

from trace2task.compiler_snapshot import MARKER, freeze_snapshot, tree_digest, validate_snapshot
from trace2task.web_console import WebConsoleController, _taskpack_tree_digest


def _create_symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except OSError as error:
        # Windows permits this only when Developer Mode is enabled or the test
        # process has the Create Symbolic Links privilege.  Do not hide other
        # filesystem failures: they can indicate a real regression.
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("Windows test process lacks symbolic-link privilege")
        raise


def fixture_pack(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    task = root / "task.yaml"
    task.write_text("id: draft\n")
    trace = root / "reference" / "trace.jsonl"
    trace.parent.mkdir()
    trace.write_text("{}\n")
    return task, trace


def freeze(tmp_path, task, trace):
    return freeze_snapshot(
        task,
        output_root=tmp_path / "snapshots",
        source_trace=trace,
        variant="compiled",
        model="test",
        reasoning_effort="low",
        fresh_taskpack=True,
    )


def test_fresh_versions_preserve_bytes_and_previous_metadata(tmp_path):
    task, trace = fixture_pack(tmp_path)
    old = freeze(tmp_path, task, trace)
    marker = task.parent / MARKER
    marker.write_text(json.dumps(old))
    marker_bytes = marker.read_bytes()
    task.write_text("id: new\n")
    new = freeze(tmp_path, task, trace)
    assert old["task_path"] != new["task_path"]
    assert Path(old["task_path"]).read_text() == "id: draft\n"
    assert Path(new["task_path"]).read_bytes() == task.read_bytes()
    assert marker.read_bytes() == marker_bytes
    assert not Path(new["taskpack_root"], MARKER).exists()
    assert (
        Path(new["taskpack_root"])
        .parent.joinpath("provenance", "previous-automatic-compiler-snapshot.json")
        .read_bytes()
        == marker_bytes
    )
    assert validate_snapshot(Path(new["task_path"])) == new


@pytest.mark.parametrize("tamper", ["edit", "add", "remove", "symlink", "trace"])
def test_frozen_content_tamper_rejected(tmp_path, tamper):
    task, trace = fixture_pack(tmp_path)
    payload = freeze(tmp_path, task, trace)
    target = Path(payload["task_path"])
    if tamper == "edit":
        target.write_text("id: changed\n")
    elif tamper == "add":
        target.with_name("extra").write_text("new")
    elif tamper == "remove":
        target.unlink()
    elif tamper == "symlink":
        _create_symlink_or_skip(target.with_name("external"), trace)
    else:
        target.parent.parent.joinpath("provenance", "source-trace.jsonl").write_text("bad")
    with pytest.raises(RuntimeError):
        validate_snapshot(target)


def test_legacy_digest_compatibility_and_original_source_binding(tmp_path):
    task, trace = fixture_pack(tmp_path)
    payload = freeze(tmp_path, task, trace)
    target = Path(payload["task_path"])
    assert tree_digest(target.parent) == _taskpack_tree_digest(target.parent)
    payload["schema_version"] = "0.1"
    manifest = target.parent.parent / "snapshot.json"
    manifest.write_text(json.dumps(payload))
    assert validate_snapshot(target) == payload
    trace.write_text("changed")
    with pytest.raises(RuntimeError, match="source trace hash mismatch"):
        validate_snapshot(target)


def test_web_marker_reuse_requires_current_source_binding(tmp_path):
    task, trace = fixture_pack(tmp_path)
    controller = WebConsoleController(tmp_path)
    kwargs = {
        "variant": "compiled",
        "model": "test",
        "reasoning_effort": "low",
        "source_trace": trace,
    }
    first = controller._freeze_automatic_compiler_snapshot(task, fresh_taskpack=True, **kwargs)
    assert (
        controller._freeze_automatic_compiler_snapshot(task, fresh_taskpack=False, **kwargs)
        == first
    )
    task.write_text("id: recompiled\n")
    with pytest.raises(RuntimeError, match="Stale or unverifiable"):
        controller._freeze_automatic_compiler_snapshot(task, fresh_taskpack=False, **kwargs)
    second = controller._freeze_automatic_compiler_snapshot(task, fresh_taskpack=True, **kwargs)
    assert first["task_path"] != second["task_path"]
    assert validate_snapshot(Path(first["task_path"])) == first


@pytest.mark.parametrize("target", ["file", "directory", "marker"])
def test_source_symlinks_rejected(tmp_path, target):
    task, trace = fixture_pack(tmp_path)
    link = task.parent / (MARKER if target == "marker" else "link")
    _create_symlink_or_skip(link, trace.parent if target == "directory" else trace)
    with pytest.raises(RuntimeError):
        freeze(tmp_path, task, trace)


def test_reference_must_match_declared_source(tmp_path):
    task, _trace = fixture_pack(tmp_path)
    other = tmp_path / "other.jsonl"
    other.write_text("other")
    with pytest.raises(RuntimeError, match="reference trace"):
        freeze(tmp_path, task, other)


def test_legacy_marker_requires_explicit_fresh_version(tmp_path):
    task, trace = fixture_pack(tmp_path)
    controller = WebConsoleController(tmp_path)
    kwargs = {
        "variant": "compiled",
        "model": "test",
        "reasoning_effort": "low",
        "source_trace": trace,
    }
    payload = controller._freeze_automatic_compiler_snapshot(task, fresh_taskpack=True, **kwargs)
    payload.pop("source_tree_sha256")
    payload["schema_version"] = "0.1"
    Path(payload["taskpack_root"]).parent.joinpath("snapshot.json").write_text(json.dumps(payload))
    (task.parent / MARKER).write_text(json.dumps(payload))
    with pytest.raises(RuntimeError, match="Stale or unverifiable"):
        controller._freeze_automatic_compiler_snapshot(task, fresh_taskpack=False, **kwargs)
    assert (
        controller._freeze_automatic_compiler_snapshot(task, fresh_taskpack=True, **kwargs)[
            "schema_version"
        ]
        == "0.2"
    )


def test_freeze_detects_source_mutation_and_preserves_partial_evidence(tmp_path, monkeypatch):
    from trace2task import compiler_snapshot

    task, trace = fixture_pack(tmp_path)
    original_copy = compiler_snapshot.shutil.copytree

    def changed_source(*args, **kwargs):
        result = original_copy(*args, **kwargs)
        task.write_text("changed while copying")
        return result

    monkeypatch.setattr(compiler_snapshot.shutil, "copytree", changed_source)
    with pytest.raises(RuntimeError, match="changed during freezing"):
        freeze(tmp_path, task, trace)
    partials = list((tmp_path / "snapshots").iterdir())
    assert len(partials) == 1
    assert not (partials[0] / "snapshot.json").exists()
    assert (partials[0] / "provenance/source-trace.jsonl").read_bytes() == trace.read_bytes()
