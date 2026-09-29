import json
import subprocess

import pytest

from trace2task.rsi_remote import (
    MAX_CONTROL_OUTPUT_BYTES,
    RSIRemoteClient,
    RSIRemoteError,
    RSIRemoteNetworkError,
    RSIRemoteProfile,
    RSIRemoteProtocolError,
    RSIRemoteRequestError,
)

RUN_ID = "a" * 32
DIGEST = "b" * 64


def profile(**changes):
    value = {
        "ssh_host": "dongjintao@100.70.228.85",
        "remote_config_path": "/mnt/sda/dongjintao/trace2task-rsi/deployment.json",
        "control_launcher_argv": ("/mnt/sda/dongjintao/trace2task-rsi/venv/bin/python", "-m",
                                   "trace2task.rsi_control"),
        "request_timeout_seconds": 12,
    }
    value.update(changes)
    return RSIRemoteProfile(**value)


def completed(*, result=None, error=None, returncode=0):
    body = {"ok": error is None}
    if error is not None:
        body["error"] = error
    else:
        body["result"] = result if result is not None else {"status": "ok"}
    return subprocess.CompletedProcess(["ssh"], returncode, json.dumps(body).encode(), b"private stderr")


def test_health_uses_only_fixed_ssh_coordinates_and_one_json_stdin(monkeypatch):
    seen = {}
    def run(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return completed(result={"ready": True})
    monkeypatch.setattr(subprocess, "run", run)

    assert RSIRemoteClient(profile()).health() == {"ready": True}
    assert seen["command"][:8] == [
        "ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o",
        "ClearAllForwardings=yes",
    ]
    assert seen["command"][8:10] == ["-o", "ConnectTimeout=10"]
    assert seen["command"][10] == "dongjintao@100.70.228.85"
    assert seen["command"][11].endswith(
        "--config /mnt/sda/dongjintao/trace2task-rsi/deployment.json"
    )
    assert "shell" not in seen["kwargs"]
    assert seen["kwargs"]["timeout"] == 12
    assert json.loads(seen["kwargs"]["input"]) == {
        "schema_version": "0.1", "operation": "health", "payload": {},
    }


def test_instruction_is_data_not_a_remote_command(monkeypatch):
    seen = {}
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs: (
        seen.update(command=command, body=kwargs["input"]) or completed(result={"id": RUN_ID})
    ))
    instruction = "open file; $(never-run)\" --bad"
    result = RSIRemoteClient(profile()).start(
        instruction=instruction, model="gpt-6-astra", reasoning_effort="low",
        max_model_calls=2, wall_seconds=120,
    )
    assert result == {"id": RUN_ID}
    assert instruction not in " ".join(seen["command"])
    assert json.loads(seen["body"])["payload"]["instruction"] == instruction


@pytest.mark.parametrize("bad", ["", "A" * 31, "A" * 33, "A" * 31 + "G"])
def test_run_id_is_validated_before_ssh(monkeypatch, bad):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("SSH must not run"))
    with pytest.raises(ValueError, match="run ID"):
        RSIRemoteClient(profile()).stop(bad)
    with pytest.raises(ValueError, match="run ID"):
        RSIRemoteClient(profile()).recovery(bad)
    with pytest.raises(ValueError, match="run ID"):
        RSIRemoteClient(profile()).recover(bad)


def test_recovery_operations_send_only_a_validated_run_id(monkeypatch):
    requests = []

    def run(command, **kwargs):
        requests.append(json.loads(kwargs["input"]))
        return completed(result={"eligible": True, "reason": "completed_boundary_verified"})

    monkeypatch.setattr(subprocess, "run", run)
    client = RSIRemoteClient(profile())

    assert client.recovery(RUN_ID)["eligible"] is True
    assert client.recover(RUN_ID)["reason"] == "completed_boundary_verified"
    assert requests == [
        {"schema_version": "0.1", "operation": "recovery", "payload": {"run_id": RUN_ID}},
        {"schema_version": "0.1", "operation": "recover", "payload": {"run_id": RUN_ID}},
    ]


def test_start_and_review_enforce_deployment_budgets_before_ssh(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("SSH must not run"))
    client = RSIRemoteClient(profile(max_model_calls=2, max_wall_seconds=20, max_project_budget=1))
    with pytest.raises(ValueError, match="not allowed"):
        client.start(instruction="practice", model="other", reasoning_effort="low",
                     max_model_calls=1, wall_seconds=10)
    with pytest.raises(ValueError, match="max_model_calls"):
        client.start(instruction="practice", model="gpt-6-astra", reasoning_effort="low",
                     max_model_calls=3, wall_seconds=10)
    with pytest.raises(ValueError, match="wall_seconds"):
        client.start(instruction="practice", model="gpt-6-astra", reasoning_effort="low",
                     max_model_calls=1, wall_seconds=21)
    with pytest.raises(ValueError, match="Candidate digest"):
        client.review(RUN_ID, digest="bad", decision="accepted")
    with pytest.raises(ValueError, match="decision"):
        client.review(RUN_ID, digest=DIGEST, decision="later")


def test_structured_remote_error_never_leaks_remote_message(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: completed(
        error={"code": "not_ready", "message": "secret=/mnt/private/auth.json"}, returncode=1
    ))
    with pytest.raises(RSIRemoteRequestError, match="not_ready") as raised:
        RSIRemoteClient(profile()).health()
    assert "secret" not in str(raised.value)


def test_network_timeout_and_ssh_connection_failure_are_classified(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(RSIRemoteNetworkError):
        RSIRemoteClient(profile()).health()
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(
        ["ssh"], 255, b"", b"connection detail"))
    with pytest.raises(RSIRemoteNetworkError):
        RSIRemoteClient(profile()).health()


@pytest.mark.parametrize("output", [b"", b"not-json", b'{"ok":true,"result":[]}'])
def test_invalid_or_oversized_control_output_is_rejected(monkeypatch, output):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(
        ["ssh"], 0, output, b""))
    with pytest.raises(RSIRemoteProtocolError):
        RSIRemoteClient(profile()).health()


def test_oversized_control_output_is_rejected(monkeypatch):
    output = b"x" * (MAX_CONTROL_OUTPUT_BYTES + 1)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(
        ["ssh"], 0, output, b""))
    with pytest.raises(RSIRemoteProtocolError):
        RSIRemoteClient(profile()).health()


def test_profile_rejects_browser_like_control_coordinates():
    with pytest.raises(ValueError, match="SSH host"):
        profile(ssh_host="-oProxyCommand=bad")
    with pytest.raises(ValueError, match="absolute POSIX"):
        profile(remote_config_path="relative.json")
    with pytest.raises(ValueError, match="launcher"):
        profile(control_launcher_argv=("python\nrm",))


def test_remote_non_network_failure_is_safe_and_generic(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(
        ["ssh"], 1, b"unexpected", b"secret output"))
    with pytest.raises(RSIRemoteError, match="启动失败") as raised:
        RSIRemoteClient(profile()).health()
    assert "secret" not in str(raised.value)
