"""Offline regression of production extraction against an existing marker recording."""
import argparse
import json
import uuid
from pathlib import Path

import cv2
from frame_clock import extract_cts_frames
from validate_sync import decode_marker

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--recording", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    recording = Path(args.recording)
    events = json.loads((recording / "markers.json").read_text(encoding="utf-8"))
    config = json.loads((recording / "clock.json").read_text(encoding="utf-8"))
    output = Path(args.output) / str(uuid.uuid4())
    (output / "frames").mkdir(parents=True)
    videos = list(recording.glob("*.mp4"))
    if len(videos) != 1:
        raise ValueError("Expected exactly one original video")
    requests = {f"{i:04d}": event["before_update"] for i, event in enumerate(events)}
    frames, summary = extract_cts_frames(videos[0], recording / "obs-frame-clock.jsonl", requests, output)
    expected = json.loads((recording / "native-clock-analysis.json").read_text(encoding="utf-8"))["comparisons"]["cts_ns"]
    for i, reference in enumerate(expected):
        actual = frames[f"{i:04d}"]
        if reference["selected"] is None:
            assert actual["path"] is None
        else:
            assert actual["frame_index"] == reference["selected"]["index"]
            image = cv2.imread(str(output / actual["path"]))
            assert decode_marker(image, config["roi"]) == reference["selected"]["marker"]
    report = {"status": "passed", "requests_checked": len(requests), **summary,
              "alignment": "obs_cts_approximate", "strict_prestate_guaranteed": False,
              "frames": frames}
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(output / "report.json")
