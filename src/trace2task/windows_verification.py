from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pygame


class VerificationOutcome(StrEnum):
    VERIFIED = "verified"
    COMPLETED_UNVERIFIED = "completed_unverified"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    FAILED_EXECUTION = "failed_execution"
    CANCELED = "canceled"


@dataclass(frozen=True)
class EffectVerifierSpec:
    verifier_type: str
    expected: str
    reference_frame: Path
    options: dict[str, Any]


@dataclass(frozen=True)
class VerificationRequest:
    task_id: str
    run_dir: Path
    observed_frame: pygame.Surface
    agent_claimed_complete: bool
    agent_reason: str | None = None
    agent_confidence: float | None = None


@dataclass(frozen=True)
class VerificationReceipt:
    schema_version: str
    task_id: str
    outcome: str
    verifier_type: str
    expected: str
    agent_claimed_complete: bool
    verified: bool
    reason: str
    checked_at: str
    evidence: dict[str, Any]

    @property
    def accepts_completion(self) -> bool:
        return self.outcome in {
            VerificationOutcome.VERIFIED,
            VerificationOutcome.COMPLETED_UNVERIFIED,
        }


class EffectVerifier(Protocol):
    def verify(
        self,
        spec: EffectVerifierSpec,
        request: VerificationRequest,
    ) -> VerificationReceipt: ...


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _save_observed(request: VerificationRequest) -> Path:
    evidence_dir = request.run_dir / "verification-evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    path = evidence_dir / "observed-final.png"
    pygame.image.save(request.observed_frame, path)
    return path


def _receipt(
    *,
    spec: EffectVerifierSpec,
    request: VerificationRequest,
    outcome: VerificationOutcome,
    reason: str,
    evidence: dict[str, Any],
) -> VerificationReceipt:
    return VerificationReceipt(
        schema_version="0.1",
        task_id=request.task_id,
        outcome=outcome.value,
        verifier_type=spec.verifier_type,
        expected=spec.expected,
        agent_claimed_complete=request.agent_claimed_complete,
        verified=outcome is VerificationOutcome.VERIFIED,
        reason=reason,
        checked_at=datetime.now(UTC).isoformat(),
        evidence=evidence,
    )


class ReviewedReferenceFrameVerifier:
    """Record the model's visual completion claim without calling it verified."""

    def verify(
        self,
        spec: EffectVerifierSpec,
        request: VerificationRequest,
    ) -> VerificationReceipt:
        observed = _save_observed(request)
        evidence = {
            "observed_frame": observed.relative_to(request.run_dir).as_posix(),
            "observed_sha256": _sha256(observed),
            "reference_frame": str(spec.reference_frame),
            "reference_sha256": _sha256(spec.reference_frame),
            "agent_reason": request.agent_reason,
            "agent_confidence": request.agent_confidence,
            "evidence_tier": "model_visual_claim",
        }
        if request.agent_claimed_complete:
            return _receipt(
                spec=spec,
                request=request,
                outcome=VerificationOutcome.COMPLETED_UNVERIFIED,
                reason=(
                    "The runtime Agent declared completion from the current and reviewed "
                    "reference frames; no independent effect verifier was configured."
                ),
                evidence=evidence,
            )
        return _receipt(
            spec=spec,
            request=request,
            outcome=VerificationOutcome.FAILED_EXECUTION,
            reason="The runtime Agent did not declare the task complete.",
            evidence=evidence,
        )


def _surface_difference(before: pygame.Surface, after: pygame.Surface) -> float:
    if before.get_size() != after.get_size():
        after = pygame.transform.smoothscale(after, before.get_size())
    size = (64, 36)
    before_pixels = pygame.surfarray.array3d(
        pygame.transform.smoothscale(before, size)
    ).astype(np.int16)
    after_pixels = pygame.surfarray.array3d(
        pygame.transform.smoothscale(after, size)
    ).astype(np.int16)
    return round(float(np.mean(np.abs(before_pixels - after_pixels))) / 255, 6)


class PixelReferenceVerifier:
    """Deterministically compare the final pixels with the reviewed reference frame."""

    def verify(
        self,
        spec: EffectVerifierSpec,
        request: VerificationRequest,
    ) -> VerificationReceipt:
        raw_threshold = spec.options.get("threshold", 0.08)
        if not isinstance(raw_threshold, (int, float)) or isinstance(raw_threshold, bool):
            raise TypeError("pixel_reference verifier threshold must be a number")
        threshold = float(raw_threshold)
        if not 0 <= threshold <= 1:
            raise ValueError("pixel_reference verifier threshold must be between 0 and 1")
        observed = _save_observed(request)
        reference = pygame.image.load(spec.reference_frame)
        difference = _surface_difference(reference, request.observed_frame)
        matched = difference <= threshold
        evidence = {
            "observed_frame": observed.relative_to(request.run_dir).as_posix(),
            "observed_sha256": _sha256(observed),
            "reference_frame": str(spec.reference_frame),
            "reference_sha256": _sha256(spec.reference_frame),
            "difference": difference,
            "threshold": threshold,
            "evidence_tier": "deterministic_pixels",
            "agent_reason": request.agent_reason,
            "agent_confidence": request.agent_confidence,
        }
        if matched:
            return _receipt(
                spec=spec,
                request=request,
                outcome=VerificationOutcome.VERIFIED,
                reason=(
                    "The final frame independently matched the reviewed reference within "
                    f"the configured threshold ({difference:.4f} <= {threshold:.4f})."
                ),
                evidence=evidence,
            )
        return _receipt(
            spec=spec,
            request=request,
            outcome=VerificationOutcome.RECONCILIATION_REQUIRED,
            reason=(
                "The Agent declared completion, but deterministic reference comparison "
                f"disagreed ({difference:.4f} > {threshold:.4f})."
            ),
            evidence=evidence,
        )


_VERIFIERS: dict[str, EffectVerifier] = {
    "reviewed_reference_frame": ReviewedReferenceFrameVerifier(),
    "pixel_reference": PixelReferenceVerifier(),
}


def register_effect_verifier(name: str, verifier: EffectVerifier) -> None:
    normalized = name.strip()
    if not normalized:
        raise ValueError("Effect verifier name must not be empty")
    if normalized in _VERIFIERS:
        raise ValueError(f"Effect verifier is already registered: {normalized}")
    _VERIFIERS[normalized] = verifier


def verify_effect(
    spec: EffectVerifierSpec,
    request: VerificationRequest,
) -> VerificationReceipt:
    verifier = _VERIFIERS.get(spec.verifier_type)
    if verifier is None:
        raise ValueError(f"Unsupported Windows effect verifier: {spec.verifier_type}")
    return verifier.verify(spec, request)


def terminal_receipt(
    spec: EffectVerifierSpec,
    *,
    task_id: str,
    outcome: VerificationOutcome,
    reason: str,
    evidence: dict[str, Any] | None = None,
) -> VerificationReceipt:
    if outcome in {
        VerificationOutcome.VERIFIED,
        VerificationOutcome.COMPLETED_UNVERIFIED,
    }:
        raise ValueError("terminal_receipt is only for non-completion outcomes")
    return VerificationReceipt(
        schema_version="0.1",
        task_id=task_id,
        outcome=outcome.value,
        verifier_type=spec.verifier_type,
        expected=spec.expected,
        agent_claimed_complete=False,
        verified=False,
        reason=reason,
        checked_at=datetime.now(UTC).isoformat(),
        evidence=evidence or {},
    )


def write_verification_receipt(run_dir: Path, receipt: VerificationReceipt) -> Path:
    path = run_dir / "verification.json"
    path.write_text(
        json.dumps(asdict(receipt), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path
