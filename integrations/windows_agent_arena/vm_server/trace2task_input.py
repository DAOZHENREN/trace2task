from __future__ import annotations

import ctypes
import ntpath
import os
import threading
import time
from ctypes import wintypes
from typing import Any

from flask import jsonify, request

_BUTTONS = {"left": 0x01, "right": 0x02, "middle": 0x04}
_DEFAULT_RESET_ROOT = r"C:\Users\Docker"


def _delete_reset_file(path: str) -> None:
    """Permanently remove one validated benchmark artifact.

    Task reset must not move artifacts into the Recycle Bin because that leaves
    observable state behind for later benchmark repetitions.
    """

    os.remove(path)
_KEYS = {
    **{chr(code).lower(): code for code in range(ord("A"), ord("Z") + 1)},
    **{str(number): ord(str(number)) for number in range(10)},
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "shift": 0x10,
    "ctrl": 0x11,
    "alt": 0x12,
    "escape": 0x1B,
    "space": 0x20,
    "page_up": 0x21,
    "page_down": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "delete": 0x2E,
    **{f"f{number}": 0x6F + number for number in range(1, 13)},
}
_USER32 = ctypes.WinDLL("user32", use_last_error=True) if os.name == "nt" else None
if _USER32 is not None:
    _USER32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _USER32.GetAsyncKeyState.restype = wintypes.SHORT
    _USER32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    _USER32.GetCursorPos.restype = wintypes.BOOL


def _input_snapshot() -> dict[str, Any]:
    if _USER32 is None:
        raise RuntimeError("Trace2Task WAA input snapshots require the Windows VM")
    cursor = wintypes.POINT()
    if not _USER32.GetCursorPos(ctypes.byref(cursor)):
        raise ctypes.WinError(ctypes.get_last_error())
    return {
        "keys_down": frozenset(
            key for key, code in _KEYS.items() if _USER32.GetAsyncKeyState(code) & 0x8000
        ),
        "buttons_down": frozenset(
            button
            for button, code in _BUTTONS.items()
            if _USER32.GetAsyncKeyState(code) & 0x8000
        ),
        "cursor_position": [int(cursor.x), int(cursor.y)],
    }


def _validated_reset_path(raw_path: object, *, user_root: str = _DEFAULT_RESET_ROOT) -> str:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise TypeError("Trace2Task reset paths must be non-empty strings")
    path = ntpath.normpath(raw_path.strip())
    root = ntpath.normpath(user_root)
    if not ntpath.isabs(path):
        raise ValueError("Trace2Task reset paths must be absolute Windows paths")
    if ntpath.commonpath([ntpath.normcase(root), ntpath.normcase(path)]) != ntpath.normcase(
        root
    ):
        raise ValueError("Trace2Task reset paths must stay inside the benchmark user profile")
    if ntpath.normcase(path) == ntpath.normcase(root):
        raise ValueError("Trace2Task will not reset the benchmark user profile root")
    return path


class _BufferedInputRecorder:
    def __init__(self, *, poll_hz: int = 240, max_events: int = 50_000) -> None:
        self.poll_interval = 1 / poll_hz
        self.max_events = max_events
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._events: list[dict[str, Any]] = []
        self._next_sequence = 0
        self._started_at = 0.0

    def start(self) -> dict[str, Any]:
        self.stop()
        with self._lock:
            self._events = []
            self._next_sequence = 0
            self._started_at = time.perf_counter()
        self._stop.clear()
        initial = _input_snapshot()
        self._thread = threading.Thread(
            target=self._sample,
            args=(initial,),
            name="trace2task-waa-input-sampler",
            daemon=True,
        )
        self._thread.start()
        return self._public_snapshot(initial)

    def stop(self) -> int:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        with self._lock:
            return len(self._events)

    def events_after(self, sequence: int) -> dict[str, Any]:
        with self._lock:
            events = [event for event in self._events if event["seq"] > sequence]
            first_sequence = self._events[0]["seq"] if self._events else None
            last_sequence = self._events[-1]["seq"] if self._events else sequence
        return {
            "events": events,
            "first_available_seq": first_sequence,
            "last_seq": last_sequence,
        }

    def _sample(self, previous: dict[str, Any]) -> None:
        while not self._stop.is_set():
            current = _input_snapshot()
            elapsed_ms = round((time.perf_counter() - self._started_at) * 1000, 3)
            events: list[dict[str, Any]] = []
            for key in sorted(current["keys_down"] - previous["keys_down"]):
                events.append({"device": "keyboard", "event": "down", "key": key})
            for key in sorted(previous["keys_down"] - current["keys_down"]):
                events.append({"device": "keyboard", "event": "up", "key": key})
            for button in sorted(current["buttons_down"] - previous["buttons_down"]):
                events.append(self._mouse_event("down", button, current["cursor_position"]))
            for button in sorted(previous["buttons_down"] - current["buttons_down"]):
                events.append(self._mouse_event("up", button, current["cursor_position"]))
            if events:
                with self._lock:
                    for event in events:
                        event.update(
                            {
                                "seq": self._next_sequence,
                                "sampled_elapsed_ms": elapsed_ms,
                            }
                        )
                        self._next_sequence += 1
                        self._events.append(event)
                    if len(self._events) > self.max_events:
                        del self._events[: len(self._events) - self.max_events]
            previous = current
            time.sleep(self.poll_interval)

    @staticmethod
    def _mouse_event(event: str, button: str, position: list[int]) -> dict[str, Any]:
        return {
            "device": "mouse",
            "event": event,
            "button": button,
            "screen_position": position,
        }

    @staticmethod
    def _public_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
        return {
            "keys_down": sorted(snapshot["keys_down"]),
            "buttons_down": sorted(snapshot["buttons_down"]),
            "cursor_position": snapshot["cursor_position"],
        }


_RECORDER = _BufferedInputRecorder()


def register_trace2task_input_routes(app) -> None:
    @app.post("/trace2task/input/start")
    def trace2task_input_start():
        return jsonify({"status": "success", "initial": _RECORDER.start()})

    @app.get("/trace2task/input/events")
    def trace2task_input_events():
        after = request.args.get("after", default=-1, type=int)
        return jsonify({"status": "success", **_RECORDER.events_after(after)})

    @app.post("/trace2task/input/stop")
    def trace2task_input_stop():
        return jsonify({"status": "success", "event_count": _RECORDER.stop()})

    @app.post("/trace2task/reset")
    def trace2task_reset():
        try:
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                raise TypeError("Trace2Task reset body must be a JSON object")
            action = payload.get("action")
            if action not in {"apply", "verify"}:
                raise ValueError("Trace2Task reset action must be 'apply' or 'verify'")
            raw_paths = payload.get("must_not_exist")
            if not isinstance(raw_paths, list) or not raw_paths:
                raise TypeError("Trace2Task reset must_not_exist must be a non-empty list")
            user_root = os.environ.get("USERPROFILE", _DEFAULT_RESET_ROOT)
            paths = [
                _validated_reset_path(raw_path, user_root=user_root)
                for raw_path in raw_paths
            ]
            removed: list[str] = []
            if action == "apply":
                for path in paths:
                    if not os.path.exists(path):
                        continue
                    if not os.path.isfile(path):
                        raise ValueError(f"Trace2Task reset target is not a file: {path}")
                    _delete_reset_file(path)
                    removed.append(path)
            remaining = [path for path in paths if os.path.exists(path)]
            if remaining:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "Trace2Task reset invariant failed",
                            "remaining": remaining,
                        }
                    ),
                    409,
                )
            return jsonify(
                {
                    "status": "success",
                    "action": action,
                    "removed": removed,
                    "verified": paths,
                }
            )
        except (TypeError, ValueError) as error:
            return jsonify({"status": "error", "message": str(error)}), 400
        except OSError as error:
            return jsonify({"status": "error", "message": str(error)}), 500
