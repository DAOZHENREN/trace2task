"""Opt-in visible-game / existing Cua background probe; no browser automation API."""
import argparse
import ctypes
import hashlib
import json
import subprocess
import sys
import time
import uuid
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace

from trace2task.cua_backend import CuaBackend
from trace2task.cua_execution import CuaExecutionBackend
from trace2task.cua_scope import CuaScope
from trace2task.execution_protocol import UnifiedAction


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owned-pid', type=int, required=True,
                        help='Actual game PID independently verified before testing; never terminated here')
    parser.add_argument('--game', choices=['ancientbeast', 'pvz', 'sts2'], default='ancientbeast')
    parser.add_argument('--driver', type=Path)
    parser.add_argument('--require-focused', action='store_true',
                        help='Control condition: require user-focused game; still deliver background input')
    args = parser.parse_args()
    root = Path('runs') / f'visible-browser-cua-{uuid.uuid4().hex[:8]}'
    root.mkdir(parents=True)
    user = ctypes.windll.user32
    user.GetForegroundWindow.restype = wintypes.HWND

    def desktop_sample():
        point = wintypes.POINT()
        user.GetCursorPos(ctypes.byref(point))
        return {'foreground': int(user.GetForegroundWindow() or 0),
                'cursor': [point.x, point.y]}

    def record(kind, **data):
        with (root / 'trace.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'kind': kind, **data}, ensure_ascii=False) + '\n')

    driver = CuaBackend(root, record)
    # Use the installed user override even when the parent shell predates its update.
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
        configured, _ = winreg.QueryValueEx(key, 'TRACE2TASK_CUA_DRIVER')
    driver.driver = Path(configured)
    if args.driver:
        driver.driver = args.driver.resolve(strict=True)
    version = subprocess.run([str(driver.driver), '--version'], capture_output=True,
                             text=True, check=True, env=driver.env,
                             creationflags=subprocess.CREATE_NO_WINDOW).stdout.strip()
    record('driver_identity', path=str(driver.driver), version=version,
           sha256=hashlib.sha256(driver.driver.read_bytes()).hexdigest())
    try:
        driver.start()
        expected_app, expected_title = {
            'ancientbeast': ('msedge.exe', 'Ancient Beast - Turn Based Strategy Game'),
            'pvz': ('PlantsVsZombies.exe', '植物大战僵尸杂交版v3.19'),
            'sts2': ('SlayTheSpire2.exe', 'Slay the Spire 2'),
        }[args.game]
        candidates = [w for w in driver.windows()
                      if w['pid'] == args.owned_pid and w.get('app_name') == expected_app
                      and w.get('title') == expected_title]
        if len(candidates) != 1:
            raise RuntimeError('Cannot uniquely bind the verified game window')
        target = {k: candidates[0][k] for k in ('pid', 'window_id')}
        record('target_identity', target=target, app=expected_app, title=expected_title,
               foreground_retry=False, require_focused=args.require_focused)
        scope = CuaScope(driver, target)
        target = scope.start()
        backend = CuaExecutionBackend(driver, scope, allow_foreground_retry=False)
        index = 0

        def observe():
            nonlocal index
            path = (root / f'{index:04d}.png').resolve()
            state = driver.call('get_window_state', {
                **target, 'session': root.name, 'include_accessibility_tree': False,
                'screenshot_out_file': str(path), 'max_image_dimension': 1600,
            })
            state['session'] = root.name
            if any(state.get(k) != v for k, v in target.items()) or not path.is_file():
                raise RuntimeError('Observation identity or screenshot missing')
            print(json.dumps({'observation': index, 'target': target,
                              'screenshot': str(path), 'desktop': desktop_sample(),
                              'size': [state['screenshot_width'], state['screenshot_height']]},
                             ensure_ascii=False), flush=True)
            index += 1
            return state

        state = observe()
        for line in sys.stdin:
            command = json.loads(line)
            if command == {'close': True}:
                break
            if command != {'observe': True}:
                if command.pop('observation', None) != index - 1:
                    raise RuntimeError('Stale observation; no action sent')
                action = UnifiedAction.from_payload(command)
                if action.skill not in {'click', 'press_key'}:
                    raise ValueError('Probe only permits screenshot-grounded click/key')
                backend.authorize(action, target)
                backend.check_current(target, state)
                prepared = backend.prepare(action, state)
                before_action = desktop_sample()
                focused = before_action['foreground'] == target['window_id']
                if focused != args.require_focused:
                    print(json.dumps({'blocked': 'Focus does not match requested test condition; no input sent'}), flush=True)
                else:
                    try:
                        receipt, _ = backend.dispatch(action, prepared, target,
                            SimpleNamespace(raise_if_requested=lambda: None, sleep=time.sleep))
                        print(json.dumps({'receipt': receipt}, ensure_ascii=False), flush=True)
                    except RuntimeError as error:
                        print(json.dumps({'error': str(error)}, ensure_ascii=False), flush=True)
                    finally:
                        record('desktop_samples', before=before_action, after=desktop_sample())
            state = observe()
    finally:
        driver.close()
        print(json.dumps({'closed': True, 'run': str(root)}), flush=True)


if __name__ == '__main__':
    main()
