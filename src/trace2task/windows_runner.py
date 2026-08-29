from __future__ import annotations

import ctypes
import os
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import pygame

from trace2task.actions import ActionCall
from trace2task.codex_app_server import (
    DEFAULT_CODEX_MODEL,
    DEFAULT_CODEX_REASONING_EFFORT,
)
from trace2task.recording import TraceWriter, make_run_dir
from trace2task.windows_agent import (
    WINDOWS_DECISION_TIMEOUT_SECONDS,
    CodexWindowsAgent,
    WindowsAgentPlan,
)
from trace2task.windows_capture import GdiWindowCapture, WindowFrameCapture
from trace2task.windows_control import (
    Win32Backend,
    WindowInfo,
    WindowSafetyError,
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
VISUAL_STABILITY_SAMPLE_MS = 200
VISUAL_STABILITY_MAX_EXTRA_MS = 4_000
VISUAL_STABILITY_THRESHOLD = 0.012
VISUAL_NO_RESPONSE_THRESHOLD = 0.004
VISUAL_RESPONSE_SKILLS = {"click", "double_click", "hold_mouse", "drag"}
LOCAL_WAIT_UNTIL_MAX_EXTRA_MS = 30_000
LOCAL_WAIT_UNTIL_STABLE_SAMPLES = 8
CYCLE_REFERENCE_MATCH_THRESHOLD = 0.08


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
class VisualCheckpointResult:
    stable: bool
    response_changed: bool | None
    stability_difference: float
    response_difference: float | None
    extra_wait_ms: int
    samples: int
    capture_ms: float


def _action_region(action: ActionCall, size: tuple[int, int]) -> pygame.Rect | None:
    if action.skill not in VISUAL_RESPONSE_SKILLS:
        return None
    width, height = size
    if action.skill == "drag":
        points = (
            (action.args["start_x"], action.args["start_y"]),
            (action.args["end_x"], action.args["end_y"]),
        )
        padding_x = width * 0.08
        padding_y = height * 0.08
        left = min(x for x, _ in points) * width - padding_x
        right = max(x for x, _ in points) * width + padding_x
        top = min(y for _, y in points) * height - padding_y
        bottom = max(y for _, y in points) * height + padding_y
    else:
        center_x = action.args["x"] * width
        center_y = action.args["y"] * height
        padding_x = width * 0.12
        padding_y = height * 0.12
        left, right = center_x - padding_x, center_x + padding_x
        top, bottom = center_y - padding_y, center_y + padding_y
    return pygame.Rect(
        max(0, round(left)),
        max(0, round(top)),
        max(1, round(min(width, right) - max(0, left))),
        max(1, round(min(height, bottom) - max(0, top))),
    )


def _surface_difference(
    before: pygame.Surface,
    after: pygame.Surface,
    *,
    region: pygame.Rect | None = None,
) -> float:
    if before.get_size() != after.get_size():
        return 1.0
    before_view = before.subsurface(region) if region is not None else before
    after_view = after.subsurface(region) if region is not None else after
    size = (64, 36)
    before_pixels = pygame.surfarray.array3d(
        pygame.transform.smoothscale(before_view, size)
    ).astype(np.int16)
    after_pixels = pygame.surfarray.array3d(
        pygame.transform.smoothscale(after_view, size)
    ).astype(np.int16)
    return round(float(np.mean(np.abs(before_pixels - after_pixels))) / 255, 6)


def _capture_with_timing(
    capture: WindowFrameCapture,
    window: WindowInfo,
) -> tuple[pygame.Surface, float]:
    started = time.perf_counter()
    surface = capture.capture(window)
    return surface, (time.perf_counter() - started) * 1000


def _wait_for_visual_stability(
    *,
    session: WindowSession,
    capture: WindowFrameCapture,
    emergency_stop: EmergencyStop,
    background: bool,
    initial_surface: pygame.Surface,
    response_baseline: pygame.Surface | None,
    response_action: ActionCall | None,
    max_extra_wait_ms: int = VISUAL_STABILITY_MAX_EXTRA_MS,
    stable_samples_required: int = 2,
) -> tuple[pygame.Surface, VisualCheckpointResult]:
    previous = initial_surface
    latest = initial_surface
    stable_samples = 0
    extra_wait_ms = 0
    sample_count = 0
    capture_ms = 0.0
    stability_difference = 1.0
    max_samples = max_extra_wait_ms // VISUAL_STABILITY_SAMPLE_MS
    for sample_index in range(max_samples + 1):
        emergency_stop.raise_if_requested()
        window = session.require_available() if background else session.require_foreground()
        latest, sample_capture_ms = _capture_with_timing(capture, window)
        capture_ms += sample_capture_ms
        sample_count += 1
        stability_difference = _surface_difference(previous, latest)
        stable_samples = (
            stable_samples + 1
            if stability_difference <= VISUAL_STABILITY_THRESHOLD
            else 0
        )
        if stable_samples >= stable_samples_required:
            break
        previous = latest
        if sample_index < max_samples:
            emergency_stop.sleep(VISUAL_STABILITY_SAMPLE_MS / 1_000)
            extra_wait_ms += VISUAL_STABILITY_SAMPLE_MS

    response_difference: float | None = None
    response_changed: bool | None = None
    if response_baseline is not None and response_action is not None:
        region = _action_region(response_action, response_baseline.get_size())
        if region is not None:
            response_difference = _surface_difference(
                response_baseline,
                latest,
                region=region,
            )
            response_changed = response_difference > VISUAL_NO_RESPONSE_THRESHOLD
    return latest, VisualCheckpointResult(
        stable=stable_samples >= stable_samples_required,
        response_changed=response_changed,
        stability_difference=stability_difference,
        response_difference=response_difference,
        extra_wait_ms=extra_wait_ms,
        samples=sample_count,
        capture_ms=capture_ms,
    )


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
    batch_count: int
    planned_actions: int
    interrupted_batches: int
    average_batch_size: float
    max_batch_size: int
    visual_checkpoints: int
    visual_checkpoint_failures: int
    visual_stability_wait_ms: int
    local_wait_until_count: int
    local_wait_until_ms: int
    wait_only_plans: int
    short_batch_count: int
    session_resets: int
    performance: dict[str, object]
    stage_timings: list[dict[str, object]]


AgentFactory = Callable[[WindowsTaskContract], WindowsPlanningAgent]


def run_windows_agent(
    task_path: Path,
    *,
    instruction: str | None = None,
    execute: bool = False,
    model: str | None = DEFAULT_CODEX_MODEL,
    reasoning_effort: str = DEFAULT_CODEX_REASONING_EFFORT,
    codex_bin: str = "codex",
    plan_horizon: int = 12,
    max_actions: int | None = None,
    max_batch_recoveries: int = 3,
    output_root: Path = Path("runs"),
    backend: WindowsBackend | None = None,
    capture: WindowFrameCapture | None = None,
    emergency_stop: EmergencyStop | None = None,
    agent_factory: AgentFactory | None = None,
    background: bool = False,
    adaptive_reasoning: bool = True,
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
    if max_batch_recoveries <= 0:
        raise ValueError("max_batch_recoveries must be positive")
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
            adaptive_reasoning=adaptive_reasoning,
        )
    )

    if not execute:
        try:
            dry_run_started = time.perf_counter()
            window = session.focus(timeout_seconds=10) if focus else session.resolve()
            if not window.is_visible or window.is_minimized:
                raise RuntimeError("Windows Agent target must be visible and unminimized")
            status_callback(
                "Requesting a multimodal plan. Network interruptions are retried "
                f"automatically for up to {WINDOWS_DECISION_TIMEOUT_SECONDS:g} seconds..."
            )
            surface, capture_ms = _capture_with_timing(active_capture, window)
            planning_started = time.perf_counter()
            plan = agent.plan(surface)
            planning_ms = (time.perf_counter() - planning_started) * 1000
            performance = {
                "total_elapsed_ms": round((time.perf_counter() - dry_run_started) * 1000, 3),
                "capture_ms": round(capture_ms, 3),
                "planning_ms": round(planning_ms, 3),
                "frame_encode_ms": round(plan.timing.frame_encode_ms, 3),
                "prompt_build_ms": round(plan.timing.prompt_build_ms, 3),
                "model_roundtrip_ms": round(plan.timing.model_roundtrip_ms, 3),
                "request_ack_ms": round(plan.timing.request_ack_ms, 3),
                "model_completion_wait_ms": round(
                    plan.timing.model_completion_wait_ms, 3
                ),
                "parse_ms": round(plan.timing.parse_ms, 3),
                "explicit_wait_ms": 0.0,
                "local_wait_until_ms": 0,
                "action_ms": 0.0,
            }
            stage_timings = [
                {
                    "stage_id": plan.stage_id,
                    "plans": 1,
                    "batches": 1 if plan.actions else 0,
                    "planned_actions": len(plan.actions),
                    "executed_actions": 0,
                    "capture_ms": round(capture_ms, 3),
                    "planning_ms": round(planning_ms, 3),
                    "model_roundtrip_ms": round(plan.timing.model_roundtrip_ms, 3),
                    "explicit_wait_ms": 0.0,
                    "local_wait_until_ms": 0,
                    "action_ms": 0.0,
                }
            ]
            proposed = ", ".join(action.skill for action in plan.actions) or "complete"
            status_callback(
                f"Plan received in {planning_ms / 1000:.1f}s: {proposed} "
                f"(confidence {plan.confidence:.2f}, {plan.model or model} / "
                f"{plan.reasoning_effort})."
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
                batch_count=1 if plan.actions else 0,
                planned_actions=len(plan.actions),
                interrupted_batches=0,
                average_batch_size=float(len(plan.actions)),
                max_batch_size=len(plan.actions),
                visual_checkpoints=0,
                visual_checkpoint_failures=0,
                visual_stability_wait_ms=0,
                local_wait_until_count=0,
                local_wait_until_ms=0,
                wait_only_plans=int(
                    len(plan.actions) == 1 and plan.actions[0].skill == "wait"
                ),
                short_batch_count=int(0 < len(plan.actions) < 5),
                session_resets=getattr(agent, "session_resets", 0),
                performance=performance,
                stage_timings=stage_timings,
            )
        finally:
            agent.close()

    active_emergency = emergency_stop or Win32EmergencyStop()
    run_started = time.perf_counter()
    writer: TraceWriter | None = None
    executed_actions = 0
    task_complete = False
    stop_reason = "action_limit"
    last_proposed: list[dict[str, object]] = []
    total_planning_ms = 0.0
    batch_count = 0
    planned_actions = 0
    interrupted_batches = 0
    consecutive_batch_interruptions = 0
    max_batch_size = 0
    visual_checkpoints = 0
    visual_checkpoint_failures = 0
    visual_stability_wait_ms = 0
    local_wait_until_count = 0
    local_wait_until_ms = 0
    wait_only_plans = 0
    short_batch_count = 0
    total_capture_ms = 0.0
    total_frame_encode_ms = 0.0
    total_prompt_build_ms = 0.0
    total_model_roundtrip_ms = 0.0
    total_request_ack_ms = 0.0
    total_model_completion_wait_ms = 0.0
    total_parse_ms = 0.0
    total_explicit_wait_ms = 0.0
    total_action_ms = 0.0
    stage_performance: dict[str, dict[str, float | int | str]] = {}
    cycle_started_at_reference = False
    cycle_departed = True
    completion_rejections = 0

    def stage_bucket(stage_id: str) -> dict[str, float | int | str]:
        return stage_performance.setdefault(
            stage_id,
            {
                "stage_id": stage_id,
                "plans": 0,
                "batches": 0,
                "planned_actions": 0,
                "executed_actions": 0,
                "capture_ms": 0.0,
                "planning_ms": 0.0,
                "model_roundtrip_ms": 0.0,
                "explicit_wait_ms": 0.0,
                "local_wait_until_ms": 0,
                "action_ms": 0.0,
            },
        )

    def performance_snapshot() -> tuple[dict[str, object], list[dict[str, object]]]:
        performance: dict[str, object] = {
            "total_elapsed_ms": round((time.perf_counter() - run_started) * 1000, 3),
            "capture_ms": round(total_capture_ms, 3),
            "planning_ms": round(total_planning_ms, 3),
            "frame_encode_ms": round(total_frame_encode_ms, 3),
            "prompt_build_ms": round(total_prompt_build_ms, 3),
            "model_roundtrip_ms": round(total_model_roundtrip_ms, 3),
            "request_ack_ms": round(total_request_ack_ms, 3),
            "model_completion_wait_ms": round(total_model_completion_wait_ms, 3),
            "parse_ms": round(total_parse_ms, 3),
            "explicit_wait_ms": round(total_explicit_wait_ms, 3),
            "local_wait_until_ms": local_wait_until_ms,
            "action_ms": round(total_action_ms, 3),
        }
        stages: list[dict[str, object]] = []
        for raw_stage in stage_performance.values():
            stage = dict(raw_stage)
            for key in (
                "capture_ms",
                "planning_ms",
                "model_roundtrip_ms",
                "explicit_wait_ms",
                "action_ms",
            ):
                stage[key] = round(float(stage[key]), 3)
            stages.append(stage)
        return performance, stages
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
        surface, initial_capture_ms = _capture_with_timing(active_capture, window)
        total_capture_ms += initial_capture_ms
        reference_difference: float | None = None
        reference_surface: pygame.Surface | None = None
        if (
            contract.task.completion_mode == "cycle"
            and contract.task.require_departure_from_reference
        ):
            reference_surface = pygame.image.load(contract.reference_frame)
            if reference_surface.get_size() != surface.get_size():
                reference_surface = pygame.transform.smoothscale(
                    reference_surface, surface.get_size()
                )
            reference_difference = _surface_difference(surface, reference_surface)
            cycle_started_at_reference = (
                reference_difference <= CYCLE_REFERENCE_MATCH_THRESHOLD
            )
            cycle_departed = not cycle_started_at_reference

        def observe_cycle_progress(current: pygame.Surface) -> None:
            nonlocal cycle_departed
            if cycle_departed or not cycle_started_at_reference or reference_surface is None:
                return
            if (
                _surface_difference(current, reference_surface)
                > CYCLE_REFERENCE_MATCH_THRESHOLD
            ):
                cycle_departed = True
        first_plan_surface: tuple[pygame.Surface, float] | None = (
            surface,
            initial_capture_ms,
        )
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
                "adaptive_reasoning": adaptive_reasoning,
                "plan_horizon": plan_horizon,
                "max_batch_recoveries": max_batch_recoveries,
                "completion_policy": {
                    "mode": contract.task.completion_mode,
                    "require_departure_from_reference": (
                        contract.task.require_departure_from_reference
                    ),
                    "started_at_reference": cycle_started_at_reference,
                    "initial_reference_difference": reference_difference,
                },
                "visual_checkpoint_policy": {
                    "stability_sample_ms": VISUAL_STABILITY_SAMPLE_MS,
                    "max_extra_wait_ms": VISUAL_STABILITY_MAX_EXTRA_MS,
                    "stability_threshold": VISUAL_STABILITY_THRESHOLD,
                    "no_response_threshold": VISUAL_NO_RESPONSE_THRESHOLD,
                    "local_wait_until_max_extra_ms": LOCAL_WAIT_UNTIL_MAX_EXTRA_MS,
                    "local_wait_until_stable_samples": LOCAL_WAIT_UNTIL_STABLE_SAMPLES,
                },
            },
        )
        status_callback(
            "Background execution started. You may use other applications; keep the target "
            "visible and unminimized. Press F9 to stop."
            if background
            else "Foreground execution started. Keep the target in the foreground; press F9 to stop."
        )
        while executed_actions < action_limit:
            active_emergency.raise_if_requested()
            if first_plan_surface is not None:
                surface, plan_capture_ms = first_plan_surface
                first_plan_surface = None
            else:
                window = (
                    session.require_available()
                    if background
                    else session.require_foreground()
                )
                surface, plan_capture_ms = _capture_with_timing(active_capture, window)
                total_capture_ms += plan_capture_ms
            status_callback(
                f"[plan {agent.replans + 1}] Requesting a multimodal decision; "
                "Codex network reconnects are allowed to finish..."
            )
            planning_started = time.perf_counter()
            plan = agent.plan(surface)
            planning_ms = (time.perf_counter() - planning_started) * 1000
            total_planning_ms += planning_ms
            plan_timing = plan.timing
            total_frame_encode_ms += plan_timing.frame_encode_ms
            total_prompt_build_ms += plan_timing.prompt_build_ms
            total_model_roundtrip_ms += plan_timing.model_roundtrip_ms
            total_request_ack_ms += plan_timing.request_ack_ms
            total_model_completion_wait_ms += plan_timing.model_completion_wait_ms
            total_parse_ms += plan_timing.parse_ms
            active_stage = stage_bucket(plan.stage_id)
            active_stage["plans"] = int(active_stage["plans"]) + 1
            active_stage["capture_ms"] = float(active_stage["capture_ms"]) + plan_capture_ms
            active_stage["planning_ms"] = float(active_stage["planning_ms"]) + planning_ms
            active_stage["model_roundtrip_ms"] = (
                float(active_stage["model_roundtrip_ms"])
                + plan_timing.model_roundtrip_ms
            )
            last_proposed = [action.to_payload() for action in plan.actions]
            proposed = ", ".join(action.skill for action in plan.actions) or "complete"
            if plan_timing.decision_repair_attempts:
                status_callback(
                    f"[plan {agent.replans}] Same-session recovery succeeded after rejecting "
                    f"{plan_timing.decision_repair_attempts} empty incomplete decision(s); "
                    f"candidate stage {plan_timing.decision_repair_stage_id or 'unknown'}. "
                    "No rejected action was executed."
                )
            status_callback(
                f"[plan {agent.replans}] Received in {planning_ms / 1000:.1f}s: "
                f"{len(plan.actions)}-action stage program ({proposed}) "
                f"(confidence {plan.confidence:.2f}, {plan.model or model} / "
                f"{plan.reasoning_effort}, stage {plan.stage_id})."
            )
            if plan.task_complete:
                if cycle_started_at_reference and not cycle_departed:
                    completion_rejections += 1
                    reason = (
                        "cycle run started at the reference anchor and has not visibly left it"
                    )
                    writer.record(
                        "completion_rejected",
                        surface,
                        details={
                            "reason": reason,
                            "completion_rejections": completion_rejections,
                        },
                    )
                    observer = getattr(agent, "observe_completion_rejected", None)
                    if callable(observer):
                        observer(reason)
                    status_callback(
                        "Completion rejected locally: the cycle has not left its start anchor."
                    )
                    if completion_rejections >= max_batch_recoveries:
                        stop_reason = "cycle_progress_not_observed"
                        break
                    continue
                writer.record(
                    "success_marker",
                    surface,
                    details={
                        "verifier": "model_reference_comparison",
                        "reason": plan.reason,
                        "confidence": plan.confidence,
                        "planning_ms": planning_ms,
                        "stage_id": plan.stage_id,
                        "planning_model": plan.model or model,
                        "planning_reasoning_effort": plan.reasoning_effort,
                        "performance": asdict(plan_timing),
                    },
                )
                task_complete = True
                stop_reason = "model_complete"
                break

            batch_count += 1
            batch_size = len(plan.actions)
            wait_only_plans += int(batch_size == 1 and plan.actions[0].skill == "wait")
            short_batch_count += int(batch_size < 5)
            planned_actions += batch_size
            max_batch_size = max(max_batch_size, batch_size)
            active_stage["batches"] = int(active_stage["batches"]) + 1
            active_stage["planned_actions"] = (
                int(active_stage["planned_actions"]) + batch_size
            )
            batch_interrupted = False
            status_callback(
                f"[batch {batch_count}] Stage {plan.stage_id}: executing {batch_size} actions "
                f"toward {plan.stage_goal or plan.reason}."
            )
            pending_visual_action: ActionCall | None = None
            pending_visual_baseline: pygame.Surface | None = None
            for batch_action_index, action in enumerate(plan.actions, start=1):
                if executed_actions >= action_limit:
                    break
                active_emergency.raise_if_requested()
                status_callback(
                    f"[action {executed_actions + 1}/{action_limit}] Executing {action.skill}..."
                )
                if action.skill in VISUAL_RESPONSE_SKILLS:
                    pending_visual_action = action
                    pending_visual_baseline = surface.copy()
                try:
                    motor_result = executor.execute(action)
                except EmergencyStopRequested:
                    raise
                except WindowSafetyError:
                    agent.observe_transition(action, False)
                    raise
                except Exception as error:
                    agent.observe_transition(action, False)
                    interrupted_batches += 1
                    consecutive_batch_interruptions += 1
                    batch_interrupted = True
                    discarded_actions = batch_size - batch_action_index
                    writer.record(
                        "windows_action_failed",
                        surface,
                        details={
                            "parameterized_action": action.to_payload(),
                            "error": f"{type(error).__name__}: {error}",
                            "batch_index": batch_count,
                            "batch_action_index": batch_action_index,
                            "batch_size": batch_size,
                            "discarded_action_count": discarded_actions,
                            "stage_id": plan.stage_id,
                            "stage_goal": plan.stage_goal,
                            "expected_end_state": plan.expected_end_state,
                            "abort_conditions": list(plan.abort_conditions),
                            "planning_model": plan.model or model,
                            "planning_reasoning_effort": plan.reasoning_effort,
                        },
                    )
                    status_callback(
                        f"[batch {batch_count}] Interrupted at action "
                        f"{batch_action_index}/{batch_size}: {type(error).__name__}: {error}. "
                        f"Discarded {discarded_actions} remaining actions; replanning immediately."
                    )
                    if consecutive_batch_interruptions >= max_batch_recoveries:
                        raise RuntimeError(
                            "Stage batch recovery limit reached after "
                            f"{consecutive_batch_interruptions} consecutive motor failures"
                        ) from error
                    break
                agent.observe_transition(action, True)
                executed_actions += 1
                active_stage["executed_actions"] = (
                    int(active_stage["executed_actions"]) + 1
                )
                if action.skill == "wait":
                    total_explicit_wait_ms += motor_result.elapsed_ms
                    active_stage["explicit_wait_ms"] = (
                        float(active_stage["explicit_wait_ms"])
                        + motor_result.elapsed_ms
                    )
                else:
                    total_action_ms += motor_result.elapsed_ms
                    active_stage["action_ms"] = (
                        float(active_stage["action_ms"]) + motor_result.elapsed_ms
                    )
                status_callback(
                    f"[action {executed_actions}/{action_limit}] {action.skill} completed "
                    f"in {motor_result.elapsed_ms:.0f}ms."
                )
                visual_checkpoint: VisualCheckpointResult | None = None
                local_wait_checkpoint: VisualCheckpointResult | None = None
                if action.skill == "wait":
                    surface, visual_checkpoint = _wait_for_visual_stability(
                        session=session,
                        capture=active_capture,
                        emergency_stop=active_emergency,
                        background=background,
                        initial_surface=surface,
                        response_baseline=pending_visual_baseline,
                        response_action=pending_visual_action,
                    )
                    visual_checkpoints += 1
                    visual_stability_wait_ms += visual_checkpoint.extra_wait_ms
                    total_capture_ms += visual_checkpoint.capture_ms
                    observe_cycle_progress(surface)
                    active_stage["capture_ms"] = (
                        float(active_stage["capture_ms"]) + visual_checkpoint.capture_ms
                    )
                    status_callback(
                        f"[checkpoint {visual_checkpoints}] stable={visual_checkpoint.stable}, "
                        f"changed={visual_checkpoint.response_changed}, "
                        f"extra_wait={visual_checkpoint.extra_wait_ms}ms."
                    )
                    if (
                        batch_action_index == batch_size
                        and visual_checkpoint.response_changed is not False
                    ):
                        status_callback(
                            "[local wait-until] Final wait reached; holding locally for a "
                            "sustained stable frame before the next model call."
                        )
                        surface, local_wait_checkpoint = _wait_for_visual_stability(
                            session=session,
                            capture=active_capture,
                            emergency_stop=active_emergency,
                            background=background,
                            initial_surface=surface,
                            response_baseline=None,
                            response_action=None,
                            max_extra_wait_ms=LOCAL_WAIT_UNTIL_MAX_EXTRA_MS,
                            stable_samples_required=LOCAL_WAIT_UNTIL_STABLE_SAMPLES,
                        )
                        local_wait_until_count += 1
                        local_wait_until_ms += local_wait_checkpoint.extra_wait_ms
                        total_capture_ms += local_wait_checkpoint.capture_ms
                        observe_cycle_progress(surface)
                        active_stage["capture_ms"] = (
                            float(active_stage["capture_ms"])
                            + local_wait_checkpoint.capture_ms
                        )
                        active_stage["local_wait_until_ms"] = (
                            int(active_stage["local_wait_until_ms"])
                            + local_wait_checkpoint.extra_wait_ms
                        )
                        status_callback(
                            "[local wait-until] "
                            f"stable={local_wait_checkpoint.stable}, "
                            f"extra_wait={local_wait_checkpoint.extra_wait_ms}ms."
                        )
                else:
                    window = (
                        session.require_available()
                        if background
                        else session.require_foreground()
                    )
                    surface, action_capture_ms = _capture_with_timing(
                        active_capture,
                        window,
                    )
                    total_capture_ms += action_capture_ms
                    observe_cycle_progress(surface)
                    active_stage["capture_ms"] = (
                        float(active_stage["capture_ms"]) + action_capture_ms
                    )
                writer.record(
                    "windows_action",
                    surface,
                    details={
                        "parameterized_action": action.to_payload(),
                        "motor_result": asdict(motor_result),
                        "model_reason": plan.reason,
                        "model_confidence": plan.confidence,
                        "planning_ms": planning_ms,
                        "stage_id": plan.stage_id,
                        "planning_model": plan.model or model,
                        "planning_reasoning_effort": plan.reasoning_effort,
                        "performance": asdict(plan_timing),
                        "batch_index": batch_count,
                        "batch_action_index": batch_action_index,
                        "batch_size": batch_size,
                        "stage_goal": plan.stage_goal,
                        "expected_end_state": plan.expected_end_state,
                        "abort_conditions": list(plan.abort_conditions),
                        "visual_checkpoint": asdict(visual_checkpoint)
                        if visual_checkpoint is not None
                        else None,
                        "local_wait_until": asdict(local_wait_checkpoint)
                        if local_wait_checkpoint is not None
                        else None,
                    },
                )
                if visual_checkpoint is not None:
                    failure_reason: str | None = None
                    if visual_checkpoint.response_changed is False:
                        failure_reason = "expected visual response did not appear"
                    elif local_wait_checkpoint is not None and not local_wait_checkpoint.stable:
                        failure_reason = "local wait-until did not stabilize before timeout"
                    elif local_wait_checkpoint is None and not visual_checkpoint.stable:
                        failure_reason = "visual state did not stabilize before timeout"
                    if failure_reason is not None:
                        failed_action = pending_visual_action or action
                        agent.observe_transition(failed_action, False)
                        interrupted_batches += 1
                        visual_checkpoint_failures += 1
                        consecutive_batch_interruptions += 1
                        batch_interrupted = True
                        discarded_actions = batch_size - batch_action_index
                        writer.record(
                            "windows_visual_checkpoint_failed",
                            surface,
                            details={
                                "failed_action": failed_action.to_payload(),
                                "reason": failure_reason,
                                "visual_checkpoint": asdict(visual_checkpoint),
                                "batch_index": batch_count,
                                "batch_action_index": batch_action_index,
                                "batch_size": batch_size,
                                "discarded_action_count": discarded_actions,
                                "stage_id": plan.stage_id,
                                "stage_goal": plan.stage_goal,
                            },
                        )
                        status_callback(
                            f"[batch {batch_count}] Visual checkpoint failed after action "
                            f"{batch_action_index}/{batch_size}: {failure_reason}. Discarded "
                            f"{discarded_actions} remaining actions; replanning immediately."
                        )
                        pending_visual_action = None
                        pending_visual_baseline = None
                        if consecutive_batch_interruptions >= max_batch_recoveries:
                            raise RuntimeError(
                                "Stage batch recovery limit reached after "
                                f"{consecutive_batch_interruptions} consecutive failures"
                            )
                        break
                    pending_visual_action = None
                    pending_visual_baseline = None
            if not batch_interrupted:
                consecutive_batch_interruptions = 0
        if executed_actions >= action_limit and not task_complete:
            stop_reason = "action_limit"
    except EmergencyStopRequested:
        stop_reason = "emergency_stop"
    except Exception as error:
        stop_reason = f"failed:{type(error).__name__}"
        status_callback(f"Execution stopped: {type(error).__name__}: {error}")
        if writer is not None:
            performance, stage_timings = performance_snapshot()
            writer.finish(
                success=False,
                extra={
                    "parameterized_action_count": executed_actions,
                    "replans": agent.replans,
                    "stop_reason": stop_reason,
                    "failure_message": str(error),
                    "planning_ms": total_planning_ms,
                    "batch_count": batch_count,
                    "planned_actions": planned_actions,
                    "interrupted_batches": interrupted_batches,
                    "average_batch_size": round(planned_actions / batch_count, 2)
                    if batch_count
                    else 0.0,
                    "max_batch_size": max_batch_size,
                    "visual_checkpoints": visual_checkpoints,
                    "visual_checkpoint_failures": visual_checkpoint_failures,
                    "visual_stability_wait_ms": visual_stability_wait_ms,
                    "local_wait_until_count": local_wait_until_count,
                    "local_wait_until_ms": local_wait_until_ms,
                    "wait_only_plans": wait_only_plans,
                    "short_batch_count": short_batch_count,
                    "session_resets": getattr(agent, "session_resets", 0),
                    "performance": performance,
                    "stage_timings": stage_timings,
                },
            )
            writer = None
        raise
    finally:
        active_emergency.close()
        agent.close()

    if writer is None and stop_reason == "emergency_stop":
        performance, stage_timings = performance_snapshot()
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
            batch_count=batch_count,
            planned_actions=planned_actions,
            interrupted_batches=interrupted_batches,
            average_batch_size=round(planned_actions / batch_count, 2)
            if batch_count
            else 0.0,
            max_batch_size=max_batch_size,
            visual_checkpoints=visual_checkpoints,
            visual_checkpoint_failures=visual_checkpoint_failures,
            visual_stability_wait_ms=visual_stability_wait_ms,
            local_wait_until_count=local_wait_until_count,
            local_wait_until_ms=local_wait_until_ms,
            wait_only_plans=wait_only_plans,
            short_batch_count=short_batch_count,
            session_resets=getattr(agent, "session_resets", 0),
            performance=performance,
            stage_timings=stage_timings,
        )
    if writer is None:
        raise RuntimeError("Windows Agent stopped before its execution trace was created")
    performance, stage_timings = performance_snapshot()
    trace = writer.finish(
        success=task_complete,
        extra={
            "parameterized_action_count": executed_actions,
            "replans": agent.replans,
            "stop_reason": stop_reason,
            "verification": "model_reference_comparison",
            "planning_ms": total_planning_ms,
            "batch_count": batch_count,
            "planned_actions": planned_actions,
            "interrupted_batches": interrupted_batches,
            "average_batch_size": round(planned_actions / batch_count, 2)
            if batch_count
            else 0.0,
            "max_batch_size": max_batch_size,
            "visual_checkpoints": visual_checkpoints,
            "visual_checkpoint_failures": visual_checkpoint_failures,
            "visual_stability_wait_ms": visual_stability_wait_ms,
            "local_wait_until_count": local_wait_until_count,
            "local_wait_until_ms": local_wait_until_ms,
            "wait_only_plans": wait_only_plans,
            "short_batch_count": short_batch_count,
            "session_resets": getattr(agent, "session_resets", 0),
            "performance": performance,
            "stage_timings": stage_timings,
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
        batch_count=batch_count,
        planned_actions=planned_actions,
        interrupted_batches=interrupted_batches,
        average_batch_size=round(planned_actions / batch_count, 2)
        if batch_count
        else 0.0,
        max_batch_size=max_batch_size,
        visual_checkpoints=visual_checkpoints,
        visual_checkpoint_failures=visual_checkpoint_failures,
        visual_stability_wait_ms=visual_stability_wait_ms,
        local_wait_until_count=local_wait_until_count,
        local_wait_until_ms=local_wait_until_ms,
        wait_only_plans=wait_only_plans,
        short_batch_count=short_batch_count,
        session_resets=getattr(agent, "session_resets", 0),
        performance=performance,
        stage_timings=stage_timings,
    )
