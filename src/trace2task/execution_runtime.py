"""Shared capture/plan/execute mechanics; task and scope policies stay with adapters."""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def capture_with_timing(capture, target):
    started = time.perf_counter()
    surface = capture.capture(target)
    return surface, (time.perf_counter() - started) * 1000


class ExecutionRuntime:
    """Own cancellable planning, action limits and common progress messages.

    Stop checks stay on the caller thread, where Windows hotkeys are registered.
    Only planning runs in a worker; input is never sent from that worker.
    """

    def __init__(self, *, stop, status_callback, max_actions, clock=time.monotonic):
        self.stop = stop
        self.status_callback = status_callback
        self.max_actions = max_actions
        self.clock = clock
        self.executed_actions = 0
        self.plans = 0
        self.last_plan_ms = 0.0

    def _check(self):
        if self.stop is not None:
            self.stop.raise_if_requested()

    def plan(self, request: Callable[[], T]) -> T:
        self.last_plan_ms = 0.0
        self._check()
        self.plans += 1
        responses = queue.Queue()

        def work():
            try:
                responses.put((request(), None))
            except Exception as error:  # noqa: BLE001 - relay provider failures to caller
                responses.put((None, error))

        started = self.clock()
        next_update = started + 10
        threading.Thread(target=work, daemon=True).start()
        self.status_callback(f"[plan {self.plans}] 正在请求模型规划…")
        try:
            while True:
                self._check()
                try:
                    value, error = responses.get(timeout=0.05)
                except queue.Empty:
                    if self.clock() >= next_update:
                        self.status_callback(
                            f"[plan {self.plans}] 模型仍在响应，已等待 "
                            f"{self.clock() - started:.1f} 秒…"
                        )
                        next_update = self.clock() + 10
                    continue
                self._check()
                if error is not None:
                    raise error
                return value
        finally:
            self.last_plan_ms = (self.clock() - started) * 1000
            self.status_callback(
                f"[plan {self.plans}] 本轮规划等待结束，耗时 {self.last_plan_ms / 1000:.2f} 秒。"
            )

    def execute(self, motor, action):
        self._check()
        if self.executed_actions >= self.max_actions:
            raise RuntimeError("Action budget exhausted; no input sent")
        result = motor.execute(action)
        self.executed_actions += 1
        self.status_callback(
            f"[action {self.executed_actions}/{self.max_actions}] {action.skill} "
            f"完成，耗时 {result.elapsed_ms:.0f} ms。"
        )
        return result
