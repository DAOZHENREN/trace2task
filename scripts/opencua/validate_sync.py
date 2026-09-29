"""Measure OBS/event-clock alignment using a local, timer-driven visual marker.

No input injection, model calls, browser extensions or production calibration.
The measured residual includes Tk/compositor/capture delay; it is NOT pure clock
offset. A post-marker prestate is a direct causal violation of the mapping.
"""
from __future__ import annotations

import argparse
import bisect
import ctypes
import json
import math
import statistics
import time
import tkinter as tk
import uuid
from pathlib import Path

from worker import OBS, save

CELL = 28
BITS = 12


def decode_marker(frame, roi):
    x, y = roi
    samples = []
    for cell in range(BITS + 2):
        patch = frame[y + 22:y + 34, x + cell * CELL + 8:x + cell * CELL + 20]
        if patch.size == 0:
            return None
        value = float(patch.mean())
        if 70 <= value <= 185:
            return None
        samples.append(value > 185)
    if samples[:2] != [True, False]:
        return None
    return sum(int(bit) << index for index, bit in enumerate(samples[2:]))


def summarize(values):
    if not values:
        return None
    return {"min_ms": round(min(values), 3), "median_ms": round(statistics.median(values), 3),
            "max_ms": round(max(values), 3), "range_ms": round(max(values) - min(values), 3)}


def analyze(video, events, origin, roi, output, *, require_native_clock=False):
    import cv2
    if require_native_clock:
        from frame_clock import ExactVideoReader
        reader = ExactVideoReader
    else:
        reader = cv2.VideoCapture
    capture = reader(str(video))
    frames, first_seen = [], {}
    try:
        if not capture.isOpened():
            raise RuntimeError("Video cannot be opened")
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            pts = capture.get(cv2.CAP_PROP_POS_MSEC) / 1000
            if not math.isfinite(pts) or frames and pts <= frames[-1]["pts"]:
                raise RuntimeError("Invalid/non-increasing video PTS")
            marker = decode_marker(frame, roi)
            row = {"index": len(frames), "pts": pts, "marker": marker}
            frames.append(row)
            if marker is not None and marker not in first_seen:
                first_seen[marker] = row
    finally:
        capture.release()
    pts_list = [frame["pts"] for frame in frames]
    results = []
    for event in events:
        seen = first_seen.get(event["marker"])
        index = bisect.bisect_left(pts_list, event["before_update"] - origin) - 1
        selected = frames[index] if index >= 0 else None
        residual = (origin + seen["pts"] - event["before_update"]) * 1000 if seen else None
        # Marker IDs strictly increase; later or same ID cannot be a pre-update frame.
        poststate = (selected["marker"] >= event["marker"]
                     if selected and selected["marker"] is not None else None)
        results.append({**event, "first_visible_frame": seen, "current_rule_prestate": selected,
                        "residual_ms": residual, "poststate_in_prestate": poststate,
                        "tk_update_ms": (event["after_update"] - event["before_update"]) * 1000})
    valid = [r for r in results if r["residual_ms"] is not None]
    # Compare first/last third descriptively; not a statistical significance claim.
    count = max(1, len(valid) // 3)
    report = {"origin": origin, "frame_count": len(frames), "marker_events": len(events),
              "matched_events": len(valid),
              "unreadable_frames": sum(f["marker"] is None for f in frames),
              "residual": summarize([r["residual_ms"] for r in valid]),
              "early_residual": summarize([r["residual_ms"] for r in valid[:count]]),
              "late_residual": summarize([r["residual_ms"] for r in valid[-count:]]),
              "poststate_prestate_count": sum(r["poststate_in_prestate"] is True for r in results),
              "unverifiable_prestate_count": sum(r["poststate_in_prestate"] is None for r in results),
              "results": results}
    save(output / "frame-timeline.json", frames)
    save(output / "analysis.json", report)
    clock_path = output / "obs-frame-clock.jsonl"
    if clock_path.exists():
        from frame_clock import compare_marker_times, match_video_frames
        clock_rows, clock_summary = match_video_frames(clock_path, pts_list)
        comparisons = compare_marker_times(clock_rows, frames, events)
        clock_summary["first_frame_cts_ns"] = clock_rows[0]["cts_ns"]
        clock_summary["ack_minus_first_cts_ms"] = (origin - clock_rows[0]["cts_ns"] / 1e9) * 1000
        clock_summary["metrics"] = {field: {
            "residual": summarize([r["residual_ms"] for r in rows if r["residual_ms"] is not None]),
            "poststate_prestate_count": sum(r["poststate_in_prestate"] is True for r in rows),
            "unverifiable_prestate_count": sum(r["poststate_in_prestate"] is None for r in rows),
        } for field, rows in comparisons.items()}
        save(output / "native-clock-analysis.json", {**clock_summary, "comparisons": comparisons})
        report["native_clock"] = clock_summary
    elif require_native_clock:
        raise RuntimeError("Requested native OBS frame clock is missing")
    # Small visual evidence for first/middle/last sample, no unrelated UI interaction.
    capture = reader(str(video))
    try:
        for idx in sorted({0, len(valid) // 2, len(valid) - 1}):
            if idx < 0 or not valid:
                continue
            capture.set(cv2.CAP_PROP_POS_FRAMES, valid[idx]["first_visible_frame"]["index"])
            ok, frame = capture.read()
            if ok:
                cv2.imwrite(str(output / f"sample-{idx:02d}.png"), frame)
    finally:
        capture.release()
    return report


def run(args):
    ctypes.windll.user32.SetProcessDPIAware()
    output = Path(args.output).resolve() / str(uuid.uuid4())
    output.mkdir(parents=True)
    root = tk.Tk()
    root.title("Trace2Task 同步验证 · 不操作其他应用")
    root.geometry("560x230+60+70")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    label = tk.Label(root, text="正在准备 OBS，同步验证约需 1–2 分钟。", font=("Microsoft YaHei", 12))
    label.pack(pady=10)
    canvas = tk.Canvas(root, width=CELL * (BITS + 2), height=56, highlightthickness=0)
    canvas.pack(pady=5)
    cells = [canvas.create_rectangle(i * CELL, 0, (i + 1) * CELL, 56, width=0)
             for i in range(BITS + 2)]
    cancelled = [False]
    def stop():
        cancelled[0] = True
    root.protocol("WM_DELETE_WINDOW", stop)
    root.bind("<Escape>", lambda _: stop())
    tk.Button(root, text="停止验证", command=stop).pack(pady=8)
    def paint(marker):
        for cell, bit in zip(cells, [1, 0] + [(marker >> i) & 1 for i in range(BITS)]):
            canvas.itemconfig(cell, fill="#eeeeee" if bit else "#111111")
        root.update_idletasks()
    def pump(seconds):
        until = time.perf_counter() + seconds
        while time.perf_counter() < until:
            root.update()
            if cancelled[0]:
                raise RuntimeError("Sync validation cancelled by user")
            time.sleep(.005)
    paint(0)
    root.update()
    roi = [canvas.winfo_rootx(), canvas.winfo_rooty()]
    reports = []
    try:
        for round_index in range(args.rounds):
            folder = output / f"round-{round_index + 1:02d}"
            folder.mkdir()
            paint(0)
            label.config(text=f"第 {round_index + 1}/{args.rounds} 轮：正在启动 OBS")
            root.update()
            obs = OBS(Path(args.obs), folder, frame_clock=args.frame_clock)
            events, clock = [], None
            try:
                clock = obs.start()
                # Exactly the same placement as the production MetadataManager call.
                origin = time.perf_counter()
                label.config(text=f"第 {round_index + 1}/{args.rounds} 轮：色块验证中，请勿遮挡窗口")
                pump(1)
                for marker in range(1, args.markers + 1):
                    if [canvas.winfo_rootx(), canvas.winfo_rooty()] != roi:
                        raise RuntimeError("Test window moved; alignment run rejected")
                    before = time.perf_counter()
                    paint(marker)
                    after = time.perf_counter()
                    events.append({"marker": marker, "before_update": before, "after_update": after,
                                   "kind": "timer_visual_marker_not_keyboard_event"})
                    pump([.413, .587, .731, .479, .653][(marker - 1) % 5])
                pump(.8)
            finally:
                video = obs.close()
                save(folder / "markers.json", events)
                save(folder / "clock.json", {"clock": clock, "origin": locals().get("origin"),
                                             "obs_events": obs.record_state_events, "roi": roi})
            if not video:
                raise RuntimeError("OBS did not finalize video")
            label.config(text=f"第 {round_index + 1}/{args.rounds} 轮：逐帧分析中")
            root.update()
            report = analyze(video, events, origin, roi, folder, require_native_clock=args.frame_clock)
            reports.append({"round": round_index + 1, "directory": str(folder),
                            **{k: v for k, v in report.items() if k != "results"}})
            print(json.dumps(reports[-1], ensure_ascii=False), flush=True)
        summary = {"status": "completed", "method": "timer marker end-to-end OBS alignment probe",
                   "production_calibration_changed": False, "rounds": reports,
                   "limitations": ["Residual includes render/compositor/capture delay, not pure clock offset.",
                                   "Timer markers are not real keyboard/mouse hook events.",
                                   "Only current primary display, 30 FPS and current load were tested."]}
        save(output / "summary.json", summary)
        print("SYNC_REPORT=" + str(output / "summary.json"), flush=True)
    except Exception as error:
        save(output / "failure.json", {"error": str(error), "completed_rounds": reports})
        raise
    finally:
        root.destroy()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--obs", default="D:/Apps/Trace2Task-OBS")
    parser.add_argument("--output", required=True)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--markers", type=int, default=24)
    parser.add_argument("--frame-clock", action="store_true", help="Capture native OBS CTS/PTS sidecar")
    args = parser.parse_args()
    if not 1 <= args.rounds <= 5 or not 3 <= args.markers <= 100:
        parser.error("rounds must be 1..5, markers 3..100")
    run(args)
