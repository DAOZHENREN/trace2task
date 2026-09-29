"""Exercise real detached start/cancel/cleanup without any model call or VM boot."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from trace2task.rsi_control import launch, load_config
from trace2task.rsi_jobs import TERMINAL, PracticeRunStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    store = PracticeRunStore(Path(config["root"]) / "jobs")
    run = store.create({
        "instruction": "Controller cancellation smoke; do not start a guest or model",
        "model": config["models"][0], "reasoning_effort": "low",
        "wall_seconds": 60, "max_model_calls": 1, "project_budget": 1,
    }, max_active=1)
    run_id = run["id"]
    store.claim_fresh_attempt(run_id)
    store.request_stop(run_id)  # Must precede launching the detached supervisor.
    container_id = launch(config, run_id)
    store.append_event(run_id, "launched", {"container_id": container_id})
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        current = store.get(run_id)
        if current["state"] in TERMINAL:
            break
        time.sleep(0.5)
    current = store.get(run_id)
    attempts = store.attempts(run_id)
    usage = store.budget_status(run_id)
    passed = (current["state"] == "cancelled" and attempts[-1]["cleanup_confirmed"] is True
              and usage["model_calls_used"] == 0 and not (store.root / run_id / "lineage").exists())
    receipt = {"kind": "real_supervisor_cancel_before_execution", "passed": passed,
               "run_id": run_id, "container_id": container_id,
               "state": current["state"], "budget": usage, "attempts": attempts,
               "real_vm_test": False}
    with (store.root / run_id / "supervisor-smoke.json").open("x", encoding="utf-8") as output:
        json.dump(receipt, output, indent=2)
    print(json.dumps(receipt, indent=2))
    if not passed:
        raise SystemExit("Cancellation did not reach a verified terminal state")


if __name__ == "__main__":
    main()
