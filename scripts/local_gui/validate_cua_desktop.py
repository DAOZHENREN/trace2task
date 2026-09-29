"""Opt-in native Cua desktop smoke in a disposable Tk text field."""
import ctypes
import json
import sys
import tkinter as tk
from pathlib import Path

from trace2task.cua_backend import CuaBackend
from trace2task.cua_desktop import CuaDesktopExecutionBackend, CuaDesktopObservation
from trace2task.execution_core import ExecutionCore


def main():
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    ctypes.windll.user32.GetForegroundWindow.restype = ctypes.c_void_p
    ctypes.windll.user32.GetAncestor.restype = ctypes.c_void_p
    ctypes.windll.user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    root = Path(sys.argv[1]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    events = []
    driver = CuaBackend(root, lambda kind, **data: events.append(dict(type=kind, **data)))
    window = tk.Tk()
    window.title('Trace2Task Cua desktop validation')
    window.geometry('500x240+100+100')
    window.attributes('-topmost', True)
    field = tk.Entry(window, font=('Arial', 18))
    field.pack(padx=25, pady=70, fill='x')
    result = {}

    class Stop:
        def raise_if_requested(self):
            pass

        def sleep(self, seconds):
            import time
            time.sleep(seconds)

    def verify():
        result['text'] = field.get()
        result['passed'] = field.get() == 'Cua desktop OK'
        (root / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        window.destroy()

    def run():
        try:
            window.focus_force()
            window.update()
            hwnd = ctypes.windll.user32.GetAncestor(window.winfo_id(), 2)
            ctypes.windll.user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
            if ctypes.windll.user32.GetForegroundWindow() != hwnd:
                raise RuntimeError('Test window is not foreground; no input sent')
            driver.start()
            observer = CuaDesktopObservation(driver)
            target, state, _ = observer.observe(None, 0)
            x = (field.winfo_rootx() + 30) / state['screen_width']
            y = (field.winfo_rooty() + field.winfo_height() / 2) / state['screen_height']
            core = ExecutionCore(CuaDesktopExecutionBackend(driver), Stop(),
                                 lambda kind, **data: events.append(dict(type=kind, **data)))
            delivery = core.execute({'actions': [
                {'skill': 'click', 'args': {'x': x, 'y': y}},
                {'skill': 'type_text', 'args': {'text': 'Cua desktop OK'}},
            ]}, target=target, state=state, observation_id='native-smoke')
            result['status'] = delivery.status
            result['dimensions'] = [state['screen_width'], state['screen_height']]
            result['scale_factor'] = state['scale_factor']
            window.after(400, verify)
        except Exception as error:  # noqa: BLE001 - archive failure and close the disposable test window
            result.update(passed=False, error=str(error))
            (root / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
            window.destroy()

    try:
        window.after(500, run)
        window.mainloop()
    finally:
        driver.close()
        (root / 'events.json').write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result))
    return 0 if result.get('passed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
