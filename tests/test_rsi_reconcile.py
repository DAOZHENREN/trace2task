from types import SimpleNamespace

from trace2task.rsi_jobs import PracticeRunStore
from trace2task.rsi_reconcile import reconcile_stopped_orphan

RUN_OWNER_LABEL = "org.trace2task.rsi.worker"


def create_active_run(tmp_path):
    store = PracticeRunStore(tmp_path)
    run = store.create({"instruction": "Practice safe saving", "max_model_calls": 4,
                        "wall_seconds": 60})["id"]
    attempt = store.claim_fresh_attempt(run)
    store.transition(run, "queued", "preflight")
    store.transition(run, "preflight", "running")
    store.request_stop(run)
    return store, run, attempt["attempt_no"]


class Worker:
    def __init__(self, run_id, attempt_no, running):
        self.id = "worker-1"
        self.labels = {RUN_OWNER_LABEL: run_id, "org.trace2task.rsi.attempt": str(attempt_no)}
        self.attrs = {"State": {"Running": running}}
        self.reloaded = 0

    def reload(self):
        self.reloaded += 1


class Docker:
    def __init__(self, workers):
        self.containers = SimpleNamespace(list=self.list)
        self.workers = workers
        self.calls = []
        self.closed = False

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return self.workers

    def close(self):
        self.closed = True


def launch_confirmed(store, run_id, attempt_no):
    store.append_event(run_id, "launched", {"container_id": f"worker-{attempt_no}"})


def test_reconcile_requires_a_stop_request_and_never_touches_docker(tmp_path):
    store = PracticeRunStore(tmp_path)
    run = store.create({"instruction": "Practice", "max_model_calls": 2, "wall_seconds": 60})["id"]
    docker = Docker([])

    result = reconcile_stopped_orphan(store, run, docker_client_factory=lambda: docker,
                                      cleanup_owned=lambda _: [])

    assert result["status"] == "not_requested"
    assert docker.calls == []


def test_running_worker_is_left_to_its_supervisor(tmp_path):
    store, run, _attempt = create_active_run(tmp_path)
    worker = Worker(run, 1, True)
    docker = Docker([worker])
    cleanup = []

    result = reconcile_stopped_orphan(store, run, docker_client_factory=lambda: docker,
                                      cleanup_owned=lambda value: cleanup.append(value))

    assert result["status"] == "pending"
    assert result["reason"] == "worker_still_running"
    assert store.get(run)["state"] == "running"
    assert store.attempts(run)[0]["state"] == "active"
    assert cleanup == []
    assert docker.calls == [{"all": True, "filters": {"label": [
        f"{RUN_OWNER_LABEL}={run}", "org.trace2task.rsi.attempt=1",
    ]}}]
    assert worker.reloaded == 1 and docker.closed is True


def test_absent_worker_without_successful_launch_receipt_stays_pending(tmp_path):
    store, run, _attempt = create_active_run(tmp_path)
    docker = Docker([])

    result = reconcile_stopped_orphan(store, run, docker_client_factory=lambda: docker,
                                      cleanup_owned=lambda _: [])

    assert result["status"] == "pending"
    assert result["reason"] == "worker_absence_unproven"
    assert store.attempts(run)[0]["state"] == "active"


def test_confirmed_absent_worker_conservatively_consumes_budget_then_cancels(tmp_path):
    store, run, attempt = create_active_run(tmp_path)
    launch_confirmed(store, run, attempt)
    docker = Docker([])
    cleanup_calls = []

    result = reconcile_stopped_orphan(
        store, run, docker_client_factory=lambda: docker,
        cleanup_owned=lambda value: cleanup_calls.append(value) or ["guest-1"],
    )

    assert result["status"] == "cancelled"
    assert store.get(run)["state"] == "cancelled"
    assert store.attempts(run)[0]["state"] == "abandoned"
    assert store.budget_status(run)["model_calls_remaining"] == 0
    assert store.budget_status(run)["active_remaining_ms"] == 0
    assert cleanup_calls == [run]
    kinds = [event["kind"] for event in store.events(run)]
    assert "stop_reconcile_worker_confirmed_stopped" in kinds
    assert "stop_reconcile_cleanup_confirmed" in kinds


def test_stopped_labeled_worker_is_verified_before_cleanup(tmp_path):
    store, run, _attempt = create_active_run(tmp_path)
    worker = Worker(run, 1, False)
    docker = Docker([worker])

    result = reconcile_stopped_orphan(store, run, docker_client_factory=lambda: docker,
                                      cleanup_owned=lambda _: [])

    assert result["status"] == "cancelled"
    assert worker.reloaded == 1


def test_finalized_recovery_attempt_can_be_stopped_and_cancelled(tmp_path):
    store, run, attempt = create_active_run(tmp_path)
    launch_confirmed(store, run, attempt)
    store.finalize_attempt(run, attempt, active_elapsed_ms=1_000, cleanup_confirmed=True)
    store.transition(run, "running", "needs_recovery")

    result = reconcile_stopped_orphan(store, run, docker_client_factory=lambda: Docker([]),
                                      cleanup_owned=lambda _: [])

    assert result["status"] == "cancelled"
    assert store.get(run)["state"] == "cancelled"
    assert store.budget_status(run)["model_calls_remaining"] == 4
    assert store.attempts(run)[0]["state"] == "finalized"


def test_abandoned_attempt_retries_cleanup_without_recharging_budget(tmp_path):
    store, run, attempt = create_active_run(tmp_path)
    launch_confirmed(store, run, attempt)
    calls = []

    first = reconcile_stopped_orphan(
        store, run, docker_client_factory=lambda: Docker([]),
        cleanup_owned=lambda _: (_ for _ in ()).throw(RuntimeError("cannot clean")),
    )
    assert first["reason"] == "cleanup_failed"
    assert store.attempts(run)[0]["state"] == "abandoned"
    calls_before = store.budget_status(run)["model_calls_used"]
    second = reconcile_stopped_orphan(
        store, run, docker_client_factory=lambda: Docker([]),
        cleanup_owned=lambda value: calls.append(value) or [],
    )
    assert second["status"] == "cancelled"
    assert calls == [run]
    assert store.budget_status(run)["model_calls_used"] == calls_before


def test_stopped_worker_from_another_attempt_is_not_evidence_for_this_attempt(tmp_path):
    store, run, _attempt = create_active_run(tmp_path)
    old_worker = Worker(run, 0, False)

    result = reconcile_stopped_orphan(store, run, docker_client_factory=lambda: Docker([old_worker]),
                                      cleanup_owned=lambda _: [])

    assert result["status"] == "pending"
    assert result["reason"] == "worker_absence_unproven"
    assert store.attempts(run)[0]["state"] == "active"


def test_old_worker_does_not_block_current_attempt_worker_evidence(tmp_path):
    store, run, attempt = create_active_run(tmp_path)
    launch_confirmed(store, run, attempt)
    old_worker = Worker(run, 0, False)
    current_worker = Worker(run, attempt, False)

    result = reconcile_stopped_orphan(
        store, run, docker_client_factory=lambda: Docker([old_worker, current_worker]),
        cleanup_owned=lambda _: [],
    )

    assert result["status"] == "cancelled"
    assert old_worker.reloaded == 1 and current_worker.reloaded == 1


def test_launch_receipt_lookup_is_paged_beyond_one_thousand_events(tmp_path):
    store, run, attempt = create_active_run(tmp_path)
    for index in range(1_000):
        store.append_event(run, "noise", {"index": index})
    launch_confirmed(store, run, attempt)

    result = reconcile_stopped_orphan(store, run, docker_client_factory=lambda: Docker([]),
                                      cleanup_owned=lambda _: [])

    assert result["status"] == "cancelled"


def test_unknown_docker_or_cleanup_failure_never_claims_cancellation(tmp_path):
    store, run, _attempt = create_active_run(tmp_path / "docker-error")

    result = reconcile_stopped_orphan(
        store, run, docker_client_factory=lambda: (_ for _ in ()).throw(OSError("offline")),
        cleanup_owned=lambda _: [],
    )
    assert result["reason"] == "docker_unavailable"
    assert store.attempts(run)[0]["state"] == "active"

    store, run, attempt = create_active_run(tmp_path / "cleanup-error")
    launch_confirmed(store, run, attempt)
    result = reconcile_stopped_orphan(
        store, run, docker_client_factory=lambda: Docker([]),
        cleanup_owned=lambda _: (_ for _ in ()).throw(RuntimeError("cannot clean")),
    )
    assert result["reason"] == "cleanup_failed"
    assert store.get(run)["state"] == "running"
    assert store.attempts(run)[0]["state"] == "abandoned"
