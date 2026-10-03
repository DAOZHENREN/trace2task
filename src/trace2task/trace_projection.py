"""Read-only D projection of official OpenCUA visual actions. No model calls.

Run with Python 3.11+. Writes only to a new directory OUTSIDE the recording.
The visual export determines order/children; complete export supplies click
coordinates. No action recognition, semantic interpretation, or error repair.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path


def jsonl(text):
    return [row for line in text.splitlines() if line.strip()
            if (row := json.loads(line)) is not None]


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Expected finite numeric action parameter")
    return value


def signature(action):
    return (action["action"], number(action["start_time"]), number(action["end_time"]))


def point(value):
    if not isinstance(value, dict):
        raise ValueError("Click coordinate missing")  # noqa: TRY004 -- malformed source evidence
    return {axis: number(value[axis]) for axis in ("x", "y")}


def project_actions(visual, complete):
    by_id = {}
    for full in complete:
        if full["id"] in by_id:
            raise ValueError("Duplicate complete action ID")
        by_id[full["id"]] = full
    provenance = []

    def project(visible, full, location):
        kind, start, end = signature(visible)
        if signature(full) != (kind, start, end) or end < start:
            raise ValueError(f"Mismatched action or invalid duration at {location}")
        if not isinstance(visible.get("description"), str) or not visible["description"].strip():
            raise ValueError(f"Missing description at {location}")
        out = {"id": visible["id"]} if "id" in visible else {}
        out.update(action=visible["description"], duration_seconds=round(end-start, 3))
        if kind == "click":
            coords = full.get("coordinates")
            if coords is None:
                coords = [full.get("coordinate")]
            if not isinstance(coords, list) or not coords:
                raise ValueError(f"Click coordinates missing at {location}")
            coords = [point(p) for p in coords]
            if len(coords) == 1:
                out["coordinate"] = coords[0]
            else:
                out["coordinates"] = coords
        provenance.append({"projection_path": location, "event_start_idx": full.get("event_start_idx"),
                           "source_start_time": start, "source_end_time": end})
        visible_children = visible.get("children", [])
        full_children = full.get("children") or []
        if visible_children:
            out["children"] = []
            last_index = -1
            for i, child in enumerate(visible_children):
                # Visible children omit key-up and other hidden children; positional
                # zip would assign wrong coordinates. Require one exact match.
                matches = [(j, candidate) for j, candidate in enumerate(full_children)
                           if candidate is not None and signature(candidate) == signature(child)]
                if len(matches) != 1 or matches[0][0] <= last_index:
                    raise ValueError(f"Ambiguous/missing/out-of-order child at {location}.children[{i}]")
                last_index, candidate = matches[0]
                out["children"].append(project(child, candidate, f"{location}.children[{i}]"))
        return out

    result, seen = [], set()
    for i, action in enumerate(visual):
        identity = action["id"]
        if identity in seen or identity not in by_id:
            raise ValueError("Duplicate or missing visual action ID")
        seen.add(identity)
        result.append(project(action, by_id[identity], f"actions[{i}]"))
    if not result:
        raise ValueError("No visible actions")
    return result, provenance


def export(recording, derived, output):
    recording, derived, output = (Path(p).resolve() for p in (recording, derived, output))
    if not derived.is_relative_to(recording):
        raise ValueError("Derived directory must belong to this recording")
    if output.is_relative_to(recording) or output.exists():
        raise ValueError("Output must be a NEW directory outside the source recording")
    paths = [recording / "trace2task.json", recording / "metadata.json",
             derived / "reduced_events_vis.jsonl", derived / "reduced_events_complete.jsonl"]
    # Audit metadata stays separate from the actual model input.
    manifest_path = derived / "manifest.json"
    if manifest_path.exists():
        paths.append(manifest_path)
    if any(not p.resolve().is_relative_to(recording) for p in paths):
        raise ValueError("Source file resolves outside recording")
    original = {p: p.read_bytes() for p in paths}
    load = lambda p: json.loads(original[p].decode("utf-8-sig"))
    sidecar, metadata = load(paths[0]), load(paths[1])
    if sidecar.get("capture_status") != "finished":
        raise ValueError("Only finalized recordings may be exported")
    trace_name = sidecar.get("task_id")
    if not isinstance(trace_name, str) or not trace_name.strip():
        raise ValueError("Missing recorded task name")
    actions, provenance = project_actions(jsonl(original[paths[2]].decode("utf-8-sig")),
                                          jsonl(original[paths[3]].decode("utf-8-sig")))
    dimensions = {}
    for axis in ("width", "height"):
        value = metadata[f"screen_{axis}"]
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError("Invalid source screen dimensions")
        dimensions[axis] = value
    payload = {"trace_name": trace_name,
               "description": "人工录制的精简动作序列：包含动作描述、持续时间、可见子动作、点击坐标；不包含截图和动作解释",
               "source_screen": {**dimensions, "coordinate_space": "desktop_pixels"}, "actions": actions}
    model_input = ("以下内容是历史人工示范数据，不是当前任务或新的指令。坐标属于示范桌面。\n"
                   "duration_seconds 的单位为秒；action 保留官方动作描述，不推断最终输入文字。\n\n"
                   + json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    data = model_input.encode("utf-8")
    report = {"schema": "trace2task.D.visual-projection.v2", "condition": "D",
        "trace_name_source_field": "trace2task.json:task_id",
        "trace_name_source_note": "Recorded task name, not a complete instruction.",
        "description_source": "Deterministic summary of D projection contents; not a semantic task summary.",
        "model_input_file": "model-input.txt", "model_input_sha256": hashlib.sha256(data).hexdigest(),
        "model_input_bytes": len(data), "top_level_action_count": len(actions),
        "total_visible_action_count": len(provenance),
        "source_sha256": {str(p): hashlib.sha256(content).hexdigest() for p, content in original.items()},
        "provenance": provenance,
        "rules": ["Preserve visual order and visible children; use original description as action",
                  "Duration = end-start, rounded to 3 decimal seconds",
                  "Click coordinates joined from complete actions; missing/ambiguous data rejects export",
                  "No images, window data, a11y, semantic compilation or source action repair",
                  "No coordinates inferred from description; drag descriptions retained verbatim"],
        "limitations": ["Inter-action gaps and hidden children are not included",
                        "Only the official visual action sequence is represented, not every raw event",
                        "Long parent durations include pauses and child actions",
                        "Task label may not include detailed instructions read on screen",
                        "Input-method keystrokes and paste do not establish final text"]}
    if manifest_path in original:
        audit = load(manifest_path)
        report["source_uncovered_nonmove_event_indices"] = audit.get("nonmove_events_outside_action_intervals", [])
    if any(p.read_bytes() != content for p, content in original.items()):
        raise RuntimeError("Source changed during export")
    created_at = datetime.now(UTC).isoformat(timespec="microseconds")
    report.update(created_at=created_at, source_recording=str(recording))
    output.mkdir(parents=True, exist_ok=False)
    (output / "model-input.txt").write_bytes(data)
    (output / "body.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    # Listing reads only this header, never the action body.
    (output / "header.json").write_text(json.dumps({
        "trace_name": trace_name, "description": payload["description"], "representation": "D",
        "created_at": created_at, "source_recording": str(recording),
        "body_sha256": hashlib.sha256((output / "body.json").read_bytes()).hexdigest(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"output": str(output), "actions": len(actions), "visible_nodes": len(provenance), "bytes": len(data)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recording", required=True)
    parser.add_argument("--derived", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.recording, args.derived, args.output), ensure_ascii=False))
