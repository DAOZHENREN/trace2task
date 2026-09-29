"""Read-only Windows OBS/Python clock check. Does not start OBS or record.

This confirms the clock domain only, NOT the epoch of a recorded video.
Only load obs.dll from the trusted dedicated OBS installation.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import time
from pathlib import Path


def probe(root: Path, samples: int = 1000) -> dict:
    if os.name != "nt":
        raise RuntimeError("This clock probe requires Windows")
    binary = root.resolve() / "bin" / "64bit"
    with os.add_dll_directory(str(binary)):
        library = ctypes.CDLL(str(binary / "obs.dll"))
        clock = library.os_gettime_ns
        clock.argtypes = []
        clock.restype = ctypes.c_uint64
        inside = 0
        widths = []
        for _ in range(samples):
            before = time.perf_counter_ns()
            obs_time = clock()
            after = time.perf_counter_ns()
            inside += before <= obs_time <= after
            widths.append(after - before)
    return {
        "test": "clock_domain_only_not_video_alignment",
        "python_clock": time.get_clock_info("perf_counter").implementation,
        "samples": samples,
        "obs_clock_inside_python_bracket": inside,
        "min_bracket_ns": min(widths),
        "max_bracket_ns": max(widths),
        "video_frame_timestamp_obtained": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--obs", type=Path, default=Path("D:/Apps/Trace2Task-OBS"))
    args = parser.parse_args()
    print(json.dumps(probe(args.obs), ensure_ascii=False, indent=2))
