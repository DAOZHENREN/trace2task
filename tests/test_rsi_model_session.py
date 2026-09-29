from pathlib import Path

import pytest

from trace2task.rsi_model_session import ModelContainerConfig, ModelOnlyTransport


def config(tmp_path):
    return ModelContainerConfig(tmp_path / "home", tmp_path / "bin",
                                tmp_path / "launcher.py", 1004, 1004)


def test_model_mounts_only_current_call_and_read_only(tmp_path):
    call = tmp_path / "job/model-calls/call-000001"
    command = config(tmp_path).command(call, "a" * 32)
    mounts = [command[i + 1] for i, value in enumerate(command) if value == "--mount"]
    assert len(mounts) == 6
    assert all(m.endswith(",readonly") for m in mounts)
    assert any(f"src={call},dst={call}," in m for m in mounts)
    assert not any("docker.sock" in m or "/dev/kvm" in m for m in mounts)
    assert "--read-only" in command and "--cap-drop=ALL" in command
    assert "--privileged" not in command and "--network=host" not in command


def test_bad_container_identity_and_root_rejected(tmp_path):
    with pytest.raises(ValueError, match="identity"):
        config(tmp_path).command(tmp_path, "untrusted;command")
    with pytest.raises(ValueError, match="non-root"):
        ModelContainerConfig(tmp_path, tmp_path, tmp_path, 0, 0).command(tmp_path, "a" * 32)


def test_unsafe_mount_rejected(tmp_path):
    with pytest.raises(ValueError, match="Unsafe"):
        config(tmp_path).command(tmp_path / "a,b", "a" * 32)
    with pytest.raises(ValueError, match="absolute"):
        config(tmp_path).command(Path("relative"), "a" * 32)


def test_operational_items_abort_model_container(monkeypatch):
    from trace2task.codex_app_server import StdioJsonTransport
    message = {"method": "item/started", "params": {"item": {"type": "commandExecution"}}}
    monkeypatch.setattr(StdioJsonTransport, "receive", lambda *_: message)
    transport = object.__new__(ModelOnlyTransport)
    closed = []
    monkeypatch.setattr(transport, "close", lambda: closed.append(True))
    with pytest.raises(RuntimeError, match="operational"):
        transport.receive(1)
    assert closed == [True]
