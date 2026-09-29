"""Independent source-clock experiment, NOT an OBS frame-to-source mapping.

Captures only the diagnostic marker rectangle; never injects keyboard/mouse input.
All images remain local. PNG and source timestamp always come from the same
AcquireNextFrame result. No timestamp is inferred from filenames or file times.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import threading
import time
import tkinter as tk
import uuid
from pathlib import Path

from validate_sync import BITS, CELL, decode_marker, summarize


class FrameInfo(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int64) for name in (
        "frequency", "before_acquire", "after_acquire", "last_present", "last_mouse", "after_copy"
    )] + [("accumulated_frames", ctypes.c_uint32), ("protected_content", ctypes.c_uint32)]


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def analyze(rows, events):
    if not rows or not events:
        raise ValueError("Missing images or markers")
    previous = -1
    for index, row in enumerate(rows):
        if (row["frequency"] <= 0 or row["frequency"] != rows[0]["frequency"] or
                row["index"] != index or row["before_acquire"] > row["after_acquire"] or
                not 0 < row["last_present"] <= row["after_acquire"] <= row["after_copy"]):
            raise ValueError("Invalid source/capture timestamps")
        if row["last_present"] <= previous or row["protected_content"]:
            raise ValueError("Repeated/non-increasing source time or protected content")
        previous = row["last_present"]
    first_seen = {}
    for row in rows:
        if row["marker"] is not None:
            first_seen.setdefault(row["marker"], row)
    results = []
    for event in events:
        cutoff = event["before_update_ns"] / 1e9
        seen = first_seen.get(event["marker"])
        eligible = [r for r in rows if r["last_present"] / r["frequency"] < cutoff]
        pre = eligible[-1] if eligible else None
        # A buffering alternative can only use an image already copied at event time.
        available = [r for r in rows if r["after_copy"] / r["frequency"] < cutoff]
        buffered = available[-1] if available else None
        results.append({
            **event, "first_visible_frame": seen["index"] if seen else None,
            "source_residual_ms": (seen["last_present"] / seen["frequency"] - cutoff) * 1000 if seen else None,
            "source_prestate_frame": pre["index"] if pre else None,
            "poststate_in_prestate": pre["marker"] >= event["marker"] if pre and pre["marker"] is not None else None,
            "buffered_prestate_frame": buffered["index"] if buffered else None,
            "buffered_poststate": buffered["marker"] >= event["marker"] if buffered and buffered["marker"] is not None else None,
            "source_prestate_age_ms": (cutoff - pre["last_present"] / pre["frequency"]) * 1000 if pre else None,
        })
    return {
        "frames": len(rows), "markers": len(events),
        "matched_markers": sum(r["first_visible_frame"] is not None for r in results),
        "unreadable_frames": sum(r["marker"] is None for r in rows),
        "poststate_in_prestate": sum(r["poststate_in_prestate"] is True for r in results),
        "unverifiable_prestate": sum(r["poststate_in_prestate"] is None for r in results),
        "buffered_poststate": sum(r["buffered_poststate"] is True for r in results),
        "residual": summarize([r["source_residual_ms"] for r in results if r["source_residual_ms"] is not None]),
        "prestate_age": summarize([r["source_prestate_age_ms"] for r in results if r["source_prestate_age_ms"] is not None]),
        "acquire_delay": summarize([(r["after_acquire"]-r["last_present"])*1000/r["frequency"] for r in rows]),
        "accumulated_more_than_one": sum(r["accumulated_frames"] > 1 for r in rows),
        "results": results,
    }


def capture(dll_path, roi, folder, stop, ready, rows, errors):
    try:
        _capture(dll_path, roi, folder, stop, ready, rows, errors)
    except Exception as error:  # noqa: BLE001 -- report capture-thread errors to its caller
        errors.append(str(error))
        ready.set()


def _capture(dll_path, roi, folder, stop, ready, rows, errors):
    import cv2
    import numpy as np
    lib = ctypes.CDLL(str(dll_path))
    lib.probe_open.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
    lib.probe_open.restype = ctypes.c_int32
    lib.probe_read.argtypes = [ctypes.c_void_p] + [ctypes.c_uint32]*4 + [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(FrameInfo)]
    lib.probe_read.restype = ctypes.c_int32
    lib.probe_close.argtypes = [ctypes.c_void_p]
    lib.probe_close.restype = None
    handle, width, height = ctypes.c_void_p(), ctypes.c_uint32(), ctypes.c_uint32()
    def check(hr):
        if hr < 0:
            raise RuntimeError(f"DXGI HRESULT 0x{hr & 0xffffffff:08X}; run rejected, no guessed recovery")
    try:
        check(lib.probe_open(ctypes.byref(handle), ctypes.byref(width), ctypes.byref(height)))
        x, y = roi
        w, h = CELL*(BITS+2), 56
        pixels = np.empty((h, w, 4), dtype=np.uint8)
        while not stop.is_set():
            started = time.perf_counter()
            info = FrameInfo()
            hr = lib.probe_read(handle, x, y, w, h, pixels.ctypes.data, pixels.nbytes, ctypes.byref(info))
            check(hr)
            if hr == 0:
                row = {name: getattr(info, name) for name, _ in info._fields_}
                row.update(index=len(rows), marker=decode_marker(pixels[:, :, :3], [0, 0]))
                relative = f"frames/{len(rows):05d}.png"
                ok, encoded = cv2.imencode(".png", pixels)
                if not ok:
                    raise RuntimeError("PNG encoding failed")
                data = encoded.tobytes()
                (folder / relative).write_bytes(data)
                row.update(image=relative, sha256=hashlib.sha256(data).hexdigest())
                rows.append(row)
                ready.set()
            stop.wait(max(0, 1/30 - (time.perf_counter()-started)))
    except Exception as error:  # noqa: BLE001 -- report native capture failures and release resources
        errors.append(str(error))
        ready.set()
    finally:
        lib.probe_close(handle)


def run(args):
    ctypes.windll.user32.SetProcessDPIAware()
    output = Path(args.output).resolve() / str(uuid.uuid4())
    output.mkdir(parents=True)
    root = tk.Tk()
    root.title("Trace2Task DXGI 源时间验证 · 不操作其他应用")
    root.geometry("560x230+60+70")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    label = tk.Label(root, text="正在验证采集源时间；仅保存此色块区域", font=("Microsoft YaHei", 12))
    label.pack(pady=10)
    canvas = tk.Canvas(root, width=CELL*(BITS+2), height=56, highlightthickness=0)
    canvas.pack(pady=5)
    cells = [canvas.create_rectangle(i*CELL, 0, (i+1)*CELL, 56, width=0) for i in range(BITS+2)]
    cancelled = threading.Event()
    root.protocol("WM_DELETE_WINDOW", cancelled.set)
    root.bind("<Escape>", lambda _: cancelled.set())
    tk.Button(root, text="停止验证", command=cancelled.set).pack(pady=8)
    def paint(marker):
        for cell, bit in zip(cells, [1, 0]+[(marker >> i)&1 for i in range(BITS)]):
            canvas.itemconfig(cell, fill="#eeeeee" if bit else "#111111")
        root.update_idletasks()
    def pump(seconds):
        deadline = time.perf_counter()+seconds
        while time.perf_counter() < deadline:
            root.update()
            if cancelled.is_set():
                raise RuntimeError("Cancelled by user")
            time.sleep(.005)
    reports = []
    try:
        paint(0)
        root.update()
        roi = [canvas.winfo_rootx(), canvas.winfo_rooty()]
        qpc = ctypes.windll.kernel32.QueryPerformanceCounter
        qpf = ctypes.windll.kernel32.QueryPerformanceFrequency
        freq = ctypes.c_int64()
        qpf(ctypes.byref(freq))
        checks = []
        for _ in range(100):
            before = time.perf_counter_ns()
            value = ctypes.c_int64()
            qpc(ctypes.byref(value))
            after = time.perf_counter_ns()
            ns = value.value*1_000_000_000//freq.value
            checks.append(before <= ns <= after)
        if not all(checks):
            raise RuntimeError("Python and QPC clock bracket check failed")
        for number in range(1, args.rounds+1):
            folder = output / f"round-{number:02d}"
            (folder / "frames").mkdir(parents=True)
            paint(0)
            label.config(text=f"第 {number}/{args.rounds} 轮，请勿移动或遮挡验证窗口")
            rows, errors, events = [], [], []
            stop, ready = threading.Event(), threading.Event()
            thread = threading.Thread(target=capture, args=(Path(args.dll).resolve(), roi, folder, stop, ready, rows, errors))
            thread.start()
            try:
                deadline = time.perf_counter()+10
                while not ready.is_set() and time.perf_counter() < deadline:
                    pump(.05)
                if errors or not ready.is_set():
                    raise RuntimeError(str(errors) if errors else "First source frame timeout")
                pump(.8)
                for marker in range(1, args.markers+1):
                    if errors:
                        raise RuntimeError(str(errors))
                    if [canvas.winfo_rootx(), canvas.winfo_rooty()] != roi:
                        raise RuntimeError("Marker window moved")
                    before = time.perf_counter_ns()
                    paint(marker)
                    events.append({"marker": marker, "before_update_ns": before,
                                   "after_update_ns": time.perf_counter_ns(), "kind": "timer_not_keyboard"})
                    pump([.413, .587, .731, .479, .653][(marker-1)%5])
                pump(.4)
            finally:
                stop.set()
                thread.join(timeout=5)
                save(folder / "markers.json", events)
                save(folder / "source-frames.json", rows)
            if thread.is_alive() or errors:
                raise RuntimeError(f"Capture did not close cleanly: {errors}")
            report = analyze(rows, events)
            save(folder / "analysis.json", report)
            reports.append({"round": number, **{k: v for k, v in report.items() if k != "results"}})
            print(json.dumps(reports[-1]), flush=True)
        save(output / "summary.json", {"status": "completed", "source": "DXGI LastPresentTime",
            "dll_sha256": hashlib.sha256(Path(args.dll).read_bytes()).hexdigest(),
            "qpc_frequency": freq.value, "qpc_bracket_passed": sum(checks), "roi": roi,
            "production_changed": False, "obs_pts_mapping_verified": False, "rounds": reports,
            "limitations": ["Timer markers, not real input hooks", "Primary unrotated display only",
                            "Source image update time, not physical monitor scanout time",
                            "30 Hz polling may skip updates; AccumulatedFrames is retained",
                            "Does not establish which DXGI texture OBS recorded"]})
        print("DXGI_REPORT="+str(output / "summary.json"), flush=True)
    except Exception as error:
        save(output / "failure.json", {"error": str(error), "completed_rounds": reports})
        raise
    finally:
        root.destroy()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dll", default="build/dxgi-clock-probe/dxgi-clock-probe.dll")
    parser.add_argument("--output", default="D:/Trace2Task-deps/opencua-dxgi-clock-validation")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--markers", type=int, default=24)
    args = parser.parse_args()
    if not 1 <= args.rounds <= 5 or not 3 <= args.markers <= 100:
        parser.error("rounds 1..5, markers 3..100")
    run(args)
