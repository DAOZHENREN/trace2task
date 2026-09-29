"""Durable practice-run ledger, independent of browser and worker lifetimes.

Only a worker may report execution progress. A stop request is an intent, not
proof the remote VM stopped. Reviewed candidates are separate artifacts; review
never mutates accepted Trace2Task guidance or the original human demonstration.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TERMINAL = frozenset({"completed", "failed", "cancelled"})
TRANSITIONS = {
    "queued": {"preflight", "cancelled", "failed", "finalizing"},
    "preflight": {"running", "failed", "cancelled", "needs_recovery", "finalizing"},
    "running": {"completed", "failed", "cancelled", "needs_recovery", "finalizing"},
    "finalizing": {"completed", "failed", "cancelled", "needs_recovery"},
    "needs_recovery": {"preflight", "cancelled", "failed"},
}


class PracticeAlreadyActiveError(RuntimeError):
    """The deployment-wide single-practice reservation is already held."""

    code = "practice_already_active"

    def __init__(self) -> None:
        super().__init__(self.code)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class PracticeRunStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "runs.sqlite3"
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, state TEXT NOT NULL, spec TEXT NOT NULL,
                    created TEXT NOT NULL, updated TEXT NOT NULL,
                    stop_requested INTEGER NOT NULL DEFAULT 0,
                    result TEXT, candidate_sha256 TEXT, review TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    time TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS budget_usage (
                    run_id TEXT PRIMARY KEY REFERENCES runs(id),
                    model_calls_used INTEGER NOT NULL DEFAULT 0,
                    active_elapsed_ms INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    attempt_no INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created TEXT NOT NULL,
                    finished TEXT,
                    model_calls_before INTEGER NOT NULL,
                    model_calls_after INTEGER,
                    active_elapsed_ms INTEGER,
                    cleanup_confirmed INTEGER,
                    lineage_manifest_sha256 TEXT,
                    boundary_projects INTEGER,
                    PRIMARY KEY(run_id, attempt_no)
                );
                CREATE INDEX IF NOT EXISTS attempts_active_by_run
                    ON attempts(run_id, state);
            """)

    def _connect(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @staticmethod
    def _event(db, run_id, kind, payload):
        db.execute("INSERT INTO events(run_id,time,kind,payload) VALUES(?,?,?,?)",
                   (run_id, utc_now(), kind, json.dumps(payload, ensure_ascii=False)))

    def create(self, spec: dict[str, Any], *, max_active: int | None = None) -> dict[str, Any]:
        # The service performs deployment/config validation; the ledger enforces
        # unconditional finite limits even when called outside the web service.
        for name in ("max_model_calls", "wall_seconds"):
            value = spec.get(name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not isinstance(spec.get("instruction"), str) or not spec["instruction"].strip():
            raise ValueError("Practice instruction is required")
        run_id = uuid.uuid4().hex
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if max_active is not None:
                if type(max_active) is not int or max_active < 1:
                    raise ValueError("max_active must be a positive integer")
                active = db.execute("SELECT COUNT(*) FROM runs WHERE state IN "
                                    "('queued','preflight','running','finalizing','needs_recovery')").fetchone()[0]
                if active >= max_active:
                    raise PracticeAlreadyActiveError()
            stamp = utc_now()
            db.execute("INSERT INTO runs(id,state,spec,created,updated) VALUES(?,?,?,?,?)",
                       (run_id, "queued", json.dumps(spec, ensure_ascii=False), stamp, stamp))
            db.execute("INSERT INTO budget_usage(run_id) VALUES(?)", (run_id,))
            self._event(db, run_id, "created", {"state": "queued"})
        return self.get(run_id)

    @staticmethod
    def _limits(spec: object) -> tuple[int, int]:
        if not isinstance(spec, dict):
            raise TypeError("Practice run specification is invalid")
        calls, seconds = spec.get("max_model_calls"), spec.get("wall_seconds")
        if type(calls) is not int or calls < 1 or type(seconds) is not int or seconds < 1:
            raise RuntimeError("Practice run limits are invalid")
        return calls, seconds * 1_000

    def _usage(self, db: sqlite3.Connection, run_id: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT model_calls_used,active_elapsed_ms FROM budget_usage WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise RuntimeError("Practice budget ledger is missing")
        return row

    def budget_status(self, run_id: str) -> dict[str, int]:
        run = self.get(run_id)
        call_limit, wall_limit_ms = self._limits(run["spec"])
        with self._connect() as db:
            usage = self._usage(db, run_id)
        return {
            "model_calls_used": usage["model_calls_used"],
            "model_calls_remaining": max(0, call_limit - usage["model_calls_used"]),
            "active_elapsed_ms": usage["active_elapsed_ms"],
            "active_remaining_ms": max(0, wall_limit_ms - usage["active_elapsed_ms"]),
        }

    def attempts(self, run_id: str) -> list[dict[str, Any]]:
        self.get(run_id)
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM attempts WHERE run_id=? ORDER BY attempt_no", (run_id,)
            ).fetchall()
        values = [dict(row) for row in rows]
        for value in values:
            value["cleanup_confirmed"] = (
                None if value["cleanup_confirmed"] is None else bool(value["cleanup_confirmed"])
            )
        return values

    def _claim_attempt(
        self,
        run_id: str,
        *,
        mode: str,
        recovery_eligible: bool = False,
        lineage_manifest_sha256: str | None = None,
        boundary_projects: int | None = None,
    ) -> dict[str, Any]:
        if mode not in {"fresh", "resume"}:
            raise ValueError("Practice attempt mode is invalid")
        if mode == "resume":
            if recovery_eligible is not True:
                raise RuntimeError("Recovery is not eligible at a completed boundary")
            if (not isinstance(lineage_manifest_sha256, str)
                    or len(lineage_manifest_sha256) != 64
                    or any(char not in "0123456789abcdef" for char in lineage_manifest_sha256)):
                raise ValueError("Recovery manifest digest is invalid")
            if type(boundary_projects) is not int or boundary_projects < 0:
                raise ValueError("Recovery boundary project count is invalid")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            run = db.execute("SELECT state,spec,stop_requested FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                raise KeyError("Unknown practice run")
            if run["stop_requested"]:
                raise RuntimeError("Stop is pending; a practice attempt cannot start")
            expected_state = "queued" if mode == "fresh" else "needs_recovery"
            if run["state"] != expected_state:
                raise RuntimeError("Practice attempt is not in a startable state")
            active = db.execute(
                "SELECT attempt_no FROM attempts WHERE run_id=? AND state='active'", (run_id,)
            ).fetchone()
            if active is not None:
                raise RuntimeError("An active practice attempt already exists")
            if mode == "resume":
                prior = db.execute(
                    "SELECT cleanup_confirmed FROM attempts WHERE run_id=? "
                    "ORDER BY attempt_no DESC LIMIT 1", (run_id,)
                ).fetchone()
                if prior is None or prior["cleanup_confirmed"] != 1:
                    raise RuntimeError("Recovery requires confirmed cleanup of the prior attempt")
            call_limit, wall_limit_ms = self._limits(json.loads(run["spec"]))
            usage = self._usage(db, run_id)
            if usage["model_calls_used"] >= call_limit or usage["active_elapsed_ms"] >= wall_limit_ms:
                raise RuntimeError("Practice budget is exhausted; recovery cannot start")
            attempt_no = db.execute(
                "SELECT COALESCE(MAX(attempt_no), 0) + 1 FROM attempts WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            stamp = utc_now()
            db.execute(
                "INSERT INTO attempts(run_id,attempt_no,mode,state,created,model_calls_before,"
                "lineage_manifest_sha256,boundary_projects) VALUES(?,?,?,?,?,?,?,?)",
                (run_id, attempt_no, mode, "active", stamp, usage["model_calls_used"],
                 lineage_manifest_sha256, boundary_projects),
            )
            self._event(db, run_id, "attempt_claimed", {
                "attempt_no": attempt_no, "mode": mode,
                "model_calls_before": usage["model_calls_used"],
                "active_elapsed_ms_before": usage["active_elapsed_ms"],
                "boundary_projects": boundary_projects,
            })
        return {
            "run_id": run_id,
            "attempt_no": attempt_no,
            "mode": mode,
            "log_dir": str(self.attempt_log_dir(run_id, attempt_no)),
            **self.budget_status(run_id),
        }

    def claim_fresh_attempt(self, run_id: str) -> dict[str, Any]:
        return self._claim_attempt(run_id, mode="fresh")

    def claim_resume_attempt(
        self, run_id: str, *, recovery_eligible: bool,
        lineage_manifest_sha256: str, boundary_projects: int,
    ) -> dict[str, Any]:
        return self._claim_attempt(
            run_id, mode="resume", recovery_eligible=recovery_eligible,
            lineage_manifest_sha256=lineage_manifest_sha256, boundary_projects=boundary_projects,
        )

    def attempt_log_dir(self, run_id: str, attempt_no: int) -> Path:
        if type(attempt_no) is not int or attempt_no < 1:
            raise ValueError("Practice attempt number is invalid")
        path = (self.root / run_id / "attempts" / f"attempt-{attempt_no:03d}").resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Practice attempt log path is unsafe")
        return path

    def reserve_model_call(self, run_id: str, attempt_no: int) -> int:
        """Charge a call before network I/O; ambiguous responses still consume it."""
        if type(attempt_no) is not int or attempt_no < 1:
            raise ValueError("Practice attempt number is invalid")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            run = db.execute(
                "SELECT state,spec,stop_requested FROM runs WHERE id=?", (run_id,)
            ).fetchone()
            if run is None:
                raise KeyError("Unknown practice run")
            if run["stop_requested"]:
                raise RuntimeError("Stop is pending; model calls are forbidden")
            call_limit, wall_limit_ms = self._limits(json.loads(run["spec"]))
            attempt = db.execute(
                "SELECT state,mode FROM attempts WHERE run_id=? AND attempt_no=?", (run_id, attempt_no)
            ).fetchone()
            if attempt is None or attempt["state"] != "active":
                raise RuntimeError("Practice attempt is not active")
            # Keep the pre-existing queued claim semantics for the narrow
            # dispatch-before-supervisor handoff.  A freshly claimed resume
            # attempt also briefly remains in needs_recovery until its worker
            # atomically enters preflight; an old fresh attempt in that state
            # is not allowed to spend any more model budget.
            permitted = {"queued", "preflight", "running"}
            if run["state"] not in permitted and not (
                run["state"] == "needs_recovery" and attempt["mode"] == "resume"
            ):
                raise RuntimeError("Practice run is not accepting model calls")
            usage = self._usage(db, run_id)
            if usage["model_calls_used"] >= call_limit:
                raise RuntimeError("Practice model-call budget is exhausted")
            if usage["active_elapsed_ms"] >= wall_limit_ms:
                raise RuntimeError("Practice wall-clock budget is exhausted")
            index = usage["model_calls_used"] + 1
            db.execute(
                "UPDATE budget_usage SET model_calls_used=? WHERE run_id=?", (index, run_id)
            )
            db.execute(
                "UPDATE attempts SET model_calls_after=? WHERE run_id=? AND attempt_no=?",
                (index, run_id, attempt_no),
            )
            self._event(db, run_id, "model_call_reserved", {
                "attempt_no": attempt_no, "global_call_index": index,
            })
        return index

    def finalize_attempt(
        self, run_id: str, attempt_no: int, *, active_elapsed_ms: int,
        cleanup_confirmed: bool,
    ) -> dict[str, Any]:
        """Close a claimed attempt and charge its bounded active elapsed time."""
        if type(attempt_no) is not int or attempt_no < 1:
            raise ValueError("Practice attempt number is invalid")
        if type(active_elapsed_ms) is not int or active_elapsed_ms < 0:
            raise ValueError("Practice active elapsed time is invalid")
        if type(cleanup_confirmed) is not bool:
            raise TypeError("Practice cleanup confirmation must be boolean")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            run = db.execute("SELECT spec FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                raise KeyError("Unknown practice run")
            _call_limit, wall_limit_ms = self._limits(json.loads(run["spec"]))
            attempt = db.execute(
                "SELECT state FROM attempts WHERE run_id=? AND attempt_no=?", (run_id, attempt_no)
            ).fetchone()
            if attempt is None or attempt["state"] != "active":
                raise RuntimeError("Practice attempt is not active")
            usage = self._usage(db, run_id)
            charged = min(active_elapsed_ms, max(0, wall_limit_ms - usage["active_elapsed_ms"]))
            elapsed = usage["active_elapsed_ms"] + charged
            calls = usage["model_calls_used"]
            db.execute(
                "UPDATE budget_usage SET active_elapsed_ms=? WHERE run_id=?", (elapsed, run_id)
            )
            db.execute(
                "UPDATE attempts SET state='finalized',finished=?,model_calls_after=?,"
                "active_elapsed_ms=?,cleanup_confirmed=? WHERE run_id=? AND attempt_no=?",
                (utc_now(), calls, charged, int(cleanup_confirmed), run_id, attempt_no),
            )
            self._event(db, run_id, "attempt_finalized", {
                "attempt_no": attempt_no, "active_elapsed_ms": charged,
                "cleanup_confirmed": cleanup_confirmed, "model_calls_used": calls,
            })
        return self.budget_status(run_id)

    def abandon_unfinished_attempt(self, run_id: str, attempt_no: int) -> dict[str, int]:
        """Conservatively consume all remaining budget after an unaccounted crash.

        This never starts another attempt. A later recovery still requires a
        separate cleanup confirmation and therefore cannot silently continue.
        """
        if type(attempt_no) is not int or attempt_no < 1:
            raise ValueError("Practice attempt number is invalid")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            run = db.execute("SELECT spec FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                raise KeyError("Unknown practice run")
            call_limit, wall_limit_ms = self._limits(json.loads(run["spec"]))
            attempt = db.execute(
                "SELECT state FROM attempts WHERE run_id=? AND attempt_no=?", (run_id, attempt_no)
            ).fetchone()
            if attempt is None or attempt["state"] != "active":
                raise RuntimeError("Practice attempt is not active")
            usage = self._usage(db, run_id)
            db.execute(
                "UPDATE budget_usage SET model_calls_used=?,active_elapsed_ms=? WHERE run_id=?",
                (call_limit, wall_limit_ms, run_id),
            )
            db.execute(
                "UPDATE attempts SET state='abandoned',finished=?,model_calls_after=?,"
                "active_elapsed_ms=?,cleanup_confirmed=0 WHERE run_id=? AND attempt_no=?",
                (utc_now(), call_limit, max(0, wall_limit_ms - usage["active_elapsed_ms"]),
                 run_id, attempt_no),
            )
            self._event(db, run_id, "attempt_abandoned_conservatively", {
                "attempt_no": attempt_no, "model_calls_used": call_limit,
                "active_elapsed_ms": wall_limit_ms,
            })
        return self.budget_status(run_id)

    def get(self, run_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError("Unknown practice run")
        value = dict(row)
        for name in ("spec", "result", "review"):
            value[name] = json.loads(value[name]) if value[name] is not None else None
        value["stop_requested"] = bool(value["stop_requested"])
        return value

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000:
            raise ValueError("Run listing limit must be 1..1000")
        with self._connect() as db:
            ids = [row[0] for row in db.execute(
                "SELECT id FROM runs ORDER BY created DESC LIMIT ?", (limit,))]
        return [self.get(run_id) for run_id in ids]

    def events(self, run_id: str, after: int = 0) -> list[dict[str, Any]]:
        self.get(run_id)
        with self._connect() as db:
            rows = db.execute(
                "SELECT seq,time,kind,payload FROM events WHERE run_id=? AND seq>? "
                "ORDER BY seq LIMIT 1000", (run_id, after)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def append_event(self, run_id: str, kind: str, payload: dict[str, Any]) -> None:
        self.get(run_id)
        with self._connect() as db:
            self._event(db, run_id, kind, payload)

    def request_stop(self, run_id: str) -> dict[str, Any]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state,stop_requested FROM runs WHERE id=?",
                             (run_id,)).fetchone()
            if row is None:
                raise KeyError("Unknown practice run")
            if row["state"] not in TERMINAL and not row["stop_requested"]:
                db.execute("UPDATE runs SET stop_requested=1,updated=? WHERE id=?",
                           (utc_now(), run_id))
                self._event(db, run_id, "stop_requested", {})
        return self.get(run_id)

    def transition(self, run_id: str, expected: str, state: str, *,
                   detail: dict[str, Any] | None = None) -> dict[str, Any]:
        if state not in TRANSITIONS.get(expected, set()):
            raise ValueError(f"Invalid practice transition: {expected} -> {state}")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state,stop_requested FROM runs WHERE id=?",
                             (run_id,)).fetchone()
            if row is None:
                raise KeyError("Unknown practice run")
            if row["state"] != expected:
                raise RuntimeError("Run state changed; stale worker update rejected")
            if row["stop_requested"] and state in {"preflight", "running", "completed"}:
                raise RuntimeError("Stop is pending; worker must acknowledge cancellation")
            payload = {"from": expected, "state": state, "detail": detail or {}}
            db.execute("UPDATE runs SET state=?,updated=?,result=? WHERE id=?",
                       (state, utc_now(), json.dumps(detail) if detail is not None else None,
                        run_id))
            self._event(db, run_id, "state", payload)
        return self.get(run_id)

    def register_candidate(self, run_id: str, candidate: dict[str, Any]) -> str:
        """Register a verified candidate as data, never as executable Python or shell."""
        evidence = candidate.get("verification")
        if not isinstance(evidence, dict) or evidence.get("verdict") != "PASS":
            raise ValueError("Only independently verified candidates may enter review")
        if not isinstance(evidence.get("artifact"), str) or not evidence["artifact"]:
            raise ValueError("Candidate requires a verifier artifact reference")
        payload = json.dumps(candidate, ensure_ascii=False, sort_keys=True).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        self.get(run_id)
        path = self.root / f"candidate-{run_id}-{digest}.json"
        # Exclusive artifact creation; never overwrite a previous candidate.
        try:
            with path.open("xb") as handle:
                handle.write(payload)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise RuntimeError("Existing candidate artifact is corrupted") from None
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT candidate_sha256 FROM runs WHERE id=?", (run_id,)).fetchone()
            if row[0] not in (None, digest):
                raise RuntimeError("A different candidate is already registered for this run")
            if row[0] is None:
                db.execute("UPDATE runs SET candidate_sha256=?,updated=? WHERE id=?",
                           (digest, utc_now(), run_id))
                self._event(db, run_id, "candidate", {"sha256": digest})
        return digest

    def review_candidate(self, run_id: str, digest: str, decision: str, note: str = ""):
        if decision not in {"accepted", "rejected"}:
            raise ValueError("Review decision must be accepted or rejected")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT candidate_sha256,review FROM runs WHERE id=?",
                             (run_id,)).fetchone()
            if row is None or row[0] is None or row[0] != digest:
                raise ValueError("Review does not match the current candidate")
            if row[1] is not None:
                raise RuntimeError("Candidate already reviewed")
            path = self.root / f"candidate-{run_id}-{digest}.json"
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise RuntimeError("Candidate changed since it was registered")
            review = {"decision": decision, "note": note, "sha256": digest, "time": utc_now()}
            db.execute("UPDATE runs SET review=?,updated=? WHERE id=?",
                       (json.dumps(review, ensure_ascii=False), utc_now(), run_id))
            self._event(db, run_id, "review", review)
        return self.get(run_id)
