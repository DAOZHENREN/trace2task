"""Opt-in native Cua probe: edit ONLY our disposable Win32 test window, never WeChat."""
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


def api():
    user = ctypes.windll.user32
    user.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
        wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
    user.CreateWindowExW.restype = wintypes.HWND
    user.GetForegroundWindow.restype = wintypes.HWND
    user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_void_p]
    user.SendMessageW.restype = ctypes.c_ssize_t
    user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    try:
        user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except OSError:
        pass
    return user


def fixture():
    user = api()
    window = user.CreateWindowExW(0, 'Static', 'Trace2Task disposable text probe',
        0x00CF0000, 100, 100, 600, 260, None, None, None, None)
    edit = user.CreateWindowExW(0, 'Edit', '', 0x50000000 | 0x00800000 | 0x80,
        20, 40, 540, 80, window, None, None, None)
    if not window or not edit:
        raise RuntimeError('Cannot create disposable fixture')
    user.ShowWindow(window, 4)  # SW_SHOWNOACTIVATE, do not steal the user's focus.
    print(json.dumps({'pid': os.getpid(), 'window_id': int(window), 'edit': int(edit)}), flush=True)
    message = wintypes.MSG()
    while user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
        user.TranslateMessage(ctypes.byref(message))
        user.DispatchMessageW(ctypes.byref(message))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--foreground-fallback', action='store_true',
                        help='Simulate a structured background refusal, then use real Cua foreground input')
    parser.add_argument('--focused-text', action='store_true',
                        help='Background-click the disposable Edit, then test native text-only Cua delivery')
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
    args.output.mkdir(parents=True, exist_ok=True)
    records = []
    driver = CuaBackend(args.output, lambda kind, **value: records.append({'type': kind, **value}))
    child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--fixture'],
        stdout=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    result = {'fixture_only': True, 'external_messages_sent': False}
    try:
        info = json.loads(child.stdout.readline())
        target = {key: info[key] for key in ('pid', 'window_id')}
        user = api()
        driver.start()
        scope = CuaScope(driver, target)
        target = scope.start()
        state, _ = driver.observe(target, 0)
        rect = wintypes.RECT()
        if not user.GetWindowRect(info['edit'], ctypes.byref(rect)):
            raise RuntimeError('Fixture rectangle unavailable')
        bounds = state['window_bounds']
        x = ((rect.left + rect.right)/2 - bounds['x'])/bounds['width']
        y = ((rect.top + rect.bottom)/2 - bounds['y'])/bounds['height']
        # Deliberately test the vision-only route, not a UIA element token.
        state.update(elements_complete=False, elements=[])
        before = int(user.GetForegroundWindow() or 0)
        stop = SimpleNamespace(raise_if_requested=lambda: None, sleep=time.sleep)
        if args.focused_text:
            result['focus_click_receipt'] = driver.call('click', {
                **target, 'session': state['session'], 'delivery_mode': 'background',
                'x': int(x*state['screenshot_width']), 'y': int(y*state['screenshot_height']),
                'button': 'left', 'count': 1})
            state, _ = driver.observe(target, 1)
            state.update(elements_complete=False, elements=[])
        if args.foreground_fallback:
            from trace2task.execution_protocol import DriverRefusal

            original_call = driver.call
            def simulated_refusal(tool, payload):
                if tool == 'type_text' and payload.get('delivery_mode') == 'background':
                    refusal = {'code': 'background_unavailable', 'effect': 'unverifiable',
                               'source': 'disposable_fixture_simulation'}
                    driver.record('simulated_background_refusal', tool=tool, request=payload,
                                  receipt=refusal)
                    raise DriverRefusal(refusal)
                return original_call(tool, payload)
            driver.call = simulated_refusal
            result['background_refusal_simulated'] = True
        core = ExecutionCore(CuaExecutionBackend(driver, scope), stop, driver.record)
        text_args = {'text': 'Trace2Task 测试'}
        if not args.focused_text:
            text_args.update(x=x, y=y)
        outcome = core.execute({'actions': [{'skill': 'type_text', 'args': text_args}]},
                               target=target, observation_id='probe-0', state=state)
        time.sleep(.3)
        value = ctypes.create_unicode_buffer(256)
        user.SendMessageW(info['edit'], 0x000D, len(value), ctypes.cast(value, ctypes.c_void_p))
        result.update(status=outcome.status, receipt=outcome.receipt, text=value.value,
                      text_matches=value.value == 'Trace2Task 测试',
                      foreground_fallback=outcome.delivery_mode == 'foreground',
                      foreground_unchanged=int(user.GetForegroundWindow() or 0) == before)
        driver.observe(target, 2 if args.focused_text else 1)
    except Exception as error:  # noqa: BLE001 - preserve diagnostics and clean up only our fixture
        result.update(error=f'{type(error).__name__}: {error}')
    finally:
        driver.close()
        child.terminate()  # Only the disposable fixture process we created.
        child.wait(timeout=5)
        (args.output/'trace.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
        (args.output/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
