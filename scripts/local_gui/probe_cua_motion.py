"""Opt-in scroll/drag integration probe on an owned disposable Win32 Edit only."""
import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace

from probe_cua_coordinate_text import api


def fixture():
    user = api()
    user.SetFocus.argtypes = [wintypes.HWND]
    text = '\r\n'.join(f'Line {i:02}: ABCDEFGHIJKLMNOPQRSTUVWXYZ 0123456789 '*3 for i in range(90))
    # Top-level scrolling surface, no ambiguous nested child routing.
    window = user.CreateWindowExW(0, 'Edit', text, 0x00CF0000 | 0x00300000 | 0xC4,
        100, 100, 640, 360, None, None, None, None)
    edit = window
    if not window or not edit:
        raise RuntimeError('Cannot create disposable fixture')
    user.ShowWindow(window, 4)
    user.SetFocus(edit)
    print(json.dumps({'pid': os.getpid(), 'window_id': int(window), 'edit': int(edit)}), flush=True)
    message = wintypes.MSG()
    while user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
        user.TranslateMessage(ctypes.byref(message))
        user.DispatchMessageW(ctypes.byref(message))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.fixture:
        fixture()
        return
    if args.output is None:
        parser.error('--output required')
    from trace2task.cua_backend import CuaBackend
    from trace2task.cua_execution import CuaExecutionBackend
    from trace2task.cua_scope import CuaScope
    from trace2task.execution_core import ExecutionCore
    from trace2task.local_gui_protocol import decode
    args.output.mkdir(parents=True, exist_ok=True)
    records, rows = [], []
    driver = CuaBackend(args.output, lambda kind, **v: records.append({'type': kind, **v}))
    child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--fixture'],
        stdout=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    result = {'fixture_only': True, 'rows': rows}
    try:
        info = json.loads(child.stdout.readline())
        target = {k: info[k] for k in ('pid', 'window_id')}
        user = api()
        foreground = int(user.GetForegroundWindow() or 0)
        driver.start()
        scope = CuaScope(driver, target)
        target = scope.start()
        core = ExecutionCore(CuaExecutionBackend(driver, scope),
                             SimpleNamespace(raise_if_requested=lambda: None, sleep=time.sleep), driver.record)
        for index, (model, tool, scale, kind) in enumerate([
            ('gui-owl-2b', 'computer_use', 1000, 'scroll'),
            ('mai-ui-2b', 'mobile_use', 999, 'scroll'),
            ('gui-owl-2b', 'computer_use', 1000, 'drag'),
            ('mai-ui-2b', 'mobile_use', 999, 'drag'),
        ]):
            # Reset only our disposable edit control, outside the tested execution path.
            user.SendMessageW(info['edit'], 0x00B1, 0, None)  # EM_SETSEL(0,0)
            user.SendMessageW(info['edit'], 0x00B7, 0, None)  # EM_SCROLLCARET
            state, _ = driver.observe(target, index)
            before_line = user.SendMessageW(info['edit'], 0x00CE, 0, None)
            if kind == 'scroll':
                native = {'action': 'scroll', 'pixels': -3} if model == 'gui-owl-2b' else {
                    'action': 'scroll', 'direction': 'down', 'amount': 1, 'by': 'page'}
            else:
                rect = wintypes.RECT()
                if not user.GetWindowRect(info['edit'], ctypes.byref(rect)):
                    raise RuntimeError('Fixture rectangle unavailable')
                bounds = state['window_bounds']
                user.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
                origin = wintypes.POINT(0, 0)
                if not user.ClientToScreen(info['edit'], ctypes.byref(origin)):
                    raise RuntimeError('Fixture client origin unavailable')
                def point(dx, origin=origin, bounds=bounds, scale=scale):
                    return [round((origin.x+dx-bounds['x'])/bounds['width']*scale),
                            round((origin.y+10-bounds['y'])/bounds['height']*scale)]
                end = 'coordinate' if model == 'gui-owl-2b' else 'end_coordinate'
                native = {'action': 'left_click_drag' if model == 'gui-owl-2b' else 'drag',
                          'start_coordinate': point(8), end: point(180), 'duration_ms': 500}
            raw = '<tool_call>' + json.dumps({'name': tool, 'arguments': native}) + '</tool_call>'
            prediction = decode(model, raw, cua=True)
            outcome = core.execute(prediction, target=target, observation_id=str(index), state=state)
            time.sleep(.2)
            after_line = user.SendMessageW(info['edit'], 0x00CE, 0, None)
            selected = user.SendMessageW(info['edit'], 0x00B0, 0, None)  # EM_GETSEL
            selected_range = [selected & 65535, (selected >> 16) & 65535]
            rows.append({'model': model, 'kind': kind, 'native': native, 'prediction': prediction,
                         'status': outcome.status, 'receipt': outcome.receipt,
                         'first_visible_line_before': before_line, 'first_visible_line_after': after_line,
                         'selected_range': selected_range,
                         'effect_observed': after_line > before_line if kind == 'scroll' else selected_range[1] > selected_range[0]})
        driver.observe(target, 4)
        result['foreground_unchanged_at_end'] = int(user.GetForegroundWindow() or 0) == foreground
    except Exception as error:  # noqa: BLE001 - record and clean up our fixture only
        result['error'] = f'{type(error).__name__}: {error}'
    finally:
        driver.close()
        child.terminate()
        child.wait(timeout=5)
        (args.output/'trace.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
        (args.output/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
