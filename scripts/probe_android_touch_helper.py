"""Explicit helper upload/readiness probe. Sends NO DOWN, MOVE, tap or swipe.

UP/EOF only closes an empty helper session. Does not call a model, launch an app,
connect a new device, change settings or replace any other app/helper.
"""
import argparse
import json
import time
from pathlib import Path

from trace2task.android_execution import AndroidDriver
from trace2task.android_touch import BoundedTouchSession


class ReadinessStop:
    def __init__(self):
        self.deadline = time.monotonic() + 45

    def raise_if_requested(self):
        if time.monotonic() >= self.deadline:
            raise TimeoutError('Readiness probe timeout')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adb', required=True)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--package', required=True)
    parser.add_argument('--server-port', type=int, default=5038)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--capture-check', action='store_true', help='Also time one scoped read-only screenshot')
    parser.add_argument('--crop-check', action='store_true', help='Time three 256px minimap-region frames, no input')
    parser.add_argument('--prepare-helper', action='store_true', required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    records = []
    def record(kind, **data):
        records.append({'type': kind, **data})
    stop = ReadinessStop()
    driver = AndroidDriver(args.output, record, stop, adb=args.adb, serial=args.serial,
                           package=args.package, server_port=args.server_port)
    session = None
    result = {'ready': False, 'input_actions': 0, 'model_calls': 0}
    try:
        driver.start()
        session = BoundedTouchSession(driver, driver.state(), stop)
        session.open()
        result['ready'] = True
        if args.crop_check:
            result['crop_frames'] = [session.capture_region((0, 0, 256, 256), args.output / f'crop-{n}.png')
                                     for n in range(3)]
        if args.capture_check:
            started = time.monotonic()
            _state, path = driver.observe(0)
            result.update(capture_ms=round((time.monotonic()-started)*1000, 2), screenshot=str(path))
    except Exception as error:  # noqa: BLE001 - archive failed device readiness, never retry input
        result['error'] = f'{type(error).__name__}: {error}'
    finally:
        if session is not None:
            session.close()
            result['empty_session_closed'] = session.released.is_set()
        driver.close()
        result['events'] = records
        assert not any(item.get('event') in {'start_requested', 'move_requested'} for item in records)
        (args.output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result['ready'] or 'error' in result or not result.get('empty_session_closed'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
