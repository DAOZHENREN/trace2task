from __future__ import annotations

import argparse
import json
import re
import select
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from desktop_env.envs.desktop_env import DesktopEnv
from trace2task_reset import apply_trace2task_reset, verify_trace2task_reset

VM_SERVER = "http://20.20.20.21:5000"
SCREEN_WIDTH = 1920
SCREEN_HEIGHT = 1200
UTC_TIMEZONE = timezone.utc  # noqa: UP017 - the WAA client runs Python 3.9
DEFAULT_MAX_SECONDS = 30 * 60
STATUS_INTERVAL_SECONDS = 30
CONTROL_EVENT_PREFIX = "TRACE2TASK_EVENT "


def _emit_control_event(event_type: str, **payload: Any) -> None:
    print(
        CONTROL_EVENT_PREFIX
        + json.dumps({"type": event_type, **payload}, ensure_ascii=False),
        flush=True,
    )


def _read_control_command(*, blocking: bool) -> str | None:
    if not blocking:
        readable, _, _ = select.select([sys.stdin], [], [], 0)
        if not readable:
            return None
    line = sys.stdin.readline()
    if not line:
        return "STOP"
    return line.strip().upper()


def _start_input_recording() -> None:
    response = requests.post(f"{VM_SERVER}/trace2task/input/start", timeout=5)
    response.raise_for_status()


def _input_events_after(sequence: int) -> list[dict[str, Any]]:
    response = requests.get(
        f"{VM_SERVER}/trace2task/input/events",
        params={"after": sequence},
        timeout=5,
    )
    response.raise_for_status()
    payload = response.json()
    return payload["events"]


def _stop_input_recording() -> None:
    response = requests.post(f"{VM_SERVER}/trace2task/input/stop", timeout=5)
    response.raise_for_status()


def _evaluate_task(env: DesktopEnv) -> tuple[float, str | None]:
    try:
        return float(env.evaluate()), None
    except (OSError, RuntimeError, ValueError) as exc:
        return 0.0, f"{type(exc).__name__}: {exc}"


def _write_event(
    run_dir: Path,
    trace_path: Path,
    *,
    sequence: int,
    started: float,
    event_type: str,
    screenshot: bytes,
    details: dict[str, Any],
) -> None:
    frame_name = f"{sequence:04d}.png"
    (run_dir / "frames" / frame_name).write_bytes(screenshot)
    event = {
        "seq": sequence,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "type": event_type,
        "frame": f"frames/{frame_name}",
        "details": details,
    }
    with trace_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")


def _normalize_raw_input(event: dict[str, Any]) -> dict[str, Any]:
    raw = dict(event)
    raw.pop("seq", None)
    if raw.get("device") != "mouse":
        return raw
    x, y = raw["screen_position"]
    inside = 0 <= x < SCREEN_WIDTH and 0 <= y < SCREEN_HEIGHT
    raw["normalized_position"] = (
        [round(x / SCREEN_WIDTH, 6), round(y / SCREEN_HEIGHT, 6)] if inside else None
    )
    raw["inside_target"] = inside
    return raw


def record(args: argparse.Namespace) -> dict[str, Any]:
    example_path = Path(args.example).resolve()
    example = json.loads(example_path.read_text(encoding="utf-8"))
    env = DesktopEnv(
        action_space="pyautogui",
        require_a11y_tree=False,
        emulator_ip="20.20.20.21",
    )
    output_root = Path(args.output).resolve()
    timestamp = datetime.now(UTC_TIMEZONE).strftime("%Y%m%d-%H%M%S-%f")
    session_id = str(getattr(args, "session_id", "") or "").strip()
    if session_id and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", session_id):
        raise ValueError("session-id contains unsupported characters")
    run_dir = output_root / (session_id or f"{timestamp}-waa-windows-human")
    (run_dir / "frames").mkdir(parents=True)
    trace_path = run_dir / "trace.jsonl"
    trace_path.write_text("", encoding="utf-8")
    try:
        applied_reset = apply_trace2task_reset(env, example)
        observation = env.reset(task_config=example)
        verified_reset = verify_trace2task_reset(env, example)
        require_reset = bool(getattr(args, "require_reset_verification", False))
        if require_reset and (applied_reset is None or verified_reset is None):
            raise RuntimeError(
                "WAA recording requires a matching verified Trace2Task reset spec"
            )
        reset_receipt = {
            "schema_version": "0.1",
            "status": "verified" if verified_reset is not None else "not_configured",
            "task_id": example["id"],
            "created_at": datetime.now(UTC_TIMEZONE).isoformat(),
            "apply": applied_reset,
            "verify": verified_reset,
        }
        (run_dir / "reset-receipt.json").write_text(
            json.dumps(reset_receipt, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception:
        env.close()
        raise
    wait_for_go = bool(getattr(args, "wait_for_go", False))
    if wait_for_go:
        _emit_control_event(
            "ready",
            run_dir=str(run_dir),
            ready_at=datetime.now(UTC_TIMEZONE).isoformat(),
            waa_task_id=example["id"],
            reset_receipt=reset_receipt,
        )
        if _read_control_command(blocking=True) != "GO":
            env.close()
            result = {
                "trace_path": str(trace_path),
                "source": "windows_human",
                "success": False,
                "stop_reason": "cancelled_before_start",
            }
            _emit_control_event("completed", result=result)
            return result

    started_at = datetime.now(UTC_TIMEZONE)
    started = time.perf_counter()
    sequence = 0
    input_events = 0
    human_marked_success = False
    evaluation_attempts = 0
    score = 0.0
    stop_reason = "timeout"
    _write_event(
        run_dir,
        trace_path,
        sequence=sequence,
        started=started,
        event_type="start",
        screenshot=observation["screenshot"],
        details={
            "window": {
                "handle": 0,
                "title": "Windows Agent Arena VM",
                "process_name": "WindowsAgentArenaVM.exe",
                "client_left": 0,
                "client_top": 0,
                "client_width": SCREEN_WIDTH,
                "client_height": SCREEN_HEIGHT,
                "dpi": 96,
            },
            "capture": "waa_vm_desktop",
            "coordinate_space": "physical_pixels",
            "waa_task_id": example["id"],
            "instruction": example["instruction"],
        },
    )
    sequence += 1
    last_input_sequence = -1
    input_recording_started = False
    print(
        "WAA human Trace recording started. "
        f"Operate the VM, press F8 for success or F9 to cancel (limit: {args.max_seconds:g}s).",
        flush=True,
    )
    try:
        _start_input_recording()
        input_recording_started = True
        _emit_control_event(
            "started",
            run_dir=str(run_dir),
            trace_started_at=started_at.isoformat(),
        )
        recording_started = time.perf_counter()
        recording_deadline = recording_started + args.max_seconds
        next_status_at = recording_started + STATUS_INTERVAL_SECONDS
        while time.perf_counter() < recording_deadline:
            should_stop = False
            if wait_for_go and _read_control_command(blocking=False) == "STOP":
                stop_reason = "cancelled"
                should_stop = True
            for event in ([] if should_stop else _input_events_after(last_input_sequence)):
                last_input_sequence = max(last_input_sequence, int(event["seq"]))
                if event.get("device") == "keyboard" and event.get("event") == "down":
                    if event.get("key") == "f9":
                        stop_reason = "cancelled"
                        should_stop = True
                        break
                    if event.get("key") == "f8":
                        evaluation_attempts += 1
                        score, evaluation_error = _evaluate_task(env)
                        screenshot = env._get_screenshot()
                        _write_event(
                            run_dir,
                            trace_path,
                            sequence=sequence,
                            started=started,
                            event_type=(
                                "success_marker" if score >= 1.0 else "evaluation_failed"
                            ),
                            screenshot=screenshot,
                            details={
                                "control_hotkey": "f8",
                                "waa_task_id": example["id"],
                                "waa_evaluator_score": score,
                                "evaluation_error": evaluation_error,
                            },
                        )
                        sequence += 1
                        if score >= 1.0:
                            human_marked_success = True
                            stop_reason = "success_marked"
                            should_stop = True
                            break
                        print(
                            "F8 validation failed (WAA score: "
                            f"{score:g}). Recording remains active; correct the task "
                            "and press F8 again, or press F9 to cancel.",
                            flush=True,
                        )
                        continue
                raw_input = _normalize_raw_input(event)
                screenshot = env._get_screenshot()
                _write_event(
                    run_dir,
                    trace_path,
                    sequence=sequence,
                    started=started,
                    event_type="windows_input",
                    screenshot=screenshot,
                    details={"raw_input": raw_input},
                )
                sequence += 1
                input_events += 1
            if should_stop:
                break
            now = time.perf_counter()
            if now >= next_status_at:
                remaining = max(0, int(recording_deadline - now))
                print(
                    "WAA human Trace recording is active: "
                    f"{input_events} input events captured, {remaining}s remaining.",
                    flush=True,
                )
                next_status_at = now + STATUS_INTERVAL_SECONDS
            time.sleep(1 / args.poll_hz)
        if stop_reason == "timeout":
            print(
                "WAA human Trace recording timed out before F8/F9 was received. "
                "Rerun with a larger --max-seconds value if needed.",
                flush=True,
            )
    finally:
        if input_recording_started:
            _stop_input_recording()
        env.close()

    metadata = {
        "schema_version": "0.1",
        "task_id": args.task_id,
        "seed": 0,
        "source": "windows_human",
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(UTC_TIMEZONE).isoformat(),
        "success": human_marked_success and score >= 1.0,
        "human_marked_success": human_marked_success,
        "waa_evaluator_score": score,
        "evaluation_attempt_count": evaluation_attempts,
        "event_count": sequence,
        "action_count": 0,
        "input_event_count": input_events,
        "focus_losses": 0,
        "stop_reason": stop_reason,
        "window_selector": {"process_name": "WindowsAgentArenaVM.exe"},
        "initial_window": {
            "handle": 0,
            "title": "Windows Agent Arena VM",
            "process_name": "WindowsAgentArenaVM.exe",
            "client_left": 0,
            "client_top": 0,
            "client_width": SCREEN_WIDTH,
            "client_height": SCREEN_HEIGHT,
            "dpi": 96,
        },
        "capture_method": "waa_vm_desktop",
        "input_sampling": "waa_vm_edge_poll_v1",
        "coordinate_space": "physical_pixels",
        "success_hotkey": "f8",
        "cancel_hotkey": "f9",
        "max_recording_seconds": args.max_seconds,
        "waa_task_id": example["id"],
        "waa_instruction": example["instruction"],
        "reset_receipt": "reset-receipt.json",
        "reset_status": reset_receipt["status"],
    }
    (run_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if not bool(getattr(args, "no_task_narration", False)):
        (run_dir / "narration.json").write_text(
            json.dumps(
                {
                    "schema_version": "0.1",
                    "created_at": datetime.now(UTC_TIMEZONE).isoformat(),
                    "transcription_engine": "waa_task_instruction",
                    "transcript": f"Task instruction: {example['instruction']}",
                    "segments": [],
                    "audio": None,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    result = {"trace_path": str(trace_path), **metadata}
    _emit_control_event("completed", result=result)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--example", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--output", default="/client/trace2task_recordings")
    parser.add_argument("--poll-hz", type=int, default=60)
    parser.add_argument("--max-seconds", type=float, default=DEFAULT_MAX_SECONDS)
    parser.add_argument("--session-id", default="")
    parser.add_argument("--wait-for-go", action="store_true")
    parser.add_argument("--no-task-narration", action="store_true")
    parser.add_argument("--require-reset-verification", action="store_true")
    args = parser.parse_args()
    if args.poll_hz <= 0 or args.max_seconds <= 0:
        parser.error("poll-hz and max-seconds must be positive")
    record(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
