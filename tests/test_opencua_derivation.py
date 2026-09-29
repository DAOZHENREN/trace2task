import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("opencua_derive", Path(__file__).parents[1] / "scripts/opencua/derive.py")
derive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(derive)


def fake_video(monkeypatch, times):
    class Capture:
        index = -1
        closed = False

        def isOpened(self): return True
        def read(self):
            self.index += 1
            return (True, self.index) if self.index < len(times) else (False, None)
        def get(self, key): return times[self.index] * 1000
        def release(self): self.closed = True

    capture = Capture()
    saved = {}
    def save(path, frame):
        saved[Path(path).name] = frame
        return True
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(
        VideoCapture=lambda _: capture, CAP_PROP_POS_MSEC=0, imwrite=save))
    return capture, saved


def test_frame_selection_never_uses_future_frame(monkeypatch, tmp_path):
    capture, saved = fake_video(monkeypatch, [0, .1, .2, .3])
    frames = derive.extract_previous_frames("video", {
        "early": 9.9, "equal": 10, "one": 10.01, "two": 10.02,
        "boundary": 10.1, "later": 10.21, "end": 11}, 10, tmp_path)
    assert frames["early"]["status"] == "missing_prestate"
    assert frames["equal"]["path"] is None
    assert saved == {"one.png": 0, "two.png": 0, "boundary.png": 0,
                     "later.png": 2, "end.png": 3}
    assert capture.closed


def test_invalid_pts_fail_explicitly(monkeypatch, tmp_path):
    capture, _ = fake_video(monkeypatch, [0, 0])
    with pytest.raises(ValueError, match="PTS"):
        derive.extract_previous_frames("video", {"a": 1}, 0, tmp_path)
    assert capture.closed


def test_no_frame_is_not_claimed_as_success(monkeypatch, tmp_path):
    fake_video(monkeypatch, [])
    frames = derive.extract_previous_frames("video", {"a": 1}, 0, tmp_path)
    assert frames["a"] == {"status": "missing_prestate", "path": None}


def test_action_references_preserve_nested_official_data():
    actions = [{"id": 0, "action": "long_press", "start_time": 1, "end_time": 2,
                "children": [{"action": "drag"}]}]
    events = [{"event_idx": 0, "action": "press", "time_stamp": 1},
              {"event_idx": 1, "action": "release", "time_stamp": 2},
              {"event_idx": 2, "action": "move", "time_stamp": 3},
              {"event_idx": 3, "action": "press", "time_stamp": 4}]
    rows, missing = derive.action_rows(actions, events)
    assert rows[0]["raw_event_indices_in_interval"] == [0, 1]
    assert "official_action" not in rows[0]
    assert "action" not in rows[0]
    assert actions[0]["children"] == [{"action": "drag"}]
    assert missing == [3]


def test_report_escapes_recorded_text(tmp_path):
    row = {"id": 7, "action": "type", "description": "<script>alert(1)</script>",
           "start_time": 1, "end_time": 2, "children": [{"text": "<img onerror=x>"}]}
    (tmp_path / "reduced_events_vis.jsonl").write_text(json.dumps(row) + '\nnull\n', encoding="utf-8")
    derive.render_report(tmp_path, {"warnings": [], "action_frames": {"7": {"before": "a", "after": "b"}},
                                  "frames": {"a": {"path": None}, "b": {"path": "frames/0001.png"}}})
    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
    assert "Content-Security-Policy" in page
    assert 'href="actions.json"' not in page
    assert 'href="reduced_events_vis.jsonl"' in page
    assert "official_action" not in page


def test_only_pure_official_methods_used(tmp_path):
    calls = []
    class Action:
        def set_id(self, value): calls.append(("id", value))
    class Reducer:
        def __init__(self, path, dims, configs):
            assert not any(configs.values())
            self.reduced_actions = [Action()]
        def compress(self, events):
            events[0]["modified"] = True
            calls.append("compress")
        def reduce_all(self): calls.append("reduce_all")
        def transform(self): calls.append("transform")
        def finish(self): calls.append("finish")
    original = [{"action": "press"}]
    derive.reduce_events(Reducer, tmp_path, {"screen_width": 10, "screen_height": 10}, original)
    assert original == [{"action": "press"}]
    assert calls == ["compress", "reduce_all", "transform", "finish", ("id", 0)]
