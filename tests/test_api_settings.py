from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from trace2task import api_settings
from trace2task.api_settings import APISettingsStore, _dpapi
from trace2task.model_api import ModelAPIConfig

KEY = "test-persistence-secret"
URL = "https://provider.example/v1"


@pytest.fixture
def protected_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> APISettingsStore:
    sealed: dict[tuple[bytes, str], bytes] = {}

    def crypto(data: bytes, endpoint: str, *, decrypt: bool = False) -> bytes:
        if decrypt:
            if (data, endpoint) not in sealed:
                raise RuntimeError("Cannot decrypt")
            return sealed[data, endpoint]
        ciphertext = f"opaque-test-ciphertext-{len(sealed)}".encode()
        sealed[ciphertext, endpoint] = data
        return ciphertext

    monkeypatch.setattr(api_settings, "_dpapi", crypto)
    return APISettingsStore(tmp_path / "local-app-data" / "model-api.json")


def _save(store: APISettingsStore, key: str = KEY, **kwargs):
    return store.save(
        ModelAPIConfig(base_url=URL, api_key=key), model="custom-vision",
        reasoning_effort="default", **kwargs,
    )


def test_profile_persists_without_exposing_key(protected_store: APISettingsStore) -> None:
    profile = _save(protected_store)
    assert profile["saved"] and profile["has_saved_key"]
    assert profile["model"] == "custom-vision"
    assert KEY not in json.dumps(profile)
    assert "protected_key" not in profile
    assert KEY not in protected_store.path.read_text(encoding="utf-8")
    reopened = APISettingsStore(protected_store.path)
    assert reopened.public_settings() == profile
    assert reopened.with_saved_key(ModelAPIConfig(base_url=URL)).api_key == KEY
    assert reopened.with_saved_key(ModelAPIConfig(base_url=URL, api_key="new")).api_key == "new"
    assert list(protected_store.path.parent.glob("*.tmp")) == []


def test_blank_key_retains_secret_for_same_endpoint(protected_store: APISettingsStore) -> None:
    _save(protected_store)
    protected_store.save(
        ModelAPIConfig(base_url=URL + "/chat/completions", response_format="json_object"),
        model="another-vision", reasoning_effort="low",
    )
    profile = protected_store.public_settings()
    assert profile["model"] == "another-vision"
    assert profile["response_format"] == "json_object"
    assert profile["has_saved_key"]
    assert protected_store.with_saved_key(ModelAPIConfig(base_url=URL)).api_key == KEY


def test_changed_destination_never_receives_saved_key(protected_store: APISettingsStore) -> None:
    _save(protected_store)
    other = ModelAPIConfig(base_url="https://other.example/v1")
    assert protected_store.with_saved_key(other).api_key == ""
    with pytest.raises(ValueError, match="重新输入"):
        protected_store.save(other, model="vision", reasoning_effort="default")
    assert protected_store.with_saved_key(ModelAPIConfig(base_url=URL)).api_key == KEY
    protected_store.save(
        ModelAPIConfig(base_url=other.base_url, api_key="different-secret"),
        model="vision", reasoning_effort="default",
    )
    assert protected_store.with_saved_key(other).api_key == "different-secret"
    assert protected_store.with_saved_key(ModelAPIConfig(base_url=URL)).api_key == ""


def test_tampered_destination_cannot_decrypt_blob(protected_store: APISettingsStore) -> None:
    _save(protected_store)
    tampered = json.loads(protected_store.path.read_text(encoding="utf-8"))
    tampered["base_url"] = "https://other.example/v1"
    protected_store.path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="无法读取"):
        protected_store.with_saved_key(ModelAPIConfig(base_url=tampered["base_url"]))


def test_clear_only_removes_profile(
    protected_store: APISettingsStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _save(protected_store)
    unrelated = protected_store.path.with_name("keep.txt")
    unrelated.write_text("keep", encoding="utf-8")
    monkeypatch.setenv("TRACE2TASK_API_KEY", "environment-key")
    cleared = protected_store.clear()
    assert not cleared["saved"] and not cleared["has_saved_key"]
    assert not protected_store.path.exists()
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert os.environ["TRACE2TASK_API_KEY"] == "environment-key"
    assert protected_store.clear() == cleared


def test_encryption_failure_preserves_previous_profile(
    protected_store: APISettingsStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _save(protected_store)
    before = protected_store.path.read_bytes()

    def fail(*args, **kwargs):
        raise RuntimeError("Encryption unavailable")

    monkeypatch.setattr(api_settings, "_dpapi", fail)
    with pytest.raises(RuntimeError):
        _save(protected_store, "replacement-key")
    assert protected_store.path.read_bytes() == before
    assert "replacement-key" not in before.decode()


def test_save_preferences_does_not_persist_environment_key(
    protected_store: APISettingsStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRACE2TASK_API_KEY", KEY)
    profile = _save(protected_store, "")
    assert profile["saved"] and not profile["has_saved_key"]
    assert KEY not in protected_store.path.read_text(encoding="utf-8")


def test_corrupt_settings_do_not_break_state_and_can_be_replaced(
    protected_store: APISettingsStore,
) -> None:
    _save(protected_store)
    protected_store.path.write_text("invalid-json", encoding="utf-8")
    assert "error" in protected_store.public_settings()
    with pytest.raises(ValueError):
        protected_store.with_saved_key(ModelAPIConfig(base_url=URL))
    _save(protected_store)
    assert protected_store.public_settings()["has_saved_key"]


@pytest.mark.skipif(os.name != "nt", reason="Windows current-user DPAPI")
def test_real_windows_dpapi_roundtrip_and_destination_binding() -> None:
    encrypted = _dpapi(KEY.encode(), URL)
    assert KEY.encode() not in encrypted
    assert _dpapi(encrypted, URL, decrypt=True) == KEY.encode()
    with pytest.raises(RuntimeError):
        _dpapi(encrypted, "https://different.example", decrypt=True)
