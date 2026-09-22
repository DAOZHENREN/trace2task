"""Scoped cleanup for a local-model child that this process just started."""
from __future__ import annotations

import subprocess
import sys


def stop_started_process(process, *, timeout=5):
    """Stop only ``process`` and, on Windows, its descendants.

    Windows virtual-environment launchers can redirect the supplied Popen PID to
    a Python child.  ``taskkill /T`` follows that one process tree; it never
    searches by image name, port, or a different service's command line.
    """
    if process is None or process.poll() is not None:
        return False
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
    return True
