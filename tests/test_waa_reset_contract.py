from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


_REQUESTS = SimpleNamespace(post=None)
with patch.dict(sys.modules, {"requests": _REQUESTS}):
    RESET_MODULE = runpy.run_path(
        str(
            Path(__file__).parents[1]
            / "integrations"
            / "windows_agent_arena"
            / "client"
            / "trace2task_reset.py"
        )
    )


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload
        self.ok = True
        self.status_code = 200
        self.text = json.dumps(payload)

    def json(self) -> dict[str, object]:
        return self._payload


def _write_spec(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "tasks": {
                    "writer": {
                        "must_not_exist": [r"C:\Users\Docker\done.marker"],
                        "must_exist_after_setup": [
                            r"C:\Users\Docker\Documents\source.odt"
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )


def test_verify_reset_requires_setup_artifact(tmp_path: Path, monkeypatch) -> None:
    spec = tmp_path / "reset.json"
    _write_spec(spec)
    monkeypatch.setenv("TRACE2TASK_WAA_RESET_SPEC", str(spec))

    def post(url: str, **kwargs):
        if url.endswith("/trace2task/reset"):
            return _Response({"status": "success"})
        return _Response({"returncode": 0, "output": "[]"})

    monkeypatch.setattr(RESET_MODULE["requests"], "post", post)
    receipt = RESET_MODULE["verify_trace2task_reset"](
        SimpleNamespace(vm_ip="vm"), {"id": "writer"}
    )

    assert receipt == {
        "status": "success",
        "verified_present": [r"C:\Users\Docker\Documents\source.odt"],
    }


def test_verify_reset_fails_when_setup_artifact_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    spec = tmp_path / "reset.json"
    _write_spec(spec)
    monkeypatch.setenv("TRACE2TASK_WAA_RESET_SPEC", str(spec))

    def post(url: str, **kwargs):
        if url.endswith("/trace2task/reset"):
            return _Response({"status": "success"})
        return _Response(
            {
                "returncode": 0,
                "output": json.dumps(
                    [r"C:\Users\Docker\Documents\source.odt"]
                ),
            }
        )

    monkeypatch.setattr(RESET_MODULE["requests"], "post", post)
    with pytest.raises(RuntimeError, match="did not create required files"):
        RESET_MODULE["verify_trace2task_reset"](
            SimpleNamespace(vm_ip="vm"), {"id": "writer"}
        )
