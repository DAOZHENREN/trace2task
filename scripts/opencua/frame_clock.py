"""Validate OBS timing evidence and select CTS-aligned approximate reference frames."""
import bisect
import itertools
import json
import math
from pathlib import Path


class ExactVideoReader:
    """Reader using decoded frame PTS, not OpenCV position guesses."""
    def __init__(self, path):
        import av
        self.path = str(path)
        self.container = av.open(self.path)
        self.frames = self.container.decode(video=0)
        self.pts = None

    def isOpened(self):
        return True

    def read(self):
        try:
            frame = next(self.frames)
        except StopIteration:
            return False, None
        if frame.pts is None or frame.time_base is None:
            raise ValueError("Decoded frame has no PTS")
        self.pts = float(frame.pts * frame.time_base)
        return True, frame.to_ndarray(format="bgr24")

    def get(self, _):
        return self.pts * 1000

    def set(self, _, index):
        import av
        self.container.close()
        self.container = av.open(self.path)
        self.frames = self.container.decode(video=0)
        for _ in range(int(index)):
            next(self.frames)

    def release(self):
        self.container.close()


def match_video_frames(path, video_pts):
    lines = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line]
    if len(lines) < 3 or lines[0].get("schema") != "trace2task.obs-frame-clock.v1":
        raise ValueError("Missing native frame-clock header")
    header, *rows, footer = lines
    if (header.get("obs_version") != "32.2.2" or header.get("clock") != "windows_qpc_ns"
            or header.get("timestamp_kind") != "composition"):
        raise ValueError("Unsupported native frame clock")
    if (footer.get("type") != "footer" or footer.get("status") != "complete"
            or footer.get("count") != len(rows) or footer.get("overflow") is not False
            or footer.get("missing_timing") is not False or footer.get("paused") is not False):
        raise ValueError("Native frame clock is incomplete or invalid")
    for row in rows:
        if (row.get("type") != "frame" or row.get("cts_ns", 0) <= 0
                or row.get("timebase_num", 0) <= 0 or row.get("timebase_den", 0) <= 0
                or row.get("timing_pts") != row.get("pts")):
            raise ValueError("Invalid native frame timing")
        row["video_pts_seconds"] = row["pts"] * row["timebase_num"] / row["timebase_den"]
        if not math.isfinite(row["video_pts_seconds"]):
            raise ValueError("Non-finite native PTS")
    # Encoder callbacks arrive in decode order; screenshots are in presentation order.
    rows.sort(key=lambda row: row["video_pts_seconds"])
    if not video_pts or len(rows) < len(video_pts) or rows[0]["pts"] != 0 or not rows[0]["keyframe"]:
        raise ValueError("Missing first frame or native frame-clock rows")
    if any(b["video_pts_seconds"] <= a["video_pts_seconds"] or b["cts_ns"] <= a["cts_ns"]
           for a, b in itertools.pairwise(rows)):
        raise ValueError("Duplicate/non-monotonic native frame times")
    indexed = {round(row["video_pts_seconds"] * 1e6): row for row in rows}
    matched = []
    for index, pts in enumerate(video_pts):
        if not math.isfinite(pts) or (index and pts <= video_pts[index - 1]):
            raise ValueError("Invalid decoded PTS")
        row = indexed.get(round(pts * 1e6))
        if row is None or abs(row["video_pts_seconds"] - pts) > 0.000001:
            raise ValueError("Native and decoded video PTS differ; no guessed offset applied")
        matched.append(row)
    if matched[0]["pts"] != 0:
        raise ValueError("Native and decoded first PTS differ")
    # Stop can discard packets in decode order (B-frames), not just a PTS suffix.
    return matched, {"matched_video_frames": len(video_pts),
                     "callback_packets_not_in_video": len(rows) - len(video_pts)}


def compare_marker_times(clock_rows, video_frames, events):
    results = {}
    for field in ("cts_ns", "fer_ns", "ferc_ns", "pir_ns"):
        if not all(row.get(field, 0) > 0 for row in clock_rows):
            continue
        records = []
        for event in events:
            visible = next((i for i, frame in enumerate(video_frames)
                            if frame["marker"] == event["marker"]), None)
            # Max presentation-order frame whose observed timestamp predates the event.
            # Do not assume FER/PIR are monotonic in presentation order (B-frames).
            eligible = [i for i, row in enumerate(clock_rows)
                        if row[field] / 1e9 < event["before_update"]]
            selected = video_frames[max(eligible)] if eligible else None
            records.append({"marker": event["marker"], "selected": selected,
                "poststate_in_prestate": (selected["marker"] >= event["marker"]
                    if selected and selected["marker"] is not None else None),
                "residual_ms": ((clock_rows[visible][field] / 1e9 - event["before_update"]) * 1000
                    if visible is not None else None)})
        results[field] = records
    return results


def select_cts_frames(clock_rows, timestamps):
    """Strictly before in CTS time only; NOT a guarantee of physical pre-action pixels."""
    times = [row["cts_ns"] for row in clock_rows]
    if not times or any(b <= a for a, b in itertools.pairwise(times)):
        raise ValueError("Missing or non-increasing CTS")
    selected = {}
    for key, stamp in timestamps.items():
        if not math.isfinite(stamp):
            raise ValueError("Invalid event timestamp")
        selected[key] = bisect.bisect_left(times, round(stamp * 1e9)) - 1
    return selected


def extract_cts_frames(video, clock_path, timestamps, output):
    """Match final video PTS before saving CTS reference images; never guess a shift.

    Decode twice to avoid keeping a recording's pixels in RAM. The first pass
    validates the entire mapping, the second materializes only selected images.
    """
    import av
    import cv2
    pts = []
    with av.open(str(video)) as container:
        for frame in container.decode(video=0):
            if frame.pts is None or frame.time_base is None:
                raise ValueError("Decoded frame has no PTS")
            pts.append(float(frame.pts * frame.time_base))
    clock_rows, summary = match_video_frames(clock_path, pts)
    selected = select_cts_frames(clock_rows, timestamps)
    wanted, results = {}, {}
    for key, index in selected.items():
        if index < 0:
            results[key] = {"status": "missing_prestate", "path": None,
                            "alignment": "obs_cts_approximate"}
        else:
            wanted.setdefault(index, []).append(key)
    with av.open(str(video)) as container:
        for index, frame in enumerate(container.decode(video=0)):
            if index not in wanted:
                continue
            if float(frame.pts * frame.time_base) != pts[index]:
                raise ValueError("Video PTS changed during extraction")
            pixels = frame.to_ndarray(format="bgr24")
            row = clock_rows[index]
            for key in wanted.pop(index):
                name = f"frames/{key}.png"
                if not cv2.imwrite(str(Path(output) / name), pixels):
                    raise OSError("Cannot save CTS reference screenshot")
                results[key] = {"status": "available", "path": name,
                    "alignment": "obs_cts_approximate", "frame_index": index,
                    "video_pts_seconds": pts[index], "cts_ns": row["cts_ns"]}
            if not wanted:
                break
    if wanted:
        raise ValueError("Selected video frames are missing")
    return results, summary
