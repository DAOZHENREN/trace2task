import copy
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("export_d", Path(__file__).parents[1] / "scripts/opencua/export_d.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def node(kind="click", start=1, end=1.435):
    return {"action": kind, "description": "Single left Click" if kind == "click" else "ctrl + clicks",
            "start_time": start, "end_time": end, "depth": 0, "target": {"mark": False}, "axtree": None}


def fixture():
    visual = [{"id": 21, **node("long_press", 0, 4), "children": [node()]}]
    full = copy.deepcopy(visual)
    full[0]["children"].insert(0, {**node("release", 0, 0), "vis": False})
    full[0]["children"][1]["coordinate"] = {"x": 2302, "y": 1231}
    return visual, full


def test_nested_projection_skips_hidden_children_and_preserves_inputs():
    visual, full = fixture()
    before = copy.deepcopy((visual, full))
    rows, refs = module.project_actions(visual, full)
    assert rows == [{"id": 21, "action": "ctrl + clicks", "duration_seconds": 4,
                     "children": [{"action": "Single left Click", "duration_seconds": .435,
                                   "coordinate": {"x": 2302, "y": 1231}}]}]
    assert len(refs) == 2
    assert (visual, full) == before


@pytest.mark.parametrize("problem", ["missing", "ambiguous", "invalid_duration", "invalid_coordinate", "duplicate_id", "empty_description"])
def test_bad_evidence_rejected(problem):
    visual, full = fixture()
    if problem == "missing": full[0]["children"].pop()
    if problem == "ambiguous": full[0]["children"].append(copy.deepcopy(full[0]["children"][1]))
    if problem == "invalid_duration": visual[0]["end_time"] = -1
    if problem == "invalid_coordinate": full[0]["children"][1]["coordinate"]["x"] = float("nan")
    if problem == "duplicate_id": full.append(copy.deepcopy(full[0]))
    if problem == "empty_description": visual[0]["children"][0]["description"] = "  "
    with pytest.raises(ValueError):
        module.project_actions(visual, full)


def test_multiclick_all_coordinates_and_typing_verbatim():
    visual = [{"id": 0, **node()}, {"id": 1, **node("type", 2, 3)}]
    visual[1]["description"] = "jiaofu$space$"
    full = copy.deepcopy(visual)
    full[0]["coordinates"] = [{"x": 1, "y": 2}, {"x": 3, "y": 4}]
    result, _ = module.project_actions(visual, full)
    assert result[0]["coordinates"] == full[0]["coordinates"]
    assert result[1]["action"] == "jiaofu$space$"
    assert all("description" not in row for row in result)
    assert "text" not in result[1]


def test_export_is_read_only_deterministic_and_separates_audit(tmp_path):
    recording = tmp_path / "recording"
    derived = recording / "derived"
    derived.mkdir(parents=True)
    visual, full = fixture()
    for name, value in [("trace2task.json", {"capture_status": "finished", "task_id": "Original task"}),
                        ("metadata.json", {"screen_width": 2560, "screen_height": 1600})]:
        (recording / name).write_text(json.dumps(value), encoding="utf-8")
    for name, value in [("vis", visual), ("complete", full)]:
        (derived / f"reduced_events_{name}.jsonl").write_text("\n".join(json.dumps(row) for row in value)+"\nnull\n", encoding="utf-8")
    original = {p: p.read_bytes() for p in recording.rglob("*") if p.is_file()}
    module.export(recording, derived, tmp_path / "out1")
    module.export(recording, derived, tmp_path / "out2")
    content = (tmp_path / "out1/model-input.txt").read_text(encoding="utf-8")
    assert content == (tmp_path / "out2/model-input.txt").read_text(encoding="utf-8")
    header = json.loads((tmp_path / 'out1/header.json').read_text(encoding='utf-8'))
    report = json.loads((tmp_path / 'out1/manifest.json').read_text(encoding='utf-8'))
    assert header['created_at'] == report['created_at']
    assert header['source_recording'] == str(recording.resolve())
    assert 'created_at' not in content and 'source_recording' not in content
    payload = json.loads(content.split("\n\n", 1)[1])
    assert payload["trace_name"] == "Original task"
    assert "name" not in payload
    assert payload["description"] == "人工录制的精简动作序列：包含动作描述、持续时间、可见子动作、点击坐标；不包含截图和动作解释"
    assert content.count('"description":') == 1
    for forbidden in ("source_instruction", "start_time", "end_time", "axtree", "depth", "target", "event_start_idx", "source_sha256"):
        assert forbidden not in content
    assert all(p.read_bytes() == data for p, data in original.items())
    with pytest.raises(ValueError, match="NEW"):
        module.export(recording, derived, tmp_path / "out1")
    with pytest.raises(ValueError, match="outside"):
        module.export(recording, derived, recording / "new")
