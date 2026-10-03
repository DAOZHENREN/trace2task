from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import webbrowser
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import yaml

from trace2task import __version__
from trace2task.api_settings import APISettingsStore
from trace2task.codex_app_server import (
    CODEX_MODELS,
    CODEX_REASONING_EFFORTS,
    CODEX_SELECTABLE_MODELS,
    DEFAULT_CODEX_MODEL,
    DEFAULT_CODEX_REASONING_EFFORT,
    classify_codex_failure,
)
from trace2task.compiler import compile_trace, confirm_taskpack
from trace2task.experience import route_experience
from trace2task.model_api import (
    API_REASONING_EFFORTS,
    DEFAULT_API_BASE_URL,
    ModelAPIConfig,
    validate_api_model,
)
from trace2task.narration import (
    MAX_NARRATION_AUDIO_BYTES,
    NARRATION_AUDIO_EXTENSIONS,
    archive_narration,
    save_narration_audio,
)
from trace2task.rsi_remote import (
    RSIRemoteClient,
    RSIRemoteError,
    RSIRemoteProfile,
    RSIRemoteRequestError,
)
from trace2task.speech_transcription import TurboTranscriber
from trace2task.trace_library import list_traces
from trace2task.waa_results import materialize_waa_feedback_candidates
from trace2task.windows_capture import GdiWindowCapture
from trace2task.windows_control import Win32Backend, WindowSelector, list_window_records
from trace2task.windows_experience import (
    DEFAULT_COMPILER_MODEL,
    DEFAULT_COMPILER_REASONING_EFFORT,
    compile_windows_semantic_experience,
    probe_codex_compiler_connection,
)
from trace2task.windows_guidance import (
    DEFAULT_REVISION_MODEL,
    DEFAULT_REVISION_REASONING_EFFORT,
    activate_guidance_revision,
    compile_guidance_revision,
    guidance_scope_payload,
    update_guidance_proposal_summary,
)
from trace2task.windows_recording import InputSnapshot, Win32InputMonitor, record_window_trace
from trace2task.windows_runner import (
    EmergencyStopRequested,
    Win32EmergencyStop,
    WindowsAgentRunFailed,
    run_windows_agent,
)
from trace2task.windows_task import WINDOWS_ADAPTER, load_windows_task
from trace2task.windows_task_model import (
    DEFAULT_TASK_MODEL_REVISION_MODEL,
    DEFAULT_TASK_MODEL_REVISION_REASONING_EFFORT,
    activate_task_model_revision,
    compile_task_model_revision,
)

MAX_REQUEST_BYTES = 16_384
MAX_NARRATION_REQUEST_BYTES = 30 * 1024 * 1024
MAX_LOG_ENTRIES = 300
WAA_CONTROL_EVENT_PREFIX = "TRACE2TASK_EVENT "
DEFAULT_WAA_ROOT = Path(r"D:\MyProject\WindowsAgentArena")
DEFAULT_WAA_DISTRO = "Trace2Task-WAA"
DEFAULT_WAA_CONTAINER = "winarena"
RETRYABLE_CODEX_FAILURE_CATEGORIES = frozenset(
    {
        "codex_connectivity",
        "first_token_timeout",
        "response_in_progress_timeout",
        "hard_timeout",
    }
)

# The control service may know more deployment details than belong in the
# browser.  Keep this projection finite and user-facing; paths, credentials,
# raw exception text, and future server-only checks stay server-side.
_RSI_HEALTH_CHECK_LABELS = {
    "git_runtime": "源码版本校验工具",
    "kvm_acceleration": "KVM 硬件虚拟化",
    "official_runtime": "官方 RSIAgent 运行时",
    "docker_client": "Docker 客户端模块",
    "instruction_corpus": "练习指令语料与清单",
    "locked_vm_image": "已校验的隔离虚拟机镜像",
    "model_transport_smoke": "Codex 模型传输验证",
    "vm_runtime_image": "虚拟机容器运行镜像",
    "docker_root_capacity": "Docker 存储空间容量门槛",
    "guest_isolation_smoke": "真实隔离与回滚验收",
}


def _load_rsi_remote_client() -> tuple[RSIRemoteClient | None, str | None]:
    """Load only a local deployment profile; browser input never reaches here."""
    raw_path = os.environ.get("TRACE2TASK_RSI_PROFILE", "").strip()
    if not raw_path:
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if not local_app_data:
            return None, "未配置远程 RSI 部署（未找到本机部署配置）"
        path = Path(local_app_data) / "Trace2Task" / "rsi-profile.json"
        if not path.is_file():
            return None, "未配置远程 RSI 部署（未找到本机部署配置）"
    else:
        path = Path(raw_path)
    try:
        path = path.expanduser().resolve()
        if not path.is_file() or path.stat().st_size > 65_536:
            raise ValueError("profile unavailable")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise TypeError("profile is not an object")
        # JSON has no tuple type, while the trusted profile deliberately uses
        # tuples for command and allowlist fields.  Convert only these known
        # array fields; every other field remains subject to dataclass checks.
        payload = dict(payload)
        for name in (
            "control_launcher_argv",
            "allowed_models",
            "allowed_reasoning_efforts",
        ):
            if name in payload:
                if not isinstance(payload[name], list):
                    raise TypeError(f"profile {name} must be an array")
                payload[name] = tuple(payload[name])
        return RSIRemoteClient(RSIRemoteProfile(**payload)), None
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        # Do not expose a profile path or deployment fields through the local UI.
        return None, "远程 RSI 部署配置无效；请由管理员检查本机配置"


def _rsi_text(value: object, *, maximum: int = 4_000) -> str:
    if not isinstance(value, str):
        return ""
    return value[:maximum]


def _rsi_health_checks(value: object) -> list[dict[str, Any]]:
    """Project the remote health data through a fixed, non-sensitive schema."""
    raw = value if isinstance(value, dict) else {}
    return [
        {"id": name, "label": label, "ready": raw.get(name) is True}
        for name, label in _RSI_HEALTH_CHECK_LABELS.items()
        if name in raw
    ]


def _rsi_remote_choices(value: object, allowed: tuple[str, ...]) -> list[str]:
    """Return only choices approved by both remote deployment and local profile."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item in allowed]


def _rsi_run_summary(value: object) -> dict[str, Any]:
    """Expose a small, display-only subset of remote ledger records."""
    if not isinstance(value, dict):
        raise TypeError("远程 RSI 控制器返回了无效运行记录")
    if not isinstance(value.get("id"), str) or not value["id"] or not value.get("state"):
        raise ValueError("远程 RSI 运行记录缺少身份或状态")
    spec = value.get("spec") if isinstance(value.get("spec"), dict) else {}
    review = value.get("review") if isinstance(value.get("review"), dict) else None
    result = value.get("result") if isinstance(value.get("result"), dict) else None
    worker_result = (result.get("worker_result") if result else None)
    if result and isinstance(worker_result, dict):
        # The supervisor wraps the worker's diagnostic after confirming cleanup.
        # Preserve that cause instead of displaying only "worker_exit".
        result = {**result, **worker_result}
    return {
        "id": _rsi_text(value.get("id"), maximum=64),
        "state": _rsi_text(value.get("state"), maximum=64),
        "created": _rsi_text(value.get("created"), maximum=80),
        "updated": _rsi_text(value.get("updated"), maximum=80),
        "stop_requested": bool(value.get("stop_requested")),
        "candidate_sha256": _rsi_text(value.get("candidate_sha256"), maximum=64),
        "spec": {
            "instruction": _rsi_text(spec.get("instruction")),
            "model": _rsi_text(spec.get("model"), maximum=200),
            "reasoning_effort": _rsi_text(spec.get("reasoning_effort"), maximum=64),
            "max_model_calls": spec.get("max_model_calls") if type(spec.get("max_model_calls")) is int else None,
            "wall_seconds": spec.get("wall_seconds") if type(spec.get("wall_seconds")) is int else None,
            "project_budget": spec.get("project_budget") if type(spec.get("project_budget")) is int else None,
        },
        "review": ({
            "decision": _rsi_text(review.get("decision"), maximum=32),
            "note": _rsi_text(review.get("note")),
            "sha256": _rsi_text(review.get("sha256"), maximum=64),
            "time": _rsi_text(review.get("time"), maximum=80),
        } if review else None),
        "result": ({
            "status": _rsi_text(result.get("status"), maximum=64),
            "reason": _rsi_text(result.get("reason")),
            "error_type": _rsi_text(result.get("error_type"), maximum=128),
            "error": _rsi_text(result.get("error")),
        } if result else None),
    }


def _rsi_recovery_summary(value: object) -> dict[str, Any]:
    """Expose only the bounded recovery verdict, never remote state details."""
    if not isinstance(value, dict) or type(value.get("eligible")) is not bool:
        raise TypeError("远程 RSI 控制器返回了无效恢复检查")
    result: dict[str, Any] = {
        "eligible": value["eligible"],
        "reason": _rsi_text(value.get("reason"), maximum=160),
    }
    for name in ("boundary_projects", "remaining_model_calls", "remaining_wall_ms"):
        item = value.get(name)
        if type(item) is int and item >= 0:
            result[name] = item
    return result


def _rsi_event_summary(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("远程 RSI 控制器返回了无效事件")
    payload = value.get("payload") if isinstance(value.get("payload"), dict) else {}
    visible: dict[str, Any] = {key: _rsi_text(payload.get(key), maximum=1_000) for key in (
        "state", "from", "type", "status", "reason", "error_type", "error", "mode",
    ) if isinstance(payload.get(key), str)}
    # These ledger fields are deliberately scalar and allowlisted.  They let
    # the local UI explain a selected run's charged budget and cleanup status
    # without rendering arbitrary remote payloads or adding another protocol
    # endpoint.  In particular, never pass container IDs, paths, role output,
    # or nested worker diagnostics through this display projection.
    for key in ("attempt_no", "model_calls_before", "model_calls_used",
                "global_call_index", "active_elapsed_ms", "active_elapsed_ms_before"):
        if type(payload.get(key)) is int and payload[key] >= 0:
            visible[key] = payload[key]
    if type(payload.get("cleanup_confirmed")) is bool:
        visible["cleanup_confirmed"] = payload["cleanup_confirmed"]
    return {
        "seq": value.get("seq") if type(value.get("seq")) is int else 0,
        "time": _rsi_text(value.get("time"), maximum=80),
        "kind": _rsi_text(value.get("kind"), maximum=128),
        "payload": visible,
    }


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _compiler_failure(error: BaseException, *, fallback: str) -> tuple[str, bool]:
    category = classify_codex_failure(error) or fallback
    return category, category in RETRYABLE_CODEX_FAILURE_CATEGORIES


def _compiler_failure_summary(category: str) -> str:
    return {
        "codex_connectivity": "Codex 连接中断",
        "first_token_timeout": "Codex 在首个响应期限内没有开始输出",
        "response_in_progress_timeout": "Codex 已开始输出，但随后长时间没有新进度",
        "hard_timeout": "Codex 编译超过总时限",
    }.get(category, "Compiler 运行失败")


def _waa_reset_contracts(project_root: Path) -> dict[str, dict[str, Any]]:
    reset_root = (
        project_root
        / "integrations"
        / "windows_agent_arena"
        / "reset_specs"
    )
    contracts: dict[str, dict[str, Any]] = {}
    for path in sorted(reset_root.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != "0.1":
            raise ValueError(f"WAA reset spec 格式无效：{path}")
        tasks = payload.get("tasks")
        if not isinstance(tasks, dict):
            raise TypeError(f"WAA reset spec 缺少 tasks：{path}")
        for task_id, task_spec in tasks.items():
            if not isinstance(task_id, str) or not task_id.strip():
                raise ValueError(f"WAA reset spec 包含无效任务 id：{path}")
            paths = (
                task_spec.get("must_not_exist")
                if isinstance(task_spec, dict)
                else None
            )
            if not isinstance(paths, list) or not paths or not all(
                isinstance(item, str) and item.strip() for item in paths
            ):
                raise ValueError(f"WAA reset spec 未定义有效的 must_not_exist：{path}")
            if task_id in contracts:
                raise ValueError(f"WAA 任务 {task_id!r} 匹配到多个 reset spec")
            contracts[task_id] = {
                "reset_spec": path.resolve(),
                "reset_paths": list(paths),
            }
    return contracts


def _matching_waa_reset_spec(project_root: Path, task_id: str) -> Path:
    contract = _waa_reset_contracts(project_root).get(task_id)
    if contract is None:
        raise FileNotFoundError(
            f"WAA 任务 {task_id!r} 没有匹配的 reset spec；为避免脏环境，禁止录制"
        )
    return Path(contract["reset_spec"])


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _taskpack_tree_digest(root: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    file_count = 0
    size_bytes = 0
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        size = path.stat().st_size
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(file_hash.encode("ascii"))
        digest.update(b"\n")
        file_count += 1
        size_bytes += size
    return digest.hexdigest(), file_count, size_bytes


def _decode_narration_audio(value: object) -> bytes | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise TypeError("讲解音频必须是 Base64 字符串")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("讲解音频不是有效的 Base64 数据") from error


def _guidance_rule_record(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    try:
        scope = guidance_scope_payload(value)
    except (TypeError, ValueError):
        return None
    return {
        "id": str(value.get("id") or ""),
        "scope": scope,
        "when": str(value.get("when") or ""),
        "prefer": str(value.get("prefer") or ""),
        "avoid": [str(item) for item in value.get("avoid", []) if isinstance(item, str)],
        "replan_when": [
            str(item) for item in value.get("replan_when", []) if isinstance(item, str)
        ],
        "expected_effect": str(value.get("expected_effect") or ""),
        "priority": str(value.get("priority") or ""),
    }


def _guidance_history(active_path: Path, *, task_id: str) -> list[dict[str, Any]]:
    revision_dir = active_path.parent / "guidance-revisions"
    paths = list(revision_dir.glob("revision-*.yaml")) if revision_dir.is_dir() else []
    paths.append(active_path)
    revisions: dict[int, dict[str, Any]] = {}
    for path in paths:
        try:
            root = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if (
            not isinstance(root, dict)
            or root.get("status") != "confirmed"
            or root.get("task_id") != task_id
        ):
            continue
        revision = root.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision <= 0:
            continue
        rules = [
            record
            for raw_rule in root.get("rules", [])
            if (record := _guidance_rule_record(raw_rule)) is not None
        ]
        raw_operations = root.get("operations")
        operations = (
            [
                {
                    "operation": str(operation.get("operation") or ""),
                    "target_rule_id": str(operation.get("target_rule_id") or ""),
                    "result_rule_id": str(operation.get("result_rule_id") or ""),
                    "scope": guidance_scope_payload(operation),
                    "reason": str(operation.get("reason") or ""),
                }
                for operation in raw_operations
                if isinstance(operation, dict)
            ]
            if isinstance(raw_operations, list)
            else []
        )
        source = root.get("source") if isinstance(root.get("source"), dict) else {}
        revision_agent = (
            root.get("revision_agent") if isinstance(root.get("revision_agent"), dict) else {}
        )
        review = root.get("review") if isinstance(root.get("review"), dict) else {}
        revisions[revision] = {
            "revision": revision,
            "parent_revision": root.get("parent_revision"),
            "summary": str(root.get("summary") or ""),
            "rule_count": len(rules),
            "rules": rules,
            "merge_mode": "incremental" if operations else "legacy_snapshot",
            "operations": operations,
            "feedback": str(source.get("feedback") or ""),
            "model": str(revision_agent.get("model") or ""),
            "reasoning_effort": str(revision_agent.get("reasoning_effort") or ""),
            "created_at": str(revision_agent.get("created_at") or ""),
            "confirmed_at": str(review.get("confirmed_at") or ""),
            "is_active": path.resolve() == active_path.resolve(),
        }
    if revisions:
        active_revision = max(
            (
                revision
                for revision, record in revisions.items()
                if record["is_active"]
            ),
            default=None,
        )
        if active_revision is not None:
            revisions[active_revision]["is_active"] = True
    return [revisions[revision] for revision in sorted(revisions, reverse=True)]


def _task_model_history(active_path: Path) -> list[dict[str, Any]]:
    revision_dir = active_path.parent / "experience-revisions"
    paths = list(revision_dir.glob("revision-*.yaml")) if revision_dir.is_dir() else []
    paths.append(active_path)
    revisions: dict[int, dict[str, Any]] = {}
    for path in paths:
        try:
            root = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(root, dict):
            continue
        revision_data = (
            root.get("task_model_revision")
            if isinstance(root.get("task_model_revision"), dict)
            else {}
        )
        revision = revision_data.get("revision", 0)
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            continue
        graph = root.get("state_graph") if isinstance(root.get("state_graph"), dict) else {}
        legacy_stage_count = len(root.get("stages", []))
        state_count = len(graph.get("states", [])) or legacy_stage_count
        transition_count = len(graph.get("transitions", [])) or legacy_stage_count
        terminal_count = len(graph.get("terminals", [])) or (1 if legacy_stage_count else 0)
        operations = revision_data.get("operations")
        revisions[revision] = {
            "revision": revision,
            "parent_revision": revision_data.get("parent_revision"),
            "summary": str(root.get("summary") or ""),
            "state_count": state_count,
            "transition_count": transition_count,
            "terminal_count": terminal_count,
            "operations": operations if isinstance(operations, list) else [],
            "feedback": str(revision_data.get("feedback") or ""),
            "confirmed_at": str(revision_data.get("confirmed_at") or ""),
            "is_active": path.resolve() == active_path.resolve(),
        }
    return [revisions[revision] for revision in sorted(revisions, reverse=True)]


def _guidance_inheritance(active_path: Path) -> dict[str, Any] | None:
    try:
        root = yaml.safe_load(active_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    inheritance = root.get("inheritance") if isinstance(root, dict) else None
    return dict(inheritance) if isinstance(inheritance, dict) else None


@dataclass
class ConsoleJob:
    job_id: str
    task_path: str
    task_id: str
    instruction: str
    mode: str
    model: str = DEFAULT_CODEX_MODEL
    reasoning_effort: str = DEFAULT_CODEX_REASONING_EFFORT
    provider: str = "codex"
    selection_mode: str = "manual"
    selection_confidence: float | None = None
    selection_reason: str | None = None
    kind: str = "agent"
    narrated: bool = False
    defer_compilation: bool = False
    background: bool = False
    adaptive_reasoning: bool = False
    use_experience: bool = True
    execution_scope: str = "window"
    orchestration: str = "legacy"
    resume_from: str = ""
    status: str = "queued"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    logs: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    stop_requested: bool = False
    stop_event: threading.Event = field(default_factory=threading.Event, repr=False)
    continuous: bool = False
    executor_backend: str = "win32"
    cua_target: dict | None = None
    approval_event: threading.Event = field(default_factory=threading.Event, repr=False)
    pending_batch: dict[str, Any] | None = None
    prompt_profile: dict[str, str] | None = field(default=None, repr=False)
    prompt_guidance: str = field(default="", repr=False)
    model_io: list[dict[str, Any]] = field(default_factory=list)
    run_facts: dict[str, Any] = field(default_factory=dict)
    observed_inference: dict[str, Any] = field(default_factory=dict)

    def snapshot(self, *, details=True) -> dict[str, Any]:
        from trace2task.run_session import operation_scope
        scope = operation_scope(self.executor_backend, self.execution_scope, self.cua_target)
        return {
            "job_id": self.job_id,
            "task_path": self.task_path,
            "task_id": self.task_id,
            "instruction": self.instruction,
            "mode": self.mode,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "provider": self.provider,
            "selection_mode": self.selection_mode,
            "execution_scope": self.execution_scope,
            "operation_scope": scope,
            "run_facts": dict(self.run_facts),
            "observed_inference": dict(self.observed_inference),
            "orchestration": self.orchestration,
            "resume_from": self.resume_from,
            "selection_confidence": self.selection_confidence,
            "selection_reason": self.selection_reason,
            "kind": self.kind,
            "narrated": self.narrated,
            "defer_compilation": self.defer_compilation,
            "input_mode": ("background_preferred" if self.executor_backend == "cua" and scope == "selected_windows"
                           else "background" if self.background else "foreground"),
            "adaptive_reasoning": self.adaptive_reasoning,
            "use_experience": self.use_experience,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "logs": list(self.logs) if details else [],
            "result": self.result,
            "error": self.error,
            "stop_requested": self.stop_requested,
            "pending_batch": self.pending_batch,
            "executor_backend": self.executor_backend,
            "cua_target": self.cua_target,
            "model_io": list(self.model_io) if details else [],
        }


class ConsoleEmergencyStop:
    """Combine the existing F9 stop with a stop request from the web console."""

    def __init__(self, stop_event: threading.Event) -> None:
        self.stop_event = stop_event
        self.hotkey = Win32EmergencyStop()

    def start(self) -> None:
        self.hotkey.start()

    def raise_if_requested(self) -> None:
        if self.stop_event.is_set():
            raise EmergencyStopRequested("Web console stop requested")
        self.hotkey.raise_if_requested()

    def sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, seconds)
        while True:
            self.raise_if_requested()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self.stop_event.wait(min(remaining, 0.05))

    def close(self) -> None:
        self.hotkey.close()


class ConsoleRecordingMonitor:
    """Expose the web stop button as the recorder's normal cancel request."""

    def __init__(self, stop_event: threading.Event) -> None:
        self.inner = Win32InputMonitor(success_key="f8", cancel_key="f9")
        self.stop_event = stop_event
        self.success_key = self.inner.success_key
        self.cancel_key = self.inner.cancel_key

    def start(self) -> None:
        self.inner.start()

    def poll(self) -> InputSnapshot:
        snapshot = self.inner.poll()
        if not self.stop_event.is_set():
            return snapshot
        return InputSnapshot(
            keys_down=snapshot.keys_down,
            buttons_down=snapshot.buttons_down,
            cursor_position=snapshot.cursor_position,
            success_requested=snapshot.success_requested,
            cancel_requested=True,
            sampled_at=snapshot.sampled_at,
        )

    def close(self) -> None:
        self.inner.close()


Runner = Callable[..., object]


class WebConsoleController:
    """Thread-safe local job controller behind the browser UI."""

    def __init__(
        self,
        project_root: Path,
        *,
        runner: Runner = run_windows_agent,
        narration_transcriber: TurboTranscriber | None = None,
        api_settings_store: APISettingsStore | None = None,
        rsi_client: RSIRemoteClient | None = None,
    ) -> None:
        self.project_root = project_root.expanduser().resolve()
        from trace2task.components import ComponentManager
        self.components = ComponentManager(self.project_root)
        self.api_settings = api_settings_store or APISettingsStore()
        self.task_root = (self.project_root / "taskpacks").resolve()
        self.candidate_root = (self.project_root / "runs" / "candidates").resolve()
        from trace2task.local_trash import LocalTrash
        self.trash = LocalTrash(self.project_root)
        self._cleanup_pending_deletions()
        self.runner = runner
        self.narration_transcriber = narration_transcriber or TurboTranscriber()
        self._lock = threading.RLock()
        self._jobs: dict[str, ConsoleJob] = {}
        from trace2task.run_session import RunStore
        self.run_store = RunStore(self.project_root)
        self._active_job_id: str | None = None
        self._waa_processes: dict[str, subprocess.Popen[str]] = {}
        self._rsi_client, self._rsi_profile_error = (
            (rsi_client, None) if rsi_client is not None else _load_rsi_remote_client()
        )

    def rsi_health(self) -> dict[str, Any]:
        """Return display-safe deployment state without attempting any mutation."""
        if self._rsi_client is None:
            return {
                "configured": False,
                "ready": False,
                "connection_state": "unconfigured",
                "message": self._rsi_profile_error,
                "checks": [],
                "models": [],
                "reasoning_efforts": [],
            }
        try:
            remote = self._rsi_client.health()
        except RSIRemoteError as error:
            return {
                "configured": True,
                "ready": False,
                "connection_state": "unreachable",
                "message": str(error),
                "checks": [],
                "models": [],
                "reasoning_efforts": [],
            }
        ready = remote.get("ready") is True
        return {
            "configured": True,
            "ready": ready,
            "connection_state": "ready" if ready else "unready",
            "message": (
                "远端隔离练习已就绪。练习结果仍须经独立 Verifier 才能成为候选。"
                if ready else "远端控制器已连接，但练习资源尚未准备完成。"
            ),
            "checks": _rsi_health_checks(remote.get("checks")),
            "models": _rsi_remote_choices(
                remote.get("models"), self._rsi_client.profile.allowed_models
            ),
            "reasoning_efforts": _rsi_remote_choices(
                remote.get("reasoning_efforts"),
                self._rsi_client.profile.allowed_reasoning_efforts,
            ),
            "max_model_calls": self._rsi_client.profile.max_model_calls,
            "max_wall_seconds": self._rsi_client.profile.max_wall_seconds,
            "max_project_budget": self._rsi_client.profile.max_project_budget,
        }

    def _register_job(self, job: ConsoleJob) -> None:
        """Journal every workflow before its worker starts, even without a UI poll."""
        self.run_store.save(job.snapshot(details=False), log="\n".join(job.logs) or None)
        self._jobs[job.job_id] = job
        self._active_job_id = job.job_id

    def _require_rsi_client(self) -> RSIRemoteClient:
        if self._rsi_client is None:
            raise RuntimeError(self._rsi_profile_error or "远程 RSI 部署未配置")
        return self._rsi_client

    def list_rsi_runs(self, limit: object = 50) -> dict[str, Any]:
        if type(limit) is not int:
            raise ValueError("limit 必须是整数")
        response = self._require_rsi_client().list(limit=limit)
        rows = response.get("runs")
        if not isinstance(rows, list):
            raise TypeError("远程 RSI 控制器未返回运行列表")
        return {"runs": [_rsi_run_summary(row) for row in rows]}

    def get_rsi_run(self, run_id: object) -> dict[str, Any]:
        return {"run": _rsi_run_summary(self._require_rsi_client().get(str(run_id))["run"])}

    def rsi_events(self, run_id: object, after: object = 0) -> dict[str, Any]:
        if type(after) is not int:
            raise ValueError("after 必须是非负整数")
        response = self._require_rsi_client().events(str(run_id), after=after)
        rows = response.get("events")
        if not isinstance(rows, list):
            raise TypeError("远程 RSI 控制器未返回事件列表")
        return {"events": [_rsi_event_summary(row) for row in rows]}

    def start_rsi_practice(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            result = self._require_rsi_client().start(
                instruction=payload.get("instruction", ""),
                model=payload.get("model", ""),
                reasoning_effort=payload.get("reasoning_effort", ""),
                max_model_calls=payload.get("max_model_calls"),
                wall_seconds=payload.get("wall_seconds"),
                project_budget=payload.get("project_budget", 1),
            )
        except RSIRemoteRequestError as error:
            if error.code == "practice_already_active":
                raise RuntimeError("已有远程练习正在运行，请刷新查看，不要重复启动") from error
            raise
        return {"run": _rsi_run_summary(result["run"])}

    def stop_rsi_practice(self, run_id: object) -> dict[str, Any]:
        return {"run": _rsi_run_summary(self._require_rsi_client().stop(str(run_id))["run"])}

    def rsi_recovery(self, run_id: object) -> dict[str, Any]:
        return {"recovery": _rsi_recovery_summary(
            self._require_rsi_client().recovery(str(run_id))
        )}

    def recover_rsi_practice(self, run_id: object) -> dict[str, Any]:
        return {"run": _rsi_run_summary(self._require_rsi_client().recover(str(run_id))["run"])}

    def get_rsi_candidate(self, run_id: object, digest: object) -> dict[str, Any]:
        payload = self._require_rsi_client().candidate(str(run_id), digest=str(digest))["candidate"]
        # Candidate prose is untrusted model output. It is returned as data only;
        # the browser renders it with textContent, never HTML.
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > 256_000:
            raise ValueError("远程候选经验过大，拒绝加载到网页")
        return {"candidate": payload}

    def review_rsi_candidate(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"run": _rsi_run_summary(self._require_rsi_client().review(
            str(payload.get("run_id", "")),
            digest=str(payload.get("digest", "")),
            decision=payload.get("decision", ""),
            note=payload.get("note", ""),
        )["run"])}

    def local_prompt_profile(self, model: str, backend: str) -> dict[str, Any]:
        from trace2task.local_gui_protocol import (
            MODELS,
            TURN_TEMPLATE,
            prompt,
            validate_prompt_profile,
        )

        if model not in MODELS or backend not in {"win32", "cua"}:
            raise ValueError("仅本地 GUI 模型可编辑执行提示词")
        default = {"system_prompt": prompt(model),
                   "turn_template": TURN_TEMPLATE}
        path = self.project_root / "runs/local-gui/prompt-profiles.json"
        with self._lock:
            saved = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if not isinstance(saved, dict):
            raise ValueError("本地提示词配置文件格式无效")  # noqa: TRY004 - persisted format validation
        key = f"{model}:{backend}"
        effective = validate_prompt_profile(saved[key]) if key in saved else default
        return {"model": model, "backend": backend, "default": default,
                "effective": effective, "customized": key in saved}

    def save_local_prompt_profile(self, model: str, backend: str,
                                  profile: dict[str, str] | None) -> dict[str, Any]:
        from trace2task.local_gui_protocol import validate_prompt_profile

        self.local_prompt_profile(model, backend)  # Validate model/backend before touching disk.
        if profile is not None:
            profile = validate_prompt_profile(profile)
        path = self.project_root / "runs/local-gui/prompt-profiles.json"
        with self._lock:
            saved = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
            if not isinstance(saved, dict):
                raise ValueError("本地提示词配置文件格式无效")  # noqa: TRY004 - persisted format validation
            key = f"{model}:{backend}"
            if profile is None:
                saved.pop(key, None)
            else:
                saved[key] = profile
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             delete=False) as stream:
                pending = Path(stream.name)
                json.dump(saved, stream, ensure_ascii=False, indent=2)
            try:
                os.replace(pending, path)
            finally:
                pending.unlink(missing_ok=True)
        return self.local_prompt_profile(model, backend)

    def chat_prompt_profile(self, provider: str) -> dict[str, Any]:
        if provider not in {"codex", "api"}:
            raise ValueError("仅 Codex 和模型 API 可使用此提示词配置")
        path = self.project_root / "runs/chat-prompt-profiles.json"
        with self._lock:
            saved = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if not isinstance(saved, dict):
            raise ValueError("对话模型提示词配置文件格式无效")  # noqa: TRY004 -- HTTP validation contract
        value = saved.get(provider, "")
        if not isinstance(value, str) or len(value) > 4_000:
            raise ValueError("对话模型提示词配置无效")
        return {"provider": provider, "guidance": value, "customized": bool(value)}

    def save_chat_prompt_profile(self, provider: str, guidance: str) -> dict[str, Any]:
        self.chat_prompt_profile(provider)
        if not isinstance(guidance, str) or len(guidance) > 4_000:
            raise ValueError("自定义提示词不能超过 4000 个字符")
        path = self.project_root / "runs/chat-prompt-profiles.json"
        with self._lock:
            saved = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
            if not isinstance(saved, dict):
                raise ValueError("对话模型提示词配置文件格式无效")  # noqa: TRY004 -- HTTP validation contract
            if guidance.strip():
                saved[provider] = guidance.strip()
            else:
                saved.pop(provider, None)
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             delete=False) as stream:
                pending = Path(stream.name)
                json.dump(saved, stream, ensure_ascii=False, indent=2)
            try:
                os.replace(pending, path)
            finally:
                pending.unlink(missing_ok=True)
        return self.chat_prompt_profile(provider)

    def _cleanup_pending_deletions(self) -> None:
        self.trash.cleanup_pending()

    def restore_trashed_asset(self, raw_path: str) -> dict[str, Any]:
        with self._lock:
            self._require_idle()
            return self.trash.restore(raw_path)

    def purge_trashed_asset(self, raw_path: str, *, confirmed=False) -> dict[str, Any]:
        with self._lock:
            self._require_idle()
            return self.trash.purge(raw_path, confirmed=confirmed)

    def list_taskpacks(self) -> list[dict[str, Any]]:
        if not self.task_root.is_dir():
            return []
        records: list[tuple[float, dict[str, Any]]] = []
        for path in self.task_root.rglob("task.yaml"):
            if ".trash" in path.relative_to(self.task_root).parts:
                continue
            if path.with_name(".trace2task-deleted.json").is_file():
                continue
            try:
                contract = load_windows_task(path)
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if contract.task.environment_adapter != WINDOWS_ADAPTER:
                continue
            relative = path.resolve().relative_to(self.project_root).as_posix()
            is_messaging_target = bool(
                contract.selector.process_name
                and contract.selector.process_name.casefold()
                in {"weixin.exe", "wechat.exe"}
            )
            missing_capabilities = (
                [
                    skill
                    for skill in ("type_text", "press_key")
                    if skill not in contract.task.actions
                ]
                if is_messaging_target
                else []
            )
            semantic = contract.semantic_experience
            guidance = contract.human_guidance
            task_document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            pending_name = task_document.get("guidance_review_pending")
            pending_rules: list[dict[str, Any]] = []
            if isinstance(pending_name, str) and Path(pending_name).name == pending_name:
                pending_path = path.with_name(pending_name)
                if pending_path.is_file():
                    pending_document = yaml.safe_load(pending_path.read_text(encoding="utf-8"))
                    if isinstance(pending_document, dict) and isinstance(pending_document.get("rules"), list):
                        pending_rules = pending_document["rules"]
            records.append(
                (
                    path.stat().st_mtime,
                    {
                        "path": relative,
                        "local_path": path.parent.resolve().relative_to(
                            self.project_root
                        ).as_posix(),
                        "task_id": contract.task.task_id,
                        "execution_scope": contract.execution_scope,
                        "instruction": contract.task.instruction,
                        "review_status": contract.task.review_status,
                        "confirmed": not contract.task.requires_confirmation,
                        "process_name": contract.selector.process_name,
                        "title_contains": contract.selector.title_contains,
                        "actions": list(contract.task.actions),
                        "max_actions": contract.task.max_actions,
                        "experience_intent": contract.task.experience_intent,
                        "experience_examples": list(contract.task.experience_examples),
                        "semantic_experience": (
                            {
                                "status": semantic.review_status,
                                "canonical_instruction": semantic.canonical_instruction,
                                "goal": semantic.goal,
                                "summary": semantic.summary,
                                "completion": {
                                    "mode": semantic.completion_mode,
                                    "success_condition": (
                                        semantic.completion_success_condition
                                    ),
                                    "reason": semantic.completion_reason,
                                },
                                 "model": semantic.model,
                                 "reasoning_effort": semantic.reasoning_effort,
                                 "narration_available": semantic.narration_available,
                                 "narration_kind": semantic.narration_kind,
                                 "compiler_variant": (
                                     "narrated_compiled"
                                     if semantic.narration_kind == "human"
                                     else (
                                         "instruction_compiled"
                                         if semantic.narration_kind == "task_instruction"
                                         else "compiled"
                                     )
                                 ),
                                 "stage_count": len(semantic.stages),
                                "state_count": len(semantic.states),
                                "transition_count": len(semantic.transitions),
                                "terminal_count": len(semantic.terminal_states),
                                "entry_state_id": semantic.entry_state_id,
                                "revision": int(
                                    (
                                        yaml.safe_load(path.read_text(encoding="utf-8"))
                                        .get("semantic_experience", {})
                                        .get("revision", 0)
                                    )
                                    or 0
                                ),
                                "history": _task_model_history(semantic.source_path),
                                "motor_policy": "semantic_intent_only_no_recorded_coordinates",
                                "narration_claims": semantic.narration_audit_payload(),
                                "states": [
                                    {
                                        "id": state.state_id,
                                        "name": state.name,
                                        "description": state.description,
                                        "preconditions": list(state.preconditions),
                                        "visual_anchors": list(state.visual_anchors),
                                        "evidence_stage_ids": list(
                                            state.evidence_stage_ids
                                        ),
                                        "confidence": state.confidence,
                                        "outgoing": [
                                            {
                                                "id": transition.transition_id,
                                                "target_type": transition.target_type,
                                                "target_id": transition.target_id,
                                                "condition": transition.condition,
                                                "action_goal": transition.action_goal,
                                            }
                                            for transition in semantic.outgoing_transitions(
                                                state.state_id
                                            )
                                        ],
                                    }
                                    for state in semantic.states
                                ],
                                "terminals": [
                                    {
                                        "id": terminal.terminal_id,
                                        "kind": terminal.kind,
                                        "name": terminal.name,
                                        "condition": terminal.condition,
                                        "visual_anchors": list(
                                            terminal.visual_anchors
                                        ),
                                        "confidence": terminal.confidence,
                                    }
                                    for terminal in semantic.terminal_states
                                ],
                                "stages": [
                                    {
                                        "id": stage.stage_id,
                                        "name": stage.name,
                                        "state_before": stage.state_before.description,
                                        "intent": stage.action_intents[0].description,
                                        "state_after": stage.state_after.description,
                                        "evidence_before": (
                                            path.parent
                                            / stage.state_before.evidence_frame
                                        ).resolve().relative_to(
                                            self.project_root
                                        ).as_posix(),
                                        "evidence_after": (
                                            path.parent
                                            / stage.state_after.evidence_frame
                                        ).resolve().relative_to(
                                            self.project_root
                                        ).as_posix(),
                                        "evidence_frame": (
                                            path.parent
                                            / stage.state_after.evidence_frame
                                        ).resolve().relative_to(
                                            self.project_root
                                        ).as_posix(),
                                        "confidence": stage.confidence,
                                        "dynamic_decisions": [
                                            {
                                                "description": decision.description,
                                                "generalization": decision.generalization,
                                                "confidence": decision.confidence,
                                            }
                                            for decision in stage.dynamic_decisions
                                        ],
                                    }
                                    for stage in semantic.stages
                                ],
                            }
                            if semantic is not None
                            else None
                        ),
                        "human_guidance": (
                            {
                                "revision": guidance.revision,
                                "summary": guidance.summary,
                                "rule_count": len(guidance.rules),
                                "model": guidance.model,
                                "reasoning_effort": guidance.reasoning_effort,
                                "history": _guidance_history(
                                    guidance.source_path,
                                    task_id=contract.task.task_id,
                                ),
                                "inheritance": _guidance_inheritance(guidance.source_path),
                                "rules": [
                                    {
                                        "id": rule.rule_id,
                                        "scope": {
                                            "type": rule.scope_type,
                                            "id": rule.scope_id,
                                        },
                                        "when": rule.when,
                                        "prefer": rule.prefer,
                                        "avoid": list(rule.avoid),
                                        "replan_when": list(rule.replan_when),
                                        "expected_effect": rule.expected_effect,
                                        "priority": rule.priority,
                                    }
                                    for rule in guidance.rules
                                ],
                            }
                            if guidance is not None
                            else None
                        ),
                        "guidance_review_pending": pending_rules,
                        "missing_message_capabilities": missing_capabilities,
                    },
                )
            )
        records.sort(key=lambda item: item[0], reverse=True)
        return [record for _, record in records]

    def route_instruction(self, instruction: str) -> dict[str, Any]:
        raise ValueError("请手动选择任务经验；自动匹配经验已停用")

    def list_windows(self) -> list[dict[str, Any]]:
        return list_window_records(backend=Win32Backend())["windows"]

    def list_waa_tasks(self, waa_root: object) -> list[dict[str, Any]]:
        if not isinstance(waa_root, (str, Path)) or not str(waa_root).strip():
            raise ValueError("请输入 WAA 根目录")
        root = Path(waa_root).expanduser().resolve()
        client_root = (root / "src" / "win-arena-container" / "client").resolve()
        example_root = client_root / "evaluation_examples_windows" / "examples"
        if not example_root.is_dir():
            raise FileNotFoundError("WAA 标准任务目录不存在")
        contracts = _waa_reset_contracts(self.project_root)
        tasks: list[dict[str, Any]] = []
        for path in example_root.rglob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            task_id = payload.get("id") if isinstance(payload, dict) else None
            if not isinstance(task_id, str) or task_id not in contracts:
                continue
            instruction = payload.get("instruction")
            if not isinstance(instruction, str) or not instruction.strip():
                continue
            trace2task_metadata = payload.get("trace2task")
            if not isinstance(trace2task_metadata, dict):
                trace2task_metadata = {}
            if trace2task_metadata.get("recordable") is False:
                continue
            evaluator = payload.get("evaluator")
            evaluator_functions = (
                evaluator.get("func") if isinstance(evaluator, dict) else []
            )
            if isinstance(evaluator_functions, str):
                evaluator_functions = [evaluator_functions]
            if not isinstance(evaluator_functions, list):
                evaluator_functions = []
            related_apps = payload.get("related_apps")
            if not isinstance(related_apps, list):
                related_apps = []
            relative = path.relative_to(client_root)
            contract = contracts[task_id]
            tasks.append(
                {
                    "id": task_id,
                    "domain": path.parent.name,
                    "instruction": instruction.strip(),
                    "related_apps": [
                        app for app in related_apps if isinstance(app, str)
                    ],
                    "evaluator": [
                        name for name in evaluator_functions if isinstance(name, str)
                    ],
                    "example_path": relative.as_posix(),
                    "reset_spec": str(contract["reset_spec"]),
                    "reset_paths": list(contract["reset_paths"]),
                    "experience_family_id": trace2task_metadata.get("family_id"),
                    "variant_id": trace2task_metadata.get("variant_id"),
                    "variant_role": trace2task_metadata.get("variant_role"),
                    "recordable": True,
                }
            )
        tasks.sort(key=lambda task: (task["domain"], task["instruction"], task["id"]))
        return tasks

    def list_recordings(self) -> list[dict[str, Any]]:
        runs_root = self.project_root / "runs"
        if not runs_root.is_dir():
            return []
        records: list[tuple[float, dict[str, Any]]] = []
        for metadata_path in runs_root.glob("*/metadata.json"):
            if metadata_path.with_name(".trace2task-deleted.json").is_file():
                continue
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                sidecar = metadata_path.with_name("trace2task.json")
                if sidecar.is_file():
                    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if metadata.get("source") not in {"windows_human", "opencua_native"}:
                continue
            opencua = metadata.get("source") == "opencua_native"
            trace_path = metadata_path.with_name("events.jsonl" if opencua else "trace.jsonl")
            if not trace_path.is_file():
                continue
            created_at = metadata.get("started_at") or datetime.fromtimestamp(
                metadata_path.stat().st_mtime,
                tz=UTC,
            ).isoformat()
            try:
                sort_key = datetime.fromisoformat(str(created_at)).timestamp()
            except ValueError:
                sort_key = metadata_path.stat().st_mtime
            narration_path = metadata_path.with_name("narration.json")
            narration_chars = 0
            if narration_path.is_file():
                try:
                    narration_data = json.loads(
                        narration_path.read_text(encoding="utf-8")
                    )
                    narration_chars = len(str(narration_data.get("transcript") or ""))
                except (OSError, json.JSONDecodeError, AttributeError):
                    narration_chars = 0
            records.append(
                (
                    sort_key,
                    {
                        "task_id": metadata.get("task_id"),
                        "success": bool(metadata.get("success")),
                        "stop_reason": metadata.get("stop_reason"),
                        "input_events": metadata.get("input_event_count", 0),
                        "recording_backend": "opencua" if opencua else "legacy",
                        **({"derivation": metadata.get("derivation")} if opencua else {}),
                        "compilation_supported": not opencua,
                        "execution_scope": metadata.get("execution_scope", "window"),
                        "narrated": narration_path.is_file(),
                        "narration_chars": narration_chars,
                        "process_name": (metadata.get("initial_window") or {}).get(
                            "process_name"
                        ),
                        "title": (metadata.get("initial_window") or {}).get("title"),
                        "created_at": created_at,
                        "trace_path": trace_path.relative_to(self.project_root).as_posix(),
                        "local_path": metadata_path.parent.relative_to(
                            self.project_root
                        ).as_posix(),
                    },
                )
            )
        records.sort(key=lambda item: item[0], reverse=True)
        return [record for _, record in records]

    def list_candidates(self) -> list[dict[str, Any]]:
        materialize_waa_feedback_candidates(self.project_root, self.candidate_root)
        if not self.candidate_root.is_dir():
            return []
        records: list[tuple[float, dict[str, Any]]] = []
        for path in self.candidate_root.glob("*/candidate.yaml"):
            if path.with_name(".trace2task-deleted.json").is_file():
                continue
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                continue
            if not isinstance(data, dict) or data.get("status") not in {
                "pending_review",
                "feedback_applied",
            }:
                continue
            revision = data.get("revision") if isinstance(data.get("revision"), dict) else None
            task_model_revision = (
                data.get("task_model_revision")
                if isinstance(data.get("task_model_revision"), dict)
                else None
            )
            review_timeline = None
            raw_review_path = data.get("review_timeline")
            if isinstance(raw_review_path, str) and raw_review_path:
                review_path = (path.parent / raw_review_path).resolve()
                if review_path.parent == path.parent.resolve() and review_path.is_file():
                    try:
                        loaded_review = json.loads(review_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        loaded_review = None
                    if isinstance(loaded_review, dict):
                        review_timeline = loaded_review
                        for key in ("initial_frame", "final_frame"):
                            value = review_timeline.get(key)
                            if isinstance(value, str) and value:
                                review_timeline[key] = (
                                    path.parent / value
                                ).relative_to(self.project_root).as_posix()
                        rounds = review_timeline.get("rounds")
                        if isinstance(rounds, list):
                            for round_data in rounds:
                                if not isinstance(round_data, dict):
                                    continue
                                for key in ("before_frame", "after_frame"):
                                    value = round_data.get(key)
                                    if isinstance(value, str) and value:
                                        round_data[key] = (
                                            path.parent / value
                                        ).relative_to(self.project_root).as_posix()
            records.append(
                (
                    path.stat().st_mtime,
                    {
                        "candidate_id": data.get("candidate_id"),
                        "status": data.get("status"),
                        "task_id": data.get("task_id"),
                        "instruction": data.get("runtime_instruction"),
                        "source_task": data.get("source_task"),
                        "execution_trace": data.get("execution_trace"),
                        "created_at": data.get("created_at"),
                        "outcome": data.get("outcome") or {},
                        "metrics": data.get("metrics") or {},
                        "revision": revision,
                        "task_model_revision": task_model_revision,
                        "source_kind": data.get("source_kind"),
                        "waa": data.get("waa") if isinstance(data.get("waa"), dict) else None,
                        "review_timeline": review_timeline,
                        "local_path": path.parent.relative_to(
                            self.project_root
                        ).as_posix(),
                    },
                )
            )
        records.sort(key=lambda item: item[0], reverse=True)
        return [record for _, record in records]

    def start_job(
        self,
        *,
        task_path: str,
        instruction: str,
        execute: bool,
        model: str = DEFAULT_CODEX_MODEL,
        reasoning_effort: str = DEFAULT_CODEX_REASONING_EFFORT,
        background: bool = False,
        adaptive_reasoning: bool = False,
        provider: str = "codex",
        use_experience: bool = False,
        execution_scope: str = "window",
        orchestration: str = "legacy",
        resume_from: str = "",
        api_options: dict[str, Any] | None = None,
        continuous: bool = False,
        executor_backend: str = "win32",
        cua_target: dict | None = None,
        inference_backend: str | None = None,
    ) -> dict[str, Any]:
        normalized_instruction = " ".join(instruction.split())
        if executor_backend not in {"win32", "cua"}:
            raise ValueError("不支持的执行后端")
        if (executor_backend == "cua" and provider != "trained_d"
                and (execution_scope != "desktop" or orchestration != "legacy"
                     or not execute or resume_from)):
            raise ValueError("Codex/API 的 Cua 只支持独立任务执行；检查点恢复仍走原有路径")
        if executor_backend == "cua":
            from trace2task.cua_desktop import DESKTOP_TARGET
            from trace2task.cua_scope import normalize_scope
            if cua_target != DESKTOP_TARGET:
                cua_target = normalize_scope(cua_target)
            elif execution_scope != "desktop":
                raise ValueError("Cua 桌面目标需要全桌面范围")
        if orchestration not in {"legacy", "langgraph"}:
            raise ValueError("不支持的任务管理模式")
        if execution_scope != "desktop" and (orchestration != "legacy" or resume_from):
            raise ValueError("LangGraph 试验仅支持桌面执行")
        if resume_from:
            if orchestration != "langgraph":
                raise ValueError("恢复任务需要 LangGraph 模式")
            resumed = (self.project_root / resume_from).resolve()
            runs_root = (self.project_root / "runs").resolve()
            if not resumed.is_relative_to(runs_root) or resumed == runs_root:
                raise ValueError("只能恢复本项目 runs 下的运行")
            if not (resumed / "checkpoints.sqlite").is_file():
                raise ValueError("找不到该运行的检查点")
            resume_from = str(resumed)
        if not isinstance(use_experience, bool):
            raise TypeError("使用经验必须是布尔值")
        if execution_scope not in {"window", "desktop"}:
            raise ValueError("不支持的执行范围")
        if execution_scope == "desktop" and background:
            raise ValueError("桌面 Baseline 仅支持前台执行")
        if execution_scope == "window" and not use_experience and not (isinstance(task_path, str) and task_path.strip()):
            raise ValueError("不使用经验时，请手动选择任务以确定目标窗口和允许的操作")
        if not normalized_instruction:
            raise ValueError("请输入一条任务指令")
        if len(normalized_instruction) > 2_000:
            raise ValueError("任务指令不能超过 2000 个字符")
        if provider == "trained_d":
            if execution_scope != "desktop" or orchestration != "legacy" or resume_from:
                raise ValueError("本地 GPU 模型仅支持主屏桌面、独立任务")
            if type(continuous) is not bool:
                raise ValueError("continuous 必须为布尔值")
            from trace2task.local_gui_protocol import MODELS
            if model == DEFAULT_CODEX_MODEL:
                model = "D-5970"  # Legacy caller omitted the D model identifier.
            if model not in ("D-5970", *MODELS):
                raise ValueError("不支持的本地 GPU 模型")
            if use_experience and model == "D-5970":
                raise ValueError("D-5970 冻结输入结构不支持经验指导，请选择 GUI 原生模型或关闭经验")
            reasoning_effort = "low"
            prompt_profile = (self.local_prompt_profile(model, executor_backend)["effective"]
                              if model != "D-5970" else None)
        if provider not in {"codex", "api", "trained_d"}:
            raise ValueError("执行模型来源必须是 codex 或 api")
        prompt_guidance = (self.chat_prompt_profile(provider)["guidance"]
                           if provider in {"codex", "api"} else "")
        if not isinstance(background, bool) or not isinstance(adaptive_reasoning, bool):
            raise TypeError("输入模式和自适应推理设置必须是布尔值")
        api_config = None
        if provider == "api":
            if not isinstance(api_options, dict):
                raise ValueError("请配置模型 API 连接")
            api_config = self.api_settings.with_saved_key(ModelAPIConfig(
                base_url=api_options.get("base_url", DEFAULT_API_BASE_URL),
                api_key=api_options.get("api_key", ""),
                thinking_mode=api_options.get("thinking_mode", "default"),
                response_format=api_options.get("response_format", "json_schema"),
                timeout_seconds=api_options.get("timeout_seconds", 120),
                context_window_tokens=api_options.get("context_window_tokens", 32768),
            )).with_credentials()
            model = validate_api_model(model)
            adaptive_reasoning = False
        if provider == "codex" and model not in CODEX_MODELS:
            raise ValueError(
                f"当前是 Codex 模式，不支持的模型：{model}。"
                "自定义 API 模型请先将“执行模型来源”切换为“模型 API”。"
            )
        allowed_efforts = API_REASONING_EFFORTS if provider == "api" else CODEX_REASONING_EFFORTS
        if reasoning_effort not in allowed_efforts:
            raise ValueError(f"不支持的思考强度：{reasoning_effort}")
        if execution_scope == "desktop" and not use_experience:
            resolved_task = None
            task_id = "桌面 · 无经验 Baseline"
            adaptive_reasoning = False
            selection_mode, selection_confidence, selection_reason = "desktop", None, "不读取经验"
        elif isinstance(task_path, str) and task_path.strip():
            resolved_task = self._resolve_execution_experience(task_path)
            selection_mode = "manual"
            selection_confidence = None
            selection_reason = "用户手动选择 Trace 经验"
        else:
            raise ValueError("使用经验时，请手动选择任务经验；不再自动匹配经验")
        if resolved_task is not None and resolved_task.name == "model-input.txt":
            from trace2task.trace_library import execution_context

            if execution_scope != "desktop" or orchestration != "legacy" or not execute:
                raise ValueError("A/D 经验支持独立任务执行，请使用桌面或 Cua 执行入口")
            task_id = execution_context(resolved_task)["task_id"]
        elif resolved_task is not None:
            if use_experience:
                raise ValueError("旧状态图经验已退出执行入口，请选择原始证据 A 或精简序列 D")
            contract = load_windows_task(resolved_task)
            task_id = contract.task.task_id
            if execution_scope == "window" and contract.execution_scope == "desktop":
                raise ValueError("桌面经验请使用桌面执行范围")
            if execution_scope == "desktop" and contract.semantic_experience is None:
                raise ValueError("请先对所选经验进行语义编译")
            if execute and contract.task.requires_confirmation:
                raise ValueError("这个示范任务仍是草稿，确认任务包后才能执行")
            if (
                execution_scope == "window" and execute
                and contract.selector.process_name
                and contract.selector.process_name.casefold() in {"weixin.exe", "wechat.exe"}
                and "type_text" not in contract.task.actions
            ):
                raise ValueError("这份微信示范缺少文本输入能力，目前只能生成计划")
        with self._lock:
            self._require_idle()
            job = ConsoleJob(
                job_id=uuid.uuid4().hex,
                task_path=resolved_task.relative_to(self.project_root).as_posix() if resolved_task else "",
                task_id=task_id,
                instruction=normalized_instruction,
                mode="execute" if execute else "plan",
                model=model,
                reasoning_effort=reasoning_effort,
                provider=provider,
                selection_mode=selection_mode,
                selection_confidence=selection_confidence,
                selection_reason=selection_reason,
                background=background,
                adaptive_reasoning=adaptive_reasoning,
                use_experience=use_experience,
                execution_scope=execution_scope,
                orchestration=orchestration,
                resume_from=resume_from,
                continuous=continuous,
                executor_backend=executor_backend,
                cua_target=cua_target,
                prompt_profile=prompt_profile if provider == "trained_d" else None,
                prompt_guidance=prompt_guidance,
            )
            from trace2task.run_session import RunSession
            job.run_facts = RunSession.for_job(job, self.project_root,
                api_url=api_config.base_url if api_config else "",
                requested_backend=inference_backend).public()
            if selection_mode == "auto":
                job.logs.append(
                    f"自动选择经验：{contract.task.task_id}（置信度 "
                    f"{selection_confidence:.0%}）。{selection_reason}"
                )
            self._register_job(job)
        thread = threading.Thread(
            target=self._run_job,
            args=(job, resolved_task, execute, api_config),
            name=f"trace2task-web-{job.job_id[:8]}",
            daemon=True,
        )
        thread.start()
        return job.snapshot()

    def start_recording(
        self,
        *,
        handle: int,
        task_id: str,
        narrated: bool = False,
        defer_compilation: bool = False,
        model: str = DEFAULT_COMPILER_MODEL,
        reasoning_effort: str = DEFAULT_COMPILER_REASONING_EFFORT,
        execution_scope: str = "window",
        recording_backend: str = "legacy",
    ) -> dict[str, Any]:
        if recording_backend not in {"legacy", "opencua"}:
            raise ValueError("不支持的录制后端")
        if recording_backend == "opencua":
            if execution_scope != "desktop" or narrated:
                raise ValueError("OpenCUA 本轮仅支持主屏幕录制，不支持语音")
            defer_compilation = True
        normalized_task_id = " ".join(task_id.split())
        if not normalized_task_id:
            raise ValueError("请输入经验名称")
        if len(normalized_task_id) > 80:
            raise ValueError("经验名称不能超过 80 个字符")
        if re.search(r"[\\/:*?\"<>|\x00-\x1f]", normalized_task_id):
            raise ValueError("经验名称包含 Windows 路径不支持的字符")
        if model not in CODEX_MODELS:
            raise ValueError(f"不支持的模型：{model}")
        if reasoning_effort not in CODEX_REASONING_EFFORTS:
            raise ValueError(f"不支持的思考强度：{reasoning_effort}")
        if not isinstance(narrated, bool):
            raise TypeError("讲解录制开关必须是布尔值")
        if not isinstance(defer_compilation, bool):
            raise TypeError("稍后编译开关必须是布尔值")
        if execution_scope not in {"window", "desktop"}:
            raise ValueError("不支持的录制范围")
        windows = self.list_windows() if execution_scope == "window" else []
        selected = next((window for window in windows if window["handle"] == handle), None)
        if execution_scope == "desktop":
            selected = {"process_name": "trace2task.desktop", "title": "主显示器（跨程序）"}
        if selected is None:
            raise ValueError("所选窗口已经不存在，请刷新窗口列表")
        with self._lock:
            self._require_idle()
            self._ensure_experience_name_available(normalized_task_id)
            job = ConsoleJob(
                job_id=uuid.uuid4().hex,
                task_path="",
                task_id=normalized_task_id,
                instruction=f"录制 {selected['process_name']} - {selected['title']}",
                mode="record",
                kind="recording",
                execution_scope=execution_scope,
                narrated=narrated,
                defer_compilation=defer_compilation,
                model=model,
                reasoning_effort=reasoning_effort,
            )
            self._register_job(job)
        thread = threading.Thread(
            target=self._run_opencua_recording if recording_backend == "opencua" else self._run_recording,
            args=(job,) if recording_backend == "opencua" else (job, handle, selected),
            name=f"trace2task-record-{job.job_id[:8]}",
            daemon=True,
        )
        thread.start()
        return job.snapshot()

    def start_waa_recording(
        self,
        *,
        waa_root: object,
        example_path: object,
        task_id: str,
        narrated: bool = True,
        defer_compilation: bool = False,
        model: str = DEFAULT_COMPILER_MODEL,
        reasoning_effort: str = DEFAULT_COMPILER_REASONING_EFFORT,
        distro: str = DEFAULT_WAA_DISTRO,
        container: str = DEFAULT_WAA_CONTAINER,
    ) -> dict[str, Any]:
        normalized_task_id = " ".join(task_id.split())
        if not normalized_task_id:
            raise ValueError("请输入经验名称")
        if len(normalized_task_id) > 80:
            raise ValueError("经验名称不能超过 80 个字符")
        if re.search(r"[\\/:*?\"<>|\x00-\x1f]", normalized_task_id):
            raise ValueError("经验名称包含 Windows 路径不支持的字符")
        if model not in CODEX_MODELS:
            raise ValueError(f"不支持的模型：{model}")
        if reasoning_effort not in CODEX_REASONING_EFFORTS:
            raise ValueError(f"不支持的思考强度：{reasoning_effort}")
        if not isinstance(narrated, bool):
            raise TypeError("讲解录制开关必须是布尔值")
        if not isinstance(defer_compilation, bool):
            raise TypeError("稍后编译开关必须是布尔值")
        if not isinstance(waa_root, (str, Path)):
            raise TypeError("WAA 根目录必须是路径")
        root = Path(waa_root).expanduser().resolve()
        client_root = (root / "src" / "win-arena-container" / "client").resolve()
        recorder_path = client_root / "trace2task_human_trace.py"
        if not recorder_path.is_file():
            raise FileNotFoundError(
                "WAA 录制器不存在；请先运行 integrations/windows_agent_arena/install_overlay.py"
            )
        if not isinstance(example_path, (str, Path)) or not str(example_path).strip():
            raise ValueError("请选择 WAA 任务 JSON")
        raw_example = str(example_path).strip().replace("\\", "/")
        if raw_example.startswith("/client/"):
            raw_example = raw_example.removeprefix("/client/")
        candidate = Path(raw_example).expanduser()
        example = (candidate if candidate.is_absolute() else client_root / candidate).resolve()
        if not example.is_relative_to(client_root) or not example.is_file():
            raise FileNotFoundError("WAA 任务 JSON 必须位于 WAA client 目录中")
        example_payload = json.loads(example.read_text(encoding="utf-8"))
        waa_task_id = example_payload.get("id") if isinstance(example_payload, dict) else None
        if not isinstance(waa_task_id, str) or not waa_task_id.strip():
            raise ValueError("WAA 任务 JSON 缺少有效的 id")
        trace2task_metadata = example_payload.get("trace2task")
        if isinstance(trace2task_metadata, dict) and (
            trace2task_metadata.get("recordable") is False
            or trace2task_metadata.get("variant_role") == "held_out_evaluation"
        ):
            raise ValueError("held-out 测试变体禁止人工录制；请选择对应的 D0 演示变体")
        reset_spec = _matching_waa_reset_spec(self.project_root, waa_task_id)
        if not isinstance(distro, str) or Path(distro).name != distro or not distro.strip():
            raise ValueError("WSL 发行版名称无效")
        if not isinstance(container, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]*", container
        ):
            raise ValueError("WAA 容器名称无效")
        session_id = (
            datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
            + f"-{uuid.uuid4().hex[:8]}-waa-windows-human"
        )
        relative_example = example.relative_to(client_root).as_posix()
        with self._lock:
            self._require_idle()
            self._ensure_experience_name_available(normalized_task_id)
            job = ConsoleJob(
                job_id=uuid.uuid4().hex,
                task_path="",
                task_id=normalized_task_id,
                instruction=f"录制 WAA 任务：{relative_example}",
                mode="record",
                kind="waa_recording",
                narrated=narrated,
                defer_compilation=defer_compilation,
                model=model,
                reasoning_effort=reasoning_effort,
                result={
                    "capture_status": "launching",
                    "session_id": session_id,
                    "waa_root": str(root),
                    "example_path": relative_example,
                    "waa_task_id": waa_task_id,
                    "reset_spec": str(reset_spec),
                },
            )
            self._register_job(job)
        thread = threading.Thread(
            target=self._run_waa_recording,
            args=(
                job,
                client_root,
                relative_example,
                reset_spec,
                distro,
                container,
            ),
            name=f"trace2task-waa-record-{job.job_id[:8]}",
            daemon=True,
        )
        thread.start()
        return job.snapshot()

    def go_waa_recording(
        self,
        job_id: str,
        *,
        audio_started_at_epoch_ms: object = None,
    ) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"Unknown job: {job_id}")
            if job.kind != "waa_recording":
                raise ValueError("该任务不是 WAA 录制")
            if job.status != "awaiting_recording_start":
                raise RuntimeError("WAA 录制尚未就绪，或已经开始")
            if job.narrated:
                if (
                    not isinstance(audio_started_at_epoch_ms, (int, float))
                    or isinstance(audio_started_at_epoch_ms, bool)
                    or not math.isfinite(float(audio_started_at_epoch_ms))
                ):
                    raise ValueError("缺少有效的麦克风开始时间")
                audio_epoch_ms: float | None = round(
                    float(audio_started_at_epoch_ms), 3
                )
            else:
                audio_epoch_ms = None
            process = self._waa_processes.get(job_id)
            if process is None or process.stdin is None or process.poll() is not None:
                raise RuntimeError("WAA 录制进程已经退出")
            payload = dict(job.result or {})
            payload["capture_status"] = "starting"
            payload["audio_started_at_epoch_ms"] = audio_epoch_ms
            job.result = payload
            job.status = "running"
            job.logs.append("麦克风与 WAA 已握手，Trace 现在正式开始。")
            job.updated_at = _now()
            process.stdin.write("GO\n")
            process.stdin.flush()
            self.run_store.save(job.snapshot(details=False), log=job.logs[-1])
            return job.snapshot()

    def transcribe_recording_narration(
        self,
        job_id: str,
        *,
        audio_base64: object,
        mime_type: object,
    ) -> dict[str, Any]:
        if not isinstance(job_id, str) or not job_id:
            raise ValueError("缺少录制任务 ID")
        audio = _decode_narration_audio(audio_base64)
        if not audio:
            raise ValueError("没有可供 Turbo 转写的讲解音频")
        if mime_type is not None and not isinstance(mime_type, str):
            raise TypeError("讲解音频类型必须是字符串")

        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"Unknown job: {job_id}")
            if job.kind not in {"recording", "waa_recording"} or not job.narrated:
                raise ValueError("该任务不是带讲解的录制")
            if job.status != "awaiting_narration":
                raise RuntimeError("录制尚未等待讲解转写")
            if not job.result or not isinstance(job.result.get("trace_path"), str):
                raise RuntimeError("录制结果缺少 Trace 路径")
            trace_path = self._resolve_recording_trace(job.result["trace_path"])
            payload = dict(job.result)
            audio_offset = payload.get("audio_start_trace_elapsed_ms")
            payload["narration"] = {
                "status": "transcribing",
                "model": "turbo",
                "audio_start_trace_elapsed_ms": audio_offset,
            }
            job.result = payload
            job.logs.append("正在使用本地 Whisper Turbo 转写讲解；首次使用需要下载模型。")
            job.updated_at = _now()
            self.run_store.save(job.snapshot(details=False), log=job.logs[-1])

        audio_path: Path | None = None
        try:
            audio_path = save_narration_audio(
                trace_path.parent,
                audio=audio,
                mime_type=mime_type,
            )
            transcription = self.narration_transcriber.transcribe(
                audio_path,
                cache_dir=self.project_root / ".cache" / "faster-whisper",
                initial_prompt=(
                    f"任务名称：{job.task_id}。这是中文桌面或游戏操作示范讲解，"
                    "可能包含按钮名、技能名、角色名和英文缩写。"
                ),
            )
        except Exception as error:
            payload = dict(job.result or {})
            payload["narration"] = {
                "status": "transcription_failed",
                "model": "turbo",
                "audio_path": str(audio_path) if audio_path else None,
                "mime_type": mime_type,
            }
            self._update(
                job,
                log=f"本地 Whisper Turbo 转写失败，已保留浏览器草稿：{error}",
                result=payload,
            )
            raise

        payload = dict(job.result or {})
        payload["narration"] = {
            "status": "awaiting_review",
            "model": transcription.model,
            "engine": f"faster_whisper:{transcription.model}",
            "device": transcription.device,
            "compute_type": transcription.compute_type,
            "audio_path": str(audio_path),
            "mime_type": mime_type,
            "transcript_chars": len(transcription.transcript),
            "transcript": transcription.transcript,
            "segments": transcription.segments,
            "audio_start_trace_elapsed_ms": audio_offset,
        }
        self._update(
            job,
            log=(
                "本地 Whisper Turbo 转写完成，"
                f"使用 {transcription.device}/{transcription.compute_type}；请检查文字后确认。"
            ),
            result=payload,
        )
        return {
            "job": job.snapshot(),
            "transcription": asdict(transcription),
        }

    def transcribe_dictation(
        self,
        *,
        audio_base64: object,
        mime_type: object,
        context: object = "",
    ) -> dict[str, Any]:
        audio = _decode_narration_audio(audio_base64)
        if not audio:
            raise ValueError("没有可供语音输入转写的音频")
        if len(audio) > MAX_NARRATION_AUDIO_BYTES:
            raise ValueError(
                f"语音输入音频不能超过 {MAX_NARRATION_AUDIO_BYTES} 字节"
            )
        if not isinstance(mime_type, str) or mime_type not in NARRATION_AUDIO_EXTENSIONS:
            raise ValueError("语音输入音频格式不受支持")
        if not isinstance(context, str):
            raise TypeError("语音输入上下文必须是字符串")
        normalized_context = " ".join(context.split())[:500]
        suffix = NARRATION_AUDIO_EXTENSIONS[mime_type]
        with tempfile.TemporaryDirectory(prefix="trace2task-dictation-") as directory:
            audio_path = Path(directory) / f"dictation{suffix}"
            audio_path.write_bytes(audio)
            transcription = self.narration_transcriber.transcribe(
                audio_path,
                cache_dir=self.project_root / ".cache" / "faster-whisper",
                initial_prompt=(
                    "这是 Trace2Task 中文桌面 Agent 控制台的语音输入，可能包含任务指令、"
                    "应用名、人名、游戏术语、操作反馈和界面状态。"
                    + (f" 当前输入框用途：{normalized_context}。" if normalized_context else "")
                ),
            )
        return {"transcription": asdict(transcription)}

    def submit_recording_narration(
        self,
        job_id: str,
        *,
        transcript: object,
        segments: object = None,
        audio_base64: object = None,
        mime_type: object = None,
        transcription_engine: object = "browser_web_speech",
    ) -> dict[str, Any]:
        if not isinstance(job_id, str) or not job_id:
            raise ValueError("缺少录制任务 ID")
        audio = _decode_narration_audio(audio_base64)
        if mime_type is not None and not isinstance(mime_type, str):
            raise TypeError("讲解音频类型必须是字符串")
        if not isinstance(transcription_engine, str):
            raise TypeError("转写引擎必须是字符串")

        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"Unknown job: {job_id}")
            if job.kind not in {"recording", "waa_recording"} or not job.narrated:
                raise ValueError("该任务不是带讲解的录制")
            if job.status != "awaiting_narration":
                raise RuntimeError("讲解已经提交，或录制尚未等待讲解确认")
            if not job.result or not isinstance(job.result.get("trace_path"), str):
                raise RuntimeError("录制结果缺少 Trace 路径")
            trace_path = self._resolve_recording_trace(job.result["trace_path"])
            pending = (
                job.result.get("narration")
                if isinstance(job.result.get("narration"), dict)
                else {}
            )
            pending_audio_path: Path | None = None
            audio_start_trace_elapsed_ms = pending.get(
                "audio_start_trace_elapsed_ms"
            )
            if audio_start_trace_elapsed_ms is None:
                audio_start_trace_elapsed_ms = job.result.get(
                    "audio_start_trace_elapsed_ms"
                )
            if audio is None and isinstance(pending.get("audio_path"), str):
                candidate = Path(pending["audio_path"]).expanduser().resolve()
                if candidate.parent == trace_path.parent.resolve() and candidate.is_file():
                    pending_audio_path = candidate
                    if mime_type is None and isinstance(pending.get("mime_type"), str):
                        mime_type = pending["mime_type"]
            job.status = "queued"
            job.logs.append(
                "讲解已提交，正在归档录制。"
                if job.defer_compilation
                else "讲解已提交，正在归档并准备 Compiler Agent。"
            )
            job.updated_at = _now()
            self.run_store.save(job.snapshot(details=False), log=job.logs[-1])

        try:
            narration = archive_narration(
                trace_path.parent,
                transcript=transcript,
                segments=segments,
                audio=audio,
                existing_audio_path=pending_audio_path,
                mime_type=mime_type,
                transcription_engine=transcription_engine or "manual",
                audio_start_trace_elapsed_ms=audio_start_trace_elapsed_ms,
            )
        except Exception:
            self._update(job, status="awaiting_narration")
            raise

        payload = dict(job.result)
        payload["narration"] = {
            "status": "archived",
            "manifest_path": str(narration.manifest_path),
            "transcript_chars": len(narration.transcript),
            "segments": narration.segment_count,
            "audio_path": str(narration.audio_path) if narration.audio_path else None,
            "audio_start_trace_elapsed_ms": audio_start_trace_elapsed_ms,
        }
        self._update(job, result=payload)
        if job.defer_compilation:
            self._finish_deferred_recording(job, payload)
            return job.snapshot()
        thread = threading.Thread(
            target=self._run_recording_compilation,
            args=(job, trace_path, payload),
            name=f"trace2task-narrated-compile-{job.job_id[:8]}",
            daemon=True,
        )
        thread.start()
        return job.snapshot()

    def upgrade_taskpack(self, raw_path: str) -> dict[str, Any]:
        task_path = self._resolve_task_path(raw_path)
        contract = load_windows_task(task_path)
        if not contract.selector.process_name or contract.selector.process_name.casefold() not in {
            "weixin.exe",
            "wechat.exe",
        }:
            raise ValueError("当前只支持给微信经验补齐消息能力")
        root = yaml.safe_load(task_path.read_text(encoding="utf-8"))
        actions = root.get("actions")
        if not isinstance(actions, list):
            raise TypeError("任务包 actions 必须是列表")
        for skill in ("type_text", "press_key", "hotkey"):
            if skill not in actions:
                actions.append(skill)
        review = root.setdefault("review", {})
        review["status"] = "draft"
        review["requires_confirmation"] = True
        review.pop("confirmed_at", None)
        checklist = review.setdefault("checklist", [])
        note = "Confirm that runtime text input and final send behavior are intended."
        if note not in checklist:
            checklist.append(note)
        task_path.write_text(
            yaml.safe_dump(root, sort_keys=False, allow_unicode=True, width=100),
            encoding="utf-8",
        )
        load_windows_task(task_path)
        return {
            "task_path": task_path.relative_to(self.project_root).as_posix(),
            "status": "draft",
            "added_capabilities": ["type_text", "press_key", "hotkey"],
        }

    def confirm_local_taskpack(self, raw_path: str) -> dict[str, Any]:
        task_path = self._resolve_task_path(raw_path)
        result = confirm_taskpack(task_path)
        return _jsonable(result)

    def compile_recording(
        self,
        raw_path: str,
        *,
        model: str = DEFAULT_COMPILER_MODEL,
        reasoning_effort: str = DEFAULT_COMPILER_REASONING_EFFORT,
        confirm_duplicate: bool = False,
    ) -> dict[str, Any]:
        trace_path = self._resolve_recording_trace(raw_path)
        confirmation = self._compilation_confirmation(trace_path)
        if confirmation and confirm_duplicate is not True:
            return confirmation
        return self._compile_trace_bundle(
            trace_path,
            model=model,
            reasoning_effort=reasoning_effort,
        )

    def _compilation_confirmation(self, trace_path: Path) -> dict[str, Any] | None:
        from trace2task.trace_library import version_time

        task_path = self._find_taskpack_for_trace(trace_path)
        if task_path is None:
            return None
        created_at, time_source = version_time({}, task_path)
        return {"confirmation_required": True, "representation": "semantic",
                "trace_path": trace_path.relative_to(self.project_root).as_posix(),
                "existing_versions": [{"path": task_path.relative_to(self.project_root).as_posix(),
                    "trace_name": task_path.parent.name, "created_at": created_at,
                    "created_at_source": time_source}],
                "message": "同一原始录制已有语义编译任务包。继续会在已有任务包重新生成语义层，请核对后确认。"}

    def generate_trace_d(self, raw_path: str, *, confirm_duplicate=False) -> dict[str, Any]:
        from trace2task.trace_library import generate_d

        with self._lock:
            self._require_idle()
            return generate_d(self.project_root, raw_path, confirm_duplicate=confirm_duplicate)

    def generate_trace_a(self, raw_path: str, *, confirm_duplicate=False) -> dict[str, Any]:
        from trace2task.trace_library import generate_a

        with self._lock:
            self._require_idle()
            return generate_a(self.project_root, raw_path, confirm_duplicate=confirm_duplicate)

    def start_compilation(
        self,
        raw_path: str,
        *,
        model: str = DEFAULT_COMPILER_MODEL,
        reasoning_effort: str = DEFAULT_COMPILER_REASONING_EFFORT,
        confirm_duplicate: bool = False,
    ) -> dict[str, Any]:
        trace_path = self._resolve_recording_trace(raw_path)
        metadata_path = trace_path.with_name("metadata.json")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        task_id = str(metadata.get("task_id") or trace_path.parent.name)
        if model not in CODEX_MODELS:
            raise ValueError(f"不支持的模型：{model}")
        if reasoning_effort not in CODEX_REASONING_EFFORTS:
            raise ValueError(f"不支持的思考强度：{reasoning_effort}")
        with self._lock:
            self._require_idle()
            confirmation = self._compilation_confirmation(trace_path)
            if confirmation and confirm_duplicate is not True:
                return confirmation
            job = ConsoleJob(
                job_id=uuid.uuid4().hex,
                task_path="",
                task_id=task_id,
                instruction=f"编译原始 Trace：{trace_path.parent.name}",
                mode="compile",
                kind="compilation",
                model=model,
                reasoning_effort=reasoning_effort,
            )
            job.logs.append("编译请求已接收，正在准备人类 Trace 证据。")
            self._register_job(job)
        thread = threading.Thread(
            target=self._run_compilation,
            args=(job, trace_path),
            name=f"trace2task-compile-{job.job_id[:8]}",
            daemon=True,
        )
        thread.start()
        return job.snapshot()

    def start_revision(
        self,
        raw_path: str,
        feedback: str,
        *,
        model: str = DEFAULT_REVISION_MODEL,
        reasoning_effort: str = DEFAULT_REVISION_REASONING_EFFORT,
    ) -> dict[str, Any]:
        candidate_path = self._resolve_candidate_manifest(raw_path)
        normalized_feedback = " ".join(feedback.split())
        if not normalized_feedback:
            raise ValueError("请输入对这次运行的改进意见")
        if len(normalized_feedback) > 2_000:
            raise ValueError("改进意见不能超过 2000 个字符")
        if model not in CODEX_MODELS:
            raise ValueError(f"不支持的模型：{model}")
        if reasoning_effort not in CODEX_REASONING_EFFORTS:
            raise ValueError(f"不支持的思考强度：{reasoning_effort}")
        candidate = yaml.safe_load(candidate_path.read_text(encoding="utf-8"))
        if not isinstance(candidate, dict):
            raise TypeError("候选经验格式无效")
        task_path = self._resolve_task_path(str(candidate.get("source_task") or ""))
        contract = load_windows_task(task_path)
        if contract.semantic_experience is None:
            raise ValueError("这份任务还没有语义经验，无法生成阶段化改进")
        with self._lock:
            self._require_idle()
            job = ConsoleJob(
                job_id=uuid.uuid4().hex,
                task_path=task_path.relative_to(self.project_root).as_posix(),
                task_id=contract.task.task_id,
                instruction=normalized_feedback,
                mode="revision",
                kind="revision",
                model=model,
                reasoning_effort=reasoning_effort,
            )
            job.logs.append("反馈已接收，Revision Agent 正在对比当前经验与本次运行。")
            self._register_job(job)
        thread = threading.Thread(
            target=self._run_revision,
            args=(job, candidate_path, task_path),
            name=f"trace2task-revision-{job.job_id[:8]}",
            daemon=True,
        )
        thread.start()
        return job.snapshot()

    def confirm_candidate_revision(self, raw_path: str) -> dict[str, Any]:
        with self._lock:
            self._require_idle()
            candidate_path = self._resolve_candidate_manifest(raw_path)
            candidate = yaml.safe_load(candidate_path.read_text(encoding="utf-8"))
            if not isinstance(candidate, dict):
                raise TypeError("候选经验格式无效")
            task_path = self._resolve_task_path(str(candidate.get("source_task") or ""))
            contract = load_windows_task(task_path)
            if contract.semantic_experience is None:
                raise ValueError("这份任务还没有语义经验")
            result = activate_guidance_revision(
                self.project_root,
                candidate_path,
                task_id=contract.task.task_id,
                stage_ids=set(contract.semantic_experience.state_ids),
                transition_ids={
                    transition.transition_id
                    for transition in contract.semantic_experience.transitions
                },
                terminal_ids=set(contract.semantic_experience.terminal_ids),
            )
            load_windows_task(task_path)
            return result

    def start_task_model_revision(
        self,
        raw_path: str,
        feedback: str,
        *,
        model: str = DEFAULT_TASK_MODEL_REVISION_MODEL,
        reasoning_effort: str = DEFAULT_TASK_MODEL_REVISION_REASONING_EFFORT,
    ) -> dict[str, Any]:
        candidate_path = self._resolve_candidate_manifest(raw_path)
        normalized_feedback = " ".join(feedback.split())
        if not normalized_feedback:
            raise ValueError("请输入要修正的阶段、状态、转移或结束条件")
        if len(normalized_feedback) > 2_000:
            raise ValueError("任务结构反馈不能超过 2000 个字符")
        if model not in CODEX_MODELS:
            raise ValueError(f"不支持的模型：{model}")
        if reasoning_effort not in CODEX_REASONING_EFFORTS:
            raise ValueError(f"不支持的思考强度：{reasoning_effort}")
        candidate = yaml.safe_load(candidate_path.read_text(encoding="utf-8"))
        if not isinstance(candidate, dict):
            raise TypeError("候选经验格式无效")
        task_path = self._resolve_task_path(str(candidate.get("source_task") or ""))
        contract = load_windows_task(task_path)
        if contract.semantic_experience is None:
            raise ValueError("这份任务还没有可修订的语义任务模型")
        with self._lock:
            self._require_idle()
            job = ConsoleJob(
                job_id=uuid.uuid4().hex,
                task_path=task_path.relative_to(self.project_root).as_posix(),
                task_id=contract.task.task_id,
                instruction=normalized_feedback,
                mode="task_model_revision",
                kind="task_model_revision",
                model=model,
                reasoning_effort=reasoning_effort,
            )
            job.logs.append(
                "结构反馈已接收，Task Model Revision Agent 正在生成有向状态图草稿。"
            )
            self._register_job(job)
        thread = threading.Thread(
            target=self._run_task_model_revision,
            args=(job, candidate_path, task_path),
            name=f"trace2task-task-model-{job.job_id[:8]}",
            daemon=True,
        )
        thread.start()
        return job.snapshot()

    def confirm_task_model_revision(self, raw_path: str) -> dict[str, Any]:
        with self._lock:
            self._require_idle()
            candidate_path = self._resolve_candidate_manifest(raw_path)
            candidate = yaml.safe_load(candidate_path.read_text(encoding="utf-8"))
            if not isinstance(candidate, dict):
                raise TypeError("候选经验格式无效")
            task_path = self._resolve_task_path(str(candidate.get("source_task") or ""))
            result = activate_task_model_revision(
                self.project_root,
                candidate_path,
                task_path,
            )
            load_windows_task(task_path)
            return result

    def update_candidate_revision_summary(
        self,
        raw_path: str,
        summary: str,
    ) -> dict[str, Any]:
        with self._lock:
            self._require_idle()
            candidate_path = self._resolve_candidate_manifest(raw_path)
            return update_guidance_proposal_summary(candidate_path, summary)

    def _freeze_automatic_compiler_snapshot(
        self,
        task_path: Path,
        *,
        variant: str,
        model: str,
        reasoning_effort: str,
        source_trace: Path,
        fresh_taskpack: bool,
    ) -> dict[str, Any]:
        from trace2task.compiler_snapshot import freeze_snapshot, tree_digest, validate_snapshot

        marker_path = task_path.parent / ".automatic-compiler-snapshot.json"
        if marker_path.is_file() and not fresh_taskpack:
            existing = json.loads(marker_path.read_text(encoding="utf-8"))
            verified = validate_snapshot(Path(str(existing.get("task_path") or "")))
            source_hash = hashlib.sha256(source_trace.read_bytes()).hexdigest()
            tree_hash = tree_digest(task_path.parent, exclude_marker=True)[0]
            if (existing != verified or existing.get("source_tree_sha256") != tree_hash
                    or existing.get("source_trace_sha256") != source_hash
                    or existing.get("variant") != variant or existing.get("model") != model
                    or existing.get("reasoning_effort") != reasoning_effort):
                raise RuntimeError("Stale or unverifiable Compiler snapshot marker; freeze a fresh version")
            return existing
        payload = freeze_snapshot(
            task_path, output_root=self.project_root / "evaluations" / "compiler-snapshots",
            source_trace=source_trace, variant=variant, model=model,
            reasoning_effort=reasoning_effort, fresh_taskpack=fresh_taskpack,
        )
        marker_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return payload

    def _compile_trace_bundle(
        self,
        trace_path: Path,
        *,
        model: str,
        reasoning_effort: str,
        status_callback: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        if model not in CODEX_MODELS:
            raise ValueError(f"不支持的模型：{model}")
        if reasoning_effort not in CODEX_REASONING_EFFORTS:
            raise ValueError(f"不支持的思考强度：{reasoning_effort}")
        report = status_callback or (lambda message: None)
        existing_task = self._find_taskpack_for_trace(trace_path)
        recording_metadata = json.loads(
            trace_path.with_name("metadata.json").read_text(encoding="utf-8")
        )
        recording_task_id = recording_metadata.get("task_id")
        if not isinstance(recording_task_id, str) or not recording_task_id.strip():
            raise ValueError("原始录制缺少有效的经验名称")
        if existing_task is None:
            duplicate_task = next(
                (
                    task
                    for task in self.list_taskpacks()
                    if self._experience_name_key(task.get("task_id"))
                    == self._experience_name_key(recording_task_id)
                ),
                None,
            )
            if duplicate_task is not None:
                raise ValueError(
                    f"已经存在同名任务经验“{recording_task_id.strip()}”，"
                    "请使用现有经验，或先删除同名任务经验后再编译"
                )
        report("正在检查 Codex Compiler 连接，失败时不会进入长时间编译。")
        preflight = probe_codex_compiler_connection()
        report(f"Codex Compiler 连接正常（{preflight['elapsed_ms']:.0f}ms）。")
        if existing_task is None:
            report("正在把原始输入编译为确定性动作证据。")
            result = compile_trace(
                trace_path,
                self.project_root / "taskpacks" / "generated" / "web",
            )
            payload = _jsonable(result)
            semantic_task = Path(result.task_path)
            payload["reused_taskpack"] = False
        else:
            report("检测到同一原始 Trace 的任务包，将重新生成语义层，不再创建副本。")
            contract = load_windows_task(existing_task)
            semantic_task = existing_task
            payload = {
                "task_id": contract.task.task_id,
                "task_path": str(existing_task),
                "source_trace": str(trace_path),
                "demonstration_actions": len(contract.demonstration),
                "review_status": contract.task.review_status,
                "requires_confirmation": contract.task.requires_confirmation,
                "reused_taskpack": True,
            }
        payload["compiler_preflight"] = preflight
        report("动作证据已就绪，Compiler Agent 正在理解阶段、状态和动作意图。")
        try:
            semantic = compile_windows_semantic_experience(
                semantic_task,
                model=model,
                reasoning_effort=reasoning_effort,
            )
        except Exception as error:  # noqa: BLE001 - deterministic artifact remains useful
            failure_category, retryable = _compiler_failure(
                error,
                fallback="compiler_error",
            )
            payload["semantic_compilation"] = {
                "status": "failed",
                "error": f"{type(error).__name__}: {error}",
                "failure_category": failure_category,
                "retryable": retryable,
            }
            if retryable:
                report(
                    f"{_compiler_failure_summary(failure_category)}；"
                    "动作任务包已保留，可稍后直接重试。"
                )
        else:
            automatic_snapshot = self._freeze_automatic_compiler_snapshot(
                semantic_task,
                variant="compiled",
                model=model,
                reasoning_effort=reasoning_effort,
                source_trace=trace_path,
                fresh_taskpack=existing_task is None,
            )
            payload["review_status"] = "draft"
            payload["requires_confirmation"] = True
            payload["semantic_compilation"] = {
                "status": "completed",
                "result": _jsonable(semantic),
            }
            payload["automatic_snapshot"] = automatic_snapshot
            report(
                "已冻结人工修改前的 Compiler 输出："
                f"{automatic_snapshot['task_path']}。"
            )
        return payload

    def _compile_waa_narration_pair(
        self,
        trace_path: Path,
        *,
        model: str,
        reasoning_effort: str,
        status_callback: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        report = status_callback or (lambda message: None)
        metadata = json.loads(
            trace_path.with_name("metadata.json").read_text(encoding="utf-8")
        )
        base_task_id = str(metadata.get("task_id") or "").strip()
        if not base_task_id:
            raise ValueError("WAA 原始录制缺少有效的经验名称")
        variants = (
            ("compiled", f"{base_task_id} · 纯Trace", False),
            ("narrated_compiled", f"{base_task_id} · 人工讲解", True),
        )
        existing_tasks: dict[str, Path | None] = {}
        for variant, task_id, _ in variants:
            existing_task = self._find_taskpack_for_trace(trace_path, task_id=task_id)
            existing_tasks[variant] = existing_task
            if existing_task is None:
                self._ensure_experience_name_available(task_id)
        report("正在检查 Codex Compiler 连接，失败时不会连续等待两个变体。")
        try:
            preflight = probe_codex_compiler_connection()
        except Exception as error:  # noqa: BLE001 - report one actionable pair failure
            error_text = f"{type(error).__name__}: {error}"
            failure_category, retryable = _compiler_failure(
                error,
                fallback="preflight_error",
            )
            report(f"Codex Compiler 连接预检失败，已跳过两个变体：{error_text}")
            return {
                "status": "failed",
                "source_trace": str(trace_path),
                "family_id": base_task_id,
                "compiler_preflight": {
                    "status": "failed",
                    "error": error_text,
                    "failure_category": failure_category,
                    "retryable": retryable,
                },
                "variants": {
                    variant: {
                        "status": "skipped",
                        "task_id": task_id,
                        "reason": "Codex Compiler 连接预检失败；请稍后从此录制重试。",
                    }
                    for variant, task_id, _ in variants
                },
            }
        report(f"Codex Compiler 连接正常（{preflight['elapsed_ms']:.0f}ms）。")
        results: dict[str, Any] = {}
        for variant_index, (variant, task_id, use_narration) in enumerate(variants):
            report(
                "正在生成“人工讲解”Compiler 变体。"
                if use_narration
                else "正在生成“纯 Trace”Compiler 变体；本轮强制忽略讲解。"
            )
            try:
                task_path = existing_tasks[variant]
                fresh_taskpack = task_path is None
                if task_path is None:
                    compiled = compile_trace(
                        trace_path,
                        self.project_root / "taskpacks" / "generated" / "web",
                        task_id_override=task_id,
                    )
                    task_path = Path(compiled.task_path)
                    deterministic_compilation = _jsonable(compiled)
                    deterministic_compilation["reused_taskpack"] = False
                else:
                    report(f"检测到“{task_id}”的已有动作任务包，将只重试语义编译。")
                    deterministic_compilation = {
                        "task_id": task_id,
                        "task_path": str(task_path),
                        "source_trace": str(trace_path),
                        "reused_taskpack": True,
                    }
                semantic = compile_windows_semantic_experience(
                    task_path,
                    model=model,
                    reasoning_effort=reasoning_effort,
                    use_narration=use_narration,
                )
                automatic_snapshot = self._freeze_automatic_compiler_snapshot(
                    task_path,
                    variant=variant,
                    model=model,
                    reasoning_effort=reasoning_effort,
                    source_trace=trace_path,
                    fresh_taskpack=fresh_taskpack,
                )
            except Exception as error:  # noqa: BLE001 - keep the successful sibling
                failure_category, retryable = _compiler_failure(
                    error,
                    fallback="compiler_error",
                )
                results[variant] = {
                    "status": "failed",
                    "task_id": task_id,
                    "error": f"{type(error).__name__}: {error}",
                    "failure_category": failure_category,
                    "retryable": retryable,
                }
                if retryable:
                    report(
                        f"{_compiler_failure_summary(failure_category)}，"
                        "已立即停止剩余 Compiler 变体。"
                    )
                    for skipped_variant, skipped_task_id, _ in variants[variant_index + 1 :]:
                        results[skipped_variant] = {
                            "status": "skipped",
                            "task_id": skipped_task_id,
                            "reason": (
                                f"前一个变体发生可重试的 Codex 故障：{failure_category}；"
                                "请稍后从此录制重试。"
                            ),
                        }
                    break
                continue
            results[variant] = {
                "status": "completed",
                "task_id": task_id,
                "task_path": str(task_path),
                "use_narration": use_narration,
                "source_trace": str(trace_path),
                "deterministic_compilation": deterministic_compilation,
                "semantic_compilation": _jsonable(semantic),
                "automatic_snapshot": automatic_snapshot,
            }
            report(
                "已冻结人工修改前的 Compiler 输出："
                f"{automatic_snapshot['task_path']}。"
            )
        completed = sum(
            result.get("status") == "completed" for result in results.values()
        )
        return {
            "status": (
                "completed"
                if completed == len(variants)
                else ("partial" if completed else "failed")
            ),
            "source_trace": str(trace_path),
            "family_id": base_task_id,
            "compiler_preflight": preflight,
            "variants": results,
        }

    def _find_taskpack_for_trace(
        self,
        trace_path: Path,
        *,
        task_id: str | None = None,
    ) -> Path | None:
        source_trace = trace_path.resolve()
        matches: list[Path] = []
        if not self.task_root.is_dir():
            return None
        for report_path in self.task_root.rglob("compiler-report.json"):
            if ".trash" in report_path.relative_to(self.task_root).parts:
                continue
            if report_path.with_name(".trace2task-deleted.json").exists():
                continue
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
                raw_source = (report.get("source") or {}).get("trace")
                if not isinstance(raw_source, str) or not raw_source:
                    continue
                candidate_source = Path(raw_source)
                if not candidate_source.is_absolute():
                    candidate_source = self.project_root / candidate_source
                task_path = report_path.with_name("task.yaml")
                if candidate_source.resolve() != source_trace or not task_path.is_file():
                    continue
                if task_id is not None:
                    contract = load_windows_task(task_path)
                    if self._experience_name_key(contract.task.task_id) != self._experience_name_key(
                        task_id
                    ):
                        continue
                matches.append(task_path)
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                continue
        if not matches:
            return None
        return max(matches, key=lambda path: path.stat().st_mtime)

    @staticmethod
    def _experience_name_key(value: object) -> str:
        return " ".join(str(value or "").split()).casefold()

    def _select_family_source(
        self,
        task_id: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any] | None:
        initial_window = (
            metadata.get("initial_window")
            if isinstance(metadata.get("initial_window"), dict)
            else {}
        )
        process_name = str(initial_window.get("process_name") or "").casefold()
        candidates = [
            task
            for task in self.list_taskpacks()
            if task.get("confirmed")
            and task.get("human_guidance") is not None
            and (
                not process_name
                or str(task.get("process_name") or "").casefold() == process_name
            )
            and self._experience_name_key(task.get("task_id"))
            != self._experience_name_key(task_id)
        ]
        if not candidates:
            return None
        requested = self._experience_name_key(task_id).replace(" ", "")
        candidates = [
            task
            for task in candidates
            if requested
            and (
                requested in self._experience_name_key(task.get("task_id")).replace(" ", "")
                or SequenceMatcher(
                    None,
                    requested,
                    self._experience_name_key(task.get("task_id")).replace(" ", ""),
                ).ratio()
                >= 0.55
            )
        ]
        if not candidates:
            return None
        try:
            match = route_experience(task_id, candidates)
        except ValueError:
            candidates.sort(
                key=lambda task: int((task.get("human_guidance") or {}).get("revision") or 0),
                reverse=True,
            )
            return candidates[0]
        if match.confidence < 0.75:
            return None
        return next(
            (task for task in candidates if task.get("path") == match.task_path),
            None,
        )

    @staticmethod
    def _set_experience_family(task_path: Path, family_id: str) -> None:
        root = yaml.safe_load(task_path.read_text(encoding="utf-8"))
        if not isinstance(root, dict):
            raise TypeError("任务经验格式无效")
        experience = root.get("experience")
        if not isinstance(experience, dict):
            experience = {}
        experience["family_id"] = " ".join(family_id.split())
        root["experience"] = experience
        task_path.write_text(
            yaml.safe_dump(root, sort_keys=False, allow_unicode=True, width=100),
            encoding="utf-8",
        )

    def _inherit_family_guidance(
        self,
        source_task: Path,
        target_task: Path,
        *,
        family_id: str,
    ) -> dict[str, Any] | None:
        if source_task.resolve() == target_task.resolve():
            return None
        source_contract = load_windows_task(source_task)
        target_contract = load_windows_task(target_task)
        if source_contract.human_guidance is None or target_contract.semantic_experience is None:
            return None
        target_scope_ids = {
            "state": set(target_contract.semantic_experience.state_ids),
            "transition": {
                transition.transition_id
                for transition in target_contract.semantic_experience.transitions
            },
            "terminal": set(target_contract.semantic_experience.terminal_ids),
        }
        source_guidance = source_contract.human_guidance.source_path
        target_guidance = target_contract.human_guidance

        def scope_is_supported(value: dict[str, Any]) -> bool:
            try:
                scope = guidance_scope_payload(value)
            except (TypeError, ValueError):
                return False
            return scope["type"] == "global" or scope["id"] in target_scope_ids.get(
                scope["type"], set()
            )

        def canonical_scoped_copy(value: dict[str, Any]) -> dict[str, Any]:
            copied = deepcopy(value)
            copied["scope"] = guidance_scope_payload(value)
            copied.pop("stage_id", None)
            return copied

        def inherited_document(path: Path) -> dict[str, Any] | None:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("status") != "confirmed":
                return None
            document = deepcopy(raw)
            rules = [
                canonical_scoped_copy(rule)
                for rule in document.get("rules", [])
                if isinstance(rule, dict)
                and scope_is_supported(rule)
            ]
            if not rules:
                return None
            document["task_id"] = target_contract.task.task_id
            document["rules"] = rules
            operations = document.get("operations")
            if isinstance(operations, list):
                document["operations"] = [
                    canonical_scoped_copy(operation)
                    for operation in operations
                    if isinstance(operation, dict)
                    and scope_is_supported(operation)
                ]
            document["inheritance"] = {
                "family_id": family_id,
                "source_task": source_task.relative_to(self.project_root).as_posix(),
                "source_task_id": source_contract.task.task_id,
                "inherited_at": _now(),
                "policy": "confirmed_human_guidance_wins",
            }
            return document

        active = inherited_document(source_guidance)
        if active is None:
            return None
        local_rule_count = 0
        if target_guidance is not None:
            source_rules = {
                str(rule.get("id")): rule
                for rule in active["rules"]
                if isinstance(rule, dict) and rule.get("id")
            }
            operations: list[dict[str, Any]] = []
            renamed_local_rules: list[dict[str, str]] = []
            for local_rule in target_guidance.rules:
                payload = local_rule.prompt_payload()
                result_rule_id = local_rule.rule_id
                existing = source_rules.get(result_rule_id)
                if existing == payload:
                    continue
                if existing is not None:
                    number = 1
                    while f"trick-{number:04d}" in source_rules:
                        number += 1
                    result_rule_id = f"trick-{number:04d}"
                    payload = {**payload, "id": result_rule_id}
                    renamed_local_rules.append(
                        {"from": local_rule.rule_id, "to": result_rule_id}
                    )
                source_rules[result_rule_id] = payload
                operations.append(
                    {
                        "operation": "add",
                        "target_rule_id": "",
                        "result_rule_id": result_rule_id,
                        **{key: value for key, value in payload.items() if key != "id"},
                        "reason": "保留该新录制已经确认的本地人工反馈。",
                    }
                )
            if len(source_rules) > 12:
                raise ValueError("经验族合并后超过 12 条人工规则，请先在网页端精简旧规则")
            local_rule_count = len(target_guidance.rules)
            source_revision = int(active["revision"])
            active["parent_revision"] = source_revision
            active["revision"] = source_revision + 1
            active["rules"] = list(source_rules.values())
            active["operations"] = operations
            active["summary"] = (
                f"{active.get('summary', '')}；本录制已确认反馈：{target_guidance.summary}"
            )
            active["source"] = {
                "type": "experience_family_inheritance",
                "feedback": "",
            }
            active.setdefault("review", {})["confirmed_at"] = _now()
            active.setdefault("revision_agent", {})["created_at"] = _now()
            active["inheritance"]["local_revision"] = target_guidance.revision
            active["inheritance"]["local_rule_count"] = local_rule_count
            active["inheritance"]["renamed_local_rules"] = renamed_local_rules
        target_guidance_path = target_task.with_name("guidance.yaml")
        target_revisions = target_task.parent / "guidance-revisions"
        source_revisions = source_guidance.parent / "guidance-revisions"
        if source_revisions.is_dir():
            for source_revision in source_revisions.glob("revision-*.yaml"):
                revision = inherited_document(source_revision)
                if revision is None:
                    continue
                target_revisions.mkdir(parents=True, exist_ok=True)
                (target_revisions / source_revision.name).write_text(
                    yaml.safe_dump(
                        revision,
                        sort_keys=False,
                        allow_unicode=True,
                        width=100,
                    ),
                    encoding="utf-8",
                )
        target_guidance_path.write_text(
            yaml.safe_dump(active, sort_keys=False, allow_unicode=True, width=100),
            encoding="utf-8",
        )
        task_root = yaml.safe_load(target_task.read_text(encoding="utf-8"))
        if not isinstance(task_root, dict):
            raise TypeError("任务经验格式无效")
        task_root["human_guidance"] = {
            "path": target_guidance_path.name,
            "revision": int(active["revision"]),
            "rule_count": len(active["rules"]),
        }
        target_task.write_text(
            yaml.safe_dump(task_root, sort_keys=False, allow_unicode=True, width=100),
            encoding="utf-8",
        )
        load_windows_task(target_task)
        return {
            "family_id": family_id,
            "source_task_id": source_contract.task.task_id,
            "revision": int(active["revision"]),
            "rule_count": len(active["rules"]),
            "local_rule_count": local_rule_count,
        }

    def _ensure_experience_name_available(self, task_id: str) -> None:
        requested = self._experience_name_key(task_id)
        duplicate_task = next(
            (
                task
                for task in self.list_taskpacks()
                if self._experience_name_key(task.get("task_id")) == requested
            ),
            None,
        )
        if duplicate_task is not None:
            raise ValueError(
                f"经验名称“{task_id}”已被任务经验使用，请换一个名称或先删除旧经验"
            )
        duplicate_recording = next(
            (
                recording
                for recording in self.list_recordings()
                if self._experience_name_key(recording.get("task_id")) == requested
            ),
            None,
        )
        if duplicate_recording is not None:
            raise ValueError(
                f"经验名称“{task_id}”已被原始录制使用，请换一个名称或先删除旧录制"
            )

    def delete_taskpack(self, raw_path: str) -> dict[str, Any]:
        with self._lock:
            self._require_idle()
            task_path = self._resolve_task_path(raw_path)
            return self._move_to_trash(
                task_path.parent,
                allowed_root=self.task_root,
                trash_root=self.task_root / ".trash",
                kind="taskpack",
            )

    def delete_human_guidance(self, raw_path: str) -> dict[str, Any]:
        with self._lock:
            self._require_idle()
            task_path = self._resolve_task_path(raw_path)
            original_task_bytes = task_path.read_bytes()
            task = yaml.safe_load(task_path.read_text(encoding="utf-8"))
            if not isinstance(task, dict):
                raise TypeError("任务经验格式无效")
            pointer = task.get("human_guidance")
            if pointer is None:
                raise ValueError("这份任务没有可删除的人工反馈经验")
            if not isinstance(pointer, dict):
                raise TypeError("人工反馈经验配置格式无效")
            raw_guidance_path = pointer.get("path")
            if not isinstance(raw_guidance_path, str) or not raw_guidance_path.strip():
                raise ValueError("人工反馈经验路径无效")
            task_dir = task_path.parent.resolve()
            guidance_path = (task_dir / raw_guidance_path).resolve()
            if (
                not guidance_path.is_relative_to(task_dir)
                or not guidance_path.is_file()
                or guidance_path == task_path
                or guidance_path.name in {'restore.json', '.trace2task-trash.json', '.trace2task-deleted.json', 'guidance-revisions'}
            ):
                raise ValueError("人工反馈经验不在当前任务目录中")

            without_guidance = dict(task)
            without_guidance.pop("human_guidance")
            without_guidance_bytes = yaml.safe_dump(
                without_guidance, sort_keys=False, allow_unicode=True, width=100).encode("utf-8")

            trash_root = self.task_root / ".trash" / "guidance"
            trash_root.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
            destination = trash_root / f"{stamp}-{task_dir.name}"
            destination.mkdir()
            revisions_path = task_dir / "guidance-revisions"
            moved: list[tuple[Path, Path]] = []
            try:
                trashed_guidance = destination / guidance_path.name
                guidance_path.replace(trashed_guidance)
                moved.append((trashed_guidance, guidance_path))
                if revisions_path.is_dir():
                    trashed_revisions = destination / revisions_path.name
                    revisions_path.replace(trashed_revisions)
                    moved.append((trashed_revisions, revisions_path))
                (destination / "restore.json").write_text(
                    json.dumps(
                        {
                            "kind": "human_guidance",
                            "task_path": task_path.relative_to(self.project_root).as_posix(),
                            "guidance_pointer": pointer,
                            "task_without_guidance_sha256": hashlib.sha256(without_guidance_bytes).hexdigest(),
                            "deleted_at": _now(),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                task.pop("human_guidance", None)
                temporary = task_path.with_suffix(f"{task_path.suffix}.tmp")
                temporary.write_bytes(without_guidance_bytes)
                temporary.replace(task_path)
                self.trash.register_guidance(destination, task_path)
            except Exception:
                for trashed, original in reversed(moved):
                    if trashed.exists() and not original.exists():
                        trashed.replace(original)
                if task_path.read_bytes() != original_task_bytes:
                    task_path.write_bytes(original_task_bytes)
                shutil.rmtree(destination, ignore_errors=True)
                raise
            load_windows_task(task_path)
            return {
                "deleted": True,
                "kind": "human_guidance",
                "task_path": task_path.relative_to(self.project_root).as_posix(),
                "trash_path": destination.relative_to(self.project_root).as_posix(),
                "recoverable": True,
            }

    def delete_recording(self, raw_path: str) -> dict[str, Any]:
        with self._lock:
            self._require_idle()
            runs_root = (self.project_root / "runs").resolve()
            trace_path = (self.project_root / raw_path).resolve()
            if trace_path.name == "events.jsonl":
                if trace_path.parent.parent != runs_root or not trace_path.is_file():
                    raise ValueError("请选择本项目的原始录制")
                metadata = json.loads(trace_path.with_name("trace2task.json").read_text(encoding="utf-8"))
                if metadata.get("source") != "opencua_native":
                    raise ValueError("不是 OpenCUA 原始录制")
            else:
                trace_path = self._resolve_recording_trace(raw_path)
            return self._move_to_trash(
                trace_path.parent,
                allowed_root=runs_root,
                trash_root=runs_root / ".trash" / "recordings",
                kind="recording",
            )

    def delete_trace_experience(self, identifier: str) -> dict[str, Any]:
        with self._lock:
            self._require_idle()
            root = (self.project_root / "trace-library").resolve()
            target = (root / identifier).resolve()
            if not identifier or target.parent != root or not (target / "header.json").is_file():
                raise ValueError("请选择有效的任务经验")
            return self._move_to_trash(target, allowed_root=root,
                trash_root=root / ".trash", kind="trace_experience")

    def delete_candidate(self, raw_path: str) -> dict[str, Any]:
        with self._lock:
            self._require_idle()
            candidate = self._resolve_candidate_manifest(raw_path).parent
            runs_root = (self.project_root / "runs").resolve()
            return self._move_to_trash(
                candidate,
                allowed_root=self.candidate_root,
                trash_root=runs_root / ".trash" / "candidates",
                kind="candidate",
            )

    def _move_to_trash(
        self,
        target: Path,
        *,
        allowed_root: Path,
        trash_root: Path,
        kind: str,
    ) -> dict[str, Any]:
        resolved_target = target.resolve()
        resolved_root = allowed_root.resolve()
        resolved_trash = trash_root.resolve()
        if (
            resolved_target == resolved_root
            or not resolved_target.is_relative_to(resolved_root)
            or resolved_target.is_relative_to(resolved_trash)
            or not resolved_target.is_dir()
        ):
            raise ValueError("删除目标不在允许的本地资产目录中")
        return self.trash.move(resolved_target, kind)

    def open_local(self, raw_path: str) -> dict[str, Any]:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("缺少本地路径")
        candidate = (self.project_root / raw_path).resolve()
        allowed_roots = ((self.project_root / "runs").resolve(), self.task_root,
                         (self.project_root / "trace-library").resolve())
        if not any(candidate.is_relative_to(root) for root in allowed_roots):
            raise ValueError("只能查看项目中的 runs 或 taskpacks 路径")
        if not candidate.exists():
            raise FileNotFoundError(f"本地路径不存在: {raw_path}")
        target = candidate if candidate.is_dir() else candidate.parent
        if os.name != "nt":
            raise RuntimeError("在本地查看目前仅支持 Windows")
        os.startfile(target)  # type: ignore[attr-defined]
        return {"opened": target.relative_to(self.project_root).as_posix()}

    def resolve_local_image(self, raw_path: str) -> Path:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("缺少本地图片路径")
        candidate = (self.project_root / raw_path).resolve()
        allowed_roots = (self.task_root, (self.project_root / "runs").resolve())
        if not any(candidate.is_relative_to(root) for root in allowed_roots):
            raise ValueError("只能读取项目 taskpacks 或 runs 中的图片")
        if candidate.suffix.casefold() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise ValueError("本地资源不是支持的图片")
        if not candidate.is_file():
            raise FileNotFoundError(f"本地图片不存在: {raw_path}")
        return candidate

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"Unknown job: {job_id}")
            return job.snapshot()

    def active_job(self) -> dict[str, Any] | None:
        with self._lock:
            if self._active_job_id is None:
                return None
            return self._jobs[self._active_job_id].snapshot()

    def workbench_runs(self):
        with self._lock:
            return {"runs": self.run_store.list(), "active_job_id": self._active_job_id}

    def stop_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"Unknown job: {job_id}")
            if job.status == "awaiting_narration":
                payload = dict(job.result or {})
                narration = (
                    payload.get("narration")
                    if isinstance(payload.get("narration"), dict)
                    else {}
                )
                audio_discarded = False
                audio_path = narration.get("audio_path")
                trace_path = payload.get("trace_path")
                if isinstance(audio_path, str) and isinstance(trace_path, str):
                    audio_candidate = Path(audio_path).expanduser().resolve()
                    trace_candidate = Path(trace_path).expanduser().resolve()
                    if (
                        audio_candidate.parent == trace_candidate.parent
                        and audio_candidate.name.startswith("narration.")
                    ):
                        audio_candidate.unlink(missing_ok=True)
                        audio_discarded = True
                payload["narration"] = {
                    "status": "discarded",
                    "audio_discarded": audio_discarded,
                }
                job.result = payload
                job.stop_requested = True
                job.status = "stopped"
                job.updated_at = _now()
                job.logs.append(
                    "已放弃本次讲解和编译；保留原始 Trace，临时录音已删除。"
                )
                self.run_store.save(job.snapshot(details=False), log=job.logs[-1])
                return job.snapshot()
            if job.status not in {
                "queued",
                "running",
                "stopping",
                "awaiting_recording_start",
            }:
                return job.snapshot()
            job.stop_requested = True
            job.status = "stopping"
            job.updated_at = _now()
            job.logs.append("网页控制台已请求停止；正在等待当前安全边界。")
            job.stop_event.set()
            self.run_store.save(job.snapshot(details=False), log=job.logs[-1])
            process = self._waa_processes.get(job_id)
            if process is not None and process.stdin is not None and process.poll() is None:
                try:
                    process.stdin.write("STOP\n")
                    process.stdin.flush()
                except OSError:
                    pass
            return job.snapshot()

    def wait(self, job_id: str, timeout: float = 10) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snapshot = self.get_job(job_id)
            if snapshot["status"] not in {
                "queued",
                "running",
                "stopping",
                "awaiting_recording_start",
            }:
                return snapshot
            time.sleep(0.01)
        raise TimeoutError(f"Job {job_id} did not finish within {timeout:g}s")

    def _resolve_task_path(self, raw_path: str) -> Path:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("请选择一个示范任务")
        candidate = (self.project_root / raw_path).resolve()
        if (not candidate.is_relative_to(self.task_root) or candidate.name != "task.yaml"
                or ".trash" in candidate.relative_to(self.task_root).parts
                or candidate.with_name(".trace2task-deleted.json").is_file()):
            raise ValueError("任务路径必须指向项目 taskpacks 目录中的 task.yaml")
        if not candidate.is_file():
            raise FileNotFoundError(f"任务包不存在: {raw_path}")
        return candidate

    def _resolve_execution_experience(self, raw_path: str) -> Path:
        candidate = (self.project_root / raw_path).resolve()
        if (candidate.name == "model-input.txt"
                and candidate.parent.parent == self.project_root / "trace-library"
                and candidate.parent.name != ".trash" and candidate.is_file()
                and not candidate.with_name(".trace2task-deleted.json").is_file()):
            return candidate
        return self._resolve_task_path(raw_path)

    def _resolve_recording_trace(self, raw_path: str) -> Path:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("请选择一个原始录制")
        runs_root = (self.project_root / "runs").resolve()
        candidate = (self.project_root / raw_path).resolve()
        if (not candidate.is_relative_to(runs_root) or candidate.name != "trace.jsonl"
                or ".trash" in candidate.relative_to(runs_root).parts
                or candidate.with_name(".trace2task-deleted.json").is_file()):
            raise ValueError("录制路径必须指向项目 runs 目录中的 trace.jsonl")
        if not candidate.is_file():
            raise FileNotFoundError(f"原始录制不存在: {raw_path}")
        return candidate

    def _resolve_candidate_manifest(self, raw_path: str) -> Path:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("缺少候选经验路径")
        candidate_dir = (self.project_root / raw_path).resolve()
        candidate_path = candidate_dir / "candidate.yaml"
        if (
            not candidate_dir.is_relative_to(self.candidate_root)
            or candidate_dir == self.candidate_root
            or not candidate_path.is_file()
            or ".trash" in candidate_dir.relative_to(self.candidate_root).parts
            or (candidate_dir / ".trace2task-deleted.json").is_file()
        ):
            raise ValueError("候选经验路径必须指向 runs/candidates 中的有效目录")
        return candidate_path

    def _require_idle(self) -> None:
        if self._active_job_id is None:
            return
        active = self._jobs[self._active_job_id]
        if active.status in {
            "queued",
            "running",
            "stopping",
            "awaiting_recording_start",
            "awaiting_narration",
        }:
            raise RuntimeError("已有任务正在运行，请先等待或停止它")

    def _run_opencua_recording(self, job: ConsoleJob) -> None:
        from trace2task.opencua_recording import record_opencua
        self._update(job, status="running", log="OpenCUA 原生录制准备中。视频和结构信息仅在本地保存。")
        try:
            result = record_opencua(
                task_id=job.task_id, output_root=self.project_root / "runs",
                stop_event=job.stop_event,
                status_callback=lambda message: self._update(job, log=message),
            )
            self._update(job, status="completed" if result["success"] else "stopped", result=result,
                         log="OpenCUA 录制已归档，可在原始录制中查看；本轮尚未启用关键帧编译。")
        except Exception as error:  # noqa: BLE001 -- expose background job failure to the UI
            self._update(job, status="failed", error=f"{type(error).__name__}: {error}",
                         log=f"OpenCUA 录制失败：{error}")

    def _run_recording(
        self,
        job: ConsoleJob,
        handle: int,
        selected_window: dict[str, Any],
    ) -> None:
        self._update(
            job,
            status="running",
            log="录制已启动。请在目标窗口完成示范，按 F8 标记成功，F9 取消。",
        )
        process_name = str(selected_window.get("process_name") or "")
        capability_profile = (
            "messaging"
            if process_name.casefold() in {"weixin.exe", "wechat.exe"}
            else None
        )
        try:
            desktop = job.execution_scope == "desktop"
            if desktop:
                self._update(job, log="即将录制整个主屏，请关闭敏感窗口。3 秒后开始；F8 完成，F9 取消。")
                if job.stop_event.wait(3):
                    self._update(job, status="stopped", log="桌面录制已取消。")
                    return
            result = record_window_trace(
                WindowSelector(process_name="trace2task.desktop") if desktop else WindowSelector(handle=handle),
                task_id=job.task_id,
                output_root=self.project_root / "runs",
                backend=Win32Backend(),
                capture=GdiWindowCapture(desktop=True) if desktop else GdiWindowCapture(),
                monitor=ConsoleRecordingMonitor(job.stop_event),
                status_callback=lambda message: self._update(job, log=message),
                capability_profile=capability_profile,
                execution_scope=job.execution_scope,
            )
            payload = asdict(result)
            if result.success:
                if job.narrated:
                    payload["narration"] = {"status": "awaiting_review"}
                    self._update(
                        job,
                        status="awaiting_narration",
                        result=payload,
                        log=(
                            "示范录制成功。请回到网页检查讲解转写；"
                            + (
                                "确认后将保存录制，稍后可手动编译。"
                                if job.defer_compilation
                                else "确认后才会启动 Compiler Agent。"
                            )
                        ),
                    )
                    return
                if job.defer_compilation:
                    self._finish_deferred_recording(job, payload)
                    return
                self._update(
                    job,
                    log="示范录制成功，正在编译动作证据并由 Compiler Agent 理解阶段。",
                )
                self._run_recording_compilation(job, Path(result.trace_path), payload)
            else:
                self._update(job, status="stopped", result=payload, log="录制未标记成功。")
        except Exception as error:  # noqa: BLE001 - recording failures must become UI state
            self._update(
                job,
                status="failed",
                error=f"{type(error).__name__}: {error}",
                log=f"录制失败：{type(error).__name__}: {error}",
            )

    def _run_waa_recording(
        self,
        job: ConsoleJob,
        client_root: Path,
        relative_example: str,
        reset_spec: Path,
        distro: str,
        container: str,
    ) -> None:
        self._update(
            job,
            status="running",
            log="正在连接 WAA 虚拟机并准备初始任务画面。",
        )
        session_id = str((job.result or {}).get("session_id") or "")
        source_dir = client_root / "trace2task_recordings" / session_id
        bundled_recorder = (
            self.project_root
            / "integrations"
            / "windows_agent_arena"
            / "client"
            / "trace2task_human_trace.py"
        )
        installed_recorder = client_root / "trace2task_human_trace.py"
        bundled_reset_helper = (
            self.project_root
            / "integrations"
            / "windows_agent_arena"
            / "client"
            / "trace2task_reset.py"
        )
        installed_reset_helper = client_root / "trace2task_reset.py"
        session_reset_dir = client_root / "trace2task_reset_specs"
        installed_reset_spec = session_reset_dir / f"{session_id}.json"
        process: subprocess.Popen[str] | None = None
        completed_result: dict[str, Any] | None = None
        try:
            if bundled_recorder.is_file() and (
                not installed_recorder.is_file()
                or installed_recorder.read_bytes() != bundled_recorder.read_bytes()
            ):
                shutil.copy2(bundled_recorder, installed_recorder)
                self._update(job, log="已同步最新 WAA Trace 录制器。")
            if not bundled_reset_helper.is_file():
                raise FileNotFoundError("Trace2Task WAA reset helper 不存在")
            if (
                not installed_reset_helper.is_file()
                or installed_reset_helper.read_bytes() != bundled_reset_helper.read_bytes()
            ):
                shutil.copy2(bundled_reset_helper, installed_reset_helper)
            session_reset_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(reset_spec, installed_reset_spec)
            command = [
                "wsl",
                "-d",
                distro,
                "--",
                "docker",
                "exec",
                "-i",
                "-e",
                (
                    "TRACE2TASK_WAA_RESET_SPEC="
                    f"/client/trace2task_reset_specs/{installed_reset_spec.name}"
                ),
                container,
                "python",
                "-u",
                "/client/trace2task_human_trace.py",
                "--example",
                f"/client/{relative_example}",
                "--task-id",
                job.task_id,
                "--output",
                "/client/trace2task_recordings",
                "--session-id",
                session_id,
                "--wait-for-go",
                "--require-reset-verification",
            ]
            if job.narrated:
                command.append("--no-task-narration")
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            with self._lock:
                self._waa_processes[job.job_id] = process
            if process.stdout is None:
                raise RuntimeError("WAA 录制进程没有标准输出")
            for raw_line in process.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                if not line.startswith(WAA_CONTROL_EVENT_PREFIX):
                    self._update(job, log=line)
                    continue
                event = json.loads(line.removeprefix(WAA_CONTROL_EVENT_PREFIX))
                if not isinstance(event, dict):
                    raise TypeError("WAA 录制器事件必须是 JSON 对象")
                event_type = event.get("type")
                if event_type == "ready":
                    reset_receipt = event.get("reset_receipt")
                    if not isinstance(reset_receipt, dict) or reset_receipt.get(
                        "status"
                    ) != "verified":
                        raise RuntimeError("WAA 任务初始状态没有通过 reset 验证")
                    payload = dict(job.result or {})
                    payload.update(
                        {
                            "capture_status": "ready",
                            "ready_at": event.get("ready_at"),
                            "waa_task_id": event.get("waa_task_id"),
                            "reset_receipt": reset_receipt,
                        }
                    )
                    self._update(
                        job,
                        status="awaiting_recording_start",
                        result=payload,
                        log=(
                            "WAA 任务状态已清理并验证；网页启动麦克风后会自动发送 GO。"
                        ),
                    )
                elif event_type == "started":
                    trace_started_at = str(event.get("trace_started_at") or "")
                    trace_epoch_ms = datetime.fromisoformat(trace_started_at).timestamp() * 1000
                    payload = dict(job.result or {})
                    audio_epoch_ms = payload.get("audio_started_at_epoch_ms")
                    audio_offset = (
                        round(float(audio_epoch_ms) - trace_epoch_ms, 3)
                        if isinstance(audio_epoch_ms, (int, float))
                        and not isinstance(audio_epoch_ms, bool)
                        else None
                    )
                    payload.update(
                        {
                            "capture_status": "recording",
                            "trace_started_at": trace_started_at,
                            "audio_start_trace_elapsed_ms": audio_offset,
                        }
                    )
                    self._update(
                        job,
                        status="running",
                        result=payload,
                        log="WAA Trace 与讲解正在同一时间轴录制；按 F8 验证成功，F9 取消。",
                    )
                elif event_type == "completed":
                    raw_result = event.get("result")
                    if isinstance(raw_result, dict):
                        completed_result = dict(raw_result)
            return_code = process.wait()
            if return_code != 0:
                raise RuntimeError(f"WAA 录制器退出码为 {return_code}")
            if completed_result is None:
                raise RuntimeError("WAA 录制器没有返回完成结果")
            if not source_dir.is_dir():
                raise FileNotFoundError(f"WAA 录制目录不存在：{source_dir}")
            destination = self.project_root / "runs" / session_id
            if destination.exists():
                raise FileExistsError(f"录制目标目录已存在：{destination}")
            shutil.copytree(source_dir, destination)
            trace_path = destination / "trace.jsonl"
            metadata_path = destination / "metadata.json"
            reset_receipt_path = destination / "reset-receipt.json"
            if (
                not trace_path.is_file()
                or not metadata_path.is_file()
                or not reset_receipt_path.is_file()
            ):
                raise RuntimeError(
                    "WAA 录制结果缺少 trace.jsonl、metadata.json 或 reset-receipt.json"
                )
            payload = dict(job.result or {})
            audio_offset = payload.get("audio_start_trace_elapsed_ms")
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["narration_alignment"] = {
                "method": "web_ready_go_v1",
                "audio_start_trace_elapsed_ms": audio_offset,
            }
            metadata_path.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            completed_result["trace_path"] = str(trace_path)
            payload.update(completed_result)
            payload["capture_status"] = "finished"
            payload["audio_start_trace_elapsed_ms"] = audio_offset
            payload["reset_receipt_path"] = str(reset_receipt_path)
            if completed_result.get("success") is True:
                if job.narrated:
                    payload["narration"] = {
                        "status": "awaiting_review",
                        "audio_start_trace_elapsed_ms": audio_offset,
                    }
                    self._update(
                        job,
                        status="awaiting_narration",
                        result=payload,
                        log="WAA 示范成功；正在停止麦克风并准备 Turbo 转写。",
                    )
                    return
                if job.defer_compilation:
                    self._finish_deferred_recording(job, payload)
                    return
                self._update(
                    job,
                    result=payload,
                    log="WAA 示范成功，正在生成 Compiler Agent 经验。",
                )
                self._run_recording_compilation(job, trace_path, payload)
                return
            self._update(
                job,
                status="stopped",
                result=payload,
                log="WAA 录制已取消，或 F8 验证未通过。",
            )
        except Exception as error:  # noqa: BLE001 - WAA failures must become UI state
            self._update(
                job,
                status="failed",
                error=f"{type(error).__name__}: {error}",
                log=f"WAA 录制失败：{type(error).__name__}: {error}",
            )
        finally:
            with self._lock:
                self._waa_processes.pop(job.job_id, None)
            installed_reset_spec.unlink(missing_ok=True)
            if process is not None and process.poll() is None:
                process.terminate()

    def _finish_deferred_recording(
        self,
        job: ConsoleJob,
        payload: dict[str, Any],
    ) -> None:
        payload["compilation"] = {"status": "deferred"}
        self._update(
            job,
            status="completed",
            result=payload,
            log=(
                "录制已完整保存，本次未启动 Compiler Agent；"
                "可在“本地经验”的原始录制列表中稍后编译。"
            ),
        )

    def _run_recording_compilation(
        self,
        job: ConsoleJob,
        trace_path: Path,
        payload: dict[str, Any],
    ) -> None:
        self._update(
            job,
            status="running",
            log="正在编译动作证据并由 Compiler Agent 理解任务说明、阶段与完成条件。",
        )
        try:
            if job.kind == "waa_recording" and job.narrated:
                compilation = self._compile_waa_narration_pair(
                    trace_path,
                    model=job.model,
                    reasoning_effort=job.reasoning_effort,
                    status_callback=lambda message: self._update(job, log=message),
                )
                compilation_status = compilation["status"]
            else:
                compilation = self._compile_trace_bundle(
                    trace_path,
                    model=job.model,
                    reasoning_effort=job.reasoning_effort,
                    status_callback=lambda message: self._update(job, log=message),
                )
                compilation_status = (
                    "completed"
                    if compilation["semantic_compilation"]["status"] == "completed"
                    else "partial"
                )
        except Exception as error:  # noqa: BLE001 - preserve successful recording
            failure_category, retryable = _compiler_failure(
                error,
                fallback="compiler_error",
            )
            payload["compilation"] = {
                "status": "failed",
                "error": f"{type(error).__name__}: {error}",
                "failure_category": failure_category,
                "retryable": retryable,
            }
            self._update(
                job,
                status="partial",
                result=payload,
                log=(
                    f"录制已完整保存，但{_compiler_failure_summary(failure_category)}。"
                    "可直接从此录制重试，无需重新录制。"
                    if retryable
                    else "录制已保存，但自动编译失败："
                    f"{type(error).__name__}: {error}"
                ),
            )
            return
        payload["compilation"] = {
            "status": compilation_status,
            "result": compilation,
        }
        self._update(
            job,
            status=payload["compilation"]["status"],
            result=payload,
            log=(
                "语义经验草稿已生成，请审查任务说明、阶段截图与循环完成条件。"
                if payload["compilation"]["status"] == "completed"
                else (
                    "仅部分 Compiler 变体成功；失败变体可直接从此录制重试。"
                    if payload["compilation"]["status"] == "partial"
                    else "Codex Compiler 当前不可用，两个变体已快速停止；"
                    "网络恢复后可直接从此录制重试。"
                )
            ),
        )

    def _run_compilation(self, job: ConsoleJob, trace_path: Path) -> None:
        self._update(job, status="running", log="编译任务已启动。")
        try:
            metadata = json.loads(
                trace_path.with_name("metadata.json").read_text(encoding="utf-8")
            )
            is_narrated_waa_recording = bool(metadata.get("waa_task_id")) and trace_path.with_name(
                "narration.json"
            ).is_file()
            if is_narrated_waa_recording:
                self._update(
                    job,
                    log="检测到 WAA 同步讲解录制，将重试纯 Trace 与人工讲解两个变体。",
                )
                payload = self._compile_waa_narration_pair(
                    trace_path,
                    model=job.model,
                    reasoning_effort=job.reasoning_effort,
                    status_callback=lambda message: self._update(job, log=message),
                )
                compilation_status = str(payload["status"])
            else:
                payload = self._compile_trace_bundle(
                    trace_path,
                    model=job.model,
                    reasoning_effort=job.reasoning_effort,
                    status_callback=lambda message: self._update(job, log=message),
                )
                compilation_status = (
                    "completed"
                    if payload["semantic_compilation"]["status"] == "completed"
                    else "partial"
                )
            self._update(
                job,
                status=compilation_status,
                result=payload,
                log=(
                    "语义经验草稿已生成，请展开阶段并审核确认。"
                    if compilation_status == "completed"
                    else (
                        "仅部分 Compiler 变体成功；可从原始录制再次重试。"
                        if compilation_status == "partial"
                        else "Codex Compiler 当前不可用；录制已保留，可稍后再次重试。"
                    )
                ),
            )
        except Exception as error:  # noqa: BLE001 - compilation must become UI state
            failure_category, retryable = _compiler_failure(
                error,
                fallback="compiler_error",
            )
            self._update(
                job,
                status="failed",
                error=f"{type(error).__name__}: {error}",
                result={
                    "source_trace": str(trace_path),
                    "failure_category": failure_category,
                    "retryable": retryable,
                },
                log=(
                    f"{_compiler_failure_summary(failure_category)}；"
                    "可从原始录制重试，无需重新录制。"
                    if retryable
                    else f"编译失败：{type(error).__name__}: {error}"
                ),
            )

    def _run_revision(
        self,
        job: ConsoleJob,
        candidate_path: Path,
        task_path: Path,
    ) -> None:
        self._update(job, status="running", log="正在提取本次运行的动作、理由和代表帧。")
        try:
            contract = load_windows_task(task_path)
            experience = contract.semantic_experience
            if experience is None:
                raise ValueError("这份任务还没有语义经验")
            result = compile_guidance_revision(
                self.project_root,
                candidate_path,
                task_path,
                experience=experience,
                reference_frame=contract.reference_frame,
                feedback=job.instruction,
                model=job.model,
                reasoning_effort=job.reasoning_effort,
            )
            self._update(
                job,
                status="completed",
                result=_jsonable(result),
                log="经验修订草稿已生成；确认前不会影响正在使用的经验。",
            )
        except Exception as error:  # noqa: BLE001 - revision failures must become UI state
            self._update(
                job,
                status="failed",
                error=f"{type(error).__name__}: {error}",
                log=f"修订失败：{type(error).__name__}: {error}",
            )

    def _run_task_model_revision(
        self,
        job: ConsoleJob,
        candidate_path: Path,
        task_path: Path,
    ) -> None:
        self._update(
            job,
            status="running",
            log="正在对照原始 Trace 片段、当前状态图、运行日志与人工结构反馈。",
        )
        try:
            contract = load_windows_task(task_path)
            experience = contract.semantic_experience
            if experience is None:
                raise ValueError("这份任务还没有语义经验")
            result = compile_task_model_revision(
                self.project_root,
                candidate_path,
                task_path,
                experience=experience,
                reference_frame=contract.reference_frame,
                feedback=job.instruction,
                model=job.model,
                reasoning_effort=job.reasoning_effort,
            )
            message = (
                "任务状态图草稿已生成，但存在 Guidance 映射冲突，暂不能确认。"
                if result.blocking_issue_count
                else "任务状态图草稿与结构差异已生成；确认前不会影响当前任务。"
            )
            self._update(
                job,
                status="completed",
                result=_jsonable(result),
                log=message,
            )
        except Exception as error:  # noqa: BLE001 - expose revision failure in UI
            self._update(
                job,
                status="failed",
                error=f"{type(error).__name__}: {error}",
                log=f"任务结构修订失败：{type(error).__name__}: {error}",
            )

    def _run_job(
        self, job: ConsoleJob, task_path: Path | None, execute: bool,
        api_config: ModelAPIConfig | None = None,
    ) -> None:
        desktop = job.execution_scope == "desktop"
        self._update(job, status="running", log=(
            f"任务已启动 · {'指定窗口 / 应用' if job.executor_backend == 'cua' and (job.cua_target or {}).get('kind') != 'desktop' else '全桌面' if desktop else '单窗口'} · "
            f"{'使用经验' if job.use_experience else '无经验 Baseline'}。"
        ))
        try:
            kwargs: dict[str, Any] = {
                "instruction": job.instruction,
                "execute": execute,
                "model": job.model,
                "reasoning_effort": job.reasoning_effort,
                "output_root": self.project_root / "runs",
                "status_callback": lambda message: self._update(job, log=message),
            }
            if not desktop:
                kwargs.update(background=job.background, adaptive_reasoning=job.adaptive_reasoning,
                              use_experience=job.use_experience,
                              focus=not execute and not job.background)
            if api_config is not None:
                kwargs["api_config"] = api_config
            if desktop or self.runner is run_windows_agent:
                kwargs["emergency_stop"] = ConsoleEmergencyStop(job.stop_event)
            run_failure: Exception | None = None
            try:
                if job.provider == "trained_d":
                    from trace2task.desktop_runner import build_desktop_experience_context
                    from trace2task.trained_model_runner import run_trained_desktop

                    if not execute:
                        raise ValueError("D 模型预览请使用预测入口")

                    def approve(batch):
                        token = uuid.uuid4().hex
                        job.approval_event.clear()
                        with self._lock:
                            job.pending_batch = {**batch, "token": token}
                        self._update(job, log="等待本批动作确认；可在页面确认或按 F9 停止。")
                        while not job.approval_event.wait(.1):
                            kwargs["emergency_stop"].raise_if_requested()
                        kwargs["emergency_stop"].raise_if_requested()

                    result = run_trained_desktop(
                        instruction=job.instruction, output_root=self.project_root / "runs",
                        emergency_stop=kwargs["emergency_stop"], status_callback=kwargs["status_callback"],
                        approve=approve, continuous=job.continuous, model=job.model,
                        executor_backend=job.executor_backend,
                        inference_backend=job.run_facts.get("inference_backend"),
                        cua_target=job.cua_target,
                        experience_context=(build_desktop_experience_context(task_path, execute=True)
                                             if job.use_experience else None),
                        prompt_profile=job.prompt_profile,
                        on_model_round=lambda entry: self._model_round_update(job, entry),
                    )
                    job.pending_batch = None
                elif (desktop and execute and job.orchestration == "legacy"
                      and (not job.use_experience or job.executor_backend == "cua"
                           or (task_path is not None and task_path.name == "model-input.txt"))):
                    from trace2task.chat_agent_runner import run_chat_agent

                    result = run_chat_agent(
                        instruction=job.instruction, model=job.model,
                        reasoning_effort=job.reasoning_effort,
                        output_root=self.project_root / "runs",
                        emergency_stop=kwargs["emergency_stop"],
                        status_callback=kwargs["status_callback"],
                        executor_backend=job.executor_backend,
                        cua_target=job.cua_target, api_config=api_config,
                        task_path=task_path, use_experience=job.use_experience,
                        prompt_guidance=job.prompt_guidance,
                        on_model_round=lambda entry: self._model_round_update(job, entry),
                    )
                elif desktop:
                    from trace2task.desktop_runner import run_desktop_baseline

                    kwargs.update(orchestration=job.orchestration, resume_from=job.resume_from or None)
                    result = run_desktop_baseline(task_path=task_path,
                                                  use_experience=job.use_experience, **kwargs)
                else:
                    result = self.runner(task_path, **kwargs)
            except WindowsAgentRunFailed as error:
                result = error.result
                run_failure = error.cause
            payload = _jsonable(result)
            if not isinstance(payload, dict):
                payload = {"value": payload}
            payload["execution_scope"] = job.execution_scope
            payload["operation_scope"] = job.snapshot(details=False)["operation_scope"]
            payload["run_facts"] = job.run_facts
            payload["use_experience"] = job.use_experience
            payload["executor_backend"] = job.executor_backend
            if (job.provider in {"codex", "api"} and not payload.get("model_io")
                    and isinstance(payload.get("trace_path"), str)):
                from trace2task.model_io_projection import project_model_io

                run_dir = Path(payload["trace_path"]).resolve().parent
                if run_dir.is_relative_to((self.project_root / "runs").resolve()):
                    audit_path = run_dir / "io-audit" / "events.jsonl"
                    if audit_path.is_file():
                        payload["audit_path"] = str(audit_path)
                        try:
                            payload["model_io"] = project_model_io(run_dir)
                        except (OSError, ValueError, TypeError) as error:
                            self._update(job, log=f"对话式日志生成失败；原始 I/O 归档仍可查看：{error}")
                        else:
                            with self._lock:
                                job.model_io = payload["model_io"]
                                for entry in job.model_io:
                                    self.run_store.round(job.job_id, entry)
            if execute and not desktop:
                try:
                    candidate = self._save_candidate(job, payload)
                except Exception as error:  # noqa: BLE001 - preserve the completed job result
                    self._update(
                        job,
                        log=f"运行已结束，但反馈记录保存失败：{type(error).__name__}: {error}",
                    )
                else:
                    if candidate is not None:
                        payload["candidate_experience"] = candidate
                        outcome_label = (
                            "成功运行"
                            if payload.get("task_complete") is True
                            else "未完成运行"
                        )
                        self._update(
                            job,
                            log=f"本次{outcome_label}已保存为待反馈运行。",
                        )
            if run_failure is not None:
                error_text = f"{type(run_failure).__name__}: {run_failure}"
                self._update(
                    job,
                    status="failed",
                    result=payload,
                    error=error_text,
                    log=f"运行失败：{error_text}",
                )
                return
            final_status = "stopped" if payload.get("stop_reason") == "emergency_stop" else "completed"
            if payload.get("stop_reason") in {
                "no_progress",
                "model_done_unverified",
                "effect_unverifiable",
                "capability_rejection_limit",
                "completion_unverifiable",
                "completion_rejected",
                "background_unavailable",
                "foreground_unavailable",
            }:
                final_status = "stopped"
            if payload.get("stop_reason") == "error" or (
                desktop and payload.get("stop_reason") == "action_limit"
            ):
                final_status = "failed"
            completion_note = ""
            if payload.get("stop_reason") == "background_unavailable":
                completion_note = "驱动拒绝后台输入；前台重试未能确认交付，已停止。"
            elif payload.get("stop_reason") == "foreground_unavailable":
                completion_note = "驱动拒绝后台输入，Windows 又不允许安全切到目标前台；未发送全局输入，任务已停止。"
            elif payload.get("stop_reason") == "completion_unverifiable":
                completion_note = "完成证据不足；已停止等待人工检查，不自动重试。"
            elif payload.get("stop_reason") == "completion_rejected":
                completion_note = "完成声明连续未通过核验；任务未完成。"
            elif payload.get("stop_reason") == "visual_completion_reviewed":
                completion_note = "已通过本地模型只读视觉核验；尚未经独立应用验证。"
            elif payload.get("stop_reason") == "no_progress":
                completion_note = "重复操作无进展，纠正后仍未恢复；已停止。"
            elif payload.get("stop_reason") == "effect_unverifiable":
                completion_note = "动作已送达，但效果无法确认；任务已停止。"
            elif payload.get("stop_reason") == "capability_rejection_limit":
                completion_note = "连续三次操作能力预检未通过；这些动作未执行，任务已停止。"
            elif desktop:
                completion_note = "模型完成声明未经独立验证。"
            self._update(
                job,
                status=final_status,
                result=payload,
                error=payload.get("error"),
                log="任务运行结束。" + completion_note,
            )
        except Exception as error:  # noqa: BLE001 - job failures must become UI state
            self._update(
                job,
                status="failed",
                error=f"{type(error).__name__}: {error}",
                log=f"运行失败：{type(error).__name__}: {error}",
            )

    def _save_candidate(
        self,
        job: ConsoleJob,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        raw_trace_path = payload.get("trace_path")
        if not isinstance(raw_trace_path, str) or not raw_trace_path.strip():
            return None
        trace_path = Path(raw_trace_path)
        if not trace_path.is_absolute():
            trace_path = self.project_root / trace_path
        trace_path = trace_path.resolve()
        runs_root = (self.project_root / "runs").resolve()
        if not trace_path.is_relative_to(runs_root) or not trace_path.is_file():
            return None

        created_at = _now()
        directory_name = (
            f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}-{job.job_id[:8]}"
        )
        candidate_dir = self.candidate_root / directory_name
        candidate_dir.mkdir(parents=True, exist_ok=False)
        candidate_id = f"candidate-{job.job_id[:12]}"
        outcome = {
            "task_complete": payload.get("task_complete") is True,
            "stop_reason": payload.get("stop_reason"),
        }
        verification_outcome = payload.get("verification_outcome")
        if isinstance(verification_outcome, str) and verification_outcome.strip():
            outcome["verification_outcome"] = verification_outcome.strip()
            outcome["verified"] = payload.get("verified") is True
        verification_receipt_path = payload.get("verification_receipt_path")
        if isinstance(verification_receipt_path, str) and verification_receipt_path.strip():
            outcome["verification_receipt_path"] = verification_receipt_path.strip()
        failure_message = payload.get("failure_message")
        if isinstance(failure_message, str) and failure_message.strip():
            outcome["failure_message"] = failure_message.strip()
        manifest = {
            "schema_version": "0.1",
            "candidate_id": candidate_id,
            "status": "pending_review",
            "created_at": created_at,
            "source_task": job.task_path,
            "task_id": job.task_id,
            "runtime_instruction": job.instruction,
            "execution_trace": trace_path.relative_to(self.project_root).as_posix(),
            "model": job.model,
            "reasoning_effort": job.reasoning_effort,
            "provider": job.provider,
            "input_mode": "background" if job.background else "foreground",
            "adaptive_reasoning": job.adaptive_reasoning,
            "use_experience": job.use_experience,
            "outcome": outcome,
            "selection": {
                "mode": job.selection_mode,
                "confidence": job.selection_confidence,
                "reason": job.selection_reason,
            },
            "metrics": {
                "executed_actions": payload.get("executed_actions", 0),
                "replans": payload.get("replans", 0),
                "planning_ms": payload.get("planning_ms", 0),
                "batch_count": payload.get("batch_count", 0),
                "planned_actions": payload.get("planned_actions", 0),
                "interrupted_batches": payload.get("interrupted_batches", 0),
                "average_batch_size": payload.get("average_batch_size", 0),
                "max_batch_size": payload.get("max_batch_size", 0),
                "visual_checkpoints": payload.get("visual_checkpoints", 0),
                "visual_checkpoint_failures": payload.get(
                    "visual_checkpoint_failures", 0
                ),
                "visual_stability_wait_ms": payload.get("visual_stability_wait_ms", 0),
                "local_wait_until_count": payload.get("local_wait_until_count", 0),
                "local_wait_until_ms": payload.get("local_wait_until_ms", 0),
                "wait_only_plans": payload.get("wait_only_plans", 0),
                "short_batch_count": payload.get("short_batch_count", 0),
                "session_resets": payload.get("session_resets", 0),
                "performance": payload.get("performance") or {},
                "stage_timings": payload.get("stage_timings") or [],
            },
        }
        manifest_path = candidate_dir / "candidate.yaml"
        manifest_path.write_text(
            yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True, width=100),
            encoding="utf-8",
        )
        return {
            "candidate_id": candidate_id,
            "status": "pending_review",
            "local_path": candidate_dir.relative_to(self.project_root).as_posix(),
        }

    def _update(
        self,
        job: ConsoleJob,
        *,
        status: str | None = None,
        log: str | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            if status is not None:
                job.status = status
            if log:
                job.logs.append(str(log))
                job.logs[:] = job.logs[-MAX_LOG_ENTRIES:]
            if result is not None:
                job.result = result
            if error is not None:
                job.error = error
            job.updated_at = _now()

            self.run_store.save(job.snapshot(details=False), log=log)

    def _model_round_update(self, job: ConsoleJob, entry: dict[str, Any]) -> None:
        with self._lock:
            index = entry["step_index"]
            item = deepcopy(entry)
            if index == len(job.model_io):
                job.model_io.append(item)
            elif 0 <= index < len(job.model_io):
                job.model_io[index] = item
            job.updated_at = _now()
            configuration = (entry.get("input") or {}).get("configuration") or {}
            if configuration.get("backend"):
                job.observed_inference = {"backend": configuration["backend"],
                    "context_policy": (entry.get("context_management") or {}).get("policy"),
                    "source": "model_response", "step_index": index}
            elif job.provider in {"codex", "api"} and entry.get("status") not in {"pending", "error", "cancelled"}:
                job.observed_inference = {"backend": job.run_facts.get("inference_backend"),
                    "source": "transport_receipt", "step_index": index}
            self.run_store.round(job.job_id, item)
            self.run_store.save(job.snapshot(details=False))


class WebConsoleHandler(BaseHTTPRequestHandler):
    controller: WebConsoleController
    asset_root: Path
    csrf_token: str

    def do_GET(self) -> None:
        try:
            self._require_loopback_host()
        except ValueError as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
            return
        parsed = urlparse(self.path)
        if parsed.path == "/api/runtime-catalog":
            from trace2task.run_session import runtime_catalog
            self._json(runtime_catalog(self.controller.project_root))
            return
        if parsed.path == "/api/workbench/runs":
            self._json(self.controller.workbench_runs())
            return
        if parsed.path.startswith("/api/workbench/runs/"):
            parts = parsed.path.removeprefix("/api/workbench/runs/").split("/")
            try:
                if len(parts) == 3 and parts[1] == "rounds":
                    self._json(self.controller.run_store.read_round(parts[0], int(parts[2])))
                elif len(parts) == 1:
                    after = int((parse_qs(parsed.query).get("after") or ["0"])[0])
                    self._json(self.controller.run_store.get(parts[0], after))
                else:
                    raise KeyError("Unknown run resource")
            except KeyError as error:
                self._error(HTTPStatus.NOT_FOUND, str(error))
            except ValueError as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
            return
        if parsed.path == "/api/rsi/health":
            self._json(self.controller.rsi_health())
            return
        if parsed.path == "/api/rsi/runs":
            try:
                values = parse_qs(parsed.query)
                raw_limit = (values.get("limit") or ["50"])[0]
                self._json(self.controller.list_rsi_runs(int(raw_limit)))
            except (TypeError, ValueError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
            except RuntimeError as error:
                self._error(HTTPStatus.CONFLICT, str(error))
            return
        if parsed.path == "/api/rsi/get":
            try:
                run_id = (parse_qs(parsed.query).get("run_id") or [""])[0]
                self._json(self.controller.get_rsi_run(run_id))
            except (TypeError, ValueError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
            except RuntimeError as error:
                self._error(HTTPStatus.CONFLICT, str(error))
            return
        if parsed.path == "/api/rsi/events":
            try:
                values = parse_qs(parsed.query)
                self._json(self.controller.rsi_events(
                    (values.get("run_id") or [""])[0], int((values.get("after") or ["0"])[0])
                ))
            except (TypeError, ValueError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
            except RuntimeError as error:
                self._error(HTTPStatus.CONFLICT, str(error))
            return
        if parsed.path == "/api/rsi/recovery":
            try:
                run_id = (parse_qs(parsed.query).get("run_id") or [""])[0]
                self._json(self.controller.rsi_recovery(run_id))
            except (TypeError, ValueError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
            except RuntimeError as error:
                self._error(HTTPStatus.CONFLICT, str(error))
            return
        if parsed.path == "/api/rsi/candidate":
            try:
                values = parse_qs(parsed.query)
                self._json(self.controller.get_rsi_candidate(
                    (values.get("run_id") or [""])[0], (values.get("digest") or [""])[0]
                ))
            except (TypeError, ValueError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
            except RuntimeError as error:
                self._error(HTTPStatus.CONFLICT, str(error))
            return
        if parsed.path == "/api/desktop-checkpoints":
            runs = self.controller.project_root / "runs"
            items = []
            for directory in sorted(runs.glob("*-desktop-*"), reverse=True):
                if not directory.resolve().is_relative_to(runs.resolve()):
                    continue
                try:
                    state = json.loads((directory / "task-state.json").read_text(encoding="utf-8"))
                    if state.get("status") == "complete" or not (directory / "checkpoints.sqlite").is_file():
                        continue
                    items.append({"path": directory.relative_to(self.controller.project_root).as_posix(),
                                  "name": directory.name, "instruction": state.get("instruction", ""),
                                  "uncertain": bool(state.get("pending_action"))})
                except (OSError, ValueError):
                    continue
                if len(items) >= 30:
                    break
            self._json({"runs": items})
            return
        if parsed.path == "/api/components":
            self._json(self.controller.components.status())
            return
        if parsed.path == "/api/local-prompts":
            values = parse_qs(parsed.query)
            try:
                self._json(self.controller.local_prompt_profile(
                    (values.get("model") or [""])[0], (values.get("backend") or [""])[0]))
            except (ValueError, OSError, json.JSONDecodeError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
            return
        if parsed.path == "/api/chat-prompts":
            values = parse_qs(parsed.query)
            self._json(self.controller.chat_prompt_profile((values.get("provider") or [""])[0]))
            return
        if parsed.path == "/api/state":
            self._json(
                {
                    "version": __version__,
                    "capabilities": {
                        "incremental_guidance": True,
                        "guidance_history": True,
                        "narrated_trace": True,
                        "local_whisper_turbo": True,
                        "cycle_completion": True,
                        "narration_claim_audit": True,
                        "coordinate_isolation": True,
                        "experience_family_inheritance": True,
                        "directed_task_graph": True,
                        "task_model_revision": True,
                        "task_model_history": True,
                        "graph_native_guidance": True,
                        "independent_guidance_delete": True,
                        "system_multi_action_planning": True,
                        "voice_dictation": True,
                        "waa_narrated_recording": True,
                        "waa_task_catalog": True,
                        "deferred_recording_compilation": True,
                    },
                    "taskpacks": self.controller.list_taskpacks(),
                    "recordings": self.controller.list_recordings(),
                    "trace_representations": list_traces(self.controller.project_root),
                    "trash": self.controller.trash.list(),
                    "candidates": self.controller.list_candidates(),
                    "active_job": (self.controller._jobs[self.controller._active_job_id].snapshot(details=False)
                                   if parsed.query == "compact=1" and self.controller._active_job_id
                                   else None if parsed.query == "compact=1" else self.controller.active_job()),
                    "agent_options": {
                        "api_defaults": {
                            "base_url": DEFAULT_API_BASE_URL,
                            "reasoning_efforts": list(API_REASONING_EFFORTS),
                            "saved_settings": self.controller.api_settings.public_settings(),
                        },
                        "models": list(CODEX_SELECTABLE_MODELS),
                        "reasoning_efforts": list(CODEX_REASONING_EFFORTS),
                        "defaults": {
                            "model": DEFAULT_CODEX_MODEL,
                            "reasoning_effort": DEFAULT_CODEX_REASONING_EFFORT,
                        },
                        "compiler_defaults": {
                            "model": DEFAULT_COMPILER_MODEL,
                            "reasoning_effort": DEFAULT_COMPILER_REASONING_EFFORT,
                        },
                        "revision_defaults": {
                            "model": DEFAULT_REVISION_MODEL,
                            "reasoning_effort": DEFAULT_REVISION_REASONING_EFFORT,
                        },
                        "task_model_revision_defaults": {
                            "model": DEFAULT_TASK_MODEL_REVISION_MODEL,
                            "reasoning_effort": (
                                DEFAULT_TASK_MODEL_REVISION_REASONING_EFFORT
                            ),
                        },
                        "waa_defaults": {
                            "root": str(DEFAULT_WAA_ROOT),
                            "example_path": (
                                "evaluation_examples_windows/examples/notepad/"
                                "366de66e-cbae-4d72-b042-26390db2b145-WOS.json"
                            ),
                        },
                    },
                }
            )
            return
        if parsed.path == "/api/windows":
            self._json({"windows": self.controller.list_windows()})
            return
        if parsed.path == "/api/waa/tasks":
            try:
                values = parse_qs(parsed.query).get("root", [])
                self._json(
                    {
                        "tasks": self.controller.list_waa_tasks(
                            values[0] if values else DEFAULT_WAA_ROOT
                        )
                    }
                )
            except FileNotFoundError as error:
                self._error(HTTPStatus.NOT_FOUND, str(error))
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
            return
        if parsed.path == '/api/traces/asset':
            from trace2task.trace_library import read_asset

            try:
                values = parse_qs(parsed.query)
                filename = (values.get('file') or [''])[0]
                data = read_asset(self.controller.project_root, (values.get('id') or [''])[0], filename)
                self._bytes(data, content_type='image/png' if filename.endswith('.png') else 'text/plain; charset=utf-8')
            except FileNotFoundError as error:
                self._error(HTTPStatus.NOT_FOUND, str(error))
            except (ValueError, TypeError, KeyError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
            return
        if parsed.path == "/api/local-image":
            try:
                values = parse_qs(parsed.query).get("path", [])
                path = self.controller.resolve_local_image(values[0] if values else "")
                content_type = {
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".webp": "image/webp",
                }[path.suffix.casefold()]
                self._bytes(path.read_bytes(), content_type=content_type)
            except FileNotFoundError as error:
                self._error(HTTPStatus.NOT_FOUND, str(error))
            except ValueError as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
            return
        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.removeprefix("/api/jobs/")
            try:
                self._json(self.controller.get_job(job_id))
            except KeyError as error:
                self._error(HTTPStatus.NOT_FOUND, str(error))
            return
        asset = ("workbench/index.html" if parsed.path == "/" and (self.asset_root / "workbench/index.html").is_file()
                 else "index.html" if parsed.path in {"/", "/legacy"} else unquote(parsed.path).removeprefix("/"))
        path = (self.asset_root / asset).resolve()
        compiled = (asset.startswith("workbench/") and path.is_relative_to((self.asset_root / "workbench").resolve())
                    and path.suffix in {".html", ".js", ".css", ".svg"} and path.is_file())
        if not compiled and asset not in {"index.html", "app.js", "components.js", "styles.css", "workbench-legacy.css"}:
            self._error(HTTPStatus.NOT_FOUND, "Not found")
            return
        path = self.asset_root / asset
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".svg": "image/svg+xml",
        }[path.suffix]
        data = path.read_bytes()
        if path.suffix == ".html":
            # A per-server token complements the loopback Host/Origin checks below.
            # It stops a hostile page from submitting a state-changing local request
            # even when the browser is pointed at a rebinding hostname.
            data = data.replace(
                b"__TRACE2TASK_CSRF_TOKEN__",
                self.csrf_token.encode("ascii"),
            )
        self._bytes(data, content_type=content_type)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            self._require_api_same_origin()
            payload = self._read_json(
                max_bytes=(
                    MAX_NARRATION_REQUEST_BYTES
                    if parsed.path
                    in {
                        "/api/trained-model/predict",
                        "/api/recordings/narration",
                        "/api/recordings/transcribe",
                        "/api/transcribe",
                    }
                    else MAX_REQUEST_BYTES
                )
            )
            if parsed.path == "/api/trash/restore":
                self._json(self.controller.restore_trashed_asset(payload.get("path", "")))
                return
            if parsed.path == "/api/trash/delete":
                self._json(self.controller.purge_trashed_asset(
                    payload.get("path", ""), confirmed=payload.get("confirmed", False)))
                return
            if parsed.path == "/api/components":
                active = self.controller.active_job()
                if active and active.get("status") in {"queued", "running", "stopping"}:
                    raise RuntimeError("请先停止任务，再管理组件")
                self._json(self.controller.components.start(
                    payload.get("action"), payload.get("directory", ""), payload.get("model", "")))
                return
            if parsed.path == "/api/rsi/start":
                self._json(self.controller.start_rsi_practice(payload), status=HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/api/rsi/stop":
                self._json(self.controller.stop_rsi_practice(payload.get("run_id", "")))
                return
            if parsed.path == "/api/rsi/recover":
                self._json(self.controller.recover_rsi_practice(payload.get("run_id", "")))
                return
            if parsed.path == "/api/rsi/review":
                self._json(self.controller.review_rsi_candidate(payload))
                return
            if parsed.path == "/api/local-model/service":
                active = self.controller.active_job()
                if active and active.get("status") not in {"completed", "failed", "cancelled", "stopped"}:
                    raise RuntimeError("请先停止当前任务，再管理本地模型服务")
                from trace2task.local_model_service import control_service
                self._json(control_service(payload.get("action"), payload.get("model"), self.controller.project_root,
                                           backend=payload.get("backend")))
                return
            if parsed.path == "/api/local-prompts":
                active = self.controller.active_job()
                if active and active.get("status") in {"queued", "running", "stopping"}:
                    raise RuntimeError("请先停止当前任务，再修改本地模型提示词")
                self._json(self.controller.save_local_prompt_profile(
                    payload.get("model"), payload.get("backend"), payload.get("profile")))
                return
            if parsed.path == "/api/chat-prompts":
                active = self.controller.active_job()
                if active and active.get("status") in {"queued", "running", "stopping"}:
                    raise RuntimeError("请先停止当前任务，再修改执行提示词")
                self._json(self.controller.save_chat_prompt_profile(
                    payload.get("provider"), payload.get("guidance", "")))
                return
            if parsed.path == "/api/cua/windows":
                import tempfile

                from trace2task.cua_backend import CuaBackend
                with tempfile.TemporaryDirectory(prefix="trace2task-catalog-") as folder:
                    driver = CuaBackend(folder, lambda *args, **kwargs: None)
                    try:
                        driver.start()
                        self._json({"windows": driver.windows(), "apps": [
                            {"name": a.get("name"), "launch_path": a["launch_path"]}
                            for a in driver.call("list_apps", {}).get("apps", []) if a.get("launch_path")]})
                    finally:
                        driver.close()
                return
            if parsed.path == "/api/trained-model/predict":
                from trace2task.local_model_service import service_busy
                if service_busy():
                    raise RuntimeError("本地模型服务正在启动或关闭，请稍候")
                from trace2task.trained_model_client import predict_local

                active = self.controller.active_job()
                if active and active.get("status") in {
                    "queued", "running", "stopping", "awaiting_recording_start", "awaiting_narration",
                }:
                    raise RuntimeError("请先结束当前任务，再使用 D 模型预览")
                selected = payload.get("model", "D-5970")
                if selected == "D-5970":
                    if payload.get("task_path"):
                        raise ValueError("D-5970 冻结输入结构不支持经验指导")
                    result = predict_local(payload.get("task", ""), image=payload.get("image"))
                else:
                    from trace2task.desktop_runner import build_desktop_experience_context
                    from trace2task.local_gui_client import predict_gui
                    chosen_task = payload.get("task_path")
                    experience_context = (build_desktop_experience_context(
                        self.controller._resolve_execution_experience(chosen_task), execute=True,
                    ) if chosen_task else None)
                    result = predict_gui(payload.get("task", ""), model=selected,
                                          image=payload.get("image"),
                                          experience_context=experience_context,
                                          prompt_profile=self.controller.local_prompt_profile(
                                              selected, "win32")["effective"])
                self._json(result)
                return
            if parsed.path in {"/api/model-settings/save", "/api/model-settings/clear"}:
                if parsed.path.endswith("/clear"):
                    self._json(self.controller.api_settings.clear())
                else:
                    config = ModelAPIConfig(
                        base_url=payload.get("base_url", DEFAULT_API_BASE_URL),
                        api_key=payload.get("api_key", ""),
                        thinking_mode=payload.get("thinking_mode", "default"),
                        response_format=payload.get("response_format", "json_schema"),
                        timeout_seconds=payload.get("timeout_seconds", 120),
                        context_window_tokens=payload.get("context_window_tokens", 32768),
                    )
                    self._json(self.controller.api_settings.save(
                        config, model=payload.get("model", ""),
                        reasoning_effort=payload.get("reasoning_effort", "default"),
                    ))
                return
            if parsed.path == "/api/jobs":
                from trace2task.local_model_service import service_busy
                if service_busy():
                    raise RuntimeError("本地模型服务正在启动或关闭，请稍候")
                result = self.controller.start_job(
                    task_path=payload.get("task_path", ""),
                    instruction=payload.get("instruction", ""),
                    execute=payload.get("mode") == "execute",
                    model=payload.get("model", DEFAULT_CODEX_MODEL),
                    reasoning_effort=payload.get(
                        "reasoning_effort", DEFAULT_CODEX_REASONING_EFFORT
                    ),
                    background=payload.get("input_mode") == "background",
                    adaptive_reasoning=False,
                    use_experience=payload.get("use_experience", False),
                    execution_scope=payload.get("execution_scope", "window"),
                    orchestration=payload.get("orchestration", "legacy"),
                    resume_from=payload.get("resume_from", ""),
                    provider=payload.get("provider", "codex"),
                    api_options=payload.get("api"),
                    continuous=payload.get("continuous", False),
                    executor_backend=payload.get("executor_backend", "win32"),
                    cua_target=payload.get("cua_target"),
                    inference_backend=payload.get("inference_backend"),
                )
                self._json(result, status=HTTPStatus.ACCEPTED)
                return
            if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/approve"):
                job_id = parsed.path.removeprefix("/api/jobs/").removesuffix("/approve")
                with self.controller._lock:
                    job = self.controller._jobs[job_id]
                    if (job.provider != "trained_d" or job.status != "running"
                            or not job.pending_batch or job.pending_batch["token"] != payload.get("token")):
                        raise ValueError("该批次已过期或不存在，请刷新")
                    job.pending_batch = None
                    job.approval_event.set()
                self._json({"status": "approved"})
                return
            if parsed.path == "/api/experience-route":
                self._json(
                    self.controller.route_instruction(payload.get("instruction", ""))
                )
                return
            if parsed.path == "/api/recordings":
                if payload.get("recording_backend", "opencua") != "opencua":
                    raise ValueError("旧本机录制入口已停用，请使用 OpenCUA")
                result = self.controller.start_recording(
                    recording_backend="opencua",
                    handle=0,
                    execution_scope="desktop",
                    task_id=payload.get("task_id", ""),
                    narrated=payload.get("narrated", False),
                    defer_compilation=payload.get("defer_compilation", False),
                    model=payload.get("model", DEFAULT_COMPILER_MODEL),
                    reasoning_effort=payload.get(
                        "reasoning_effort", DEFAULT_COMPILER_REASONING_EFFORT
                    ),
                )
                self._json(result, status=HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/api/waa/recordings":
                result = self.controller.start_waa_recording(
                    waa_root=payload.get("waa_root", DEFAULT_WAA_ROOT),
                    example_path=payload.get("example_path", ""),
                    task_id=payload.get("task_id", ""),
                    narrated=payload.get("narrated", True),
                    defer_compilation=payload.get("defer_compilation", False),
                    model=payload.get("model", DEFAULT_COMPILER_MODEL),
                    reasoning_effort=payload.get(
                        "reasoning_effort", DEFAULT_COMPILER_REASONING_EFFORT
                    ),
                    distro=payload.get("distro", DEFAULT_WAA_DISTRO),
                    container=payload.get("container", DEFAULT_WAA_CONTAINER),
                )
                self._json(result, status=HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/api/waa/recordings/go":
                self._json(
                    self.controller.go_waa_recording(
                        payload.get("job_id", ""),
                        audio_started_at_epoch_ms=payload.get(
                            "audio_started_at_epoch_ms"
                        ),
                    )
                )
                return
            if parsed.path == "/api/recordings/narration":
                self._json(
                    self.controller.submit_recording_narration(
                        payload.get("job_id", ""),
                        transcript=payload.get("transcript", ""),
                        segments=payload.get("segments"),
                        audio_base64=payload.get("audio_base64"),
                        mime_type=payload.get("mime_type"),
                        transcription_engine=payload.get(
                            "transcription_engine", "browser_web_speech"
                        ),
                    ),
                    status=HTTPStatus.ACCEPTED,
                )
                return
            if parsed.path == "/api/recordings/transcribe":
                self._json(
                    self.controller.transcribe_recording_narration(
                        payload.get("job_id", ""),
                        audio_base64=payload.get("audio_base64"),
                        mime_type=payload.get("mime_type"),
                    )
                )
                return
            if parsed.path == "/api/transcribe":
                self._json(
                    self.controller.transcribe_dictation(
                        audio_base64=payload.get("audio_base64"),
                        mime_type=payload.get("mime_type"),
                        context=payload.get("context", ""),
                    )
                )
                return
            if parsed.path == "/api/taskpacks/upgrade":
                self._json(self.controller.upgrade_taskpack(payload.get("task_path", "")))
                return
            if parsed.path == "/api/taskpacks/confirm":
                self._json(
                    self.controller.confirm_local_taskpack(payload.get("task_path", ""))
                )
                return
            if parsed.path == "/api/recordings/compile":
                result = self.controller.start_compilation(
                    payload.get("trace_path", ""),
                    model=payload.get("model", DEFAULT_COMPILER_MODEL),
                    reasoning_effort=payload.get(
                        "reasoning_effort", DEFAULT_COMPILER_REASONING_EFFORT
                    ),
                    confirm_duplicate=payload.get("confirm_duplicate", False),
                )
                self._json(result, status=HTTPStatus.OK if result.get("confirmation_required") else HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/api/traces/generate-d":
                self._json(self.controller.generate_trace_d(payload.get("trace_path", ""),
                    confirm_duplicate=payload.get("confirm_duplicate", False)))
                return
            if parsed.path == '/api/traces/generate-a':
                self._json(self.controller.generate_trace_a(payload.get('trace_path', ''),
                    confirm_duplicate=payload.get('confirm_duplicate', False)))
                return
            if parsed.path == "/api/traces/body":
                from trace2task.trace_library import read_body
                self._json(read_body(self.controller.project_root, payload.get("id", "")))
                return
            if parsed.path == "/api/traces/model-input":
                from trace2task.trace_library import read_model_input
                self._json(read_model_input(self.controller.project_root, payload.get("id", "")))
                return
            if parsed.path == "/api/traces/delete":
                self._json(self.controller.delete_trace_experience(payload.get("id", "")))
                return
            if parsed.path == "/api/taskpacks/delete":
                self._json(self.controller.delete_taskpack(payload.get("task_path", "")))
                return
            if parsed.path == "/api/taskpacks/guidance/delete":
                self._json(
                    self.controller.delete_human_guidance(
                        payload.get("task_path", "")
                    )
                )
                return
            if parsed.path == "/api/recordings/delete":
                self._json(
                    self.controller.delete_recording(payload.get("trace_path", ""))
                )
                return
            if parsed.path == "/api/candidates/delete":
                self._json(
                    self.controller.delete_candidate(payload.get("path", ""))
                )
                return
            if parsed.path == "/api/candidates/revise":
                self._json(
                    self.controller.start_revision(
                        payload.get("path", ""),
                        payload.get("feedback", ""),
                        model=payload.get("model", DEFAULT_REVISION_MODEL),
                        reasoning_effort=payload.get(
                            "reasoning_effort", DEFAULT_REVISION_REASONING_EFFORT
                        ),
                    ),
                    status=HTTPStatus.ACCEPTED,
                )
                return
            if parsed.path == "/api/candidates/task-model/revise":
                self._json(
                    self.controller.start_task_model_revision(
                        payload.get("path", ""),
                        payload.get("feedback", ""),
                        model=payload.get(
                            "model", DEFAULT_TASK_MODEL_REVISION_MODEL
                        ),
                        reasoning_effort=payload.get(
                            "reasoning_effort",
                            DEFAULT_TASK_MODEL_REVISION_REASONING_EFFORT,
                        ),
                    ),
                    status=HTTPStatus.ACCEPTED,
                )
                return
            if parsed.path == "/api/candidates/task-model/confirm":
                self._json(
                    self.controller.confirm_task_model_revision(
                        payload.get("path", "")
                    )
                )
                return
            if parsed.path == "/api/candidates/revisions/confirm":
                self._json(
                    self.controller.confirm_candidate_revision(payload.get("path", ""))
                )
                return
            if parsed.path == "/api/candidates/revisions/summary":
                self._json(
                    self.controller.update_candidate_revision_summary(
                        payload.get("path", ""),
                        payload.get("summary", ""),
                    )
                )
                return
            if parsed.path == "/api/open-local":
                self._json(self.controller.open_local(payload.get("path", "")))
                return
            if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/stop"):
                job_id = parsed.path.removeprefix("/api/jobs/").removesuffix("/stop")
                self._json(self.controller.stop_job(job_id))
                return
            self._error(HTTPStatus.NOT_FOUND, "Not found")
        except KeyError as error:
            self._error(HTTPStatus.NOT_FOUND, str(error))
        except (FileNotFoundError, TypeError, ValueError) as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
        except RuntimeError as error:
            self._error(HTTPStatus.CONFLICT, str(error))
        except OSError as error:
            self._error(
                HTTPStatus.CONFLICT,
                f"本地文件操作失败：{type(error).__name__}: {error}",
            )

    def _require_api_same_origin(self) -> None:
        # State-changing routes can start desktop control, read audio/screenshots,
        # mutate taskpacks, or manage a local model.  Keep all of them behind the
        # same protection, not just routes that happen to use an API key.
        host = self._require_loopback_host()
        origin = self.headers.get("Origin")
        if origin != f"http://{host}":
            raise ValueError("不允许跨站提交模型 API 请求")
        if not secrets.compare_digest(
            self.headers.get("X-Trace2Task-CSRF", ""), self.csrf_token
        ):
            raise ValueError("本机控制台请求缺少有效的安全令牌；请刷新页面后重试")
        if self.headers.get_content_type() != "application/json":
            raise ValueError("模型 API 请求必须使用 application/json")

    def _require_loopback_host(self) -> str:
        """Reject DNS-rebinding hosts before serving local task data or images."""
        port = self.server.server_address[1]
        host = self.headers.get("Host", "")
        if host not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            raise ValueError("本机控制台请求必须通过 127.0.0.1 或 localhost 访问")
        return host

    def _read_json(self, *, max_bytes: int = MAX_REQUEST_BYTES) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as error:
            raise ValueError("Invalid Content-Length") from error
        if length <= 0 or length > max_bytes:
            raise ValueError(f"Request body must be between 1 and {max_bytes} bytes")
        try:
            payload = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as error:
            raise ValueError("Request body must be valid JSON") from error
        if not isinstance(payload, dict):
            raise TypeError("Request JSON must be an object")
        return payload

    def _json(self, payload: object, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._bytes(data, content_type="application/json; charset=utf-8", status=status)

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json({"error": message}, status=status)

    def _bytes(
        self,
        data: bytes,
        *,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        embedded_legacy = urlparse(self.path).path == "/legacy"
        # The local structured-action preview returns an annotated image as a data
        # URL.  Permit that one rendering path without opening scripts, network
        # connections, frames, or form submissions to arbitrary origins.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; form-action 'self'; "
            "base-uri 'none'; object-src 'none'; frame-ancestors " + ("'self'" if embedded_legacy else "'none'"),
        )
        self.send_header("X-Frame-Options", "SAMEORIGIN" if embedded_legacy else "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        return


def create_web_server(
    project_root: Path,
    *,
    port: int = 8765,
    controller: WebConsoleController | None = None,
) -> ThreadingHTTPServer:
    if not 1 <= port <= 65_535 and port != 0:
        raise ValueError("Web console port must be between 1 and 65535")
    active_controller = controller or WebConsoleController(project_root)
    asset_root = Path(__file__).with_name("web")
    class BoundWebConsoleHandler(WebConsoleHandler):
        pass

    BoundWebConsoleHandler.controller = active_controller
    BoundWebConsoleHandler.asset_root = asset_root
    BoundWebConsoleHandler.csrf_token = secrets.token_urlsafe(32)

    return ThreadingHTTPServer(("127.0.0.1", port), BoundWebConsoleHandler)


def serve_web_console(
    *,
    project_root: Path | None = None,
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    server = create_web_server(project_root or Path.cwd(), port=port)
    actual_port = server.server_address[1]
    url = f"http://127.0.0.1:{actual_port}/"
    print(f"Trace2Task web console: {url}")
    print("This console is local-only. Press Ctrl+C here to stop the server.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
