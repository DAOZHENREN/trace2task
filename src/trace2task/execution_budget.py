"""Bounded task budgets; expiry uses the existing no-late-input stop path."""
import time

from trace2task.windows_runner import EmergencyStopRequested

DEFAULT_ANDROID_BUDGET = {'max_actions': 80, 'max_seconds': 1200}


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

    def __getattr__(self, name):
        return getattr(self.stop, name)

    def raise_if_requested(self):
        self.stop.raise_if_requested()
        if time.monotonic() >= self.deadline:
            raise TimeBudgetExceeded('时间预算耗尽，任务未完成')

    def sleep(self, seconds):
        self.raise_if_requested()
        # Underlying stop retains its F9/event checks. No sleep may pass expiry.
        self.stop.sleep(min(seconds, max(0, self.deadline - time.monotonic())))
        self.raise_if_requested()
