from pathlib import Path

import pytest

from trace2task.rsi_jobs import PracticeAlreadyActiveError, PracticeRunStore


def create(store):
    return store.create({"instruction": "Practice saving a document", "max_model_calls": 8,
                         "wall_seconds": 120})["id"]


def test_restart_retains_job_and_rejects_stale_worker(tmp_path):
    store = PracticeRunStore(tmp_path)
    run = create(store)
    store.transition(run, "queued", "preflight")
    reopened = PracticeRunStore(tmp_path)
    assert reopened.get(run)["state"] == "preflight"
    with pytest.raises(RuntimeError, match="stale"):
        reopened.transition(run, "queued", "preflight")
    assert len(reopened.events(run)) == 2


def test_stop_is_intent_until_worker_acknowledges(tmp_path):
    store = PracticeRunStore(tmp_path)
    run = create(store)
    store.transition(run, "queued", "preflight")
    store.transition(run, "preflight", "running")
    assert store.request_stop(run)["state"] == "running"
    assert store.request_stop(run)["stop_requested"] is True
    with pytest.raises(RuntimeError, match="Stop is pending"):
        store.transition(run, "running", "completed")
    assert store.transition(run, "running", "cancelled")["state"] == "cancelled"
    assert sum(e["kind"] == "stop_requested" for e in store.events(run)) == 1


def test_candidate_review_is_hash_bound_and_non_destructive(tmp_path):
    store = PracticeRunStore(tmp_path / "rsi")
    original = tmp_path / "guidance.yaml"
    original.write_text("human guidance")
    run = create(store)
    candidate = {"summary": "Check saved file", "verification": {
        "verdict": "PASS", "artifact": "verifier/receipt.json",
    }}
    digest = store.register_candidate(run, candidate)
    assert store.register_candidate(run, candidate) == digest
    with pytest.raises(ValueError, match="match"):
        store.review_candidate(run, "wrong", "accepted")
    result = store.review_candidate(run, digest, "accepted", "Reviewed evidence")
    assert result["review"]["decision"] == "accepted"
    assert original.read_text() == "human guidance"
    with pytest.raises(RuntimeError, match="already reviewed"):
        store.review_candidate(run, digest, "rejected")


def test_corrupt_candidate_and_unverified_candidate_rejected(tmp_path):
    store = PracticeRunStore(tmp_path)
    run = create(store)
    with pytest.raises(ValueError, match="verified"):
        store.register_candidate(run, {"verification": {"verdict": "UNVERIFIED"}})
    digest = store.register_candidate(run, {"verification": {
        "verdict": "PASS", "artifact": "evidence.json",
    }})
    next(tmp_path.glob("candidate-*.json")).write_text("tampered")
    with pytest.raises(RuntimeError, match="changed"):
        store.review_candidate(run, digest, "accepted")


@pytest.mark.parametrize("budget", [0, -1, True, 1.2, None])
def test_no_unbounded_or_ambiguous_budget(tmp_path, budget):
    with pytest.raises(ValueError):
        PracticeRunStore(tmp_path).create({"instruction": "test", "max_model_calls": budget,
                                          "wall_seconds": 10})


def test_attempt_claim_and_call_reservation_are_atomic_and_global(tmp_path):
    store = PracticeRunStore(tmp_path)
    run = create(store)

    first = store.claim_fresh_attempt(run)

    assert first["attempt_no"] == 1
    assert first["model_calls_remaining"] == 8
    assert Path(first["log_dir"]).parts[-3:] == (run, "attempts", "attempt-001")
    with pytest.raises(RuntimeError, match="active"):
        store.claim_fresh_attempt(run)
    assert store.reserve_model_call(run, 1) == 1
    assert store.reserve_model_call(run, 1) == 2
    assert store.finalize_attempt(run, 1, active_elapsed_ms=12_000,
                                  cleanup_confirmed=True) == {
        "model_calls_used": 2,
        "model_calls_remaining": 6,
        "active_elapsed_ms": 12_000,
        "active_remaining_ms": 108_000,
    }
    assert store.attempts(run)[0]["cleanup_confirmed"] is True


def test_create_reports_a_stable_code_when_global_practice_is_already_active(tmp_path):
    store = PracticeRunStore(tmp_path)
    create(store)
    with pytest.raises(PracticeAlreadyActiveError, match="^practice_already_active$") as raised:
        store.create({"instruction": "Another practice", "max_model_calls": 1, "wall_seconds": 1},
                     max_active=1)
    assert raised.value.code == "practice_already_active"


def test_resume_claim_requires_closed_cleanup_boundary_and_preserves_budget(tmp_path):
    store = PracticeRunStore(tmp_path)
    run = create(store)
    store.transition(run, "queued", "preflight")
    store.transition(run, "preflight", "running")
    store.transition(run, "running", "needs_recovery")
    with pytest.raises(RuntimeError, match="not eligible"):
        store.claim_resume_attempt(run, recovery_eligible=False,
                                   lineage_manifest_sha256="a" * 64, boundary_projects=1)
    with pytest.raises(RuntimeError, match="cleanup"):
        store.claim_resume_attempt(run, recovery_eligible=True,
                                   lineage_manifest_sha256="a" * 64, boundary_projects=1)

    # A prior finalized attempt is the durable supervisor cleanup receipt.
    store = PracticeRunStore(tmp_path / "with-cleanup")
    run = create(store)
    first = store.claim_fresh_attempt(run)
    store.reserve_model_call(run, first["attempt_no"])
    store.finalize_attempt(run, first["attempt_no"], active_elapsed_ms=10_000,
                           cleanup_confirmed=True)
    store.transition(run, "queued", "preflight")
    store.transition(run, "preflight", "running")
    store.transition(run, "running", "needs_recovery")

    resumed = store.claim_resume_attempt(run, recovery_eligible=True,
                                         lineage_manifest_sha256="b" * 64, boundary_projects=1)

    assert resumed["attempt_no"] == 2
    assert resumed["model_calls_used"] == 1
    assert resumed["active_elapsed_ms"] == 10_000
    assert store.reserve_model_call(run, 2) == 2


def test_stop_or_unfinished_attempt_never_silently_restarts(tmp_path):
    store = PracticeRunStore(tmp_path)
    run = create(store)
    attempt = store.claim_fresh_attempt(run)
    store.request_stop(run)
    with pytest.raises(RuntimeError, match="Stop is pending"):
        store.claim_fresh_attempt(run)
    charged = store.abandon_unfinished_attempt(run, attempt["attempt_no"])
    assert charged["model_calls_remaining"] == 0
    assert charged["active_remaining_ms"] == 0
    assert store.attempts(run)[0]["state"] == "abandoned"
    assert store.attempts(run)[0]["cleanup_confirmed"] is False


def test_model_call_reservation_refuses_stop_or_nonrunning_state(tmp_path):
    store = PracticeRunStore(tmp_path)
    run = create(store)
    attempt = store.claim_fresh_attempt(run)
    store.transition(run, "queued", "preflight")
    store.request_stop(run)

    with pytest.raises(RuntimeError, match="Stop is pending"):
        store.reserve_model_call(run, attempt["attempt_no"])
    assert store.budget_status(run)["model_calls_used"] == 0

    store = PracticeRunStore(tmp_path / "recovery")
    run = create(store)
    attempt = store.claim_fresh_attempt(run)
    store.transition(run, "queued", "preflight")
    store.transition(run, "preflight", "running")
    store.transition(run, "running", "needs_recovery")
    with pytest.raises(RuntimeError, match="not accepting"):
        store.reserve_model_call(run, attempt["attempt_no"])


def test_model_call_reservation_checks_persisted_wall_budget(tmp_path):
    store = PracticeRunStore(tmp_path)
    run = create(store)
    attempt = store.claim_fresh_attempt(run)
    store.transition(run, "queued", "preflight")
    with store._connect() as db:  # Test a durable ledger value left by a prior supervisor.
        db.execute("UPDATE budget_usage SET active_elapsed_ms=120000 WHERE run_id=?", (run,))

    with pytest.raises(RuntimeError, match="wall-clock"):
        store.reserve_model_call(run, attempt["attempt_no"])
    assert store.budget_status(run)["model_calls_used"] == 0
