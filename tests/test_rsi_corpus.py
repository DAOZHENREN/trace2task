from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _module():
    path = Path(__file__).parents[1] / "scripts/rsi/build_instruction_corpus.py"
    spec = importlib.util.spec_from_file_location("rsi_corpus", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_extracts_only_literal_class_level_base_instruction(tmp_path: Path):
    module = _module()
    source = tmp_path / "task_026.py"
    source.write_text(
        "raise RuntimeError('must not execute')\n"
        "class Task026:\n"
        "    base_instruction = ('static ' 'instruction')\n",
        encoding="utf-8",
    )
    assert module.extract_static_base_instruction(source, "Task026") == "static instruction"


def test_allows_only_unique_module_literal_interpolation(tmp_path: Path):
    module = _module()
    source = tmp_path / "task_026.py"
    source.write_text(
        "PROJECT = 'safe-name'\n"
        "class Task026:\n"
        "    base_instruction = f'Use {PROJECT}'\n",
        encoding="utf-8",
    )
    assert module.extract_static_base_instruction(source, "Task026") == "Use safe-name"


@pytest.mark.parametrize(
    "body",
    [
        "base_instruction = helper()",
        "base_instruction = f'Use {unknown}'",
        "base_instruction = 'one'\n    base_instruction = 'two'",
        "instruction = 'not the approved field'",
    ],
)
def test_rejects_nonliteral_missing_or_ambiguous_instruction(tmp_path: Path, body: str):
    module = _module()
    source = tmp_path / "task_041.py"
    source.write_text(f"class Task041:\n    {body}\n", encoding="utf-8")
    with pytest.raises(module.CorpusBuildError):
        module.extract_static_base_instruction(source, "Task041")
