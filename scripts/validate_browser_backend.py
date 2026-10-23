"""Opt-in real-browser qualification of the production driver/core; no model calls.

Run from the source checkout with its Python environment. Each stdin line is
either {"observe": true}, {"close": true}, or a unified {"actions": [...]} plan.
Choose coordinates only after looking at the latest returned screenshot.
"""
import json
import sys
import time
import uuid
from pathlib import Path

from trace2task.browser_execution import (
    BROWSER_TARGET,
    BrowserDriver,
    BrowserExecutionBackend,
    BrowserObservation,
)
from trace2task.execution_core import ExecutionCore


class Stop:
    def raise_if_requested(self):
        pass

    def sleep(self, seconds):
        time.sleep(seconds)


def main():
    root = Path('runs') / f'browser-backend-validation-{uuid.uuid4().hex[:8]}'
    root.mkdir(parents=True)

    def record(kind, **data):
        with (root / 'trace.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'type': kind, **data}, ensure_ascii=False) + '\n')

    driver = BrowserDriver(root, record, Stop())
    observer = BrowserObservation(driver)
    core = ExecutionCore(BrowserExecutionBackend(driver), Stop(), record, max_actions=20)
    index = 0

    def observe():
        nonlocal index
        target, state, path = observer.observe(BROWSER_TARGET, index)
        index += 1
        print(json.dumps({'screenshot': str(path), 'state': state, 'run': str(root)},
                         ensure_ascii=False), flush=True)
        return target, state

    try:
        driver.start()
        target, state = observe()
        for line in sys.stdin:
            command = json.loads(line)
            if command == {'close': True}:
                break
            if command != {'observe': True}:
                result = core.execute(command, target=target, state=state,
                                      observation_id=f'validation-{index}')
                print(json.dumps({'status': result.status, 'receipt': result.receipt}), flush=True)
            target, state = observe()
    finally:
        driver.close()
        print(json.dumps({'closed': True, 'run': str(root)}), flush=True)


if __name__ == '__main__':
    main()
