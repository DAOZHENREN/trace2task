from __future__ import annotations

import runpy
import sys
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest


def _load_reset_helpers():
    flask = ModuleType("flask")
    flask.jsonify = lambda *args, **kwargs: (args, kwargs)
    flask.request = nullcontext()
    path = (
        Path(__file__).parents[1]
        / "integrations"
        / "windows_agent_arena"
        / "vm_server"
        / "trace2task_input.py"
    )
    with patch.dict(sys.modules, {"flask": flask}):
        module = runpy.run_path(str(path))
    return module["_validated_reset_path"], module["_delete_reset_file"]


_VALIDATED_RESET_PATH, _DELETE_RESET_FILE = _load_reset_helpers()


def test_reset_path_must_stay_below_benchmark_profile() -> None:
    assert _VALIDATED_RESET_PATH(
        r"C:\Users\Docker\Documents\draft.txt",
        user_root=r"C:\Users\Docker",
    ) == r"C:\Users\Docker\Documents\draft.txt"

    for unsafe in (
        r"C:\Users\Other\draft.txt",
        r"C:\Users\Docker",
        r"Documents\draft.txt",
    ):
        with pytest.raises(ValueError):
            _VALIDATED_RESET_PATH(unsafe, user_root=r"C:\Users\Docker")


def test_reset_deletes_generated_file_without_recycle_bin_residue(
    tmp_path: Path,
) -> None:
    generated = tmp_path / "draft.txt"
    generated.write_text("This is a draft.", encoding="utf-8")

    _DELETE_RESET_FILE(str(generated))

    assert not generated.exists()
