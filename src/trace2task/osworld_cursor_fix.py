"""Owned-guest compatibility patch for the pinned image's leaking Xcursor.

Upstream screenshot capture is retained; only resource lifetime is corrected.
Never edit the immutable base image or retry an application action.
"""

import hashlib
import json
import time
from pathlib import Path

MAIN_SHA = "41376a5c4f0b68ed7bbbbcc73ece60427f4a58dd8bd4710176f9911de726339e"
CURSOR_SHA = "0d8e3071049614b480e417a58a512c277050034bdb9d3bc01a3ba4b0e7f76137"
REVISION = "xcursor-owned-lifetime-v1"


def patched_sources(main, cursor):
    if (
        hashlib.sha256(main.encode()).hexdigest() != MAIN_SHA
        or hashlib.sha256(cursor.encode()).hexdigest() != CURSOR_SHA
    ):
        raise ValueError("Unrecognized guest screenshot source; refusing blind patch")
    start = main.index("        cursor_obj = Xcursor()")
    end = main.index('    elif user_platform == "Darwin":', start)
    body = main[start:end].splitlines(True)
    main = (
        main[:start]
        + "        with Xcursor() as cursor_obj:\n"
        + "".join("    " + line for line in body[1:])
        + main[end:]
    )
    cursor = cursor.replace(
        "        return cursor_data[0]",
        "        self._cursor_data = cursor_data\n        return cursor_data[0]",
    )
    marker = "    def argbdata_to_pixdata"
    lifecycle = """    def __enter__(self):
        if not self.display:
            raise RuntimeError("XOpenDisplay failed")
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        data = getattr(self, '_cursor_data', None)
        if data is not None:
            self.xlib.XFree.argtypes = [ctypes.c_void_p]
            self.xlib.XFree.restype = ctypes.c_int
            self.xlib.XFree(data)
            self._cursor_data = None
        if self.display:
            self.xlib.XCloseDisplay.argtypes = [ctypes.POINTER(Display)]
            self.xlib.XCloseDisplay.restype = ctypes.c_int
            self.xlib.XCloseDisplay(self.display)
            self.display = None

"""
    cursor = cursor.replace(marker, lifecycle + marker)
    compile(main, "main.py", "exec")
    compile(cursor, "pyxcursor.py", "exec")
    return main, cursor


def install(base_url, evidence):
    import requests

    evidence = Path(evidence)
    evidence.mkdir(exist_ok=True)
    session = requests.Session()
    session.trust_env = False

    def execute(code):
        response = session.post(
            base_url + "/execute",
            json={"command": ["python3", "-c", code], "shell": False},
            timeout=(3, 15),
        )
        response.raise_for_status()
        result = response.json()
        if result.get("returncode") != 0:
            raise RuntimeError("Cursor repair failed: " + str(result))
        return json.loads(result["output"])

    try:
        original = execute(
            "import os,json,pathlib; print(json.dumps({'pid':os.getppid(), 'files':{n:pathlib.Path('/home/user/server/'+n).read_text() for n in ['main.py','pyxcursor.py']}}))"
        )
        main, cursor = patched_sources(
            original["files"]["main.py"], original["files"]["pyxcursor.py"]
        )
        for name, text in original["files"].items():
            (evidence / ("original-" + name)).write_text(text)
        files = {"pyxcursor.py": cursor, "main.py": main}
        # Write cursor first, main last triggers the guest's existing Werkzeug reload.
        try:
            execute(
                "import pathlib,json,os; files="
                + repr(files)
                + "\nfor n,s in files.items():\n p=pathlib.Path('/home/user/server/'+n); tmp=p.with_suffix('.repair-tmp'); tmp.write_text(s); os.replace(tmp,p)\nprint(json.dumps(True))"
            )
        except (requests.ConnectionError, requests.Timeout):
            # Reload may close this request after writing; verify, never replay it.
            pass
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                current = execute(
                    "import os,json,pathlib,hashlib; print(json.dumps({'pid':os.getppid(),'hashes':{n:hashlib.sha256(pathlib.Path('/home/user/server/'+n).read_bytes()).hexdigest() for n in ['main.py','pyxcursor.py']}}))"
                )
                expected = {n: hashlib.sha256(s.encode()).hexdigest() for n, s in files.items()}
                if current["pid"] != original["pid"] and current["hashes"] == expected:
                    record = {
                        "revision": REVISION,
                        "old_pid": original["pid"],
                        "new_pid": current["pid"],
                        "sources": {"main.py": MAIN_SHA, "pyxcursor.py": CURSOR_SHA},
                        "patched": {
                            n: hashlib.sha256(s.encode()).hexdigest() for n, s in files.items()
                        },
                    }
                    (evidence / "repair.json").write_text(json.dumps(record, indent=2))
                    return record
            except requests.RequestException:
                pass
            time.sleep(0.5)
        raise RuntimeError("Guest screenshot server did not reload after cursor repair")
    finally:
        session.close()
