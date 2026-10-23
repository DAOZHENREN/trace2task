"""Frozen run facts and a local, cursor-based event journal (no telemetry export).

Borrow trace/span vocabulary, not an external monitoring dependency. Existing
forensic files stay untouched. Round content is fetched separately from events.
"""
import hashlib
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
    experience_id: str = ""
    experience_representation: str = ""
    experience_sha256: str = ""
    memory_mode: str = 'off'
    experience_source_sha256: str = ''

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
        destination = "本机" if local else (host or "Codex 订阅服务")
        context_policy, context_description = policy.context_policy, policy.context_description
        if getattr(job, 'android_model_profile', ''):
            from trace2task.android_model_connection import DESTINATION, validate_selection
            validate_selection(job.android_model_profile, provider=job.provider,
                               executor=job.executor_backend, model=job.model, base_url=api_url,
                               mobileforge=job.mobileforge)
            local, destination, profile = False, DESTINATION, job.android_model_profile
            context_policy = 'fresh_android_image_and_delivered_actions'
            context_description = '每轮将当前手机截图与已执行动作历史通过 SSH 发送到 3090；本机校验并执行 ADB 指令。4K 上下文预算，超限停止。'
        identity, representation, experience_sha, source_sha = "", "", "", ""
        if getattr(job, 'use_experience', False) and Path(job.task_path).name == 'model-input.txt':
            from trace2task.trace_library import execution_context, list_traces
            path = (Path(root) / job.task_path).resolve()
            context = execution_context(path)
            identity = path.parent.name
            representation = next(item['representation'] for item in list_traces(root)
                                  if item['id'] == identity)
            experience_sha = context['sha256']
            source_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        return cls(job.job_id, job.model, profile, backend, job.executor_backend, scope,
            "local" if local else "remote", destination,
            context_policy, context_description, job.task_path,
            "remote_android" if job.executor_backend == "android" else "isolated_browser" if job.executor_backend == "browser" else
            "background_preferred" if job.executor_backend == "cua" and scope == "selected_windows"
            else "background" if job.background else "foreground",
            identity, representation, experience_sha, getattr(job, 'memory_mode', 'manual' if identity else 'off'),
            experience_source_sha256=source_sha)

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
            "performance", "trace_path", "audit_path", "output_directory", "representation",
            "compiled_trace_id", "trace_name", "experience_draft_id", "source_run_id",
            "memory_mode", "memory_selection", "use_experience", "experience_task_path",
            "experience_snapshot", "lineage", "revision_changed", "assisted_by_user") if k in snapshot["result"]}
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
            CREATE TABLE IF NOT EXISTS session_trash (run_id TEXT PRIMARY KEY, deleted_at TEXT NOT NULL);
        """)
        # An old process ending is not evidence of success; never replay it.
        for key, value in self.db.execute("SELECT id,snapshot FROM sessions WHERE state IN ('queued','running','stopping','awaiting_recording_start','awaiting_narration')").fetchall():
            snap = json.loads(value)
            snap.update(status="interrupted", error="上次控制台会话已结束；不会自动恢复或重放，请核对目标应用。")
            intervention = snap.get('interventions') or {}
            if intervention.get('supported') and intervention.get('phase') != 'closed':
                intervention.update(phase='closed', closed_reason='session_interrupted',
                                    message='会话中断；仅保留已有人工记录，不补造截图或自动恢复。')
                for segment in intervention.get('takeovers', []):
                    if segment.get('status') != 'returned':
                        segment['status'] = 'not_returned'
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
                "SELECT snapshot FROM sessions WHERE id NOT IN (SELECT run_id FROM session_trash) ORDER BY updated DESC LIMIT ?", (min(100, max(1, limit)),))]

    def trash_list(self):
        with self.lock:
            return [{"path": "workbench-run/" + key, "kind": "run", "name":
                     json.loads(snapshot).get("instruction") or json.loads(snapshot).get("task_id") or key,
                     "original_path": "运行记录/" + key, "deleted_at": deleted,
                     "can_restore": True, "can_delete": True, "can_open": False}
                    for key, snapshot, deleted in self.db.execute(
                        "SELECT id,snapshot,deleted_at FROM sessions JOIN session_trash ON id=run_id ORDER BY deleted_at DESC")]

    def trash_action(self, run_id, action, *, confirmed=False):
        self._id(run_id)
        with self.lock, self.db:
            row = self.db.execute("SELECT state FROM sessions WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise ValueError("运行记录不存在")
            if row[0] in {'queued', 'running', 'stopping', 'awaiting_recording_start', 'awaiting_narration'}:
                raise ValueError("正在运行或等待录制完成的记录不能移入回收站")
            trashed = self.db.execute("SELECT 1 FROM session_trash WHERE run_id=?", (run_id,)).fetchone()
            if action == 'move':
                if trashed:
                    raise ValueError("运行记录已在回收站")
                self.db.execute("INSERT INTO session_trash VALUES (?,?)", (run_id, datetime.now(UTC).isoformat()))
            elif action in {'restore', 'purge'}:
                if not trashed:
                    raise ValueError("运行记录不在回收站")
                if action == 'purge':
                    if confirmed is not True:
                        raise ValueError("永久删除必须再次明确确认")
                    for table, key in [('events', 'run_id'), ('rounds', 'run_id'), ('sessions', 'id')]:
                        self.db.execute(f"DELETE FROM {table} WHERE {key}=?", (run_id,))
                self.db.execute("DELETE FROM session_trash WHERE run_id=?", (run_id,))
            else:
                raise ValueError("无效的回收站操作")
            return {"kind": "run", "action": action, "recoverable": action != 'purge'}

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

    def closed_evidence(self, run_id):
        """Consistent snapshot for an explicit improvement request, never a polling response."""
        self._id(run_id)
        with self.lock:
            row = self.db.execute('SELECT state,snapshot FROM sessions WHERE id=?', (run_id,)).fetchone()
            if row is None:
                raise ValueError('找不到源运行')
            if row[0] not in {'completed', 'failed', 'stopped', 'cancelled', 'interrupted', 'partial'}:
                raise ValueError('请先结束源运行，再生成改进候选')
            if self.db.execute('SELECT 1 FROM session_trash WHERE run_id=?', (run_id,)).fetchone():
                raise ValueError('源运行已移入回收站，请先恢复')
            rounds = self.db.execute('SELECT value FROM rounds WHERE run_id=? ORDER BY step',
                                     (run_id,)).fetchall()
            return {'session': json.loads(row[1]), 'rounds': [json.loads(r[0]) for r in rounds]}
