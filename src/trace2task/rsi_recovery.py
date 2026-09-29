"""Fail-closed admission for an official Phase-1 completed-boundary resume.

This module intentionally does not reconstruct an interrupted project.  It
checks the immutable Trace2Task manifest, then delegates the boundary proof to
the pinned upstream validator.  The latter is the authority for episode,
memory and Curriculum-transcript continuity.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RecoveryEligibility:
    eligible: bool
    reason: str
    lineage_manifest_sha256: str | None = None
    boundary_projects: int | None = None


def _canonical_json(value: object) -> object:
    """Normalize JSON's tuple/list distinction before an exact comparison."""
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _load_real_json(path: Path) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("missing_or_unsafe_file")
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise TypeError("not_an_object")
    return value, raw


def _official_validator() -> Callable[..., dict[str, Any]]:
    # Import only after the pinned upstream checkout and import path have been
    # admitted by the worker.  No local approximation of this validator exists.
    from explore.practice_loop import _resume_completed_phase1_boundary

    return _resume_completed_phase1_boundary


def validate_completed_boundary(
    *,
    lineage: Path,
    expected_manifest: Mapping[str, Any],
    target_direction: str,
    project_budget: int,
    checkpoint_projects: tuple[int, ...],
    phase1_exploration: bool = True,
    phase1_target_conditioned: bool = False,
    validator: Callable[..., dict[str, Any]] | None = None,
) -> RecoveryEligibility:
    """Return eligibility without modifying the lineage, VM, or job state.

    ``validator`` exists only as a deterministic test seam.  Production passes
    ``None`` and calls the frozen upstream private validator directly.
    """
    try:
        if lineage.is_symlink() or not lineage.is_dir():
            return RecoveryEligibility(False, "lineage_missing_or_unsafe")
        if not isinstance(target_direction, str) or not target_direction.strip():
            return RecoveryEligibility(False, "invalid_target_direction")
        if type(project_budget) is not int or project_budget < 0:
            return RecoveryEligibility(False, "invalid_project_budget")
        if (not isinstance(checkpoint_projects, tuple)
                or any(type(index) is not int or index < 0 for index in checkpoint_projects)):
            return RecoveryEligibility(False, "invalid_checkpoint_schedule")
        manifest, raw_manifest = _load_real_json(lineage / "manifest.json")
        expected = _canonical_json(dict(expected_manifest))
        if manifest != expected:
            return RecoveryEligibility(False, "immutable_manifest_mismatch")
        selected_validator = validator or _official_validator()
        boundary = selected_validator(
            lineage,
            target_direction,
            project_budget=project_budget,
            checkpoint_projects=checkpoint_projects,
            phase1_exploration=phase1_exploration,
            phase1_target_conditioned=phase1_target_conditioned,
        )
        projects = boundary.get("project_index") if isinstance(boundary, dict) else None
        if type(projects) is not int or projects < 0:
            return RecoveryEligibility(False, "official_boundary_result_invalid")
        return RecoveryEligibility(
            True,
            "completed_boundary_verified",
            hashlib.sha256(raw_manifest).hexdigest(),
            projects,
        )
    except Exception:  # noqa: BLE001 -- all validator failures are fail-closed admission denials
        return RecoveryEligibility(False, "official_boundary_rejected")
