from __future__ import annotations

import base64
import io
import json
import secrets
import threading
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pygame

from trace2task.actions import ActionCall
from trace2task.codex_app_server import classify_codex_failure
from trace2task.windows_agent import (
    WINDOWS_EXPERIENCE_MODES,
    CodexWindowsAgent,
    WindowsAgentPlan,
)
from trace2task.windows_task import WindowsTaskContract, load_windows_task

MAX_REQUEST_BYTES = 12 * 1024 * 1024
DEFAULT_WAA_BRIDGE_PORT = 8776
WAA_PLANNER_ATTEMPT_TIMEOUT_SECONDS = 150
MAX_WAA_PLANNER_RETRIES = 1
WAA_MOTOR_SKILLS = (
    "click",
    "double_click",
    "hold_mouse",
    "drag",
    "type_text",
    "press_key",
    "hold_key",
    "hotkey",
    "wait",
)


@dataclass(frozen=True)
class WaaBridgePlan:
    task_complete: bool
    actions: tuple[str, ...]
    structured_actions: tuple[dict[str, Any], ...]
    reason: str
    confidence: float
    stage_id: str
    stage_goal: str
    expected_end_state: str
    abort_conditions: tuple[str, ...]
    model: str | None
    reasoning_effort: str
    experience_mode: str
    timing: dict[str, Any]
    planner_retries: int
    planner_retry_categories: tuple[str, ...]
    planner_failure_category: str | None = None
    planner_failure_message: str | None = None


class CodexWaaAgent(CodexWindowsAgent):
    """Use the reviewed Trace2Task planner without host-window-only focus actions."""

    def _allowed_skills(self) -> tuple[str, ...]:
        # Keep the motor policy identical across all controlled experience conditions.
        # Otherwise the task pack's Trace-derived skill list leaks evidence into baseline.
        return WAA_MOTOR_SKILLS

    def _completion_context(self) -> str:
        # WAA supplies the authoritative task at reset time.  A task pack's terminal
        # reference frame is evidence about the demonstration, not an extra benchmark
        # requirement (for example, it must not make the agent close every window just
        # because the human recording ended on the desktop).
        return (
            "Completion policy: Windows Agent Arena task. Judge completion only from the "
            "current WAA instruction and visible pixels; the task-pack success frame and "
            "original expected result are demonstration evidence, not additional goals. "
            "Do not close applications or return to the desktop unless the current WAA "
            "instruction explicitly requires it. Once the requested result is visibly "
            "satisfied, return task_complete=true and no actions. A separate WAA evaluator "
            "will decide final success.\n"
        )


def _pixel(value: float, extent: int) -> int:
    if extent <= 0:
        raise ValueError("WAA screenshot dimensions must be positive")
    return round(value * (extent - 1))


def _pyautogui_key(key: str) -> str:
    return {
        "escape": "esc",
        "page_down": "pagedown",
        "page_up": "pageup",
    }.get(key, key)


def action_to_waa(action: ActionCall, *, width: int, height: int) -> str:
    """Translate one validated normalized motor action to WAA's pyautogui action space."""

    args = action.args
    if action.skill == "focus_window":
        raise ValueError("WAA actions must not use the host-only focus_window skill")
    if action.skill in {"click", "double_click"}:
        x = _pixel(args["x"], width)
        y = _pixel(args["y"], height)
        function = "doubleClick" if action.skill == "double_click" else "click"
        return f"pyautogui.{function}(x={x}, y={y}, button={args['button']!r})"
    if action.skill == "hold_mouse":
        x = _pixel(args["x"], width)
        y = _pixel(args["y"], height)
        duration = args["duration_ms"] / 1000
        return (
            f"pyautogui.moveTo({x}, {y}); "
            f"pyautogui.mouseDown(button={args['button']!r}); "
            f"time.sleep({duration!r}); "
            f"pyautogui.mouseUp(button={args['button']!r})"
        )
    if action.skill == "drag":
        start_x = _pixel(args["start_x"], width)
        start_y = _pixel(args["start_y"], height)
        end_x = _pixel(args["end_x"], width)
        end_y = _pixel(args["end_y"], height)
        duration = args["duration_ms"] / 1000
        return (
            f"pyautogui.moveTo({start_x}, {start_y}); "
            f"pyautogui.dragTo({end_x}, {end_y}, duration={duration!r}, "
            f"button={args['button']!r})"
        )
    if action.skill == "type_text":
        return (
            f"import pyperclip; pyperclip.copy({args['text']!r}); "
            "pyautogui.hotkey('ctrl', 'v')"
        )
    if action.skill == "press_key":
        return f"pyautogui.press({_pyautogui_key(args['key'])!r})"
    if action.skill == "hold_key":
        key = _pyautogui_key(args["key"])
        duration = args["duration_ms"] / 1000
        return (
            f"pyautogui.keyDown({key!r}); time.sleep({duration!r}); "
            f"pyautogui.keyUp({key!r})"
        )
    if action.skill == "hotkey":
        keys = ", ".join(repr(_pyautogui_key(key)) for key in args["keys"])
        return f"pyautogui.hotkey({keys})"
    if action.skill == "wait":
        return f"time.sleep({args['duration_ms'] / 1000!r})"
    raise AssertionError(f"Unhandled WAA motor skill: {action.skill}")


class WaaBridge:
    """Own one bounded Codex session and expose it to the isolated WAA client."""

    def __init__(
        self,
        task_path: Path,
        *,
        experience_mode: str,
        model: str,
        reasoning_effort: str,
        codex_bin: str = "codex",
        plan_horizon: int = 12,
        timeout_seconds: float = WAA_PLANNER_ATTEMPT_TIMEOUT_SECONDS,
        agent_type: type[CodexWaaAgent] = CodexWaaAgent,
    ) -> None:
        if experience_mode not in WINDOWS_EXPERIENCE_MODES:
            raise ValueError(
                "WAA experience_mode must be one of "
                f"{', '.join(WINDOWS_EXPERIENCE_MODES)}"
            )
        self.contract = load_windows_task(task_path)
        self.experience_mode = experience_mode
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.codex_bin = codex_bin
        self.plan_horizon = plan_horizon
        self.timeout_seconds = timeout_seconds
        self.agent_type = agent_type
        self._agent: CodexWaaAgent | None = None
        self._instruction: str | None = None
        self._pending_actions: tuple[ActionCall, ...] = ()
        self._lock = threading.Lock()

    def reset(self, instruction: str | None = None) -> None:
        with self._lock:
            self._reset_locked(instruction)

    def _reset_locked(self, instruction: str | None) -> None:
        if self._agent is not None:
            self._agent.close()
        self._agent = None
        self._instruction = None
        self._pending_actions = ()
        if instruction is not None:
            self._start_agent(instruction)

    def _start_agent(self, instruction: str) -> None:
        normalized = " ".join(instruction.split())
        if not normalized:
            raise ValueError("WAA instruction must not be empty")
        contract: WindowsTaskContract = self.contract.with_instruction(normalized)
        self._agent = self.agent_type(
            contract,
            model=self.model,
            reasoning_effort=self.reasoning_effort,
            codex_bin=self.codex_bin,
            plan_horizon=self.plan_horizon,
            timeout_seconds=self.timeout_seconds,
            adaptive_reasoning=False,
            experience_mode=self.experience_mode,
        )
        self._instruction = normalized

    def plan(self, instruction: str, screenshot: bytes) -> WaaBridgePlan:
        with self._lock:
            normalized = " ".join(instruction.split())
            if self._agent is None or self._instruction != normalized:
                self._reset_locked(normalized)
            assert self._agent is not None
            for action in self._pending_actions:
                self._agent.observe_transition(action, True)
            self._pending_actions = ()
            try:
                surface = pygame.image.load(io.BytesIO(screenshot))
            except pygame.error as error:
                raise ValueError("WAA screenshot is not a supported image") from error
            planner_retry_categories: list[str] = []
            while True:
                try:
                    plan: WindowsAgentPlan = self._agent.plan(surface)
                    break
                except Exception as error:
                    category = classify_codex_failure(error)
                    if category is None:
                        raise
                    if len(planner_retry_categories) >= MAX_WAA_PLANNER_RETRIES:
                        print(
                            "[waa bridge] planner exhausted its bounded response budget: "
                            f"{category}: {error}"
                        )
                        return WaaBridgePlan(
                            task_complete=False,
                            actions=("FAIL",),
                            structured_actions=(),
                            reason=(
                                "Planner failed to produce a complete response after one "
                                "transport-isolated retry."
                            ),
                            confidence=0.0,
                            stage_id="unknown",
                            stage_goal="Planner response unavailable.",
                            expected_end_state="No further motor action is executed.",
                            abort_conditions=("Planner response budget exhausted.",),
                            model=self.model,
                            reasoning_effort=self.reasoning_effort,
                            experience_mode=self.experience_mode,
                            timing={},
                            planner_retries=len(planner_retry_categories),
                            planner_retry_categories=tuple(planner_retry_categories),
                            planner_failure_category=category,
                            planner_failure_message=str(error),
                        )
                    planner_retry_categories.append(category)
                    print(
                        "[waa bridge] planner infrastructure retry "
                        f"{len(planner_retry_categories)}/{MAX_WAA_PLANNER_RETRIES}: "
                        f"{category}: {error}"
                    )
                    # The failed model turn returned no motor program, so no action can have
                    # been executed. Preserve semantic stage/history, but replace the unhealthy
                    # Codex transport before retrying the same authoritative screenshot.
                    self._agent.reset_planner_session()
            structured = tuple(action.to_payload() for action in plan.actions)
            actions = tuple(
                action_to_waa(action, width=surface.get_width(), height=surface.get_height())
                for action in plan.actions
            )
            if plan.task_complete:
                actions = ("DONE",)
            else:
                self._pending_actions = plan.actions
            return WaaBridgePlan(
                task_complete=plan.task_complete,
                actions=actions,
                structured_actions=structured,
                reason=plan.reason,
                confidence=plan.confidence,
                stage_id=plan.stage_id,
                stage_goal=plan.stage_goal,
                expected_end_state=plan.expected_end_state,
                abort_conditions=plan.abort_conditions,
                model=plan.model,
                reasoning_effort=plan.reasoning_effort,
                experience_mode=self.experience_mode,
                timing=asdict(plan.timing),
                planner_retries=len(planner_retry_categories),
                planner_retry_categories=tuple(planner_retry_categories),
            )

    def close(self) -> None:
        self.reset()


class _WaaBridgeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], bridge: WaaBridge, token: str) -> None:
        super().__init__(address, _WaaBridgeHandler)
        self.bridge = bridge
        self.token = token


class _WaaBridgeHandler(BaseHTTPRequestHandler):
    server: _WaaBridgeServer

    def log_message(self, format: str, *args: object) -> None:
        print(f"[waa bridge] {self.address_string()} {format % args}")

    def _authorized(self) -> bool:
        supplied = self.headers.get("X-Trace2Task-Token", "")
        return bool(supplied) and secrets.compare_digest(supplied, self.server.token)

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _body(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("Content-Length is required")
        length = int(raw_length)
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("WAA bridge request is too large")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise TypeError("WAA bridge request body must be an object")
        return payload

    def do_GET(self) -> None:
        if self.path != "/health":
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        self._json(
            HTTPStatus.OK,
            {
                "status": "ok",
                "experience_mode": self.server.bridge.experience_mode,
                "model": self.server.bridge.model,
                "reasoning_effort": self.server.bridge.reasoning_effort,
            },
        )

    def do_POST(self) -> None:
        if not self._authorized():
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        try:
            payload = self._body()
            if self.path == "/v1/reset":
                instruction = payload.get("instruction")
                if instruction is not None and not isinstance(instruction, str):
                    raise TypeError("WAA reset instruction must be a string")
                self.server.bridge.reset(instruction)
                self._json(HTTPStatus.OK, {"status": "reset"})
                return
            if self.path == "/v1/plan":
                instruction = payload.get("instruction")
                encoded = payload.get("screenshot_base64")
                if not isinstance(instruction, str):
                    raise TypeError("WAA plan instruction must be a string")
                if not isinstance(encoded, str) or not encoded:
                    raise TypeError("WAA plan screenshot_base64 must be a non-empty string")
                screenshot = base64.b64decode(encoded, validate=True)
                result = self.server.bridge.plan(instruction, screenshot)
                self._json(HTTPStatus.OK, asdict(result))
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": type(error).__name__, "message": str(error)},
            )
        except Exception as error:  # noqa: BLE001 - surface planner failure to WAA
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": type(error).__name__, "message": str(error)},
            )


def create_waa_bridge_server(
    task_path: Path,
    *,
    experience_mode: str,
    model: str,
    reasoning_effort: str,
    token: str,
    host: str = "127.0.0.1",
    port: int = DEFAULT_WAA_BRIDGE_PORT,
    codex_bin: str = "codex",
    plan_horizon: int = 12,
) -> _WaaBridgeServer:
    if not token:
        raise ValueError("WAA bridge token must not be empty")
    bridge = WaaBridge(
        task_path,
        experience_mode=experience_mode,
        model=model,
        reasoning_effort=reasoning_effort,
        codex_bin=codex_bin,
        plan_horizon=plan_horizon,
    )
    return _WaaBridgeServer((host, port), bridge, token)


def serve_waa_bridge(
    task_path: Path,
    *,
    experience_mode: str,
    model: str,
    reasoning_effort: str,
    token: str,
    host: str = "127.0.0.1",
    port: int = DEFAULT_WAA_BRIDGE_PORT,
    codex_bin: str = "codex",
    plan_horizon: int = 12,
) -> None:
    server = create_waa_bridge_server(
        task_path,
        experience_mode=experience_mode,
        model=model,
        reasoning_effort=reasoning_effort,
        token=token,
        host=host,
        port=port,
        codex_bin=codex_bin,
        plan_horizon=plan_horizon,
    )
    print(
        f"Trace2Task WAA bridge listening on http://{host}:{port} "
        f"({experience_mode}, {model}/{reasoning_effort})."
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
        server.bridge.close()
