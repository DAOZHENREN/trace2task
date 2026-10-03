"""Frozen run facts and a local, cursor-based event journal (no telemetry export).

Borrow trace/span vocabulary, not an external monitoring dependency. Existing
forensic files stay untouched. Round content is fetched separately from events.
"""
import json
import re
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from trace2task.model_registry import GUI_MODELS, INFERENCE_BACKENDS, profile_for, public_catalog

ACTIVE_STATES = {"queued", "running", "stopping", "awaiting_recording_start", "awaiting_narration"}


def operation_scope(executor, scope, target):
    if executor == "cua" and (target or {}).get("kind") != "desktop":
        return "selected_windows"
    return scope


@dataclass(frozen=True)
class RunSession:
    run_id: str
    model: str
    profile: str
    inference_backend: str
    execution_backend: str
    operation_scope: str
    data_flow: str
    destination: str
    context_policy: str
    context_description: str
    experience_path: str
    input_mode: str

    @classmethod
    def for_job(cls, job, root, api_url="", requested_backend=None):
        from trace2task.local_gui_llama import read_backend
        local = job.provider == "trained_d"
        if local and job.model in GUI_MODELS:
            backend = read_backend(Path(root) / "runs/local-gui")
            profile_for(job.model, requested_backend or backend)
            if requested_backend and requested_backend != backend:
                raise ValueError("所选推理引擎与服务配置不同，请先在模型与连接中启动所选引擎")
            profile = job.model
        else:
            backend = "frozen-d" if local else "codex" if job.provider == "codex" else "chat-completions"
            profile = "frozen-d" if local else "chat-actions"
        host = urlsplit(api_url).hostname if api_url else ""
        local = local or (job.provider == "api" and host in {"127.0.0.1", "localhost", "::1"})
        scope = operation_scope(job.executor_backend, job.execution_scope, job.cua_target)
        policy = INFERENCE_BACKENDS[backend]
        return cls(job.job_id, job.model, profile, backend, job.executor_backend, scope,
            "local" if local else "remote", "本机" if local else (host or "Codex 订阅服务"),
            policy.context_policy, policy.context_description, job.task_path,
            "background_preferred" if job.executor_backend == "cua" and scope == "selected_windows"
            else "background" if job.background else "foreground")

    def public(self):
        return asdict(self)


def runtime_catalog(root):
    from urllib.request import ProxyHandler, build_opener

    from trace2task.components import gui_paths
    from trace2task.local_gui_llama import (
        NoRedirect,
        read_backend,
        required_runtime_paths,
        summary_model_path,
    )
    python, models = gui_paths(root)
    result = public_catalog()
    try:
        result["configured_backend"] = read_backend(Path(root) / "runs/local-gui")
    except (OSError, ValueError, KeyError) as error:
        result["configuration_error"] = str(error)
    for item in result["models"]:
        item["availability"] = {}
        for backend in item["engines"]:
            paths = ([python, models / item["id"] / "verified.json"] if backend == "transformers" else
                     [python, *required_runtime_paths(models, item["id"])])
            missing = [str(p) for p in paths if not p.exists()]
            item["availability"][backend] = {"files_present": not missing, "missing": missing,
                "validation": "checksums_on_load", "task_quality": "not_certified"}
    result["summary_model_present"] = summary_model_path(models).is_file()
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open("http://127.0.0.1:8768/health", timeout=1) as response:
            health = json.load(response)
        result["service"] = ({"reachable": True, "model": health.get("model"), "backend": health.get("backend")}
                             if health.get("service") == "trace2task-local-gui" else
                             {"reachable": False, "error": "服务身份不匹配"})
    except (OSError, ValueError, RuntimeError) as error:
        result["service"] = {"reachable": False, "error": str(error)}
    return result


def compact_snapshot(snapshot):
    result = {k: v for k, v in snapshot.items() if k not in {"logs", "model_io", "pending_batch", "result"}}
    if isinstance(snapshot.get("result"), dict):
        # Whitelist the status summary; raw prompts/history/screenshots stay in the
        # existing run archive and per-round records, never in every poll response.
        result["result"] = {k: snapshot["result"][k] for k in (
            "status", "completed", "stop_reason", "execution_status", "task_verification_status",
            "performance", "trace_path", "audit_path", "output_directory") if k in snapshot["result"]}
    return result


def round_summary(entry):
    execution = entry.get("execution") or {}
    return {"step_index": entry["step_index"], "status": entry.get("status"),
            "purpose": entry.get("purpose", "plan"), "duration_ms": entry.get("model_roundtrip_ms"),
            "tokens": entry.get("tokens") or {}, "screenshot": entry.get("screenshot"),
            "execution_status": execution.get("status"), "executed": execution.get("executed"),
            "verification": entry.get("verification"), "error": str(entry.get("error") or "")[:1000]}


class RunStore:
    def __init__(self, root):
        path = Path(root) / "runs/workbench.sqlite3"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, updated TEXT, state TEXT, snapshot TEXT);
            CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, value TEXT);
            CREATE INDEX IF NOT EXISTS events_by_run ON events(run_id, seq);
            CREATE TABLE IF NOT EXISTS rounds (run_id TEXT, step INTEGER, value TEXT, PRIMARY KEY(run_id, step));
        """)
        # An old process ending is not evidence of success; never replay it.
        for key, value in self.db.execute("SELECT id,snapshot FROM sessions WHERE state IN ('queued','running','stopping','awaiting_recording_start','awaiting_narration')").fetchall():
            snap = json.loads(value)
            snap.update(status="interrupted", error="上次控制台会话已结束；不会自动恢复或重放，请核对目标应用。")
            self.db.execute("UPDATE sessions SET state=?,snapshot=? WHERE id=?", ("interrupted", json.dumps(snap, ensure_ascii=False), key))
        self.db.commit()

    @staticmethod
    def _id(value):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
            raise ValueError("Invalid run ID")
        return value

    def event(self, run_id, name, attributes, step=None):
        value = {"schema_version": 1, "trace_id": run_id, "span_id": f"{run_id}:{step}" if step is not None else run_id,
                 "parent_span_id": run_id if step is not None else None, "name": name,
                 "time": datetime.now(UTC).isoformat(), "attributes": attributes}
        self.db.execute("INSERT INTO events(run_id,value) VALUES (?,?)", (run_id, json.dumps(value, ensure_ascii=False)))

    def save(self, snapshot, *, log=None):
        snapshot = compact_snapshot(snapshot)
        key = self._id(snapshot["job_id"])
        with self.lock, self.db:
            old = self.db.execute("SELECT state FROM sessions WHERE id=?", (key,)).fetchone()
            self.db.execute("INSERT OR REPLACE INTO sessions VALUES (?,?,?,?)",
                (key, snapshot["updated_at"], snapshot["status"], json.dumps(snapshot, ensure_ascii=False)))
            if old is None or old[0] != snapshot["status"]:
                self.event(key, "run.status", {"status": snapshot["status"]})
            if log:
                self.event(key, "run.log", {"message": str(log)})

    def round(self, run_id, entry):
        self._id(run_id)
        step = entry["step_index"]
        with self.lock, self.db:
            encoded = json.dumps(entry, ensure_ascii=False)
            old = self.db.execute("SELECT value FROM rounds WHERE run_id=? AND step=?", (run_id, step)).fetchone()
            if old and old[0] == encoded:
                return
            self.db.execute("INSERT OR REPLACE INTO rounds VALUES (?,?,?)", (run_id, step, encoded))
            summary = round_summary(entry)
            summary["artifact_ref"] = f"/api/workbench/runs/{run_id}/rounds/{step}"
            self.event(run_id, "model.round", summary, step)

    def list(self, limit=40):
        with self.lock:
            return [json.loads(row[0]) for row in self.db.execute(
                "SELECT snapshot FROM sessions ORDER BY updated DESC LIMIT ?", (min(100, max(1, limit)),))]

    def get(self, run_id, after=0):
        self._id(run_id)
        if type(after) is not int or after < 0:
            raise ValueError("Invalid event cursor")
        with self.lock:
            row = self.db.execute("SELECT snapshot FROM sessions WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError("Unknown run")
            events = [{**json.loads(value), "seq": seq} for seq, value in self.db.execute(
                "SELECT seq,value FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT 200", (run_id, after))]
            return {"session": json.loads(row[0]), "events": events,
                    "cursor": events[-1]["seq"] if events else after, "has_more": len(events) == 200}

    def read_round(self, run_id, step):
        self._id(run_id)
        with self.lock:
            row = self.db.execute("SELECT value FROM rounds WHERE run_id=? AND step=?", (run_id, step)).fetchone()
            if row is None:
                raise KeyError("Unknown model round")
            return json.loads(row[0])
