#!/usr/bin/env python3
"""Build the RSIAgent OSWorld instruction corpus without running GitLab tasks.

The official builder loads every task class so it can read its instruction.  Two
locked OSWorld-V2 tasks (026 and 041) import the GitLab controller at module
load time, which requires a live administrator token even though their
``base_instruction`` values are static class constants.  This adapter keeps
the official loader for the other 106 task classes and performs a deliberately
narrow AST-only extraction for those two constants.  It then uses the official
normalization, shingling and manifest validation functions unchanged.

It is a host-side anti-leakage input adapter, not a way to instantiate, run or
evaluate the two GitLab tasks.  The generated receipt makes that distinction
auditable.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ADAPTED_TASKS = {"task_026": "Task026", "task_041": "Task041"}
EXPECTED_RSI_COMMIT = "a9e56263f6deaa493496ad6b155fe24bf131bc12"


class CorpusBuildError(RuntimeError):
    """Raised when frozen inputs or the narrow adaptation cannot be verified."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CorpusBuildError(f"Cannot read JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise CorpusBuildError(f"Expected a JSON object: {path}")
    return value


def _literal_string(node: ast.AST, constants: dict[str, str]) -> str:
    """Evaluate a source-only string expression without executing its module."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        pieces: list[str] = []
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                pieces.append(part.value)
            elif (
                isinstance(part, ast.FormattedValue)
                and isinstance(part.value, ast.Name)
                and part.value.id in constants
                and part.conversion == -1
                and part.format_spec is None
            ):
                pieces.append(constants[part.value.id])
            else:
                raise CorpusBuildError("base_instruction contains a non-static interpolation")
        return "".join(pieces)
    raise CorpusBuildError("base_instruction must be a static string expression")


def _module_string_constants(module: ast.Module) -> dict[str, str]:
    """Return uniquely assigned top-level literal string constants only."""
    values: dict[str, str] = {}
    duplicates: set[str] = set()
    for node in module.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        try:
            value = _literal_string(node.value, {})
        except CorpusBuildError:
            continue
        if target.id in values:
            duplicates.add(target.id)
        else:
            values[target.id] = value
    return {name: value for name, value in values.items() if name not in duplicates}


def extract_static_base_instruction(source_path: Path, class_name: str) -> str:
    """Return exactly one literal class-level ``base_instruction`` string.

    This intentionally never imports or executes the task module.  It accepts
    literal concatenation and a f-string which only interpolates unique,
    module-level literal-string constants. Expressions, multiple definitions and
    missing definitions are refused rather than guessed.
    """
    try:
        source = source_path.read_text(encoding="utf-8")
        module = ast.parse(source, filename=str(source_path))
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        raise CorpusBuildError(f"Cannot parse frozen task source: {source_path}") from exc
    constants = _module_string_constants(module)
    matches: list[ast.AST] = []
    for node in module.body:
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        for statement in node.body:
            assigned_value: ast.AST | None = None
            if isinstance(statement, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "base_instruction"
                for target in statement.targets
            ):
                assigned_value = statement.value
            if isinstance(statement, ast.AnnAssign) and isinstance(
                statement.target, ast.Name
            ) and statement.target.id == "base_instruction":
                assigned_value = statement.value
            if assigned_value is not None:
                matches.append(assigned_value)
    if len(matches) != 1:
        raise CorpusBuildError(
            f"Expected exactly one class-level base_instruction in {source_path.name}"
        )
    value = _literal_string(matches[0], constants)
    if not value.strip():
        raise CorpusBuildError(
            f"base_instruction must be a non-empty static string in {source_path.name}"
        )
    return value


def load_official_modules(rsi_root: Path, osworld_root: Path) -> tuple[Any, Any]:
    """Load the pinned official loader and corpus functions after path setup."""
    for root in (str(osworld_root), str(rsi_root)):
        if root not in sys.path:
            sys.path.insert(0, root)
    try:
        from benchmarks.osworld.assets import task_content_identity
        from explore.commit import (
            CORPUS_NORMALIZATION,
            build_corpus,
            normalize_instruction_for_corpus,
            validate_instruction_corpus,
        )
        from task_loader import load_task_config, resolve_task_json_path
    except ImportError as exc:
        raise CorpusBuildError(
            "Official RSIAgent/OSWorld dependencies are unavailable; use the dedicated "
            "controller environment."
        ) from exc
    return (
        task_content_identity,
        {
            "CORPUS_NORMALIZATION": CORPUS_NORMALIZATION,
            "build_corpus": build_corpus,
            "normalize_instruction_for_corpus": normalize_instruction_for_corpus,
            "validate_instruction_corpus": validate_instruction_corpus,
            "load_task_config": load_task_config,
            "resolve_task_json_path": resolve_task_json_path,
        },
    )


def verify_frozen_inputs(rsi_root: Path, osworld_root: Path, task_content_identity: Any) -> dict[str, Any]:
    """Verify release manifest, source manifest and all 108 locked task files."""
    lock_path = rsi_root / "config/osworld/baseline.lock.json"
    source_manifest_path = rsi_root / "docs/source-manifest.json"
    lock = _read_object(lock_path)
    source_manifest = _read_object(source_manifest_path)
    release = lock.get("release_manifest")
    task_files = lock.get("task_files")
    if not isinstance(release, dict) or not isinstance(task_files, dict):
        raise CorpusBuildError("Baseline lock has no release/task source identity")
    release_path = osworld_root / str(release.get("path", ""))
    if not release_path.is_file() or sha256_file(release_path) != release.get("sha256"):
        raise CorpusBuildError("Pinned OSWorld release manifest hash mismatch")
    exported = source_manifest.get("exported_file_sha256")
    if not isinstance(exported, dict):
        raise CorpusBuildError("Frozen RSIAgent source manifest has no file hashes")
    official_tool = rsi_root / "tools/build_p2_corpus.py"
    if not official_tool.is_file():
        raise CorpusBuildError("Frozen official corpus builder is missing")
    try:
        git = ["git", "-c", f"safe.directory={rsi_root}", "-C", str(rsi_root)]
        commit = subprocess.check_output(
            [*git, "rev-parse", "HEAD"], text=True
        ).strip()
        tracked_status = subprocess.check_output(
            [*git, "status", "--porcelain", "--untracked-files=no"],
            text=True,
        ).strip()
        committed_tool_hash = hashlib.sha256(
            subprocess.check_output([*git, "show", "HEAD:tools/build_p2_corpus.py"])
        ).hexdigest()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CorpusBuildError("Cannot verify the pinned RSIAgent Git identity") from exc
    actual_tool_hash = sha256_file(official_tool)
    if commit != EXPECTED_RSI_COMMIT or tracked_status or committed_tool_hash != actual_tool_hash:
        raise CorpusBuildError("Pinned RSIAgent Git identity or official builder is not clean")
    declared_tool_hash = exported.get("tools/build_p2_corpus.py")
    manifest_drift = {
        "source_manifest_sha256": sha256_file(source_manifest_path),
        "declared_builder_sha256": declared_tool_hash,
        "actual_builder_sha256": actual_tool_hash,
        "matches": declared_tool_hash == actual_tool_hash,
        "handling": "Recorded only. The clean fixed Git commit and git-show file hash are the "
        "admission identity; the upstream manifest is not modified.",
    }
    task_root = osworld_root / "evaluation_examples/task_class"
    identity = task_content_identity(task_root)
    if identity.get("task_count") != lock.get("task_count"):
        raise CorpusBuildError("Pinned task count mismatch")
    for key in ("content_tree_sha256", "total_bytes"):
        if identity.get(key) != task_files.get(key):
            raise CorpusBuildError(f"Pinned task {key} mismatch")
    return {
        "baseline_lock_sha256": sha256_file(lock_path),
        "release_manifest": {
            "path": str(release.get("path")),
            "sha256": str(release.get("sha256")),
        },
        "rsi_git": {
            "commit": commit,
            "tracked_status_clean": True,
            "git_show_builder_sha256": committed_tool_hash,
        },
        "source_manifest_builder_drift": manifest_drift,
        "task_source_identity": identity,
    }


def build_instruction_corpus(
    *, rsi_root: Path, osworld_root: Path, output: Path, receipt: Path
) -> dict[str, Any]:
    task_content_identity, official = load_official_modules(rsi_root, osworld_root)
    verified = verify_frozen_inputs(rsi_root, osworld_root, task_content_identity)
    old_cwd = Path.cwd()
    instructions: list[str] = []
    absent: list[str] = []
    adaptations: list[dict[str, str]] = []
    try:
        os.chdir(osworld_root)
        for index in range(1, 121):
            task_id = f"task_{index:03d}"
            if task_id in ADAPTED_TASKS:
                source = osworld_root / "evaluation_examples/task_class" / f"{task_id}.py"
                instruction = extract_static_base_instruction(source, ADAPTED_TASKS[task_id])
                adaptations.append(
                    {
                        "task_id": task_id,
                        "class_name": ADAPTED_TASKS[task_id],
                        "source_sha256": sha256_file(source),
                        "instruction_sha256": hashlib.sha256(
                            instruction.encode("utf-8")
                        ).hexdigest(),
                        "reason": "module import requires GitLab administrator configuration; "
                        "AST extracted only the unique class-level base_instruction and "
                        "any unique module-level literal-string interpolation",
                        "official_instantiation": "not attempted",
                    }
                )
            else:
                try:
                    config = official["resolve_task_json_path"](
                        task_id=task_id, base_dir="evaluation_examples", eval_version="v2"
                    )
                    task = official["load_task_config"](
                        config, task_id=task_id, base_dir="evaluation_examples", eval_version="v2"
                    )
                    instruction = str(
                        getattr(task, "base_instruction", None)
                        or getattr(task, "instruction", None)
                        or task["instruction"]
                    )
                except Exception:  # noqa: BLE001 - mirrors official absent-task handling
                    absent.append(task_id)
                    continue
            instructions.append(official["normalize_instruction_for_corpus"](instruction))
    finally:
        os.chdir(old_cwd)
    if len(instructions) != 108:
        raise CorpusBuildError(
            f"Expected all 108 locked task instructions, observed {len(instructions)}; "
            f"absent={absent}"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or receipt.exists():
        raise FileExistsError("Refusing to overwrite an existing corpus or receipt")
    official["build_corpus"](str(output), instructions)
    corpus_bytes = output.read_bytes()
    manifest = {
        "schema_version": 2,
        "benchmark": "OSWorld-V2",
        "evaluation_version": "v2",
        "normalization": official["CORPUS_NORMALIZATION"],
        "instruction_count": len(instructions),
        "absent_task_ids": absent,
        "shingle_count": len(json.loads(corpus_bytes)),
        "corpus_sha256": hashlib.sha256(corpus_bytes).hexdigest(),
    }
    manifest_path = Path(f"{output}.MANIFEST.json")
    if manifest_path.exists():
        raise FileExistsError("Refusing to overwrite an existing corpus manifest")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    official["validate_instruction_corpus"](str(output), expected_instruction_count=108)
    result = {
        "schema_version": 1,
        "kind": "trace2task_rsi_instruction_loader_adaptation",
        "scope": "host-side anti-leakage corpus only; never actor context, memory, guest, task setup, or evaluation",
        "verified_inputs": verified,
        "corpus": {
            "path": str(output),
            "manifest_path": str(manifest_path),
            **manifest,
        },
        "adaptations": adaptations,
        "non_claim": "Tasks 026 and 041 were not officially instantiated by this adapter.",
    }
    receipt.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rsi-root", type=Path, required=True)
    parser.add_argument("--osworld-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    result = build_instruction_corpus(
        rsi_root=args.rsi_root.resolve(strict=True),
        osworld_root=args.osworld_root.resolve(strict=True),
        output=args.output.resolve(),
        receipt=args.receipt.resolve(),
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
