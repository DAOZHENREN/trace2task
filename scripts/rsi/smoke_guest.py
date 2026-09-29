"""Verify real official guest isolation and verifier rollback without any model call."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import traceback
import uuid
from pathlib import Path

from trace2task.rsi_control import cleanup_owned, health, load_config
from trace2task.rsi_isolation import Phase1OfflineIsolation
from trace2task.rsi_runtime_image import install_pinned_osworld_runtime, verify_guest_runtime_image
from trace2task.rsi_worker import OSWORLD_REVISION, UPSTREAM_REVISION, check_checkout


def write_json(path, value):
    with path.open("x", encoding="utf-8") as output:
        json.dump(value, output, indent=2, ensure_ascii=False)
        output.flush()
        os.fsync(output.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    root = Path(config["root"])
    status = health(config, require_guest_smoke=False)
    if not status["ready"]:
        raise RuntimeError(f"Deployment is not ready: {status['checks']}")
    upstream, osworld = root / "upstream", root / "OSWorld-V2"
    check_checkout(upstream, UPSTREAM_REVISION)
    check_checkout(osworld, OSWORLD_REVISION)
    identity = uuid.uuid4().hex
    output = root / "smoke" / identity
    output.mkdir(mode=0o700)
    os.environ.update({
        "RSIAGENT_ROOT": str(upstream), "OSWORLD_ROOT": str(osworld),
        "RSIAGENT_VM_OWNER": f"trace2task_{identity}",
        "RSIAGENT_OSWORLD_CACHE_DIR": str(output / "vm-cache"),
        "RSIAGENT_BOOT_DIAGNOSTICS_DIR": str(output / "boot-diagnostics"),
    })
    sys.path[:0] = [str(upstream), str(osworld)]
    from benchmarks.osworld.provider import prepare_checkpointable_docker_provider
    from core.verifier_runtime import AgenticVerifierExecutor
    from desktop_env.desktop_env import DesktopEnv
    from env.vm import VM

    prepare_checkpointable_docker_provider("rollback_mirror")
    install_pinned_osworld_runtime()
    os.chdir(osworld)
    receipt = {
        "schema_version": 1,
        "kind": "real_official_guest_and_verifier_smoke",
        "passed": False,
        "id": identity,
        "model_calls": 0,
        "upstream_revision": UPSTREAM_REVISION,
        "osworld_revision": OSWORLD_REVISION,
        # This is an already-verified receipt; do not rehash the 28 GiB image
        # during a smoke run. Health later compares this immutable metadata with
        # the current verifier receipt before permitting practice.
        "vm_verification": json.loads(
            (root / "downloads" / "vm-verified.json").read_text(encoding="utf-8")
        ),
    }
    started = time.monotonic()
    desktop = None
    isolation = None
    print(json.dumps({"smoke_id": identity, "stage": "boot", "output": str(output)}), flush=True)
    try:
        desktop = DesktopEnv(provider_name="docker", action_space="pyautogui", os_type="Ubuntu",
                             screen_size=(1920, 1080), headless=True, require_a11y_tree=False,
                             cache_dir=str(output / "vm-cache"), volume_size=60)
        desktop.reset(task_config=None)
        vm = VM(desktop)
        receipt["runtime_image"] = verify_guest_runtime_image(vm)
        isolation = Phase1OfflineIsolation(output)
        receipt["offline_isolation"] = isolation.seal_after_reset(vm)
        print(json.dumps({"stage": "isolation_passed"}), flush=True)
        screenshot = desktop.controller.get_screenshot()
        if not screenshot:
            raise RuntimeError("Guest did not return a real screenshot")
        with (output / "desktop.png").open("xb") as image:
            image.write(screenshot)
        receipt["screenshot_sha256"] = hashlib.sha256(screenshot).hexdigest()
        # All mutations are inside the new disposable guest. The private marker
        # tests actual memory hiding, not merely a model prompt restriction.
        marker = "/tmp/trace2task-rsi-rollback-" + identity
        preparation = vm.run_script("python", (
            "from pathlib import Path\n"
            "p=Path('/home/user/.memory'); p.mkdir(exist_ok=True)\n"
            "(p/'probe.txt').write_text('actor-private-probe')\n"
            f"Path({marker!r}).write_text('before')\n"
            "print('RSI_SMOKE_PREPARED')\n"), timeout=30, cap=0)
        write_json(output / "guest-preparation.json", vars(preparation))
        if preparation.exit_code != 0 or "RSI_SMOKE_PREPARED" not in preparation.stdout:
            raise RuntimeError("Guest preparation failed")
        verifier = AgenticVerifierExecutor(vm, hide_actor_memory=True, execution_mode="rollback_mirror")
        try:
            result = verifier("python", (
                "from pathlib import Path\n"
                "try:\n"
                "    value=Path('/home/user/.memory/probe.txt').read_text()\n"
                "except OSError:\n"
                "    value=None\n"
                "assert value is None, 'Actor private memory was exposed'\n"
                f"assert Path({marker!r}).read_text() == 'before'\n"
                f"Path({marker!r}).write_text('verifier-mutated')\n"
                "print('RSI_SMOKE_VERIFIER_HIDDEN_AND_MUTATED')\n"), timeout=60, cap=0)
            write_json(output / "verifier-execution.json", vars(result))
            if result.exit_code != 0 or "RSI_SMOKE_VERIFIER_HIDDEN_AND_MUTATED" not in result.stdout:
                raise RuntimeError("Independent verifier isolation check failed")
        finally:
            verifier.close()  # Official QEMU loadvm + readiness + delvm.
        restored = vm.run_script("python", (
            "from pathlib import Path\n"
            f"assert Path({marker!r}).read_text() == 'before'\n"
            "assert Path('/home/user/.memory/probe.txt').read_text() == 'actor-private-probe'\n"
            "print('RSI_SMOKE_RESTORED')\n"), timeout=30, cap=0)
        write_json(output / "rollback-verification.json", vars(restored))
        if restored.exit_code != 0 or "RSI_SMOKE_RESTORED" not in restored.stdout:
            raise RuntimeError("Verifier mutation or private memory was not rolled back")
        receipt["memory_hidden"] = True
        receipt["verifier_effects_rolled_back"] = True
        receipt["passed"] = True
    except BaseException as error:
        receipt["error"] = {"type": type(error).__name__, "message": str(error)}
        with (output / "error.log").open("x", encoding="utf-8") as log:
            traceback.print_exc(file=log)
        raise
    finally:
        errors = []
        for item in (isolation, desktop):
            if item is not None:
                try:
                    item.close()
                except Exception as error:  # noqa: BLE001 - persist cleanup failure
                    errors.append(f"{type(error).__name__}: {error}")
        try:
            receipt["cleanup_removed"] = cleanup_owned(identity)
        except Exception as error:  # noqa: BLE001 - persist cleanup failure
            errors.append(f"{type(error).__name__}: {error}")
        receipt["cleanup_errors"] = errors
        receipt["passed"] = receipt["passed"] and not errors
        receipt["elapsed_seconds"] = round(time.monotonic() - started, 3)
        write_json(output / "guest-smoke.json", receipt)
        print(json.dumps(receipt, indent=2), flush=True)
    if not receipt["passed"]:
        raise SystemExit("Guest smoke cleanup failed")


if __name__ == "__main__":
    main()
