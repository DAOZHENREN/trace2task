"""Fail-closed reconciliation for an orphaned, stop-requested RSI worker.

The ordinary supervisor owns worker and guest cleanup.  This module is only a
recovery path for the narrow case where that supervisor no longer makes
progress.  It never guesses that a Docker launch failed: a missing worker is
actionable only after the durable ledger records that the launch call returned
successfully.  All other uncertain states remain pending for an operator.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from trace2task.rsi_jobs import TERMINAL, PracticeRunStore

_WORKER_LABEL = "org.trace2task.rsi.worker"
_ATTEMPT_LABEL = "org.trace2task.rsi.attempt"
_LAUNCH_EVENTS = frozenset({"launched", "recovery_launched"})


def _default_docker_client() -> Any:
    import docker

    return docker.from_env()


def _default_cleanup_owned(run_id: str) -> list[str]:
    # Import lazily: rsi_control imports the store and should not need this
    # reconciliation module merely to service ordinary start/stop requests.
    from trace2task.rsi_control import cleanup_owned

    return cleanup_owned(run_id)


def _pending(store: PracticeRunStore, run_id: str, reason: str, **detail: Any) -> dict[str, Any]:
    store.append_event(run_id, "stop_reconcile_pending", {"reason": reason, **detail})
    return {"status": "pending", "reason": reason, "run": store.get(run_id)}


def _reconcilable_attempt(store: PracticeRunStore, run_id: str) -> dict[str, Any] | None:
    active = [attempt for attempt in store.attempts(run_id) if attempt["state"] == "active"]
    if len(active) > 1:
        return None
    if active:
        return active[0]
    attempts = store.attempts(run_id)
    if not attempts:
        return None
    latest = attempts[-1]
    # A normal supervisor may have finalized a recovery-worthy attempt before
    # the user requests stop.  An earlier reconciliation may also have
    # abandoned it before cleanup failed.  Neither case should strand the run.
    return latest if latest["state"] in {"finalized", "abandoned"} else None


def _events_all(store: PracticeRunStore, run_id: str) -> list[dict[str, Any]]:
    """Read the append-only event log beyond the store's one-page limit."""
    events: list[dict[str, Any]] = []
    after = 0
    while True:
        page = store.events(run_id, after)
        if not page:
            return events
        events.extend(page)
        next_after = page[-1].get("seq")
        if type(next_after) is not int or next_after <= after:
            # A malformed or changing ledger page is not proof of a launch.
            return []
        after = next_after
        if len(page) < 1_000:
            return events


def _launch_is_durably_confirmed(store: PracticeRunStore, run_id: str, attempt_no: int) -> bool:
    """Return true only for a successful launch receipt after this claim.

    ``launch_uncertain`` deliberately does not count.  A timed-out Docker API
    request could still create its container later, so treating absence as a
    stopped worker in that case would race a late worker start.
    """
    events = _events_all(store, run_id)
    claim_seq = max((event["seq"] for event in events
                     if event["kind"] == "attempt_claimed"
                     and event["payload"].get("attempt_no") == attempt_no), default=None)
    if claim_seq is None:
        return False
    return any(event["seq"] > claim_seq and event["kind"] in _LAUNCH_EVENTS for event in events)


def _inspect_workers(
    run_id: str, attempt_no: int, docker_client_factory: Callable[[], Any],
) -> tuple[str, list[str]]:
    """Return ``running``, ``stopped``, ``absent`` or ``unknown``.

    The sole Docker discovery operation is an exact label filter.  Every
    returned object is reloaded and its label and running flag are verified;
    malformed or changing container metadata is never treated as stopped.
    """
    client = docker_client_factory()
    try:
        workers = client.containers.list(all=True, filters={"label": [
            f"{_WORKER_LABEL}={run_id}", f"{_ATTEMPT_LABEL}={attempt_no}",
        ]})
        if not isinstance(workers, list):
            return "unknown", []
        if not workers:
            return "absent", []
        identifiers: list[str] = []
        states: list[bool] = []
        for worker in workers:
            reload = getattr(worker, "reload", None)
            if not callable(reload):
                return "unknown", []
            reload()
            labels = getattr(worker, "labels", None)
            attrs = getattr(worker, "attrs", None)
            if not isinstance(labels, dict) or labels.get(_WORKER_LABEL) != run_id:
                return "unknown", []
            # Docker's label filter is advisory at this boundary; a stale
            # container from an older attempt is irrelevant, not contrary
            # evidence about the current attempt.  Ignore it after reload.
            if labels.get(_ATTEMPT_LABEL) != str(attempt_no):
                continue
            if not isinstance(attrs, dict):
                return "unknown", []
            state = attrs.get("State")
            if not isinstance(state, dict) or type(state.get("Running")) is not bool:
                return "unknown", []
            identifier = getattr(worker, "id", None)
            if not isinstance(identifier, str) or not identifier:
                return "unknown", []
            identifiers.append(identifier)
            states.append(state["Running"])
        if not states:
            return "absent", []
        return ("running" if any(states) else "stopped"), identifiers
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


def reconcile_stopped_orphan(
    store: PracticeRunStore,
    run_id: str,
    *,
    docker_client_factory: Callable[[], Any] = _default_docker_client,
    cleanup_owned: Callable[[str], list[str]] = _default_cleanup_owned,
) -> dict[str, Any]:
    """Safely settle a stop-requested run after a supervisor loss.

    A running or unprovable worker is left untouched.  Once a worker is proven
    absent (after a durable successful launch receipt) or stopped, the active
    attempt is first conservatively abandoned, then only this run's guest
    cleanup callback runs.  A cleanup failure leaves the run pending rather
    than falsely reporting cancellation.
    """
    if not callable(docker_client_factory) or not callable(cleanup_owned):
        raise TypeError("RSI reconciliation dependencies must be callable")
    run = store.get(run_id)
    if run["state"] in TERMINAL:
        return {"status": "terminal", "reason": "already_terminal", "run": run}
    if not run["stop_requested"]:
        return {"status": "not_requested", "reason": "stop_not_requested", "run": run}

    attempt = _reconcilable_attempt(store, run_id)
    if attempt is None:
        return _pending(store, run_id, "active_attempt_unproven")
    attempt_no = attempt["attempt_no"]
    store.append_event(run_id, "stop_reconcile_started", {"attempt_no": attempt_no})
    try:
        worker_state, worker_ids = _inspect_workers(run_id, attempt_no, docker_client_factory)
    except Exception as error:  # noqa: BLE001 -- Docker availability is untrusted evidence
        return _pending(store, run_id, "docker_unavailable", error_type=type(error).__name__)
    if worker_state == "running":
        return _pending(store, run_id, "worker_still_running", worker_count=len(worker_ids))
    if worker_state == "unknown":
        return _pending(store, run_id, "worker_state_unproven")
    if worker_state == "absent" and not _launch_is_durably_confirmed(store, run_id, attempt_no):
        return _pending(store, run_id, "worker_absence_unproven")

    store.append_event(run_id, "stop_reconcile_worker_confirmed_stopped", {
        "attempt_no": attempt_no,
        "worker_state": worker_state,
        "worker_count": len(worker_ids),
    })
    if attempt["state"] == "active":
        try:
            store.abandon_unfinished_attempt(run_id, attempt_no)
        except (KeyError, RuntimeError, ValueError) as error:
            return _pending(store, run_id, "attempt_abandon_rejected", error_type=type(error).__name__)
    try:
        removed = cleanup_owned(run_id)
    except Exception as error:  # noqa: BLE001 -- keeping pending is safer than a false terminal state
        return _pending(store, run_id, "cleanup_failed", error_type=type(error).__name__)
    if not isinstance(removed, list) or any(not isinstance(item, str) for item in removed):
        return _pending(store, run_id, "cleanup_receipt_invalid")
    store.append_event(run_id, "stop_reconcile_cleanup_confirmed", {
        "removed_container_ids": removed,
    })
    current = store.get(run_id)
    if current["state"] in TERMINAL:
        return {"status": "terminal", "reason": "already_terminal", "run": current}
    try:
        cancelled = store.transition(
            run_id, current["state"], "cancelled",
            detail={"reason": "stop_orphan_reconciled", "cleanup_confirmed": True,
                    "partial_project_replay_allowed": False},
        )
    except (KeyError, RuntimeError, ValueError) as error:
        return _pending(store, run_id, "terminal_transition_rejected", error_type=type(error).__name__)
    return {"status": "cancelled", "reason": "worker_confirmed_stopped", "run": cancelled}


__all__ = ["reconcile_stopped_orphan"]
