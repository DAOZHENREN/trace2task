import json

from trace2task.rsi_recovery import validate_completed_boundary


def test_completed_boundary_delegates_to_official_validator_without_writes(tmp_path):
    lineage = tmp_path / "lineage"
    lineage.mkdir()
    manifest = {"kind": "trace2task_subscription_adapted_rsi_phase1", "budget": 2}
    state = {"status": "infra", "projects": 1}
    (lineage / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (lineage / "state.json").write_text(json.dumps(state), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in lineage.iterdir()}
    captured = {}

    def official(path, target, **kwargs):
        captured.update(path=path, target=target, **kwargs)
        return {"project_index": 1}

    result = validate_completed_boundary(
        lineage=lineage,
        expected_manifest=manifest,
        target_direction="Practice document reliability",
        project_budget=2,
        checkpoint_projects=(0, 2),
        validator=official,
    )

    assert result.eligible is True
    assert result.reason == "completed_boundary_verified"
    assert result.boundary_projects == 1
    assert len(result.lineage_manifest_sha256) == 64
    assert captured == {
        "path": lineage,
        "target": "Practice document reliability",
        "project_budget": 2,
        "checkpoint_projects": (0, 2),
        "phase1_exploration": True,
        "phase1_target_conditioned": False,
    }
    assert {path.name: path.read_bytes() for path in lineage.iterdir()} == before


def test_recovery_rejects_manifest_drift_without_calling_validator(tmp_path):
    lineage = tmp_path / "lineage"
    lineage.mkdir()
    (lineage / "manifest.json").write_text('{"locked": false}', encoding="utf-8")

    result = validate_completed_boundary(
        lineage=lineage,
        expected_manifest={"locked": True},
        target_direction="practice",
        project_budget=1,
        checkpoint_projects=(0, 1),
        validator=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    assert result.eligible is False
    assert result.reason == "immutable_manifest_mismatch"


def test_recovery_keeps_official_validator_failure_fail_closed(tmp_path):
    lineage = tmp_path / "lineage"
    lineage.mkdir()
    manifest = {"locked": True}
    (lineage / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = validate_completed_boundary(
        lineage=lineage,
        expected_manifest=manifest,
        target_direction="practice",
        project_budget=1,
        checkpoint_projects=(0, 1),
        validator=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("partial episode")),
    )

    assert result == type(result)(False, "official_boundary_rejected")
