from __future__ import annotations

import base64
import ctypes
import json
import os
import tempfile
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

from trace2task.model_api import API_REASONING_EFFORTS, ModelAPIConfig, validate_api_model


def _dpapi(data: bytes, endpoint: str, *, decrypt: bool = False) -> bytes:
    """Use current-user DPAPI; bind the encrypted credential to its destination."""
    if os.name != "nt":
        raise RuntimeError("此系统暂不支持安全保存 API Key；请使用环境变量")

    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.c_void_p)]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    operation = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    operation.argtypes = [
        ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(Blob),
    ]
    operation.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    source = ctypes.create_string_buffer(data)
    entropy_bytes = ("Trace2Task/model-api/v1:" + endpoint).encode("utf-8")
    entropy = ctypes.create_string_buffer(entropy_bytes)
    source_blob = Blob(len(data), ctypes.cast(source, ctypes.c_void_p))
    entropy_blob = Blob(len(entropy_bytes), ctypes.cast(entropy, ctypes.c_void_p))
    result = Blob()
    try:
        if not operation(
            ctypes.byref(source_blob), None, ctypes.byref(entropy_blob), None, None,
            1, ctypes.byref(result),  # CRYPTPROTECT_UI_FORBIDDEN, not LOCAL_MACHINE
        ):
            raise RuntimeError("Windows 无法加密或解密 API Key，请重新输入并保存") from None
        return ctypes.string_at(result.data, result.size)
    finally:
        ctypes.memset(source, 0, ctypes.sizeof(source))
        if result.data:
            ctypes.memset(result.data, 0, result.size)
            kernel32.LocalFree(result.data)


class APISettingsStore:
    """One local profile, outside the repository. Public reads never decrypt the key."""

    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            root = (
                Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
                if os.name == "nt" else Path.home() / ".config"
            )
            path = root / "Trace2Task" / "model-api.json"
        self.path = path
        self._lock = threading.RLock()

    def _load(self) -> dict[str, Any] | None:
        try:
            with self.path.open("rb") as stream:
                raw = stream.read(65_537)
        except FileNotFoundError:
            return None
        if len(raw) > 65_536:
            raise ValueError("保存的 API 配置过大，请清除后重新保存")
        try:
            value = json.loads(raw)
            if not isinstance(value, dict) or value.get("version") != 1:
                raise ValueError
            config = ModelAPIConfig(
                base_url=value["base_url"],
                response_format=value["response_format"],
                timeout_seconds=value["timeout_seconds"],
            )
            validate_api_model(value["model"])
            if value["reasoning_effort"] not in API_REASONING_EFFORTS:
                raise ValueError
            protected = value.get("protected_key", "")
            if not isinstance(protected, str) or (
                protected and value.get("key_storage") != "windows_dpapi_v1"
            ):
                raise ValueError
            value["base_url"] = config.base_url
            return value
        except (KeyError, TypeError, ValueError):
            raise ValueError("保存的 API 配置无效，请重新输入保存或清除旧配置") from None

    def public_settings(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "saved": False, "has_saved_key": False,
            "secure_key_storage": os.name == "nt",
        }
        with self._lock:
            try:
                stored = self._load()
            except (OSError, ValueError):
                return {**result, "error": "保存的 API 配置无法读取，请清除后重新保存"}
            if stored is None:
                return result
            return {
                **result, "saved": True, "has_saved_key": bool(stored.get("protected_key")),
                **{key: stored[key] for key in (
                    "base_url", "model", "reasoning_effort", "response_format", "timeout_seconds",
                )},
            }

    def save(
        self, config: ModelAPIConfig, *, model: str, reasoning_effort: str,
    ) -> dict[str, Any]:
        model = validate_api_model(model)
        if reasoning_effort not in API_REASONING_EFFORTS:
            raise ValueError("不支持的 API 思考强度")
        with self._lock:
            protected = ""
            if config.api_key:
                protected = base64.b64encode(
                    _dpapi(config.api_key.encode("utf-8"), config.endpoint)
                ).decode("ascii")
            else:
                previous = self._load()
                if previous and previous.get("protected_key"):
                    if ModelAPIConfig(base_url=previous["base_url"]).endpoint != config.endpoint:
                        raise ValueError("修改 API 地址须重新输入密钥，或先清除旧配置")
                    protected = previous["protected_key"]
            payload = {
                "version": 1, "base_url": config.base_url, "model": model,
                "reasoning_effort": reasoning_effort, "response_format": config.response_format,
                "timeout_seconds": config.timeout_seconds,
                "key_storage": "windows_dpapi_v1" if protected else None,
                "protected_key": protected,
            }
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor, name = tempfile.mkstemp(
                prefix=".model-api-", suffix=".tmp", dir=self.path.parent,
            )
            temporary = Path(name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(payload, stream, ensure_ascii=False, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(self.path)
            finally:
                temporary.unlink(missing_ok=True)
            return self.public_settings()

    def with_saved_key(self, config: ModelAPIConfig) -> ModelAPIConfig:
        if config.api_key:
            return config
        with self._lock:
            stored = self._load()
            if not stored or not stored.get("protected_key"):
                return config
            if ModelAPIConfig(base_url=stored["base_url"]).endpoint != config.endpoint:
                return config
            try:
                key = _dpapi(
                    base64.b64decode(stored["protected_key"], validate=True),
                    config.endpoint, decrypt=True,
                ).decode("utf-8")
                if not key:
                    raise ValueError
                return replace(config, api_key=key)
            except (ValueError, RuntimeError, OSError):
                raise ValueError("无法读取保存的 API Key，请重新输入密钥或清除旧配置") from None

    def clear(self) -> dict[str, Any]:
        with self._lock:
            self.path.unlink(missing_ok=True)
            return self.public_settings()
