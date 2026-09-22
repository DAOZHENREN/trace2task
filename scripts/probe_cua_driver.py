"""Manual, logged Cua compatibility probe; never part of the production executor."""
import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

DRIVER = Path('D:/Tools/cua-driver-probe/v0.28.2/cua-driver-rs-0.28.2-windows-x86_64/cua-driver.exe')
OUTPUT = Path('D:/MyProject/trace2task/runs/cua-driver-probe-20260921')


def desktop_state():
    class Point(ctypes.Structure):
        _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]
    point = Point()
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    user32.GetCursorPos(ctypes.byref(point))
    return {'foreground': user32.GetForegroundWindow(), 'cursor': [point.x, point.y]}


if __name__ == '__main__':
    tool, payload = sys.argv[1], json.loads(sys.argv[2])
    OUTPUT.mkdir(parents=True, exist_ok=True)
    before = desktop_state()
    started = time.perf_counter()
    result = subprocess.run([str(DRIVER), 'call', tool, '--socket', r'\\.\pipe\trace2task-cua-probe'],
                            input=json.dumps(payload, ensure_ascii=False), encoding='utf-8',
                            capture_output=True, timeout=45, check=False,
                            env={**os.environ, 'CUA_DRIVER_RS_TELEMETRY_ENABLED': 'false'},
                            creationflags=subprocess.CREATE_NO_WINDOW)
    entry = {'tool': tool, 'input': payload, 'elapsed_ms': round((time.perf_counter()-started)*1000),
             'before': before, 'after': desktop_state(), 'returncode': result.returncode,
             'stdout': result.stdout, 'stderr': result.stderr}
    with (OUTPUT / 'calls.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + '\n')
    display = dict(entry)
    try:
        body = json.loads(result.stdout)
        if 'elements' in body:
            body.pop('tree_markdown', None)
            body['elements'] = body['elements'][:8]
        display['stdout'] = body
    except ValueError:
        pass
    print(json.dumps(display, ensure_ascii=False))
