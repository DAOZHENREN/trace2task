import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("frame_clock", Path(__file__).parents[1] / "scripts/opencua/frame_clock.py")
clock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clock)


def evidence():
    header = {"type": "header", "schema": "trace2task.obs-frame-clock.v1", "obs_version": "32.2.2",
                  "clock": "windows_qpc_ns", "timestamp_kind": "composition"}
    rows = [{"type": "frame", "pts": i, "timing_pts": i, "cts_ns": 10_000_000_000 + i * 100_000_000,
                 "timebase_num": 1, "timebase_den": 10, "keyframe": i == 0} for i in [0, 2, 1, 3]]
    footer = {"type": "footer", "status": "complete", "count": 4, "overflow": False, "missing_timing": False, "paused": False}
    return [header, *rows, footer]


def write(tmp_path, items):
    path = tmp_path / "clock.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in items), encoding="utf-8")
    return path


def test_reorder_pts_and_report_muxer_discard(tmp_path):
    rows, summary = clock.match_video_frames(write(tmp_path, evidence()), [0, .1, .2])
    assert [row["pts"] for row in rows] == [0, 1, 2]
    assert summary["callback_packets_not_in_video"] == 1


@pytest.mark.parametrize("kind", ["truncated", "missing", "overflow", "paused", "duplicate", "version", "pts", "cts"])
def test_invalid_evidence_rejected(tmp_path, kind):
    items = evidence()
    if kind == "truncated": items.pop()
    elif kind == "missing": items[-1]["missing_timing"] = True
    elif kind in {"overflow", "paused"}: items[-1][kind] = True
    elif kind == "duplicate": items[2] = items[1].copy()
    elif kind == "version": items[0]["obs_version"] = "33.0.0"
    elif kind == "pts": items[1]["timing_pts"] = 99
    elif kind == "cts": items[1]["cts_ns"] = 0
    with pytest.raises(ValueError):
        clock.match_video_frames(write(tmp_path, items), [0, .1, .2])


def test_no_guess_for_container_shift_or_unknown_pts(tmp_path):
    path = write(tmp_path, evidence())
    for pts in ([.1, .2], [0, .25]):
        with pytest.raises(ValueError, match="PTS differ"):
            clock.match_video_frames(path, pts)


def test_missing_muxer_frame_does_not_shift_other_matches(tmp_path):
    rows, summary = clock.match_video_frames(write(tmp_path, evidence()), [0, .2, .3])
    assert [row["pts"] for row in rows] == [0, 2, 3]
    assert summary["callback_packets_not_in_video"] == 1


def test_reports_poststate_leakage_and_strict_boundary():
    rows = [{"cts_ns": 10_000_000_000}, {"cts_ns": 10_100_000_000}]
    frames = [{"marker": 0}, {"marker": 1}]
    comparisons = clock.compare_marker_times(rows, frames, [
        {"marker": 1, "before_update": 10.11}, {"marker": 1, "before_update": 10.1}])
    assert comparisons["cts_ns"][0]["poststate_in_prestate"] is True
    assert comparisons["cts_ns"][1]["poststate_in_prestate"] is False


def test_cts_selection_uses_per_frame_time_not_start_ack():
    rows = [{"cts_ns": 10_000_000_000}, {"cts_ns": 10_100_000_000}, {"cts_ns": 10_300_000_000}]
    assert clock.select_cts_frames(rows, {"before": 9, "equal": 10, "edge": 10.1,
                                        "gap": 10.25, "end": 11}) == {
        "before": -1, "equal": -1, "edge": 0, "gap": 1, "end": 2}
    with pytest.raises(ValueError):
        clock.select_cts_frames(rows, {"bad": float("nan")})


def test_cts_extract_validates_all_before_writing(monkeypatch, tmp_path):
    import sys
    from fractions import Fraction
    from types import SimpleNamespace
    saved = {}
    class Frame:
        time_base = Fraction(1, 10)
        def __init__(self, pts): self.pts = pts
        def to_ndarray(self, **_): return self.pts
    class Container:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def decode(self, **_): return iter(Frame(i) for i in [0, 1, 2])
    monkeypatch.setitem(sys.modules, "av", SimpleNamespace(open=lambda _: Container()))
    def imwrite(path, pixels):
        saved[Path(path).name] = pixels
        return True
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(imwrite=imwrite))
    path = write(tmp_path, evidence())
    results, summary = clock.extract_cts_frames("video", path, {"a": 10.15, "b": 9, "c": 12}, tmp_path)
    assert saved == {"a.png": 1, "c.png": 2}
    assert results["a"]["cts_ns"] == 10_100_000_000
    assert results["a"]["alignment"] == "obs_cts_approximate"
    assert results["b"]["path"] is None
    assert summary["matched_video_frames"] == 3
    saved.clear()
    with pytest.raises(FileNotFoundError):
        clock.extract_cts_frames("video", tmp_path / "missing", {"a": 10.15}, tmp_path)
    assert not saved
    bad = evidence()
    bad[-1]["status"] = "incomplete"
    with pytest.raises(ValueError):
        clock.extract_cts_frames("video", write(tmp_path, bad), {"a": 10.15}, tmp_path)
    assert not saved
