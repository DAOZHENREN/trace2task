"""Opt-in native validation of the unified local GUI-agent loop.

This is deliberately *not* a benchmark or a general desktop agent.  It creates
one disposable Win32 ``Edit`` control, sends only the canned text
``Trace2Task fixture`` to that control, then destroys the fixture.  It exists
to validate the real observe -> decode -> ExecutionCore -> driver -> reobserve
path with both native tool-call formats and both execution backends.

It never enumerates or targets user-selected applications.  The Win32 variant
briefly focuses the fixture because SendInput is foreground-only; it restores
the previous foreground window only after proving that it is still the same
non-minimized window.  If Windows refuses the focus request, it fails before
any input is sent.

Examples (run explicitly; this module never runs on import)::

    uv run python scripts/local_gui/validate_native_agent_loop.py --output runs/native-loop-probe
    uv run python scripts/local_gui/validate_native_agent_loop.py --backend cua --model gui-owl-2b \
        --output runs/native-loop-probe
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

FIXTURE_TITLE = "Trace2Task disposable native-loop fixture"
FIXTURE_TEXT = "Trace2Task fixture"
SW_SHOWNOACTIVATE = 4
SW_SHOW = 5
WM_GETTEXT = 0x000D


def _user32():
    from trace2task.windows_control import configure_physical_dpi_api

    user = ctypes.WinDLL("user32", use_last_error=True)
    user.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p,
    ]
    user.CreateWindowExW.restype = wintypes.HWND
    user.GetForegroundWindow.restype = wintypes.HWND
    user.SetForegroundWindow.argtypes = [wintypes.HWND]
    user.SetForegroundWindow.restype = wintypes.BOOL
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.GetWindowRect.restype = wintypes.BOOL
    user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_void_p]
    user.SendMessageW.restype = ctypes.c_ssize_t
    user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user.ShowWindow.restype = wintypes.BOOL
    configure_physical_dpi_api(user)
    return user


def _physical_rect(window_id: int) -> wintypes.RECT:
    from trace2task.windows_control import physical_dpi_context

    user = _user32()
    rect = wintypes.RECT()
    with physical_dpi_context(user):
        if not user.GetWindowRect(window_id, ctypes.byref(rect)):
            raise RuntimeError("Disposable fixture Edit rectangle disappeared")
    return rect


def _fixture_process(*, activate: bool = False) -> None:
    """Run the fixture in a child process so cleanup can target it exactly."""
    user = _user32()
    # WS_OVERLAPPEDWINDOW for the parent, WS_VISIBLE | WS_BORDER | ES_AUTOHSCROLL
    # for the single disposable edit control.
    window = user.CreateWindowExW(
        0, "Static", FIXTURE_TITLE, 0x00CF0000,
        160, 120, 640, 260, None, None, None, None,
    )
    edit = user.CreateWindowExW(
        0, "Edit", "", 0x50000000 | 0x00800000 | 0x80,
        20, 70, 590, 72, window, None, None, None,
    )
    if not window or not edit:
        raise RuntimeError("Cannot create disposable native-loop fixture")
    # Cua never takes focus.  The opt-in Win32 probe asks this child to show
    # and focus only its own window; the parent still verifies the exact HWND
    # before any model-decoded action is sent.
    user.ShowWindow(window, SW_SHOW if activate else SW_SHOWNOACTIVATE)
    if activate:
        user.SetForegroundWindow(window)
    print(json.dumps({"pid": os.getpid(), "window_id": int(window), "edit_id": int(edit)}), flush=True)
    message = wintypes.MSG()
    while user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
        user.TranslateMessage(ctypes.byref(message))
        user.DispatchMessageW(ctypes.byref(message))


def _fixture_text(edit_id: int) -> str:
    value = ctypes.create_unicode_buffer(512)
    _user32().SendMessageW(edit_id, WM_GETTEXT, len(value), ctypes.cast(value, ctypes.c_void_p))
    return value.value


class _Stop:
    def raise_if_requested(self) -> None:
        return None

    @staticmethod
    def sleep(seconds: float) -> None:
        time.sleep(seconds)


class _Audit:
    """Small local audit with no model service, network call, or hidden action."""

    def __init__(self, root: Path, result: dict[str, Any], record) -> None:
        self.root, self.result, self.record = root, result, record
        self.result["model_io"] = []
        self.result["performance"] = {
            "capture_ms": 0.0, "action_ms": 0.0, "explicit_wait_ms": 0.0,
            "model_roundtrip_ms": 0.0, "planning_ms": 0.0,
        }

    def elapsed(self, field: str, started: float) -> float:
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        self.result["performance"][field] = round(self.result["performance"].get(field, 0.0) + elapsed, 2)
        return elapsed

    def predict(self, predictor, _stop, model, task, screenshot, history, step, context=None, **kwargs):
        started = time.perf_counter()
        output = predictor(task, model=model, history=history, step=step, context=context, **kwargs)
        elapsed = self.elapsed("model_roundtrip_ms", started)
        self.result["performance"]["planning_ms"] = self.result["performance"]["model_roundtrip_ms"]
        folder = self.root / "model-io" / f"{step:04d}"
        folder.mkdir(parents=True, exist_ok=True)
        entry = {
            "step_index": step, "purpose": (context or {}).get("purpose", "plan"),
            "model": model, "task": task, "screenshot": str(screenshot),
            "history": history, "context": context, "output": output,
            "elapsed_ms": elapsed,
        }
        (folder / "round.json").write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
        self.result["model_io"].append(entry)
        self.record("model_input", step_index=step, model=model, screenshot=str(screenshot), history=history,
                    context=context)
        self.record("model_output", step_index=step, output=output, elapsed_ms=elapsed)
        return output

    def execution(self, payload: dict[str, Any]) -> None:
        if self.result["model_io"]:
            self.result["model_io"][-1]["execution"] = payload


class _Win32Observer:
    def __init__(self, session, capture, root: Path, edit_id: int, fixture_window_id: int) -> None:
        self.session, self.capture, self.root, self.edit_id = session, capture, root, edit_id
        self.fixture_window_id = fixture_window_id

    def observe(self, _target, index):
        import pygame

        from trace2task.windows_control import physical_dpi_context

        window = self.session.observe()
        # The desktop screenshot contains other applications.  A canned probe
        # coordinate is safe only while the exact child-owned parent remains
        # foreground.  Never allow the following capture to become a plan for
        # an application the user switched to.
        if window.handle != self.fixture_window_id:
            raise RuntimeError("Owned fixture lost foreground; no input sent")
        frame = self.capture.capture(window)
        path = self.root / f"{index:04d}.png"
        pygame.image.save(frame, path)
        rect = _physical_rect(self.edit_id)
        with physical_dpi_context(self.capture.user32):
            width = self.capture.user32.GetSystemMetrics(0)
            height = self.capture.user32.GetSystemMetrics(1)
        self._input_point = ((rect.left + rect.right) / 2 / width, (rect.top + rect.bottom) / 2 / height)
        identity = {"foreground_handle": window.handle, "screen_size": (width, height)}
        return identity, dict(identity), path

    def context(self, _target, state, backend):
        # Probe-only coordinate derived from our own Edit control, never from a
        # user application or real model vision output.
        return {
            "execution_scope": "win32_desktop",
            "capabilities": backend.capabilities(state),
            "probe_fixture": {"input_center": list(self._input_point)},
        }


class _CuaObserver:
    def __init__(self, scope, driver, edit_id: int) -> None:
        self.scope, self.driver, self.edit_id = scope, driver, edit_id
        self._point = None

    def observe(self, target, index):
        target = self.scope.switch(target)
        state, path = self.driver.observe(target, index)
        rect = _physical_rect(self.edit_id)
        bounds = state["window_bounds"]
        self._point = [
            ((rect.left + rect.right) / 2 - bounds["x"]) / bounds["width"],
            ((rect.top + rect.bottom) / 2 - bounds["y"]) / bounds["height"],
        ]
        if not all(0 <= item <= 1 for item in self._point):
            raise RuntimeError("Fixture Edit is not within the Cua-bound window")
        return target, state, path

    def context(self, target, state, backend):
        context = self.scope.context(target)
        context['execution_scope'] = 'cua_window'
        context["capabilities"] = backend.capabilities(state)
        context["probe_fixture"] = {"input_center": list(self._point)}
        return context


def _native_call(model: str, action: dict[str, Any]) -> str:
    tool = "computer_use" if model == "gui-owl-2b" else "mobile_use"
    return "<tool_call>" + json.dumps({"name": tool, "arguments": action}) + "</tool_call>"


def _canned_predictor(edit_id: int, backend_name: str):
    """Decode real GUI-Owl/MAI wire text, but never call a model service."""
    from trace2task.local_gui_protocol import decode

    def predict(_task, *, model, history, context, **_kwargs):
        if context == {"purpose": "verify_completion"}:
            actual = _fixture_text(edit_id)
            if actual == FIXTURE_TEXT:
                return {"status": "reviewed", "verification": {
                    "verdict": "complete", "evidence": "Disposable Edit contains the exact fixture text.",
                    "missing": "",
                }}
            return {"status": "reviewed", "verification": {
                "verdict": "incomplete", "evidence": f"Disposable Edit contains {actual!r}.",
                "missing": "The fixture text is not visible in the owned Edit control.",
            }}
        point = context["probe_fixture"]["input_center"]
        scale = 1000 if model == "gui-owl-2b" else 999
        click_name = "left_click" if model == "gui-owl-2b" else "click"
        if not history:
            action = {"action": click_name, "coordinate": [point[0] * scale, point[1] * scale]}
        elif len(history) == 1:
            action = {"action": "type", "text": FIXTURE_TEXT}
            if backend_name == "cua":
                action["coordinate"] = [point[0] * scale, point[1] * scale]
        else:
            action = {"action": "terminate", "status": "success"}
        wire = _native_call(model, action)
        return {
            "status": "predicted",
            "raw_output": wire,
            "prediction": decode(model, wire, cua=backend_name == "cua"),
            "protocol_normalizations": ["native_canned_tool_call_decoded"],
        }

    return predict


def _restore_foreground(native, previous_handle: int, previous_window, fixture_handle: int, record) -> None:
    """Restore only the exact prior window if no third party changed focus."""
    current_handle = native.foreground_handle()
    if previous_window is None:
        record("foreground_restore_skipped", reason="no_previous_window")
        return
    if current_handle == previous_handle:
        record("foreground_restore_skipped", reason="previous_already_foreground")
        return
    if current_handle != fixture_handle:
        record("foreground_restore_skipped", reason="foreground_changed_by_other_process",
               current=current_handle, fixture=fixture_handle)
        return
    current = native.get_window(previous_handle)
    if (current is not None and current.process_id == previous_window.process_id
            and not current.is_minimized):
        restored = native.focus_window(previous_handle)
        record("foreground_restore", handle=previous_handle, success=bool(restored),
               actual_foreground=native.foreground_handle())
    else:
        record("foreground_restore_skipped", reason="previous_window_changed_or_minimized",
               previous=previous_handle)


def _run_one(root: Path, *, backend_name: str, model: str) -> dict[str, Any]:
    from trace2task.cua_backend import CuaBackend
    from trace2task.cua_execution import CuaExecutionBackend
    from trace2task.cua_scope import CuaScope
    from trace2task.desktop_runner import DesktopSession
    from trace2task.execution_core import ExecutionCore
    from trace2task.local_agent_loop import run_local_agent_loop
    from trace2task.win32_execution import Win32ExecutionBackend
    from trace2task.windows_capture import GdiWindowCapture
    from trace2task.windows_control import Win32Backend, WindowsMotorExecutor, physical_dpi_context

    run_root = root / f"{backend_name}-{model}"
    run_root.mkdir(parents=True, exist_ok=False)
    trace = run_root / "trace.jsonl"
    records: list[dict[str, Any]] = []

    def record(kind: str, **data) -> None:
        row = {"type": kind, "time": time.time(), **data}
        records.append(row)
        with trace.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")

    fixture_command = [sys.executable, str(Path(__file__).resolve()), "--fixture"]
    if backend_name == "win32":
        fixture_command.append("--activate-fixture")
    child = subprocess.Popen(
        fixture_command, stdout=subprocess.PIPE,
        text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    driver = None
    native = None
    previous_handle = 0
    previous_window = None
    result: dict[str, Any] = {
        "probe": "owned_disposable_edit", "backend": backend_name, "model": model,
        "fixture_text": FIXTURE_TEXT, "actions": 0, "history": [], "task_complete": False,
        "verified": False, "stop_reason": "action_limit", "trace_path": str(trace),
        # Keep error-before-audit diagnostics writable as well (for example a
        # missing Cua executable or a denied foreground focus request).
        "performance": {},
    }
    stop = _Stop()
    started = time.perf_counter()
    try:
        line = child.stdout.readline() if child.stdout else ""
        fixture = json.loads(line)
        target = {"pid": fixture["pid"], "window_id": fixture["window_id"]}
        edit_id = fixture["edit_id"]
        predictor = _canned_predictor(edit_id, backend_name)
        audit = _Audit(run_root, result, record)

        if backend_name == "cua":
            driver = CuaBackend(run_root, record)
            driver.start()
            scope = CuaScope(driver, {"targets": [target], "initial_index": 0})
            initial_target = scope.start()
            observer = _CuaObserver(scope, driver, edit_id)
            core = ExecutionCore(CuaExecutionBackend(driver, scope), stop, record, max_actions=3)
            record("probe_scope", target=initial_target, policy="owned_fixture_only")
        else:
            native = Win32Backend()
            previous_handle = native.foreground_handle()
            previous_window = native.get_window(previous_handle) if previous_handle else None
            if not native.focus_window(fixture["window_id"]):
                raise RuntimeError("Windows refused to focus the owned fixture; no input sent")
            if native.foreground_handle() != fixture["window_id"]:
                raise RuntimeError("Owned fixture is not foreground after focus request; no input sent")
            capture = GdiWindowCapture(desktop=True)

            def screen_size():
                with physical_dpi_context(capture.user32):
                    return capture.user32.GetSystemMetrics(0), capture.user32.GetSystemMetrics(1)

            session = DesktopSession(native, screen_size)
            motor = WindowsMotorExecutor(session, sleeper=stop.sleep)
            observer = _Win32Observer(session, capture, run_root, edit_id, fixture["window_id"])
            core = ExecutionCore(
                Win32ExecutionBackend(motor, foreground_handle=native.foreground_handle, screen_size=screen_size),
                stop, record, max_actions=3,
            )
            initial_target = None
            record("probe_foreground", fixture=fixture["window_id"], previous=previous_handle,
                   policy="focus_owned_fixture_or_fail_closed")

        run_local_agent_loop(
            observer=observer, initial_target=initial_target, core=core, audit=audit,
            predictor=predictor, instruction="Type the fixed fixture text into the owned disposable Edit control.",
            model=model, root=run_root, result=result, record=record, stop=stop,
            status_callback=lambda message: record("status", message=message), max_actions=3,
            continuous=True,
        )
        result["fixture_observed_text"] = _fixture_text(edit_id)
        result["fixture_text_matches"] = result["fixture_observed_text"] == FIXTURE_TEXT
        if not result["fixture_text_matches"]:
            result.setdefault("error", "Fixture text did not match; no retry was attempted")
    except Exception as error:  # noqa: BLE001 - persist exact native failure and do not retry it
        result.update(stop_reason="error", error=f"{type(error).__name__}: {error}")
        record("error", error=result["error"], note="No automatic retry after uncertain native input")
    finally:
        if native is not None:
            _restore_foreground(native, previous_handle, previous_window, fixture["window_id"], record)
        if driver is not None:
            driver.close()
        if child.poll() is None:
            child.terminate()  # Only the fixture process started by this invocation.
            child.wait(timeout=5)
        result["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        result["performance"]["total_elapsed_ms"] = round(result["elapsed_seconds"] * 1000, 2)
        (run_root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", action="store_true", help="Internal: run only the disposable child fixture")
    parser.add_argument("--activate-fixture", action="store_true", help="Internal: focus only the child-owned fixture")
    parser.add_argument("--output", type=Path, help="New output directory; required unless --fixture")
    parser.add_argument("--backend", choices=("win32", "cua", "all"), default="all")
    parser.add_argument("--model", choices=("gui-owl-2b", "mai-ui-2b", "all"), default="all")
    args = parser.parse_args()
    if args.fixture:
        _fixture_process(activate=args.activate_fixture)
        return 0
    if args.output is None:
        parser.error("--output is required")
    if args.output.exists():
        parser.error("--output must not already exist, so this probe never overwrites prior evidence")
    args.output.mkdir(parents=True)
    backends = ("win32", "cua") if args.backend == "all" else (args.backend,)
    models = ("gui-owl-2b", "mai-ui-2b") if args.model == "all" else (args.model,)
    results = [_run_one(args.output, backend_name=backend, model=model)
               for backend in backends for model in models]
    summary = {"probe": "owned_disposable_edit", "results": results}
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary_path": str(args.output / "summary.json"), "cases": [
        {key: item.get(key) for key in (
            "backend", "model", "actions", "task_complete", "fixture_text_matches",
            "stop_reason", "error")}
        for item in results]}, ensure_ascii=False, indent=2))
    return 0 if all(item.get("fixture_text_matches") and item.get("task_complete") for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
