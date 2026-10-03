"""Local-only evidence export. Official raw archives are never rewritten.

Reuse the pinned Reducer's four pure reduction stages, NOT reduce_pipeline:
its target matching mutates HTML and can call a remote AI service.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import html
import itertools
import json
import math
import os
import sys
import uuid
from pathlib import Path


def read_jsonl(path):
    with Path(path).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def fingerprint(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def reduce_events(reducer_type, recording, metadata, events):
    reducer = reducer_type(str(recording),
        {"width": metadata["screen_width"], "height": metadata["screen_height"]},
        {"generate_window_a11y": False, "generate_element_a11y": False})
    # Official compression mutates its input dictionaries; use an in-memory copy.
    reducer.compress(copy.deepcopy(events))
    reducer.reduce_all()
    reducer.transform()
    reducer.finish()
    for index, action in enumerate(reducer.reduced_actions):
        action.set_id(index)
    return reducer


def action_rows(actions, events):
    """Keep official nesting; interval references are evidence, not inferred causality."""
    rows = []
    covered = set()
    for action in actions:
        start = float(action["start_time"])
        end = action.get("end_time")
        end = float(end) if end is not None else start
        if not math.isfinite(start) or not math.isfinite(end) or end < start:
            raise ValueError("Invalid official action timestamps")
        indices = [e["event_idx"] for e in events if start <= e["time_stamp"] <= end]
        covered.update(indices)
        rows.append({"id": action["id"], "start_time": start,
                     "end_time": end, "raw_event_indices_in_interval": indices})
    uncovered = [e["event_idx"] for e in events
                 if e["event_idx"] not in covered and e["action"] != "move"]
    return rows, uncovered


def extract_previous_frames(video, timestamps, origin, output):
    """Decode once; select greatest decoded PTS strictly before each request.

    Same clock subtraction as upstream, but full PNGs instead of annotated clips
    or click-centred crops. The OBS origin is approximate, not frame-exact sync.
    """
    import cv2
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise ValueError("Cannot decode recording video")
    results = {}
    previous = None
    previous_pts = None
    last_pts = -1.0
    frame_index = -1
    exhausted = False
    lookahead = None
    try:
        for key, stamp in sorted(timestamps.items(), key=lambda pair: pair[1]):
            target = stamp - origin
            # One-frame lookahead avoids seeking to a frame after the input.
            while not exhausted:
                if lookahead is not None:
                    if lookahead[1] >= target:
                        break
                    previous, previous_pts, previous_index = lookahead
                    lookahead = None
                ok, frame = cap.read()
                if not ok:
                    exhausted = True
                    break
                frame_index += 1
                pts = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
                if not math.isfinite(pts) or (frame_index > 0 and pts <= last_pts):
                    raise ValueError("Video PTS are not strictly increasing; cannot align safely")
                last_pts = pts
                lookahead = (frame, pts, frame_index)
            if previous is None:
                results[key] = {"status": "missing_prestate", "path": None}
            else:
                name = f"frames/{key}.png"
                if not cv2.imwrite(str(output / name), previous):
                    raise OSError("Cannot save evidence screenshot")
                results[key] = {"status": "last_available" if exhausted else "available",
                                "path": name, "frame_index": previous_index,
                                "video_pts_seconds": previous_pts}
    finally:
        cap.release()
    return results


def render_report(output, manifest):
    # Keep official visual ordering; join details by ID, never list position.
    actions = [a for a in read_jsonl(output / "reduced_events_vis.jsonl") if a is not None]
    complete_path = output / "reduced_events_complete.jsonl"
    complete = {}
    if complete_path.exists():
        for item in read_jsonl(complete_path):
            if item is None:
                continue
            if item["id"] in complete:
                raise ValueError("Duplicate official action ID")
            complete[item["id"]] = item
    esc = lambda value: html.escape(str(value), quote=True)
    cards = []
    for action in actions:
        association = manifest["action_frames"][str(action["id"])]
        images = []
        for field, label in (("before", "动作起点参考（近似对齐）"), ("after", "后续边界参考（不是成功判定）")):
            frame = manifest["frames"][association[field]]
            picture = (f'<a href="{esc(frame["path"])}"><img loading="lazy" src="{esc(frame["path"])}"></a>'
                       if frame.get("path") else '<p>没有时间边界之前的可用参考帧。</p>')
            images.append(f'<figure><figcaption>{label}</figcaption>{picture}</figure>')
        duration = (action.get("end_time") or action["start_time"]) - action["start_time"]
        click_details = ""
        if action["action"] == "click":
            detail = complete.get(action["id"], {})
            if detail.get("action") != "click":
                detail = {}
            points = detail.get("coordinates") or [detail.get("coordinate")]
            positions = []
            for point in points:
                if isinstance(point, dict) and all(
                    isinstance(point.get(axis), (int, float)) and not isinstance(point[axis], bool)
                    and math.isfinite(point[axis]) for axis in ("x", "y")
                ):
                    positions.append(f'x={point["x"]}, y={point["y"]}')
                else:
                    positions.append("坐标未提供或无效")
            button = {"left": "左键", "right": "右键", "middle": "中键"}.get(
                detail.get("button"), detail.get("button") or "未提供")
            click_details = (f'<p>按钮：{esc(button)} · 点击坐标：{esc(" → ".join(positions))}'
                             '（原始桌面像素坐标）<br><small>来源：官方完整动作，'
                             f'ID {esc(action["id"])}；多次点击按记录顺序列出。</small></p>')
        cards.append(f'<article><h2>{action["id"] + 1}. {esc(action.get("description") or action["action"])}</h2>'
                     f'<p>动作：{esc(action["action"])} · 持续时间：{duration:.3f} 秒</p>'
                     f'{click_details}'
                     f'<div class="images">{"".join(images)}</div><details><summary>官方展示动作（含可见子动作）</summary>'
                     f'<pre>{esc(json.dumps(action, ensure_ascii=False, indent=2))}</pre></details></article>')
    warnings = ''.join(f'<li>{esc(w)}</li>' for w in manifest["warnings"])
    page = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src \'self\' file:; style-src \'unsafe-inline\'">'
            '<title>OpenCUA 动作与截图</title><style>body{max-width:1300px;margin:32px auto;padding:0 20px;'
            'font:16px system-ui;background:#f3f6f5;color:#18352c}article{background:white;padding:20px;margin:20px 0;'
            'border-radius:12px} .images{display:flex;gap:16px}figure{margin:0;width:50%}img{width:100%}'
            'pre{white-space:pre-wrap;overflow-wrap:anywhere}figcaption{margin:8px 0}</style>'
            f'<h1>动作与对应截图 · {len(actions)} 个动作组</h1><p>官方 Reducer 整理，Trace2Task 抽帧与展示。'
            '原始记录未改写；子动作保留在完整动作中；未调用模型、未执行动作。</p><ul>'
            f'{warnings}</ul><p><a href="reduced_events_vis.jsonl">官方展示动作</a> · '
            '<a href="reduced_events_complete.jsonl">完整官方细节</a> · '
            '<a href="manifest.json">来源、哈希和适配说明</a></p>' + ''.join(cards) + '</html>')
    (output / "index.html").write_text(page, encoding="utf-8")


def derive(recording, upstream_root):
    from worker import REVISION, upstream
    recording = Path(recording).resolve()
    metadata = json.loads((recording / "metadata.json").read_text(encoding="utf-8"))
    if not math.isfinite(float(metadata["video_start_timestamp"])):
        raise ValueError("Invalid video clock origin")
    sidecar_path = recording / "trace2task.json"
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    if sidecar.get("capture_status") != "finished":
        raise ValueError("Only finalized recordings can be processed")
    output = recording / "derived" / str(uuid.uuid4())
    (output / "frames").mkdir(parents=True)
    # Upstream logger writes into cwd; never into the raw recording.
    os.chdir(output)
    upstream(upstream_root)
    from core.action_reduction import Reducer
    # Defense in depth: even unexpected upstream changes cannot upload this archive.
    def local_only(event, args):
        if event in {"socket.connect", "socket.getaddrinfo"}:
            raise RuntimeError("Network access disabled during local evidence derivation")
    sys.addaudithook(local_only)
    raw_paths = [p for p in recording.iterdir() if p.is_file() and p.name != "trace2task.json"]
    hashes = {p.name: fingerprint(p) for p in raw_paths}
    events = read_jsonl(recording / "events.jsonl")
    if not events:
        raise ValueError("No recorded input events")
    if any(not math.isfinite(float(e["time_stamp"])) for e in events):
        raise ValueError("Invalid event timestamp")
    if any(a["time_stamp"] > b["time_stamp"] for a, b in itertools.pairwise(events)):
        raise ValueError("Raw timestamps out of order; review before conversion")
    reducer = reduce_events(Reducer, recording, metadata, events)
    reducer.complete_dump(str(output))
    reducer.vis_dump(str(output))
    complete_actions = read_jsonl(output / "reduced_events_complete.jsonl")
    actions = [a for a in read_jsonl(output / "reduced_events_vis.jsonl") if a is not None]
    if not actions:
        raise ValueError("Official Reducer produced no complete actions; raw input retained")
    rows, _ = action_rows(actions, events)
    _, uncovered = action_rows(complete_actions, events)
    warnings = ["下一动作边界的截图是效果参考，不保证动画结束或任务成功。",
                "官方动作保留原始键名；中文输入法和粘贴不能仅靠键码还原最终文本。",
                "此官方版本可能把开头双击保留为两次单击、把静止长按标为 click；请结合持续时间与原始事件判断，未擅自改写官方标签。"]
    if uncovered:
        warnings.append(f"{len(uncovered)} 条非移动事件未落入完整动作区间，详见 manifest；没有静默补成成功动作。")
    # Deduplicate equal timestamps so a cached later frame never serves an earlier request.
    stamps = sorted({row["start_time"] for row in rows})
    end = sidecar.get("stop_perf_counter")
    if end is None:
        timings = metadata.get("obs_record_state_timings", {})
        end = next(iter(timings.get("OBS_WEBSOCKET_OUTPUT_STOPPING", [])), None)
        warnings.append("旧录制没有 F8 的单调时钟时间，末图使用 OBS 停止时间作为近似结束参考。")
    if end is None or not math.isfinite(float(end)) or end <= stamps[-1]:
        raise ValueError("Missing or invalid recording end timestamp")
    stamps.append(float(end))
    requests = {f"{i:04d}": stamp for i, stamp in enumerate(stamps)}
    clock_config = sidecar.get("frame_clock", {})
    clock_path = recording / "obs-frame-clock.jsonl"
    timing_summary = None
    if clock_config.get("used_for_frame_selection") or clock_path.exists():
        from frame_clock import extract_cts_frames
        # If a new recording's sidecar is missing/invalid, extraction fails. Never
        # silently substitute the old start-ack approximation for advertised CTS.
        frames, timing_summary = extract_cts_frames(recording / "video.mp4", clock_path, requests, output)
        alignment = "obs_cts_approximate"
        warnings.insert(0, "使用 OBS CTS 近似对齐。CTS 不是桌面图像的真实采集时刻，参考图可能已包含本次操作效果；不保证严格前态。")
    else:
        frames = extract_previous_frames(recording / "video.mp4", requests,
                                         float(metadata["video_start_timestamp"]), output)
        alignment = "legacy_start_timestamp_approximate"
        warnings.insert(0, "旧录制没有 CTS 数据，使用原 video_start_timestamp 近似对齐；不是 CTS 对齐，不保证严格前态。")
    by_stamp = {stamp: key for key, stamp in requests.items()}
    action_frames = {}
    for i, row in enumerate(rows):
        action_frames[str(row["id"])] = {
            "before": by_stamp[row["start_time"]],
            "after": by_stamp[rows[i + 1]["start_time"] if i + 1 < len(rows) else float(end)]}
    if any(f["status"] == "missing_prestate" for f in frames.values()):
        warnings.append("部分动作没有更早的视频帧，已明确标记缺图。")
    if any(a.get("exception") or not a.get("complete") for a in complete_actions):
        warnings.append("官方 Reducer 标记了异常或不完整动作，需要人工核对原始事件。")
    if any(fingerprint(p) != hashes[p.name] for p in raw_paths):
        raise RuntimeError("Raw archive changed while processing; export not accepted")
    manifest = {"schema": "trace2task.opencua-evidence.v2", "status": "completed",
                "frames": frames, "action_frames": action_frames,
                "upstream_revision": REVISION, "raw_sha256": hashes,
                "action_count": len(rows), "raw_event_count": len(events),
                "nonmove_events_outside_action_intervals": uncovered,
                "warnings": warnings, "alignment": alignment, "timing_validation": timing_summary,
                "strict_prestate_guaranteed": False,
                "official_methods": ["compress", "reduce_all", "transform", "finish", "complete_dump", "vis_dump"],
                "adaptations": ["isolated derived directory", "local-only stages; no destructive/AI target matching",
                                "full-frame PNG before action and at next boundary", "HTML review and raw interval references"]}
    write_json(output / "manifest.json", manifest)
    render_report(output, manifest)
    result = {"status": "completed", "action_count": len(rows),
              "directory": str(output), "review_path": str(output / "index.html"),
              "manifest_path": str(output / "manifest.json")}
    sidecar["derivation"] = result
    from worker import save
    save(sidecar_path, sidecar)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--recording", required=True)
    parser.add_argument("--upstream", required=True)
    args = parser.parse_args()
    print(json.dumps(derive(args.recording, args.upstream), ensure_ascii=False))
