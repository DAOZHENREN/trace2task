"""Read-only ADB evidence capture; never sends touch, keys or app launches."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import time
from pathlib import Path

from PIL import Image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adb', required=True)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--server-port', type=int, default=5038)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=False)
    command = [args.adb, '-P', str(args.server_port), '-s', args.serial]

    def run(*parts):
        return subprocess.run(command + list(parts), capture_output=True, timeout=20,
                              check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout

    started = time.monotonic()
    png = run('exec-out', 'screencap', '-p')
    with Image.open(io.BytesIO(png)) as image:
        image.load()
        width, height = image.size
    path = root / 'screenshot.png'
    path.write_bytes(png)
    result = {'serial': args.serial, 'server_port': args.server_port,
              'screenshot': str(path), 'width': width, 'height': height,
              'sha256': hashlib.sha256(png).hexdigest(),
              'capture_seconds': time.monotonic() - started,
              'input_actions': 0}
    for key in ('ro.product.model', 'ro.build.version.release', 'ro.product.cpu.abi'):
        result[key] = run('shell', 'getprop', key).decode('utf-8', errors='replace').strip()
    (root / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                    encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
