"""Interrupt one real RSI executor only after a durable project boundary.

This is an acceptance aid for completed-boundary recovery.  It neither creates
nor recovers a run, and never writes a run, lineage, or ledger record.  After
observing ``PROJECT_CLOSED`` it sends SIGINT only to the matching executor
child inside the current, label-bound controller container.  The official
practice loop then writes its normal ``infra`` state, which the production
supervisor must clean up before the operator can use the regular recovery API.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

from trace2task.rsi_control import load_config, recovery_status
from trace2task.rsi_jobs import PracticeRunStore

_RUN_ID = re.compile(r"^[0-9a-f]{32}$")
_WORKER_LABEL = "org.trace2task.rsi.worker"
_ATTEMPT_LABEL = "org.trace2task.rsi.attempt"


def _run_id(value: str) -> str:
    if not isinstance(value, str) or not _RUN_ID.fullmatch(value):
        raise argparse.ArgumentTypeError("--run-id must be a 32-character lowercase hex ID")
    return value


def _positive(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _events_all(store: PracticeRunStore, run_id: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    after = 0
    while True:
        page = store.events(run_id, after)
        if not page:
            return events
        events.extend(page)
        sequence = page[-1].get("seq")
        if type(sequence) is not int or sequence <= after:
            raise RuntimeError("Practice event ledger is malformed")
        after = sequence
        if len(page) < 1_000:
            return events


def _official_project_event(event: dict[str, Any], event_type: str, project: int) -> bool:
    payload = event.get("payload")
    return (
        event.get("kind") == "official_event"
        and isinstance(payload, dict)
        and payload.get("type") == event_type
        and isinstance(payload.get("payload"), dict)
        and payload["payload"].get("project_index") == project
    )


def _next_project_opened(store: PracticeRunStore, run_id: str, project: int) -> bool:
    return any(_official_project_event(event, "PROJECT_OPENED", project + 1)
               for event in _events_all(store, run_id))


def _closed_project_observed(store: PracticeRunStore, run_id: str, project: int) -> bool:
    return any(_official_project_event(event, "PROJECT_CLOSED", project)
               for event in _events_all(store, run_id))


def _current_active_attempt(store: PracticeRunStore, run_id: str) -> dict[str, Any]:
    active = [attempt for attempt in store.attempts(run_id) if attempt["state"] == "active"]
    if len(active) != 1:
        raise RuntimeError("Expected exactly one active practice attempt")
    return active[0]


def _matching_worker(client: Any, run_id: str, attempt_no: int) -> Any:
    """Return exactly one live controller container with both ownership labels."""
    workers = client.containers.list(all=True, filters={"label": [
        f"{_WORKER_LABEL}={run_id}", f"{_ATTEMPT_LABEL}={attempt_no}",
    ]})
    if not isinstance(workers, list):
        raise TypeError("Docker did not return a worker list")
    matching: list[Any] = []
    for worker in workers:
        reload = getattr(worker, "reload", None)
        if not callable(reload):
            raise TypeError("Worker container cannot be inspected")
        reload()
        labels = getattr(worker, "labels", None)
        attrs = getattr(worker, "attrs", None)
        state = attrs.get("State") if isinstance(attrs, dict) else None
        if (not isinstance(labels, dict) or labels.get(_WORKER_LABEL) != run_id
                or labels.get(_ATTEMPT_LABEL) != str(attempt_no)
                or not isinstance(state, dict) or state.get("Running") is not True):
            raise RuntimeError("Current controller worker identity or state is unproven")
        matching.append(worker)
    if len(matching) != 1:
        raise RuntimeError("Expected exactly one live controller worker for the active attempt")
    return matching[0]


_FIND_AND_INTERRUPT_EXECUTOR = r"""
import json, os, signal, sys
run_id, attempt = sys.argv[1].encode(), sys.argv[2].encode()
matches = []
for entry in os.scandir('/proc'):
    if not entry.name.isdigit():
        continue
    try:
        tokens = open('/proc/' + entry.name + '/cmdline', 'rb').read().split(b'\0')
    except OSError:
        continue
    try:
        module = tokens.index(b'trace2task.rsi_control')
        execute = tokens.index(b'--execute')
        attempt_flag = tokens.index(b'--attempt')
    except ValueError:
        continue
    if (module == 0 or tokens[module - 1] != b'-m'
            or execute + 1 >= len(tokens) or attempt_flag + 1 >= len(tokens)
            or tokens[execute + 1] != run_id or tokens[attempt_flag + 1] != attempt):
        continue
    matches.append(int(entry.name))
if len(matches) != 1:
    print(json.dumps({'matches': matches, 'interrupted': False}))
    raise SystemExit(4)
os.kill(matches[0], signal.SIGINT)
print(json.dumps({'matches': matches, 'interrupted': True}))
"""


def _interrupt_executor(container: Any, controller_python: str, run_id: str, attempt_no: int) -> int:
    """Use the trusted controller interpreter to find and signal only executor argv."""
    result = container.exec_run(
        [controller_python, "-c", _FIND_AND_INTERRUPT_EXECUTOR, run_id, str(attempt_no)],
        stdout=True,
        stderr=True,
    )
    code = getattr(result, "exit_code", None)
    output = getattr(result, "output", b"")
    if code != 0:
        raise RuntimeError("Executor process identity is unproven; SIGINT was not sent")
    if isinstance(output, tuple):
        output = b"".join(part for part in output if isinstance(part, bytes))
    try:
        parsed = json.loads(bytes(output).decode("utf-8"))
    except (TypeError, UnicodeDecodeError, ValueError) as error:
        raise RuntimeError("Executor interrupt confirmation was malformed") from error
    matches = parsed.get("matches") if isinstance(parsed, dict) else None
    if (parsed.get("interrupted") is not True or not isinstance(matches, list)
            or len(matches) != 1 or type(matches[0]) is not int):
        raise RuntimeError("Executor interrupt was not confirmed")
    return matches[0]


def _partial_next_episode(store: PracticeRunStore, run_id: str, project: int) -> bool:
    path = store.root / run_id / "lineage" / "episodes" / f"ep{project + 1:03d}"
    try:
        return path.is_symlink() or (path.exists() and any(path.iterdir()))
    except OSError:
        return True


def _write_receipt(store: PracticeRunStore, run_id: str, receipt: dict[str, Any]) -> Path:
    attempt_no = receipt.get("attempt_no")
    if type(attempt_no) is not int or attempt_no < 1:
        raise ValueError("Interrupt receipt requires a positive attempt number")
    path = store.root / run_id / f"interrupt-boundary-attempt-{attempt_no:03d}.json"
    with path.open("x", encoding="utf-8") as output:
        json.dump(receipt, output, ensure_ascii=False, indent=2)
        output.write("\n")
    return path


def run(*, config: dict[str, Any], run_id: str, project: int, timeout_seconds: int,
        docker_client_factory=None, sleep=time.sleep, monotonic=time.monotonic) -> dict[str, Any]:
    """Monitor an already-running production attempt and validate recovery admission."""
    root = Path(config["root"])
    store = PracticeRunStore(root / "jobs")
    attempt = _current_active_attempt(store, run_id)
    if store.get(run_id)["stop_requested"]:
        raise RuntimeError("A stop request is already pending")
    deadline = monotonic() + timeout_seconds
    receipt: dict[str, Any] = {
        "kind": "completed_boundary_external_sigint",
        "run_id": run_id,
        "attempt_no": attempt["attempt_no"],
        "project": project,
        "timeout_seconds": timeout_seconds,
        "interrupted": False,
    }
    try:
        while monotonic() < deadline:
            if _next_project_opened(store, run_id, project) or _partial_next_episode(store, run_id, project):
                raise RuntimeError("Next project opened before the boundary interrupt; recovery must be refused")
            if _closed_project_observed(store, run_id, project):
                break
            current = store.get(run_id)
            if current["state"] not in {"queued", "preflight", "running", "finalizing"}:
                raise RuntimeError("Run left its active lifecycle before the required project closed")
            sleep(0.25)
        else:
            raise TimeoutError("Timed out waiting for the completed project boundary")

        # Recheck directly before signaling; never interrupt if the next project
        # has already become durable while Docker discovery was in progress.
        if _next_project_opened(store, run_id, project) or _partial_next_episode(store, run_id, project):
            raise RuntimeError("Next project opened before executor interrupt; no signal sent")
        client = (docker_client_factory or _default_docker_client)()
        try:
            worker = _matching_worker(client, run_id, attempt["attempt_no"])
            pid = _interrupt_executor(
                worker, str(root / "controller-venv" / "bin" / "python"), run_id, attempt["attempt_no"]
            )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
        receipt["interrupted"] = True
        receipt["executor_pid"] = pid

        while monotonic() < deadline:
            current = store.get(run_id)
            attempts = store.attempts(run_id)
            current_attempt = next(item for item in attempts if item["attempt_no"] == attempt["attempt_no"])
            if _next_project_opened(store, run_id, project) or _partial_next_episode(store, run_id, project):
                raise RuntimeError("Next project opened despite interrupt; recovery must be refused")
            if current["state"] == "needs_recovery" and current_attempt["cleanup_confirmed"] is True:
                status = recovery_status(config, store, run_id)
                receipt.update({
                    "state": current["state"], "cleanup_confirmed": True,
                    "budget": store.budget_status(run_id), "recovery": status,
                    "passed": status.get("eligible") is True
                })
                if status.get("eligible") is not True:
                    raise RuntimeError("Official completed-boundary recovery validation rejected this run")
                return receipt
            if current["state"] in {"completed", "failed", "cancelled"}:
                raise RuntimeError("Run reached an unexpected terminal state after executor interrupt")
            sleep(0.25)
        raise TimeoutError("Timed out waiting for cleanup-confirmed needs_recovery")
    except BaseException as error:
        receipt.update({"passed": False, "error_type": type(error).__name__, "error": str(error)})
        raise
    finally:
        # Evidence only.  This exclusive write deliberately cannot overwrite a
        # receipt from a prior attempted interruption of the same immutable run.
        _write_receipt(store, run_id, receipt)


def _default_docker_client() -> Any:
    import docker

    return docker.from_env()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-id", type=_run_id, required=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--project", type=_positive, default=1)
    group.add_argument("--project1", dest="project", action="store_const", const=1,
                       help="Alias for --project 1")
    parser.add_argument("--timeout-seconds", type=_positive, default=3000)
    args = parser.parse_args()
    if args.timeout_seconds > 3000:
        parser.error("--timeout-seconds must not exceed 3000")
    config = load_config(args.config)
    receipt = run(config=config, run_id=args.run_id, project=args.project,
                  timeout_seconds=args.timeout_seconds)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
