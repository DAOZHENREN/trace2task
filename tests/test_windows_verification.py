from __future__ import annotations

import json
from pathlib import Path

import pygame

from trace2task.windows_verification import (
    EffectVerifierSpec,
    VerificationOutcome,
    VerificationRequest,
    verify_effect,
    write_verification_receipt,
)


def _surface(color: tuple[int, int, int]) -> pygame.Surface:
    surface = pygame.Surface((120, 80))
    surface.fill(color)
    return surface


def _spec(tmp_path: Path, verifier_type: str, **options: object) -> EffectVerifierSpec:
    reference = tmp_path / "reference.png"
    pygame.image.save(_surface((20, 100, 60)), reference)
    return EffectVerifierSpec(
        verifier_type=verifier_type,
        expected="The reviewed final state is visible.",
        reference_frame=reference,
        options=options,
    )


def test_reviewed_reference_records_completion_without_claiming_verification(
    tmp_path: Path,
) -> None:
    receipt = verify_effect(
        _spec(tmp_path, "reviewed_reference_frame"),
        VerificationRequest(
            task_id="demo",
            run_dir=tmp_path / "run",
            observed_frame=_surface((20, 100, 60)),
            agent_claimed_complete=True,
            agent_reason="The final screen looks correct.",
            agent_confidence=0.9,
        ),
    )
    path = write_verification_receipt(tmp_path / "run", receipt)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert receipt.outcome == VerificationOutcome.COMPLETED_UNVERIFIED
    assert receipt.accepts_completion is True
    assert receipt.verified is False
    assert payload["evidence"]["evidence_tier"] == "model_visual_claim"
    assert (tmp_path / "run" / payload["evidence"]["observed_frame"]).is_file()


def test_pixel_reference_independently_verifies_matching_pixels(tmp_path: Path) -> None:
    receipt = verify_effect(
        _spec(tmp_path, "pixel_reference", threshold=0.01),
        VerificationRequest(
            task_id="demo",
            run_dir=tmp_path / "run",
            observed_frame=_surface((20, 100, 60)),
            agent_claimed_complete=True,
        ),
    )

    assert receipt.outcome == VerificationOutcome.VERIFIED
    assert receipt.verified is True
    assert receipt.evidence["difference"] == 0.0


def test_pixel_reference_requires_reconciliation_when_agent_and_pixels_disagree(
    tmp_path: Path,
) -> None:
    receipt = verify_effect(
        _spec(tmp_path, "pixel_reference", threshold=0.01),
        VerificationRequest(
            task_id="demo",
            run_dir=tmp_path / "run",
            observed_frame=_surface((220, 20, 20)),
            agent_claimed_complete=True,
        ),
    )

    assert receipt.outcome == VerificationOutcome.RECONCILIATION_REQUIRED
    assert receipt.accepts_completion is False
    assert receipt.evidence["difference"] > receipt.evidence["threshold"]
