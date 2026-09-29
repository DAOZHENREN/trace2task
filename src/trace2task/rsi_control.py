"""Allowlisted SSH control protocol and out-of-process practice supervisor.

This is trusted deployment code, never a tool accessible to any RSI role.
Configuration comes from a server-owned file, never from a browser request.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from trace2task.rsi_capacity import probe_docker_root_capacity
from trace2task.rsi_exam_fence import build_exam_fence_cache, verify_exam_fence_cache
from trace2task.rsi_jobs import TERMINAL, PracticeAlreadyActiveError, PracticeRunStore
from trace2task.rsi_model_session import ModelContainerConfig, session_factory
from trace2task.rsi_remote import _positive_limit, _run_id
from trace2task.rsi_runtime_image import OSWORLD_RUNTIME_IMAGE_ID, runtime_manifest
from trace2task.rsi_worker import (
    OSWORLD_REVISION,
    UPSTREAM_REVISION,
    _osworld_task_source_identity,
    check_checkout,
)

PROTOCOL = "0.1"
MAX_WIRE = 1_000_000
VM_ARCHIVE_SHA = "eb737ae70b49849e24af407de6a518439a23de05a8497096a948334ce0a909aa"
SUPPORTED_REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")


def kvm_available() -> bool:
    """Exercise the read-only KVM API query, not just the device filename."""
    try:
        import fcntl
        descriptor = os.open("/dev/kvm", os.O_RDWR | os.O_CLOEXEC)
        try:
            return fcntl.ioctl(descriptor, 0xAE00, 0) == 12  # KVM_GET_API_VERSION
        finally:
            os.close(descriptor)
    except (ImportError, OSError):
        return False


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    for key in ("root", "codex_home", "codex_bin"):
        value = Path(config[key])
        if not value.is_absolute() or value == Path(value.anchor):
            raise ValueError("Absolute deployment paths are required")
    config["config_path"] = str(path.resolve())
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", config.get("controller_image", "")):
        raise ValueError("Pin the locally built controller image by its immutable image ID")
    if "instruction_corpus" in config:
        corpus = Path(config["instruction_corpus"])
        if not corpus.is_absolute() or not corpus.resolve().is_relative_to(Path(config["root"]).resolve()):
            raise ValueError("Instruction corpus must be inside the dedicated RSI deployment")
    if "reasoning_efforts" in config:
        efforts = config["reasoning_efforts"]
        if (not isinstance(efforts, list) or not efforts
                or any(type(effort) is not str or effort not in SUPPORTED_REASONING_EFFORTS
                       for effort in efforts)
                or len(set(efforts)) != len(efforts)):
            raise ValueError("Deployment reasoning efforts must be a unique supported list")
    return config


def _reasoning_efforts(config: dict) -> tuple[str, ...]:
    """Return the server-owned reasoning allowlist, never a browser choice."""
    efforts = config.get("reasoning_efforts", SUPPORTED_REASONING_EFFORTS)
    # ``load_config`` validates deployments.  Keep direct programmatic calls
    # fail-closed too, rather than advertising an effort that ``start`` cannot
    # reliably admit.
    if (not isinstance(efforts, (list, tuple)) or not efforts
            or any(type(effort) is not str or effort not in SUPPORTED_REASONING_EFFORTS
                   for effort in efforts)
            or len(set(efforts)) != len(efforts)):
        raise ValueError("Deployment reasoning efforts are invalid")
    return tuple(efforts)


def _guest_smoke_passed(root: Path) -> bool:
    """Accept only the newest complete receipt for this exact frozen runtime."""
    try:
        vm_receipt = json.loads((root / "downloads/vm-verified.json").read_text(encoding="utf-8"))
        paths = sorted((root / "smoke").glob("*/guest-smoke.json"),
                       key=lambda path: path.stat().st_mtime_ns, reverse=True)
        if not paths:
            return False
        receipt = json.loads(paths[0].read_text(encoding="utf-8"))
        runtime = receipt.get("runtime_image")
        isolation = receipt.get("offline_isolation")
        vm_provenance = receipt.get("vm_verification")
        if not isinstance(runtime, dict) or not isinstance(isolation, dict):
            return False
        if receipt.get("schema_version") != 1 or receipt.get("passed") is not True:
            return False
        if receipt.get("memory_hidden") is not True or receipt.get("verifier_effects_rolled_back") is not True:
            return False
        if (receipt.get("cleanup_errors") != [] or isolation.get("status") != "sealed"
                or isolation.get("mode") != "null"):
            return False
        # A receipt from before the runtime resource boundary existed must not
        # enable practice merely because it happens to name the same image.
        # ``verify_guest_runtime_image`` adds a per-container ID, so compare
        # the fixed manifest fields rather than requiring object equality.
        expected_runtime = runtime_manifest()
        if any(runtime.get(name) != expected for name, expected in expected_runtime.items()):
            return False
        if receipt.get("upstream_revision") != UPSTREAM_REVISION:
            return False
        if receipt.get("osworld_revision") != OSWORLD_REVISION:
            return False
        if not isinstance(vm_provenance, dict):
            return False
        return all(vm_provenance.get(name) == vm_receipt.get(name) for name in (
            "archive_sha256", "image_sha256", "image_size", "image_mtime_ns",
        ))
    except (OSError, TypeError, ValueError, KeyError):
        return False


def health(config: dict, *, require_guest_smoke: bool = True) -> dict:
    root = Path(config["root"])
    checks = {}
    checks["git_runtime"] = shutil.which("git") is not None
    checks["kvm_acceleration"] = kvm_available()
    for name, module in (("official_runtime", "desktop_env.desktop_env"), ("docker_client", "docker")):
        try:
            importlib.import_module(module)
            checks[name] = True
        except (ImportError, OSError):
            checks[name] = False
    corpus = Path(config.get("instruction_corpus", root / "upstream/results/explore/corpus_shingles.json"))
    try:
        data = corpus.read_bytes()
        manifest = json.loads(Path(str(corpus) + ".MANIFEST.json").read_text())
        checks["instruction_corpus"] = (
            manifest["instruction_count"] == 108 and manifest["schema_version"] == 2
            and manifest["corpus_sha256"] == hashlib.sha256(data).hexdigest()
            and len(json.loads(data)) == manifest["shingle_count"] > 0)
    except (OSError, ValueError, KeyError, TypeError):
        checks["instruction_corpus"] = False
    vm = root / "OSWorld-V2/docker_vm_data/osworld-v2-ubuntu-x86.qcow2"
    try:
        receipt = json.loads((root / "downloads/vm-verified.json").read_text())
        checks["locked_vm_image"] = (
            receipt["archive_sha256"] == VM_ARCHIVE_SHA
            and receipt["image_size"] == vm.stat().st_size
            and receipt["image_mtime_ns"] == vm.stat().st_mtime_ns
            and len(receipt["image_sha256"]) == 64)
    except (OSError, ValueError, KeyError, TypeError):
        checks["locked_vm_image"] = False
    # Credentials remain host-side; the short-lived control container need not
    # mount them. A model smoke receipt is required before enabling practice.
    checks["model_transport_smoke"] = False
    for path in (root / "smoke").glob("*/result.json"):
        try:
            if json.loads(path.read_text()).get("passed") is True:
                checks["model_transport_smoke"] = True
                break
        except (OSError, ValueError, AttributeError):
            continue  # A partial smoke receipt is not evidence of readiness.
    try:
        import docker
        client = docker.from_env()
        try:
            client.ping()
            image = client.images.get(OSWORLD_RUNTIME_IMAGE_ID)
            # Do not resolve a mutable tag here.  A tag can be retargeted while
            # still existing locally, which would make readiness lie.
            checks["vm_runtime_image"] = getattr(image, "id", None) == OSWORLD_RUNTIME_IMAGE_ID
        finally:
            client.close()
    except Exception:  # noqa: BLE001 -- report admission failure, never start a VM
        checks["vm_runtime_image"] = False
    # The guest's 60 GiB temporary disk plus a 50 GiB overlay require free
    # capacity on DockerRootDir's host filesystem. This static probe does not
    # use the controller overlay's own disk view and does not expose Docker to
    # any model container.
    checks["docker_root_capacity"] = probe_docker_root_capacity(config)["ready"]
    try:
        verify_exam_fence_cache(
            root / "upstream", upstream_revision=UPSTREAM_REVISION,
            osworld_revision=OSWORLD_REVISION,
            osworld_task_source_identity=_osworld_task_source_identity(
                root / "OSWorld-V2" / "evaluation_examples/task_class"),
        )
        checks["official_exam_fence_cache"] = True
    except (OSError, TypeError, ValueError, RuntimeError):
        checks["official_exam_fence_cache"] = False
    if require_guest_smoke:
        checks["guest_isolation_smoke"] = _guest_smoke_passed(root)
    return {"configured": True, "ready": all(checks.values()), "checks": checks,
            "mode": "distribution_guided_capability_exploration",
            "models": config.get("models", ["gpt-6-astra"]),
            "reasoning_efforts": list(_reasoning_efforts(config))}


def prepare_exam_fence_cache(config: dict) -> dict:
    """Trusted host-only setup for the official lazy fence cache.

    The checkouts are verified before importing the official builder.  Workers
    never call this function and keep their upstream mount read-only.
    """
    root = Path(config["root"])
    upstream, osworld = root / "upstream", root / "OSWorld-V2"
    check_checkout(upstream, UPSTREAM_REVISION)
    check_checkout(osworld, OSWORLD_REVISION)
    return build_exam_fence_cache(
        upstream, osworld, upstream_revision=UPSTREAM_REVISION,
        osworld_revision=OSWORLD_REVISION,
        osworld_task_source_identity=_osworld_task_source_identity(
            osworld / "evaluation_examples/task_class"),
    )


def launch(config: dict, run_id: str) -> str:
    import docker
    root = config["root"]
    attempt = _active_attempt(PracticeRunStore(Path(root) / "jobs"), run_id)
    attempt_no = attempt["attempt_no"]
    client = docker.from_env()
    try:
        container = client.containers.run(
            config["controller_image"], [f"{root}/controller-venv/bin/python", "-m", "trace2task.rsi_control",
                            "--config", config["config_path"], "--supervise", run_id,
                            "--attempt", str(attempt_no)],
            name=f"trace2task-rsi-worker-{run_id}-{attempt_no}", detach=True,
            labels={"org.trace2task.rsi.worker": run_id,
                    "org.trace2task.rsi.attempt": str(attempt_no)},
            user=f"{os.getuid()}:{os.getgid()}",
            group_add=[str(config["docker_gid"])], network_mode="host",
            devices=["/dev/kvm:/dev/kvm:rwm"],
            mem_limit="4g", nano_cpus=2_000_000_000,
            environment={"PYTHONPATH": f"{root}/controller/src:{root}/OSWorld-V2"},
            volumes={root: {"bind": root, "mode": "rw"},
                     f"{root}/upstream": {"bind": f"{root}/upstream", "mode": "ro"},
                     f"{root}/OSWorld-V2": {"bind": f"{root}/OSWorld-V2", "mode": "ro"},
                     "/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"},
                     "/usr/bin/docker": {"bind": "/usr/local/bin/docker", "mode": "ro"}},
        )
        return container.id
    finally:
        client.close()


def cleanup_owned(run_id: str) -> list[str]:
    """Only this run's labeled model and guest containers, never other services."""
    import docker
    client = docker.from_env()
    removed = []
    try:
        for key, value in (("rsiagent.owner", f"trace2task_{run_id}"),
                           ("org.trace2task.rsi.run", run_id)):
            for container in client.containers.list(all=True, filters={"label": f"{key}={value}"}):
                container.reload()
                if container.labels.get(key) != value:
                    raise RuntimeError("Container ownership changed")
                if key == "rsiagent.owner" and not container.labels.get("rsiagent.launch_token"):
                    raise RuntimeError("Guest launch ownership token is absent")
                identity = container.id
                # Remove this disposable guest's anonymous /storage volume too.
                # Docker does not remove bind-mounted base images/evidence or
                # named volumes here. Leaving v=False leaks a disk on every
                # interrupted boot/run, even after reporting cleanup success.
                container.remove(force=True, v=True)
                removed.append(identity)
    finally:
        client.close()
    return removed


def _active_attempt(store, run_id, attempt_no=None):
    active = [a for a in store.attempts(run_id) if a["state"] == "active"]
    if len(active) != 1 or (attempt_no is not None and active[0]["attempt_no"] != attempt_no):
        raise RuntimeError("Expected exactly one matching active attempt")
    return active[0]


def recovery_status(config, store, run_id):
    from trace2task.rsi_worker import inspect_recovery
    run = store.get(run_id)
    usage = store.budget_status(run_id)
    status = {"eligible": False, "reason": "not_recovery_state",
              "remaining_model_calls": usage["model_calls_remaining"],
              "remaining_wall_ms": usage["active_remaining_ms"]}
    if run["state"] != "needs_recovery" or run["stop_requested"]:
        return status
    attempts = store.attempts(run_id)
    if not attempts or attempts[-1]["state"] == "active" or not attempts[-1]["cleanup_confirmed"]:
        return {**status, "reason": "cleanup_not_confirmed"}
    if not usage["model_calls_remaining"] or not usage["active_remaining_ms"]:
        return {**status, "reason": "budget_exhausted"}
    root = Path(config["root"])
    boundary = inspect_recovery(store=store, run_id=run_id, upstream=root / "upstream",
                                osworld=root / "OSWorld-V2",
                                corpus_path=Path(config["instruction_corpus"]) if config.get("instruction_corpus") else None)
    return {**status, "eligible": boundary.eligible, "reason": boundary.reason,
            "boundary_projects": boundary.boundary_projects,
            "lineage_manifest_sha256": boundary.lineage_manifest_sha256}


def supervise(config: dict, run_id: str, attempt_no: int):
    store = PracticeRunStore(Path(config["root"]) / "jobs")
    attempt = _active_attempt(store, run_id, attempt_no)
    directory = store.attempt_log_dir(run_id, attempt_no)
    directory.mkdir(parents=True, exist_ok=False)
    stop_reason = None
    queued_seconds = max(0, (datetime.now(UTC) - datetime.fromisoformat(attempt["created"])).total_seconds())
    started = time.monotonic()
    deadline = started + store.budget_status(run_id)["active_remaining_ms"] / 1000 - queued_seconds
    process = None
    try:
        with (directory / "worker.log").open("xb") as log:
            if store.get(run_id)["stop_requested"] or time.monotonic() >= deadline:
                stop_reason = "user_stop" if store.get(run_id)["stop_requested"] else "wall_budget"
            else:
                process = subprocess.Popen([sys.executable, "-m", "trace2task.rsi_control", "--config",
                                            config["config_path"], "--execute", run_id,
                                            "--attempt", str(attempt_no)],
                                           stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                while process.poll() is None:
                    if store.get(run_id)["stop_requested"] or time.monotonic() >= deadline:
                        stop_reason = "user_stop" if store.get(run_id)["stop_requested"] else "wall_budget"
                        store.append_event(run_id, "supervisor_stop", {"reason": stop_reason})
                        os.killpg(process.pid, signal.SIGINT)
                        try:
                            process.wait(timeout=30)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait(timeout=10)
                        break
                    time.sleep(0.25)
    except Exception as error:  # noqa: BLE001 -- preserve and always clean owned compute
        stop_reason = "supervisor_error"
        store.append_event(run_id, stop_reason, {"error_type": type(error).__name__})
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
    try:
        removed = cleanup_owned(run_id)
    except Exception as error:
        current = store.get(run_id)
        store.append_event(run_id, "cleanup_failed", {"error_type": type(error).__name__})
        if current["state"] in {"preflight", "running", "finalizing"}:
            store.transition(run_id, current["state"], "needs_recovery",
                             detail={"reason": "cleanup_failed", "cleanup_confirmed": False})
        store.finalize_attempt(run_id, attempt_no,
            active_elapsed_ms=round((queued_seconds + time.monotonic() - started) * 1000),
            cleanup_confirmed=False)
        raise
    store.append_event(run_id, "cleanup_confirmed", {"removed_container_ids": removed})
    store.finalize_attempt(run_id, attempt_no,
        active_elapsed_ms=round((queued_seconds + time.monotonic() - started) * 1000),
        cleanup_confirmed=True)
    current = store.get(run_id)
    if current["state"] not in TERMINAL:
        intent = current["result"] or {}
        target = intent.get("intended_state", "needs_recovery")
        if target == "completed" and (process is None or process.returncode != 0):
            target = "needs_recovery"
        if stop_reason == "user_stop" or current["stop_requested"]:
            target = "cancelled"
        elif stop_reason == "wall_budget":
            target = "needs_recovery"
        if current["state"] == "queued" and target == "needs_recovery":
            target = "failed"
        if current["state"] != target:
            store.transition(run_id, current["state"], target, detail={
                "reason": stop_reason or "worker_exit", "exit_code": process.returncode if process else None,
                "partial_project_replay_allowed": False,
                "worker_result": intent.get("worker_result", {}), "cleanup_confirmed": True})


def dispatch(config: dict, request: dict) -> dict:
    if request.get("schema_version") != PROTOCOL:
        raise ValueError("Unsupported protocol")
    operation, payload = request["operation"], request.get("payload", {})
    if not isinstance(payload, dict):
        raise TypeError("Object payload required")
    store = PracticeRunStore(Path(config["root"]) / "jobs")
    if operation == "health":
        return health(config)
    if operation == "list":
        return {"runs": store.list(_positive_limit(payload.get("limit", 100), 1000, "limit"))}
    if operation == "start":
        if not health(config)["ready"]:
            raise RuntimeError("not_ready")
        text = payload.get("instruction")
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise ValueError("Invalid practice direction")
        if payload.get("model") not in config.get("models", ["gpt-6-astra"]):
            raise ValueError("Invalid model")
        if payload.get("reasoning_effort") not in _reasoning_efforts(config):
            raise ValueError("Invalid reasoning effort")
        spec = {"instruction": text.strip(), "model": payload["model"],
                "reasoning_effort": payload["reasoning_effort"]}
        for key, maximum in (("max_model_calls", 50), ("wall_seconds", 3600), ("project_budget", 10)):
            spec[key] = _positive_limit(payload.get(key, 1), maximum, key)
        run = store.create(spec, max_active=1)
        store.claim_fresh_attempt(run["id"])
        try:
            identity = launch(config, run["id"])
            store.append_event(run["id"], "launched", {"container_id": identity})
        except Exception as error:
            # A timed-out Docker launch might have succeeded. Never blindly
            # relaunch; preserve the queued reservation for reconciliation.
            store.append_event(run["id"], "launch_uncertain", {"error_type": type(error).__name__})
            raise RuntimeError("launch_uncertain") from error
        return {"run": store.get(run["id"])}
    run_id = _run_id(payload.get("run_id"))
    if operation in {"recovery", "recover"}:
        status = recovery_status(config, store, run_id)
        if operation == "recovery":
            return status
        if not status["eligible"]:
            raise RuntimeError("recovery_not_eligible")
        if not health(config)["ready"]:
            raise RuntimeError("not_ready")
        store.claim_resume_attempt(run_id, recovery_eligible=True,
            lineage_manifest_sha256=status["lineage_manifest_sha256"],
            boundary_projects=status["boundary_projects"])
        try:
            identity = launch(config, run_id)
            store.append_event(run_id, "recovery_launched", {"container_id": identity})
        except Exception as error:
            store.append_event(run_id, "launch_uncertain", {"error_type": type(error).__name__})
            raise RuntimeError("launch_uncertain") from error
        return {"run": store.get(run_id)}
    if operation == "get":
        return {"run": store.get(run_id)}
    if operation == "events":
        after = payload.get("after", 0)
        if type(after) is not int or not 0 <= after < 2**63:
            raise ValueError("Invalid event cursor")
        return {"events": store.events(run_id, after)}
    if operation == "stop":
        from trace2task.rsi_reconcile import reconcile_stopped_orphan
        store.request_stop(run_id)
        reconciled = reconcile_stopped_orphan(store, run_id)
        return {"run": reconciled["run"], "stop_status": reconciled["status"]}
    if operation in {"candidate", "review"}:
        run = store.get(run_id)
        digest = run["candidate_sha256"]
        if not digest or payload.get("digest") != digest:
            raise ValueError("Candidate digest does not match")
        if operation == "review":
            note = payload.get("note", "")
            if not isinstance(note, str) or len(note) > 4000:
                raise ValueError("Invalid review note")
            return {"run": store.review_candidate(run_id, digest, payload["decision"], note)}
        path = store.root / f"candidate-{run_id}-{digest}.json"
        if path.is_symlink() or path.stat().st_size > 500_000:
            raise ValueError("Unsafe or oversized candidate")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("Candidate integrity check failed")
        return {"candidate": json.loads(data), "sha256": digest}
    raise ValueError("Unsupported operation")


def _runtime_error_code(error: RuntimeError) -> str:
    """Map only explicit, safe control errors to the wire protocol."""
    if isinstance(error, PracticeAlreadyActiveError):
        return error.code
    if str(error) in {"not_ready", "launch_uncertain", "recovery_not_eligible"}:
        return str(error)
    return "control_failed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--supervise")
    mode.add_argument("--execute")
    mode.add_argument("--prepare-exam-fence", action="store_true")
    parser.add_argument("--attempt", type=int)
    args = parser.parse_args()
    config = load_config(args.config)
    if args.prepare_exam_fence:
        print(json.dumps(prepare_exam_fence_cache(config), ensure_ascii=False))
        return
    if args.supervise:
        if args.attempt is None or args.attempt < 1:
            parser.error("A claimed --attempt is required")
        supervise(config, _run_id(args.supervise), args.attempt)
        return
    if args.execute:
        from trace2task.rsi_worker import run_official_practice
        root = Path(config["root"])
        run_id = _run_id(args.execute)
        if args.attempt is None or args.attempt < 1:
            parser.error("A claimed --attempt is required")
        model_config = ModelContainerConfig(Path(config["codex_home"]), Path(config["codex_bin"]),
                                            root / "controller/src/trace2task/rsi_model_only.py",
                                            os.getuid(), os.getgid(), run_id)
        run_official_practice(store=PracticeRunStore(root / "jobs"), run_id=run_id,
                             upstream=root / "upstream", osworld=root / "OSWorld-V2",
                             session_factory=session_factory(model_config), supervised=True,
                             attempt_no=args.attempt,
                             corpus_path=Path(config["instruction_corpus"]) if config.get("instruction_corpus") else None)
        return
    try:
        raw = sys.stdin.buffer.read(MAX_WIRE + 1)
        if len(raw) > MAX_WIRE:
            raise ValueError("Request too large")
        # Dependency import banners/logging must not corrupt the one-object
        # SSH protocol. Raw diagnostics remain stderr, not browser error text.
        with contextlib.redirect_stdout(sys.stderr):
            result = dispatch(config, json.loads(raw))
        response = {"ok": True, "result": result}
    except (ValueError, KeyError, TypeError):
        response = {"ok": False, "error": {"code": "invalid_request"}}
    except RuntimeError as error:
        response = {"ok": False, "error": {"code": _runtime_error_code(error)}}
    print(json.dumps(response, ensure_ascii=False))


if __name__ == "__main__":
    main()
