import json
from pathlib import Path

import pytest

from trace2task import rsi_exam_fence


def _identity():
    return {"content_tree_sha256": "a" * 64, "total_bytes": 123, "task_count": 108}


def _sources(tmp_path: Path) -> tuple[Path, Path]:
    upstream, osworld = tmp_path / "upstream", tmp_path / "OSWorld-V2"
    (upstream / "tools").mkdir(parents=True)
    (upstream / "tools/exam_fence.py").write_text("# frozen official source\n", encoding="utf-8")
    osworld.mkdir()
    return upstream, osworld


def test_build_uses_official_builder_then_attests_output(tmp_path, monkeypatch):
    upstream, osworld = _sources(tmp_path)
    built = []

    class OfficialBuilder:
        def __enter__(self):
            def build(path):
                built.append(path)
                value = {"001": ["only-host-constant"]}
                Path(path).write_text(json.dumps(value), encoding="utf-8")
                return value
            return build

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(rsi_exam_fence, "_official_exam_fence", lambda *_: OfficialBuilder())
    receipt = rsi_exam_fence.build_exam_fence_cache(
        upstream, osworld, upstream_revision="u" * 40, osworld_revision="o" * 40,
        osworld_task_source_identity=_identity(),
    )
    cache = upstream / rsi_exam_fence.FENCE_CACHE_RELATIVE
    assert built == [str(cache)]
    assert receipt["generator"] == "tools.exam_fence.build_denylist"
    assert receipt["cache"]["sha256"] == rsi_exam_fence._sha256_file(cache)
    assert rsi_exam_fence.verify_exam_fence_cache(
        upstream, upstream_revision="u" * 40, osworld_revision="o" * 40,
        osworld_task_source_identity=_identity(),
    ) == receipt


def test_unattested_or_tampered_cache_fails_closed(tmp_path, monkeypatch):
    upstream, osworld = _sources(tmp_path)
    cache = upstream / rsi_exam_fence.FENCE_CACHE_RELATIVE
    cache.parent.mkdir(parents=True)
    cache.write_text('{"001": ["x"]}', encoding="utf-8")
    with pytest.raises(rsi_exam_fence.ExamFenceCacheError, match="missing"):
        rsi_exam_fence.build_exam_fence_cache(
            upstream, osworld, upstream_revision="u" * 40, osworld_revision="o" * 40,
            osworld_task_source_identity=_identity(),
        )


def test_symlinked_cache_path_is_rejected(tmp_path):
    upstream, _ = _sources(tmp_path)
    try:
        (upstream / "results").symlink_to(tmp_path / "outside", target_is_directory=True)
    except OSError:
        pytest.skip("Windows test account cannot create symlinks")
    with pytest.raises(rsi_exam_fence.ExamFenceCacheError, match="symlink"):
        rsi_exam_fence.verify_exam_fence_cache(
            upstream, upstream_revision="u" * 40, osworld_revision="o" * 40,
            osworld_task_source_identity=_identity(),
        )
