"""One real Actor-format subscription turn; parse-only, with no VM or program run."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

from trace2task.rsi_codex import (
    UPSTREAM_REVISION,
    CodexRSITransport,
    program_boundary_preflight_instructions,
)
from trace2task.rsi_control import load_config
from trace2task.rsi_model_session import ModelContainerConfig, session_factory

_TARGET = "/home/user/evolution_project/trace2task-program-boundary-smoke.txt"
_WRITE_MARKER = re.compile(
    r"(?:write_text|write_bytes|\bopen\s*\(|\btee\b|\bprintf\b|\bmkdir\b|\btouch\b)",
    re.IGNORECASE,
)


def _official_actor_system(upstream: Path) -> tuple[str, str]:
    """Import the pinned upstream Actor surface and return its actual SYSTEM value."""
    upstream = upstream.resolve()
    actor_path = upstream / "core" / "actor.py"
    if upstream.is_symlink() or actor_path.is_symlink() or not actor_path.is_file():
        raise RuntimeError("Pinned upstream Actor source is unavailable")
    revision = subprocess.run(
        ["git", "-C", str(upstream), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    if revision != UPSTREAM_REVISION:
        raise RuntimeError("Pinned upstream revision does not match the RSI transport")
    status = subprocess.run(
        ["git", "-C", str(upstream), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout
    if status:
        raise RuntimeError("Pinned upstream checkout is dirty")
    sys.path.insert(0, str(upstream))
    actor = importlib.import_module("core.actor")
    if Path(actor.__file__).resolve() != actor_path:
        raise RuntimeError("Actor module was not imported from the pinned upstream checkout")
    system = getattr(actor, "SYSTEM", None)
    if not isinstance(system, str) or not system.strip():
        raise RuntimeError("Pinned core.actor.SYSTEM is unavailable")
    return system, hashlib.sha256(actor_path.read_bytes()).hexdigest()


def _parse_mutating_program(answer: str, upstream: Path) -> tuple[str, str]:
    """Use the official parser, but never evaluate, compile, or execute its code."""
    sys.path.insert(0, str(upstream.resolve()))
    actor = importlib.import_module("core.actor")
    parsed = actor.parse_turn(answer)
    if not isinstance(parsed, actor.Program):
        raise TypeError("Actor response did not contain one parseable Program")
    if parsed.lang not in {"python", "bash"}:
        raise RuntimeError("Actor Program language is unsupported for this preflight")
    # The probe asks for a create-and-readback proposal. This is intentionally a
    # shallow boundary assertion, not an attempted static proof of program behavior.
    if _TARGET not in parsed.code or _WRITE_MARKER.search(parsed.code) is None:
        raise RuntimeError("Actor Program appears read-only; required guest write was not proposed")
    return parsed.lang, hashlib.sha256(parsed.code.encode("utf-8")).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    if "gpt-6-astra" not in config.get("models", []):
        raise RuntimeError("Deployment does not explicitly allow gpt-6-astra for this smoke")
    root = Path(config["root"]).resolve()
    upstream = root / "upstream"
    actor_system, actor_sha256 = _official_actor_system(upstream)
    run_dir = root / "smoke" / uuid.uuid4().hex
    run_dir.mkdir(parents=True, exist_ok=False)
    model_config = ModelContainerConfig(
        Path(config["codex_home"]),
        Path(config["codex_bin"]),
        root / "controller/src/trace2task/rsi_model_only.py",
        os.getuid(),
        os.getgid(),
    )
    adapter = CodexRSITransport(
        session_factory=session_factory(model_config),
        model="gpt-6-astra",
        reasoning_effort="low",
        audit_dir=run_dir / "calls",
        max_calls=1,
        wall_seconds=180,
    )
    user = (
        "Program-boundary preflight only. Return exactly one Actor JSON program proposal. "
        f"For the isolated guest, create the small text file {_TARGET!r}, then read it back "
        "and print a verification line. Do not use a Look, Ask, or Done object. Do not claim "
        "that anything executed: this proposal will be parsed only and discarded."
    )
    started = time.monotonic()
    receipt = {
        "kind": "subscription_actor_program_boundary",
        "upstream_revision": UPSTREAM_REVISION,
        "actor_system_sha256": actor_sha256,
        "model": "gpt-6-astra",
        "reasoning_effort": "low",
        "max_model_calls": 1,
        "wall_seconds": 180,
        "vm_started": False,
        "program_executed": False,
        "program_submitted_to_guest": False,
    }
    try:
        answer = adapter.chat(
            "gpt-6-astra",
            actor_system + program_boundary_preflight_instructions(),
            user,
        )
        language, program_sha256 = _parse_mutating_program(answer, upstream)
        receipt.update(
            status="completed",
            parsed_action="Program",
            program_language=language,
            program_sha256=program_sha256,
            program_is_non_readonly=True,
        )
    except Exception as error:
        receipt.update(status="error", error_type=type(error).__name__)
        raise
    finally:
        receipt["elapsed_ms"] = round((time.monotonic() - started) * 1000, 1)
        receipt["audit_call_dir"] = "calls/call-000001"
        (run_dir / "result.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
