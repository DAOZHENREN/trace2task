import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

from trace2task import rsi_control
from trace2task.rsi_capacity import MINIMUM_DOCKER_AVAILABLE_BYTES, probe_docker_root_capacity
from trace2task.rsi_jobs import PracticeAlreadyActiveError, PracticeRunStore
from trace2task.rsi_runtime_image import OSWORLD_RUNTIME_IMAGE_ID, runtime_manifest


def request(operation, **payload):
    return {"schema_version": "0.1", "operation": operation, "payload": payload}


def spec():
    return {"instruction": "Practice file operations", "model": "gpt-6-astra",
            "reasoning_effort": "low", "max_model_calls": 5, "wall_seconds": 300, "project_budget": 1}


def test_controller_image_must_be_immutable(tmp_path):
    config = tmp_path / "config.json"
    fields = {"root": str(tmp_path), "codex_home": str(tmp_path), "codex_bin": str(tmp_path),
              "controller_image": "trace2task-rsi-controller:latest"}
    config.write_text(json.dumps(fields))
    with pytest.raises(ValueError, match="immutable"):
        rsi_control.load_config(config)
    fields["controller_image"] = "sha256:" + "a" * 64
    config.write_text(json.dumps(fields))
    assert rsi_control.load_config(config)["controller_image"] == fields["controller_image"]


def test_health_checks_the_pinned_runtime_id_not_a_mutable_tag(tmp_path, monkeypatch):
    root = tmp_path
    corpus = root / "upstream/results/explore/corpus_shingles.json"
    corpus.parent.mkdir(parents=True)
    corpus.write_text('["stable"]')
    (Path(str(corpus) + ".MANIFEST.json")).write_text(json.dumps({
        "instruction_count": 108, "schema_version": 2,
        "corpus_sha256": rsi_control.hashlib.sha256(corpus.read_bytes()).hexdigest(),
        "shingle_count": 1,
    }))
    vm = root / "OSWorld-V2/docker_vm_data/osworld-v2-ubuntu-x86.qcow2"
    vm.parent.mkdir(parents=True)
    vm.write_bytes(b"vm")
    (root / "downloads").mkdir()
    (root / "downloads/vm-verified.json").write_text(json.dumps({
        "archive_sha256": rsi_control.VM_ARCHIVE_SHA,
        "image_size": vm.stat().st_size,
        "image_mtime_ns": vm.stat().st_mtime_ns,
        "image_sha256": "a" * 64,
    }))
    smoke = root / "smoke/one"
    smoke.mkdir(parents=True)
    (smoke / "result.json").write_text('{"passed": true}')
    monkeypatch.setattr(rsi_control.shutil, "which", lambda _: "/usr/bin/git")
    monkeypatch.setattr(rsi_control.importlib, "import_module", lambda _: object())
    monkeypatch.setattr(rsi_control, "verify_exam_fence_cache", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(rsi_control, "_osworld_task_source_identity", lambda *_: {})
    requested = []

    class Images:
        def get(self, image):
            requested.append(image)
            return SimpleNamespace(id=OSWORLD_RUNTIME_IMAGE_ID)

    client = SimpleNamespace(ping=lambda: None, images=Images(), close=lambda: None)
    monkeypatch.setitem(sys.modules, "docker", SimpleNamespace(from_env=lambda: client))
    health = rsi_control.health({"root": str(root)})
    assert health["checks"]["vm_runtime_image"] is True
    assert health["checks"]["official_exam_fence_cache"] is True
    assert health["reasoning_efforts"][-1] == "ultra"
    assert requested == [OSWORLD_RUNTIME_IMAGE_ID]
    client.images.get = lambda image: SimpleNamespace(id="sha256:" + "b" * 64)
    assert rsi_control.health({"root": str(root)})["checks"]["vm_runtime_image"] is False
    monkeypatch.setattr(
        rsi_control, "verify_exam_fence_cache",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("unattested")),
    )
    assert rsi_control.health({"root": str(root)})["checks"]["official_exam_fence_cache"] is False


def test_guest_smoke_gate_requires_matching_complete_isolation_receipt(tmp_path):
    root = tmp_path
    vm = root / "OSWorld-V2/docker_vm_data/osworld-v2-ubuntu-x86.qcow2"
    vm.parent.mkdir(parents=True)
    vm.write_bytes(b"vm")
    verification = {
        "archive_sha256": rsi_control.VM_ARCHIVE_SHA,
        "image_sha256": "a" * 64,
        "image_size": vm.stat().st_size,
        "image_mtime_ns": vm.stat().st_mtime_ns,
    }
    (root / "downloads").mkdir()
    (root / "downloads/vm-verified.json").write_text(json.dumps(verification))
    path = root / "smoke/one/guest-smoke.json"
    path.parent.mkdir(parents=True)
    receipt = {
        "schema_version": 1,
        "passed": True,
        "memory_hidden": True,
        "verifier_effects_rolled_back": True,
        "cleanup_errors": [],
        "upstream_revision": rsi_control.UPSTREAM_REVISION,
        "osworld_revision": rsi_control.OSWORLD_REVISION,
        "vm_verification": verification,
        "runtime_image": {**runtime_manifest(), "container_id": "guest-container"},
        "offline_isolation": {"status": "sealed", "mode": "null"},
    }
    path.write_text(json.dumps(receipt))
    assert rsi_control._guest_smoke_passed(root) is True

    path.unlink()
    assert rsi_control._guest_smoke_passed(root) is False
    path.write_text(json.dumps({**receipt, "runtime_image": {"image_id": "sha256:" + "b" * 64}}))
    assert rsi_control._guest_smoke_passed(root) is False
    path.write_text(json.dumps({**receipt, "cleanup_errors": ["cleanup failed"]}))
    assert rsi_control._guest_smoke_passed(root) is False
    path.write_text(json.dumps({
        **receipt,
        "runtime_image": {**runtime_manifest(), "container_id": "guest-container",
                          "resource_limits": {"nano_cpus": 1}},
    }))
    assert rsi_control._guest_smoke_passed(root) is False


def test_docker_root_capacity_uses_pinned_static_utility_and_not_controller_overlay(tmp_path, monkeypatch):
    docker_root = tmp_path / "docker-root"
    docker_root.mkdir()
    image_id = "sha256:" + "c" * 64
    calls = []

    class Images:
        def get(self, requested):
            assert requested == image_id
            return SimpleNamespace(id=image_id)

    class Containers:
        def run(self, image, command, **kwargs):
            calls.append((image, command, kwargs))
            return json.dumps({"available_bytes": MINIMUM_DOCKER_AVAILABLE_BYTES}).encode()

    client = SimpleNamespace(info=lambda: {"DockerRootDir": str(docker_root)}, images=Images(),
                             containers=Containers(), close=lambda: None)
    monkeypatch.setitem(sys.modules, "docker", SimpleNamespace(from_env=lambda: client))

    result = probe_docker_root_capacity({"controller_image": image_id})

    assert result["ready"] is True
    image, command, kwargs = calls[0]
    assert image == image_id and command[:3] == ["python", "-I", "-c"]
    assert "statvfs('/capacity')" in command[3]
    assert kwargs["network_disabled"] is True
    assert kwargs["read_only"] is True
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["volumes"] == {str(docker_root): {"bind": "/capacity", "mode": "ro"}}
    assert "/var/run/docker.sock" not in str(kwargs)


@pytest.mark.parametrize("info", [{}, {"DockerRootDir": "/"}, {"DockerRootDir": "relative"}])
def test_docker_root_capacity_fails_closed_for_invalid_root_or_utility(tmp_path, monkeypatch, info):
    image_id = "sha256:" + "d" * 64
    client = SimpleNamespace(
        info=lambda: info,
        images=SimpleNamespace(get=lambda _: SimpleNamespace(id=image_id)),
        containers=SimpleNamespace(run=lambda *_args, **_kwargs: b"not-json"),
        close=lambda: None,
    )
    monkeypatch.setitem(sys.modules, "docker", SimpleNamespace(from_env=lambda: client))
    assert probe_docker_root_capacity({"controller_image": image_id})["ready"] is False


def test_start_not_admitted_before_readiness(tmp_path, monkeypatch):
    monkeypatch.setattr(rsi_control, "health", lambda _: {"ready": False})
    with pytest.raises(RuntimeError, match="not_ready"):
        rsi_control.dispatch({"root": str(tmp_path)}, request("start", **spec()))
    assert PracticeRunStore(tmp_path / "jobs").list() == []


def test_start_reserves_before_launch_and_limits_concurrency(tmp_path, monkeypatch):
    config = {"root": str(tmp_path)}
    monkeypatch.setattr(rsi_control, "health", lambda _: {"ready": True})
    seen = []
    def launch(_, run_id):
        seen.append(PracticeRunStore(tmp_path / "jobs").get(run_id)["state"])
        return "owned-container-id"
    monkeypatch.setattr(rsi_control, "launch", launch)
    result = rsi_control.dispatch(config, request("start", **spec()))
    assert seen == ["queued"]
    with pytest.raises(PracticeAlreadyActiveError, match="^practice_already_active$"):
        rsi_control.dispatch(config, request("start", **spec()))
    stopped = rsi_control.dispatch(config, request("stop", run_id=result["run"]["id"]))
    assert stopped["run"]["state"] == "queued"
    assert stopped["run"]["stop_requested"]


def test_only_explicit_active_reservation_error_gets_its_wire_code():
    assert rsi_control._runtime_error_code(PracticeAlreadyActiveError()) == "practice_already_active"
    assert rsi_control._runtime_error_code(RuntimeError("not_ready")) == "not_ready"
    assert rsi_control._runtime_error_code(RuntimeError("unexpected detail")) == "control_failed"


def test_deployment_reasoning_allowlist_admits_astra_ultra_and_is_advertised(tmp_path, monkeypatch):
    config = {"root": str(tmp_path)}
    monkeypatch.setattr(rsi_control, "health", lambda _: {"ready": True})
    monkeypatch.setattr(rsi_control, "launch", lambda *_: "owned-container-id")
    ultra = {**spec(), "reasoning_effort": "ultra"}
    result = rsi_control.dispatch(config, request("start", **ultra))
    assert result["run"]["spec"]["reasoning_effort"] == "ultra"
    assert "ultra" in rsi_control._reasoning_efforts(config)

    restricted = {"root": str(tmp_path / "restricted"), "reasoning_efforts": ["low"]}
    with pytest.raises(ValueError, match="Invalid reasoning effort"):
        rsi_control.dispatch(restricted, request("start", **ultra))


def test_uncertain_launch_keeps_reservation(tmp_path, monkeypatch):
    monkeypatch.setattr(rsi_control, "health", lambda _: {"ready": True})
    def launch(*_):
        raise TimeoutError("Docker acknowledgment lost")
    monkeypatch.setattr(rsi_control, "launch", launch)
    with pytest.raises(RuntimeError, match="launch_uncertain"):
        rsi_control.dispatch({"root": str(tmp_path)}, request("start", **spec()))
    store = PracticeRunStore(tmp_path / "jobs")
    run = store.list()[0]
    assert run["state"] == "queued"
    assert store.events(run["id"])[-1]["kind"] == "launch_uncertain"


def test_launch_mounts_frozen_osworld_read_only(tmp_path, monkeypatch):
    store = PracticeRunStore(tmp_path / "jobs")
    run_id = store.create(spec())["id"]
    store.claim_fresh_attempt(run_id)
    captured = {}

    class Containers:
        def run(self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return SimpleNamespace(id="worker")

    client = SimpleNamespace(containers=Containers(), close=lambda: None)
    monkeypatch.setitem(sys.modules, "docker", SimpleNamespace(from_env=lambda: client))
    monkeypatch.setattr(rsi_control.os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setattr(rsi_control.os, "getgid", lambda: 1000, raising=False)
    config = {
        "root": str(tmp_path), "controller_image": "sha256:" + "e" * 64,
        "config_path": str(tmp_path / "deployment.json"), "docker_gid": 999,
    }

    assert rsi_control.launch(config, run_id) == "worker"
    volumes = captured["kwargs"]["volumes"]
    assert volumes[f"{tmp_path}/OSWorld-V2"] == {
        "bind": f"{tmp_path}/OSWorld-V2", "mode": "ro"
    }


def test_candidate_cannot_be_uploaded_by_request(tmp_path):
    store = PracticeRunStore(tmp_path / "jobs")
    run_id = store.create(spec())["id"]
    with pytest.raises(ValueError):
        rsi_control.dispatch({"root": str(tmp_path)}, request(
            "candidate", run_id=run_id, digest="a" * 64, candidate={"verification": {"verdict": "PASS"}}))


def test_cleanup_only_touches_matching_labels(monkeypatch):
    import sys
    removed = []
    run_id = "a" * 32
    class Container:
        id = "owned"
        labels: ClassVar = {"rsiagent.owner": f"trace2task_{run_id}", "rsiagent.launch_token": "token"}
        def reload(self):
            pass
        def remove(self, **kwargs):
            removed.append((self.id, kwargs))
    def listing(**kwargs):
        return [Container()] if kwargs["filters"]["label"].startswith("rsiagent.owner=") else []
    client = SimpleNamespace(containers=SimpleNamespace(list=listing), close=lambda: None)
    monkeypatch.setitem(sys.modules, "docker", SimpleNamespace(from_env=lambda: client))
    assert rsi_control.cleanup_owned(run_id) == ["owned"]
    assert removed == [("owned", {"force": True, "v": True})]
    Container.labels = {}
    with pytest.raises(RuntimeError, match="ownership"):
        rsi_control.cleanup_owned(run_id)


def test_finalizing_is_not_completed_before_supervisor_cleanup(tmp_path):
    store = PracticeRunStore(tmp_path)
    run_id = store.create(spec())["id"]
    store.transition(run_id, "queued", "preflight")
    store.transition(run_id, "preflight", "running")
    result = store.transition(run_id, "running", "finalizing", detail={"intended_state": "completed"})
    assert result["state"] not in rsi_control.TERMINAL
    store.request_stop(run_id)
    with pytest.raises(RuntimeError, match="Stop"):
        store.transition(run_id, "finalizing", "completed")
    assert store.transition(run_id, "finalizing", "cancelled")["state"] == "cancelled"


def test_candidate_fetch_checks_hash(tmp_path):
    store = PracticeRunStore(tmp_path / "jobs")
    run_id = store.create(spec())["id"]
    candidate = {"verification": {"verdict": "PASS", "artifact": "episode/outcome.json"}}
    digest = store.register_candidate(run_id, candidate)
    result = rsi_control.dispatch({"root": str(tmp_path)}, request("candidate", run_id=run_id, digest=digest))
    assert result["candidate"] == candidate
    (store.root / f"candidate-{run_id}-{digest}.json").write_text(json.dumps({"tampered": True}))
    with pytest.raises(ValueError, match="integrity"):
        rsi_control.dispatch({"root": str(tmp_path)}, request("candidate", run_id=run_id, digest=digest))


def test_supervisor_cancels_before_spawning_and_confirms_cleanup(tmp_path, monkeypatch):
    store = PracticeRunStore(tmp_path / "jobs")
    run_id = store.create(spec())["id"]
    attempt = store.claim_fresh_attempt(run_id)
    store.request_stop(run_id)
    cleaned = []
    monkeypatch.setattr(rsi_control, "cleanup_owned", lambda identity: cleaned.append(identity) or [])
    def forbidden(*args, **kwargs):
        raise AssertionError("A stopped run must not spawn its executor")
    monkeypatch.setattr(rsi_control.subprocess, "Popen", forbidden)
    rsi_control.supervise({"root": str(tmp_path)}, run_id, attempt["attempt_no"])
    assert cleaned == [run_id]
    assert store.get(run_id)["state"] == "cancelled"
    assert store.attempts(run_id)[0]["cleanup_confirmed"] is True
    assert store.attempts(run_id)[0]["state"] == "finalized"


@pytest.mark.parametrize(("return_code", "expected"), [(0, "completed"), (1, "needs_recovery")])
def test_worker_exit_and_cleanup_must_support_terminal_claim(tmp_path, monkeypatch, return_code, expected):
    store = PracticeRunStore(tmp_path / "jobs")
    run_id = store.create(spec())["id"]
    attempt = store.claim_fresh_attempt(run_id)
    def spawn(*args, **kwargs):
        store.transition(run_id, "queued", "preflight")
        store.transition(run_id, "preflight", "running")
        store.transition(run_id, "running", "finalizing", detail={"intended_state": "completed"})
        return SimpleNamespace(poll=lambda: return_code, returncode=return_code)
    monkeypatch.setattr(rsi_control.subprocess, "Popen", spawn)
    monkeypatch.setattr(rsi_control, "cleanup_owned", lambda _: [])
    rsi_control.supervise({"root": str(tmp_path), "config_path": "trusted.json"}, run_id, attempt["attempt_no"])
    assert store.get(run_id)["state"] == expected
    assert store.attempts(run_id)[0]["cleanup_confirmed"] is True


def test_kvm_requires_successful_api_query_and_closes_device(monkeypatch):
    closed = []
    monkeypatch.setattr(rsi_control.os, "O_CLOEXEC", 0, raising=False)
    monkeypatch.setattr(rsi_control.os, "open", lambda *_: 17)
    monkeypatch.setattr(rsi_control.os, "close", closed.append)
    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace(ioctl=lambda fd, code, arg: 12))
    assert rsi_control.kvm_available() is True
    assert closed == [17]
    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace(ioctl=lambda *_: 0))
    assert rsi_control.kvm_available() is False


def test_kvm_unavailable_fails_closed(monkeypatch):
    monkeypatch.setattr(rsi_control.os, "O_CLOEXEC", 0, raising=False)
    def unavailable(*_):
        raise PermissionError("device inaccessible")
    monkeypatch.setattr(rsi_control.os, "open", unavailable)
    assert rsi_control.kvm_available() is False
