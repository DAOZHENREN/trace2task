"""Opt-in native check of the window-scoped execution adapter.

Only a child-owned disposable Edit control can receive these two canned actions.
An unconfirmed background effect is reported, never replayed automatically.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pygame
from validate_native_agent_loop import (
    FIXTURE_TEXT,
    _fixture_text,
    _physical_rect,
    _restore_foreground,
)

from trace2task.actions import ActionCall
from trace2task.window_execution import WindowExecutionAdapter
from trace2task.windows_capture import GdiWindowCapture
from trace2task.windows_control import (
    Win32Backend,
    WindowSelector,
    WindowSession,
    WindowsMotorExecutor,
)


class Stop:
    @staticmethod
    def raise_if_requested():
        return None

    @staticmethod
    def sleep(seconds):
        import time
        time.sleep(seconds)


def run_case(root, *, background):
    name = 'background' if background else 'foreground'
    case_root = root / name
    case_root.mkdir()
    child = subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name('validate_native_agent_loop.py')),
         '--fixture', *([] if background else ['--activate-fixture'])],
        stdout=subprocess.PIPE, text=True,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    backend = Win32Backend()
    previous_handle = backend.foreground_handle()
    previous_window = backend.get_window(previous_handle) if previous_handle else None
    fixture = None
    result = {'mode': name, 'actions': [], 'effect': 'not_checked'}
    events = []
    try:
        fixture = json.loads(child.stdout.readline())
        session = WindowSession(WindowSelector(handle=fixture['window_id']), backend)
        window = session.require_available()
        if (not background and (not backend.focus_window(window.handle)
                                or backend.foreground_handle() != window.handle)):
            raise RuntimeError('Windows refused to focus owned fixture; no input sent')
        frame = GdiWindowCapture().capture(window)
        pygame.image.save(frame, case_root / 'before.png')
        rect = _physical_rect(fixture['edit_id'])
        x = ((rect.left + rect.right) / 2 - window.client_left) / window.client_width
        y = ((rect.top + rect.bottom) / 2 - window.client_top) / window.client_height
        if not 0 <= x <= 1 or not 0 <= y <= 1:
            raise RuntimeError('Owned Edit lies outside bound screenshot; no input sent')
        stop = Stop()
        adapter = WindowExecutionAdapter(
            WindowsMotorExecutor(session, sleeper=stop.sleep, background=background), stop,
        )
        for action in (ActionCall('click', {'x': x, 'y': y}),
                       ActionCall('type_text', {'text': FIXTURE_TEXT})):
            adapter.bind(window)
            delivery = adapter.execute(action)
            result['actions'].append({'action': action.to_payload(),
                                      'receipt': delivery.receipt})
        # Posted input can reach the fixture's message queue shortly after the
        # motor returns.  Poll read-only; never send a second input.
        for _ in range(10):
            result['observed_text'] = _fixture_text(fixture['edit_id'])
            if result['observed_text'] == FIXTURE_TEXT:
                break
            stop.sleep(.05)
        result['effect'] = ('observed_exact_text' if result['observed_text'] == FIXTURE_TEXT
                            else 'not_observed')
        pygame.image.save(GdiWindowCapture().capture(session.require_available()),
                          case_root / 'after.png')
    except Exception as error:  # noqa: BLE001 - archive the bounded native failure
        result['error'] = f'{type(error).__name__}: {error}'
    finally:
        if fixture is not None:
            _restore_foreground(backend, previous_handle, previous_window,
                                fixture['window_id'],
                                lambda kind, **fields: events.append({'type': kind, **fields}))
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
        result['events'] = events
        (case_root / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                               encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('--output must be new; prior evidence is never overwritten')
    args.output.mkdir(parents=True)
    results = [run_case(args.output, background=background) for background in (True, False)]
    (args.output / 'summary.json').write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                              encoding='utf-8')
    print(json.dumps([{'mode': item['mode'], 'effect': item['effect'],
                       'actions': len(item['actions']), 'error': item.get('error')}
                      for item in results], ensure_ascii=False, indent=2))
    return 0 if all(item['effect'] == 'observed_exact_text' for item in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
