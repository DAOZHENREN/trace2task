"""Narrow, credential-free client for a trusted remote RSI control service.

The browser must never choose an SSH host, a remote path, or a command.  Those
values live in :class:`RSIRemoteProfile`, which is loaded by the local
application deployment layer.  This module only sends one JSON request over
stdin to a fixed remote control launcher and accepts one JSON response on
stdout.  It deliberately has no generic ``run`` or shell API.
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

_RUN_ID = re.compile(r"^[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SSH_HOST = re.compile(r"^(?:[A-Za-z0-9_.-]+@)?[A-Za-z0-9][A-Za-z0-9_.:-]*$")
_ERROR_CODE = re.compile(r"^[a-z0-9_]{1,64}$")

PROTOCOL_VERSION = "0.1"
MAX_CONTROL_OUTPUT_BYTES = 1_000_000
MAX_INSTRUCTION_CHARS = 4_000
MAX_REVIEW_NOTE_CHARS = 4_000
DEFAULT_ALLOWED_MODELS = ("gpt-6-astra",)
DEFAULT_REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")


class RSIRemoteError(RuntimeError):
    """A safe-to-display remote-control failure.

    Raw SSH stderr and remote error messages are intentionally not included:
    both may contain deployment-specific paths or sensitive service output.
    """


class RSIRemoteNetworkError(RSIRemoteError):
    """The remote control service was unreachable or exceeded its deadline."""


class RSIRemoteProtocolError(RSIRemoteError):
    """The trusted launcher did not speak the expected one-object protocol."""


class RSIRemoteRequestError(RSIRemoteError):
    """The remote service rejected a validly delivered request."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"远程 RSI 控制器拒绝了请求（{code}）")


@dataclass(frozen=True)
class RSIRemoteProfile:
    """Trusted, non-browser-supplied deployment coordinates.

    ``control_launcher_argv`` is an executable and its fixed arguments, for
    example ``("/srv/rsi/venv/bin/python", "-m", "trace2task.rsi_control")``.
    Operation names and request data are carried only in JSON stdin.
    """

    ssh_host: str
    remote_config_path: str
    control_launcher_argv: tuple[str, ...]
    ssh_executable: str = "ssh"
    connect_timeout_seconds: int = 10
    request_timeout_seconds: int = 30
    allowed_models: tuple[str, ...] = DEFAULT_ALLOWED_MODELS
    allowed_reasoning_efforts: tuple[str, ...] = DEFAULT_REASONING_EFFORTS
    max_model_calls: int = 50
    max_wall_seconds: int = 3_600
    max_project_budget: int = 10

    def __post_init__(self) -> None:
        if not isinstance(self.ssh_host, str) or not _SSH_HOST.fullmatch(self.ssh_host):
            raise ValueError("RSI SSH host profile is invalid")
        if not isinstance(self.remote_config_path, str):
            raise TypeError("RSI remote config path must be text")
        path = PurePosixPath(self.remote_config_path)
        if not path.is_absolute() or str(path) == "/" or _has_control(self.remote_config_path):
            raise ValueError("RSI remote config path must be an absolute POSIX file path")
        _validate_argv(self.control_launcher_argv, "RSI control launcher")
        if not isinstance(self.ssh_executable, str) or not self.ssh_executable.strip() or _has_control(
            self.ssh_executable
        ):
            raise ValueError("RSI SSH executable is invalid")
        for name in ("connect_timeout_seconds", "request_timeout_seconds", "max_model_calls",
                     "max_wall_seconds", "max_project_budget"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        _validate_choices(self.allowed_models, "RSI allowed models")
        _validate_choices(self.allowed_reasoning_efforts, "RSI allowed reasoning efforts")


def _has_control(value: str) -> bool:
    return "\x00" in value or "\r" in value or "\n" in value


def _validate_argv(value: object, label: str) -> None:
    if not isinstance(value, tuple) or not value:
        raise ValueError(f"{label} must be a non-empty tuple")
    if any(not isinstance(item, str) or not item or len(item) > 512 or _has_control(item)
           for item in value):
        raise ValueError(f"{label} contains an invalid argument")


def _validate_choices(value: object, label: str) -> None:
    if not isinstance(value, tuple) or not value:
        raise ValueError(f"{label} must be a non-empty tuple")
    if any(not isinstance(item, str) or not item or len(item) > 200 or _has_control(item)
           for item in value):
        raise ValueError(f"{label} contains an invalid value")


class RSIRemoteClient:
    """Small allowlisted protocol client; it is not a general remote executor."""

    def __init__(self, profile: RSIRemoteProfile) -> None:
        self.profile = profile

    def health(self) -> dict[str, Any]:
        return self._request("health", {})

    def list(self, *, limit: int = 100) -> dict[str, Any]:
        return self._request("list", {"limit": _positive_limit(limit, 1_000, "limit")})

    def get(self, run_id: str) -> dict[str, Any]:
        return self._request("get", {"run_id": _run_id(run_id)})

    def events(self, run_id: str, *, after: int = 0) -> dict[str, Any]:
        if type(after) is not int or after < 0 or after > 2**63 - 1:
            raise ValueError("after must be a non-negative integer")
        return self._request("events", {"run_id": _run_id(run_id), "after": after})

    def start(
        self,
        *,
        instruction: str,
        model: str,
        reasoning_effort: str,
        max_model_calls: int,
        wall_seconds: int,
        project_budget: int = 1,
    ) -> dict[str, Any]:
        if not isinstance(instruction, str):
            raise TypeError("Practice instruction must be text")
        instruction = instruction.strip()
        if not instruction or len(instruction) > MAX_INSTRUCTION_CHARS or _has_control(instruction):
            raise ValueError(f"Practice instruction must be 1..{MAX_INSTRUCTION_CHARS} characters")
        if model not in self.profile.allowed_models:
            raise ValueError("Selected RSI model is not allowed by this deployment")
        if reasoning_effort not in self.profile.allowed_reasoning_efforts:
            raise ValueError("Selected RSI reasoning effort is not allowed by this deployment")
        return self._request("start", {
            "instruction": instruction,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "max_model_calls": _positive_limit(
                max_model_calls, self.profile.max_model_calls, "max_model_calls"
            ),
            "wall_seconds": _positive_limit(wall_seconds, self.profile.max_wall_seconds, "wall_seconds"),
            "project_budget": _positive_limit(
                project_budget, self.profile.max_project_budget, "project_budget"
            ),
        })

    def stop(self, run_id: str) -> dict[str, Any]:
        return self._request("stop", {"run_id": _run_id(run_id)})

    def recovery(self, run_id: str) -> dict[str, Any]:
        """Read the server-verified completed-boundary recovery eligibility."""
        return self._request("recovery", {"run_id": _run_id(run_id)})

    def recover(self, run_id: str) -> dict[str, Any]:
        """Ask the server to revalidate and resume one eligible practice run."""
        return self._request("recover", {"run_id": _run_id(run_id)})

    def review(self, run_id: str, *, digest: str, decision: str, note: str = "") -> dict[str, Any]:
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise ValueError("Candidate digest must be a SHA-256 hex value")
        if decision not in {"accepted", "rejected"}:
            raise ValueError("Candidate decision must be accepted or rejected")
        if not isinstance(note, str) or len(note) > MAX_REVIEW_NOTE_CHARS or _has_control(note):
            raise ValueError(f"Review note must not exceed {MAX_REVIEW_NOTE_CHARS} characters")
        return self._request("review", {
            "run_id": _run_id(run_id), "digest": digest, "decision": decision, "note": note.strip(),
        })

    def candidate(self, run_id: str, *, digest: str) -> dict[str, Any]:
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise ValueError("Candidate digest must be a SHA-256 hex value")
        return self._request("candidate", {"run_id": _run_id(run_id), "digest": digest})

    def _request(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        if operation not in {
            "health", "list", "get", "events", "start", "stop", "recovery", "recover",
            "review", "candidate",
        }:
            raise ValueError("Unsupported RSI control operation")
        encoded = json.dumps(
            {"schema_version": PROTOCOL_VERSION, "operation": operation, "payload": payload},
            ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        remote_command = shlex.join((*self.profile.control_launcher_argv, "--config",
                                     self.profile.remote_config_path))
        command = [
            self.profile.ssh_executable,
            "-T",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", "ClearAllForwardings=yes",
            "-o", f"ConnectTimeout={self.profile.connect_timeout_seconds}",
            self.profile.ssh_host,
            remote_command,
        ]
        try:
            completed = subprocess.run(
                command,
                input=encoded,
                capture_output=True,
                check=False,
                timeout=self.profile.request_timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise RSIRemoteNetworkError("远程 RSI 控制器请求超时；未确认任务是否已启动") from error
        except OSError as error:
            raise RSIRemoteNetworkError("无法启动 SSH 连接远程 RSI 控制器") from error
        if len(completed.stdout) > MAX_CONTROL_OUTPUT_BYTES:
            raise RSIRemoteProtocolError("远程 RSI 控制器响应超过安全大小限制")
        if completed.returncode != 0:
            # A well-formed service rejection is more useful than SSH's opaque
            # exit code, but neither branch exposes remote stderr to the UI.
            if completed.stdout:
                try:
                    return _decode_response(completed.stdout)
                except RSIRemoteRequestError:
                    raise
                except RSIRemoteProtocolError:
                    pass
            if completed.returncode == 255:
                raise RSIRemoteNetworkError("无法连接远程 RSI 控制器")
            raise RSIRemoteError("远程 RSI 控制器启动失败")
        return _decode_response(completed.stdout)


def _run_id(value: object) -> str:
    if not isinstance(value, str) or not _RUN_ID.fullmatch(value):
        raise ValueError("Practice run ID must be 32 lowercase hexadecimal characters")
    return value


def _positive_limit(value: object, maximum: int, name: str) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be an integer in 1..{maximum}")
    return value


def _decode_response(raw: bytes) -> dict[str, Any]:
    if not raw:
        raise RSIRemoteProtocolError("远程 RSI 控制器未返回响应")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RSIRemoteProtocolError("远程 RSI 控制器返回了无效响应") from error
    if not isinstance(payload, dict) or type(payload.get("ok")) is not bool:
        raise RSIRemoteProtocolError("远程 RSI 控制器响应格式无效")
    if payload["ok"]:
        result = payload.get("result")
        if not isinstance(result, dict):
            raise RSIRemoteProtocolError("远程 RSI 控制器成功响应缺少对象结果")
        return result
    error = payload.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    if not isinstance(code, str) or not _ERROR_CODE.fullmatch(code):
        code = "request_rejected"
    raise RSIRemoteRequestError(code)
