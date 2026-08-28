from __future__ import annotations

import ctypes
import os
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

import pygame

from trace2task.actions import ActionCall
from trace2task.codex_app_server import (
    DEFAULT_CODEX_MODEL,
    DEFAULT_CODEX_REASONING_EFFORT,
)
from trace2task.recording import TraceWriter, make_run_dir
from trace2task.windows_agent import CodexWindowsAgent, WindowsAgentPlan
from trace2task.windows_capture import GdiWindowCapture, WindowFrameCapture
from trace2task.windows_control import (
    Win32Backend,
    WindowsBackend,
    WindowSession,
    WindowsMotorExecutor,
)
from trace2task.windows_task import WindowsTaskContract, load_windows_task

WM_HOTKEY = 0x0312
PM_REMOVE = 0x0001
MOD_NOREPEAT = 0x4000
EMERGENCY_HOTKEY_ID = 0x545204
VK_F9 = 0x78


class EmergencyStopRequested(RuntimeError):
    """Raised when the user presses the execution emergency-stop hotkey."""


class EmergencyStop(Protocol):
    def start(self) -> None: ...

    def raise_if_requested(self) -> None: ...

    def sleep(self, seconds: float) -> None: ...

    def close(self) -> None: ...


class WindowsPlanningAgent(Protocol):
    replans: int

    def plan(self, surface: pygame.Surface) -> WindowsAgentPlan: ...

    def observe_transition(self, action: ActionCall, applied: bool) -> None: ...

    def close(self) -> None: ...


class Win32EmergencyStop:
    """Reserve F9 and make executor sleeps interruptible in short polling slices."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        if os.name != "nt":
            raise RuntimeError("The Windows emergency stop is available only on Windows")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.RegisterHotKey.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self.user32.RegisterHotKey.restype = wintypes.BOOL
        self.user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.UnregisterHotKey.restype = wintypes.BOOL
        self.user32.PeekMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self.user32.PeekMessageW.restype = wintypes.BOOL
        self.clock = clock
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        if not self.user32.RegisterHotKey(
            None,
            EMERGENCY_HOTKEY_ID,
            MOD_NOREPEAT,
            VK_F9,
        ):
            raise RuntimeError("Could not reserve F9 as the Windows Agent emergency stop")
        self._started = True

    def raise_if_requested(self) -> None:
        if not self._started:
            raise RuntimeError("Emergency stop must be started before polling")
        message = wintypes.MSG()
        while self.user32.PeekMessageW(
            ctypes.byref(message),
            None,
            WM_HOTKEY,
            WM_HOTKEY,
            PM_REMOVE,
        ):
            if message.wParam == EMERGENCY_HOTKEY_ID:
                raise EmergencyStopRequested("F9 emergency stop requested")

    def sleep(self, seconds: float) -> None:
        deadline = self.clock() + max(0.0, seconds)
        while True:
            self.raise_if_requested()
            remaining = deadline - self.clock()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 0.05))

    def close(self) -> None:
        if not self._started:
            return
        self.user32.UnregisterHotKey(None, EMERGENCY_HOTKEY_ID)
        self._started = False


@dataclass(frozen=True)
class WindowsAgentResult:
    mode: str
    task_id: str
    execute: bool
    task_complete: bool
    executed_actions: int
    replans: int
    stop_reason: str
    proposed_actions: list[dict[str, object]]
    trace_path: str | None
    input_mode: str
    model: str | None
    reasoning_effort: str
    planning_ms: float


AgentFactory = Callable[[WindowsTaskContract], WindowsPlanningAgent]


def run_windows_agent(
    task_path: Path,
    *,
    instruction: str | None = None,
    execute: bool = False,
    model: str | None = DEFAULT_CODEX_MODEL,
    reasoning_effort: str = DEFAULT_CODEX_REASONING_EFFORT,
    codex_bin: str = "codex",
    plan_horizon: int = 4,
    max_actions: int | None = None,
    output_root: Path = Path("runs"),
    backend: WindowsBackend | None = None,
    capture: WindowFrameCapture | None = None,
    emergency_stop: EmergencyStop | None = None,
    agent_factory: AgentFactory | None = None,
    background: bool = False,
    focus: bool = False,
    status_callback: Callable[[str], None] = print,
) -> WindowsAgentResult:
    """Plan from a target window; inject input only with --execute and a confirmed pack."""

    contract = load_windows_task(task_path)
    if instruction is not None:
        contract = contract.with_instruction(instruction)
    if execute and contract.task.requires_confirmation:
        raise RuntimeError(
            "This Windows task pack is still a draft. Review and confirm it before --execute."
        )
    if background and focus:
        raise ValueError("--focus cannot be combined with --background")
    action_limit = max_actions if max_actions is not None else contract.task.max_actions
    if action_limit <= 0:
        raise ValueError("max_actions must be positive")
    active_backend = backend or Win32Backend()
    active_capture = capture or GdiWindowCapture()
    session = WindowSession(contract.selector, active_backend)
    agent = (
        agent_factory(contract)
        if agent_factory is not None
        else CodexWindowsAgent(
            contract,
            model=model,
            reasoning_effort=reasoning_effort,
            codex_bin=codex_bin,
            plan_horizon=plan_horizon,
            background=background,
        )
    )

    if not execute:
        try:
            window = session.focus(timeout_seconds=10) if focus else session.resolve()
            if not window.is_visible or window.is_minimized:
                raise RuntimeError("Windows Agent target must be visible and unminimized")
            status_callback("Requesting a multimodal plan; this can take up to 120 seconds...")
            planning_started = time.perf_counter()
            plan = agent.plan(active_capture.capture(window))
            planning_ms = (time.perf_counter() - planning_started) * 1000
            proposed = ", ".join(action.skill for action in plan.actions) or "complete"
            status_callback(
                f"Plan received in {planning_ms / 1000:.1f}s: {proposed} "
                f"(confidence {plan.confidence:.2f})."
            )
            return WindowsAgentResult(
                mode="windows_agent_dry_run",
                task_id=contract.task.task_id,
                execute=False,
                task_complete=plan.task_complete,
                executed_actions=0,
                replans=agent.replans,
                stop_reason="model_complete" if plan.task_complete else "dry_run_plan_only",
                proposed_actions=[action.to_payload() for action in plan.actions],
                trace_path=None,
                input_mode="background" if background else "foreground",
                model=model,
                reasoning_effort=reasoning_effort,
                planning_ms=planning_ms,
            )
        finally:
            agent.close()

    active_emergency = emergency_stop or Win32EmergencyStop()
    writer: TraceWriter | None = None
    executed_actions = 0
    task_complete = False
    stop_reason = "action_limit"
    last_proposed: list[dict[str, object]] = []
    total_planning_ms = 0.0
    executor = WindowsMotorExecutor(
        session,
        sleeper=active_emergency.sleep,
        background=background,
    )
    try:
        active_emergency.start()
        active_emergency.raise_if_requested()
        window = (
            session.require_available()
            if background
            else session.focus(timeout_seconds=10)
        )
        surface = active_capture.capture(window)
        writer = TraceWriter(
            make_run_dir(output_root, "windows-agent"),
            task_id=contract.task.task_id,
            seed=0,
            source="codex_windows_agent",
        )
        writer.record(
            "start",
            surface,
            details={
                "window": asdict(window),
                "task_path": str(contract.task.source_path),
                "instruction": contract.instruction,
                "demonstration_instruction": contract.task.instruction,
                "emergency_hotkey": "f9",
                "input_mode": "background" if background else "foreground",
                "model": model,
                "reasoning_effort": reasoning_effort,
            },
        )
        status_callback(
            "Execution started. Keep the target game in the foreground; press F9 to stop."
        )
        while executed_actions < action_limit:
            active_emergency.raise_if_requested()
            window = session.require_available() if background else session.require_foreground()
            surface = active_capture.capture(window)
            status_callback(
                f"[plan {agent.replans + 1}] Requesting a multimodal decision..."
            )
            planning_started = time.perf_counter()
            plan = agent.plan(surface)
            planning_ms = (time.perf_counter() - planning_started) * 1000
            total_planning_ms += planning_ms
            last_proposed = [action.to_payload() for action in plan.actions]
            proposed = ", ".join(action.skill for action in plan.actions) or "complete"
            status_callback(
                f"[plan {agent.replans}] Received in {planning_ms / 1000:.1f}s: {proposed} "
                f"(confidence {plan.confidence:.2f})."
            )
            if plan.task_complete:
                writer.record(
                    "success_marker",
                    surface,
                    details={
                        "verifier": "model_reference_comparison",
                        "reason": plan.reason,
                        "confidence": plan.confidence,
                        "planning_ms": planning_ms,
                    },
                )
                task_complete = True
                stop_reason = "model_complete"
                break

            for action in plan.actions:
                if executed_actions >= action_limit:
                    break
                active_emergency.raise_if_requested()
                status_callback(
                    f"[action {executed_actions + 1}/{action_limit}] Executing {action.skill}..."
                )
                try:
                    motor_result = executor.execute(action)
                except Exception:
                    agent.observe_transition(action, False)
                    raise
                agent.observe_transition(action, True)
                executed_actions += 1
                status_callback(
                    f"[action {executed_actions}/{action_limit}] {action.skill} completed "
                    f"in {motor_result.elapsed_ms:.0f}ms."
                )
                window = (
                    session.require_available() if background else session.require_foreground()
                )
                surface = active_capture.capture(window)
                writer.record(
                    "windows_action",
                    surface,
                    details={
                        "parameterized_action": action.to_payload(),
                        "motor_result": asdict(motor_result),
                        "model_reason": plan.reason,
                        "model_confidence": plan.confidence,
                        "planning_ms": planning_ms,
                    },
                )
        if executed_actions >= action_limit and not task_complete:
            stop_reason = "action_limit"
    except EmergencyStopRequested:
        stop_reason = "emergency_stop"
    except Exception as error:
        stop_reason = f"failed:{type(error).__name__}"
        status_callback(f"Execution stopped: {type(error).__name__}: {error}")
        if writer is not None:
            writer.finish(
                success=False,
                extra={
                    "parameterized_action_count": executed_actions,
                    "replans": agent.replans,
                    "stop_reason": stop_reason,
                    "failure_message": str(error),
                    "planning_ms": total_planning_ms,
                },
            )
            writer = None
        raise
    finally:
        active_emergency.close()
        agent.close()

    if writer is None and stop_reason == "emergency_stop":
        return WindowsAgentResult(
            mode="windows_agent",
            task_id=contract.task.task_id,
            execute=True,
            task_complete=False,
            executed_actions=0,
            replans=agent.replans,
            stop_reason=stop_reason,
            proposed_actions=[],
            trace_path=None,
            input_mode="background" if background else "foreground",
            model=model,
            reasoning_effort=reasoning_effort,
            planning_ms=total_planning_ms,
        )
    if writer is None:
        raise RuntimeError("Windows Agent stopped before its execution trace was created")
    trace = writer.finish(
        success=task_complete,
        extra={
            "parameterized_action_count": executed_actions,
            "replans": agent.replans,
            "stop_reason": stop_reason,
            "verification": "model_reference_comparison",
            "planning_ms": total_planning_ms,
        },
    )
    return WindowsAgentResult(
        mode="windows_agent",
        task_id=contract.task.task_id,
        execute=True,
        task_complete=task_complete,
        executed_actions=executed_actions,
        replans=agent.replans,
        stop_reason=stop_reason,
        proposed_actions=last_proposed,
        trace_path=str(trace.trace_path),
        input_mode="background" if background else "foreground",
        model=model,
        reasoning_effort=reasoning_effort,
        planning_ms=total_planning_ms,
    )
