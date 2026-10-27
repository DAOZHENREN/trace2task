"""Bounded task budgets; expiry uses the existing no-late-input stop path."""
import threading
import time

from trace2task.windows_runner import EmergencyStopRequested

DEFAULT_ANDROID_BUDGET = {'max_actions': 160, 'max_seconds': 2400}


def android_budget(value=None):
    if value is None:
        return dict(DEFAULT_ANDROID_BUDGET)
    if not isinstance(value, dict) or set(value) != set(DEFAULT_ANDROID_BUDGET):
        raise ValueError('Android 预算需要 max_actions 和 max_seconds')
    for key, low, high in [('max_actions', 1, 500), ('max_seconds', 30, 7200)]:
        if type(value[key]) is not int or not low <= value[key] <= high:
            raise ValueError(f'{key} 必须为 {low}–{high} 的整数')
    return dict(value)


class TimeBudgetExceeded(EmergencyStopRequested):
    pass


class BudgetStop:
    def __init__(self, stop, seconds):
        self.stop = stop
        self.deadline = time.monotonic() + seconds
        self._pause_started = None
        self._pause_until = 0.0
        self._lock = threading.RLock()
        self.paused_seconds = 0.0

    def __getattr__(self, name):
        return getattr(self.stop, name)

    def raise_if_requested(self):
        self.stop.raise_if_requested()
        with self._lock:
            self._account_pause()
            if time.monotonic() >= self.deadline and self._pause_started is None:
                raise TimeBudgetExceeded('时间预算耗尽，任务未完成')

    def _account_pause(self):
        if self._pause_started is not None:
            now = time.monotonic()
            elapsed = max(0.0, min(now, self._pause_until) - self._pause_started)
            self.deadline += elapsed
            self.paused_seconds += elapsed
            self._pause_started = now if now < self._pause_until else None

    def rate_limit_wait(self, seconds):
        with self._lock:
            if seconds is not None:
                self.raise_if_requested()
            self._account_pause()
            self._pause_started = time.monotonic() if seconds is not None else None
            self._pause_until = time.monotonic() + seconds if seconds is not None else 0.0

    def sleep(self, seconds):
        self.raise_if_requested()
        # Underlying stop retains its F9/event checks. No sleep may pass expiry.
        self.stop.sleep(min(seconds, max(0, self.deadline - time.monotonic())))
        self.raise_if_requested()
