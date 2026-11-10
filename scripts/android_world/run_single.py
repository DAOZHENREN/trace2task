"""One-pass MAI-Swift native105 evaluation; no director, GUI-only, official scoring."""

import argparse
import fcntl
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlencode

from trace2task.android_world_benchmark import WorldClient, WorldBackend, SCOPE, native_plan
from trace2task.execution_core import ExecutionCore
from trace2task.execution_protocol import ActionUnavailable
from trace2task.mai_swift_hybrid import load_runtime


def save(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    tmp.replace(path)


def speed_summary(rows):
    import statistics

    out = {"steps": len(rows), "cache_hit_steps": sum(x.get("cached_tokens", 0) > 0 for x in rows)}
    for name in ("input_seconds", "forward_seconds", "total_inference_seconds"):
        values = sorted(x[name] for x in rows if name in x)
        if values:
            out[name] = {
                "mean": statistics.mean(values),
                "median": statistics.median(values),
                "p95": values[max(0, __import__("math").ceil(0.95 * len(values)) - 1)],
                "max": max(values),
            }
    out["cached_tokens"] = sum(x.get("cached_tokens", 0) for x in rows)
    out["new_tokens"] = sum(x.get("new_tokens", 0) for x in rows)
    hits = [x["total_inference_seconds"] for x in rows if x.get("cached_tokens", 0) > 0]
    out["cache_hit_mean_inference_seconds"] = statistics.mean(hits) if hits else None
    return out


class Stop:
    def __init__(self, root):
        self.root = root

    def raise_if_requested(self):
        if (self.root / "STOP").exists():
            raise RuntimeError("User requested stop")

    def sleep(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.raise_if_requested()
            time.sleep(min(0.2, max(0, end - time.monotonic())))


STEP_INSTRUCTION = "Complete the task based on the current screenshot."


def periodic_instruction(goal, index, interval):
    if interval <= 0:
        return goal
    return STEP_INSTRUCTION + ("\nTask: " + goal if index % interval == 0 else "")


def task_aware_inputs(runtime, turns, goal, interval):
    inputs = runtime.inputs(turns)
    reason = "disabled" if interval <= 0 else "none"
    if interval > 0:
        full = periodic_instruction(goal, 0, interval)
        reason = "scheduled" if turns[-1]["instruction"] == full else "none"
        count = int(inputs["image_grid_thw"].shape[0])
        if not 1 <= count <= len(turns):
            raise ValueError("Invalid retained conversation image count")
        if not any(t["instruction"] == full for t in turns[-count:]):
            # Only amend the NEW user turn, never a previously cached turn.
            turns[-1]["instruction"] = full
            inputs = runtime.inputs(turns)
            reason = "last_task_reminder_evicted"
    return inputs, reason


def evaluate(
    runtime,
    world,
    task,
    root,
    stop,
    budget,
    progress,
    task_reminder_interval=0,
    validate_actions=False,
):
    from PIL import Image

    root.mkdir(exist_ok=False)
    identity = world.request("/trace2task/identity")
    if identity["scope"] != SCOPE:
        raise RuntimeError("Unexpected environment scope")
    target = {"kind": "androidworld", "instance": identity["instance"]}
    query = urlencode({"task_type": task["task_type"], "task_idx": task["task_index"]})

    def record(kind, **fields):
        with (root / "trace.jsonl").open("a") as f:
            f.write(
                json.dumps({"type": kind, "time": time.time(), **fields}, ensure_ascii=False) + "\n"
            )

    core = ExecutionCore(
        WorldBackend(world, identity["instance"]), stop, record, max_actions=budget
    )
    if hasattr(runtime, "reset_cache"):
        runtime.reset_cache()
    model_timings = []
    turns = []
    receipt = None
    initialized = False
    result = None
    start = time.monotonic()
    try:
        initialized = True
        world.request("/task/initialize?" + query, data={})
        goal = world.request("/task/goal?" + query)["goal"]
        if goal != task["instruction"]:
            raise RuntimeError("Task goal changed")
        reason = "action_budget"
        delivered = 0
        for index in range(budget):
            stop.raise_if_requested()
            progress(task["id"], index, "observing")
            t = time.monotonic()
            pixels = world.screenshot(
                wait_to_stabilize=True, expected_instance=identity["instance"]
            )
            path = root / f"screen-{index:04d}.png"
            image = Image.fromarray(pixels)
            image.save(path)
            record("observation", index=index, elapsed_seconds=time.monotonic() - t, path=str(path))
            turns.append(
                {
                    "instruction": periodic_instruction(goal, index, task_reminder_interval),
                    "receipt": receipt,
                    "image": {
                        "path": str(path),
                        "width": image.width,
                        "height": image.height,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    },
                }
            )
            progress(task["id"], index, "inferring")
            t = time.monotonic()
            inputs, reminder_reason = task_aware_inputs(
                runtime, turns, goal, task_reminder_interval
            )
            inputs_end = time.monotonic()
            logits = runtime.logits(inputs)
            logits_end = time.monotonic()
            native = runtime.c.decode(logits)
            inference_end = time.monotonic()
            timing = {
                "input_seconds": inputs_end - t,
                "logits_seconds": logits_end - inputs_end,
                "decode_seconds": inference_end - logits_end,
                "total_inference_seconds": inference_end - t,
                **getattr(runtime, "last_metrics", {}),
            }
            record("model_timing", index=index, **timing)
            model_timings.append(timing)
            save(root / "speed-summary.json", speed_summary(model_timings))
            save(
                root / f"prediction-{index:04d}.json",
                {
                    "turns": turns,
                    "input_ids": inputs["input_ids"][0].tolist(),
                    "slot_positions": inputs["slot_positions"][0].tolist(),
                    "raw_slot_argmax_ids": logits.argmax(-1).tolist(),
                    "native_action": native,
                    "inference_seconds": inference_end - t,
                    "timing": timing,
                    "task_reminder_reason": reminder_reason,
                    "task_reminder_interval": task_reminder_interval,
                },
            )
            record(
                "prediction",
                index=index,
                native_action=native,
                input_tokens=int(inputs["lengths"][0]),
                elapsed_seconds=time.monotonic() - t,
            )
            if native["action"] in {"done", "failed"}:
                reason = "model_" + native["action"]
                break
            try:
                plan = native_plan(native)
            except (ActionUnavailable, ValueError) as error:
                receipt = {
                    "executed_action": None,
                    "outcome": "rejected_before_execution",
                    "proposed_action": native,
                    "reason": str(error),
                }
                record("invalid_action", index=index, **receipt)
                continue
            if validate_actions:
                actions = plan["actions"]
                if len(actions) != 1:
                    raise ValueError("Standalone native action must map to one GUI action")
                check = world.request(
                    "/trace2task/validate-action",
                    data={"instance": identity["instance"], "action": actions[0]},
                )
                if (
                    check.get("instance") != identity["instance"]
                    or type(check.get("valid")) is not bool
                    or check.get("executed") is not False
                ):
                    raise RuntimeError("Malformed action preflight response")
                if not check["valid"]:
                    if not isinstance(check.get("reason"), str) or not check["reason"]:
                        raise RuntimeError("Missing action rejection reason")
                    receipt = {
                        "executed_action": None,
                        "outcome": "rejected_before_execution",
                        "proposed_action": native,
                        "reason": check["reason"],
                        "instruction": "This action was not executed. Choose a valid GUI action from the new screenshot.",
                    }
                    record("invalid_action", index=index, **receipt)
                    continue
            progress(task["id"], index, "acting")
            outcome = core.execute(
                plan,
                target=target,
                observation_id=f"{identity['instance']}:{index}",
                state={"instance": identity["instance"]},
                execution_context={"target": target},
            )
            executed = any(s["receipt"].get("executed") for s in outcome.steps)
            delivered += int(executed)
            receipt = {
                "executed_action": native if executed else None,
                "outcome": "delivered; environment_success_unverified"
                if executed
                else outcome.status,
                "delivery_receipts": [s["receipt"] for s in outcome.steps],
            }
            if outcome.status not in {"delivered", "rejected", "reobserve"}:
                raise RuntimeError("Ambiguous action delivery: " + outcome.status)
        progress(task["id"], index, "scoring")
        score = world.request("/task/score?" + query)["score"]
        if type(score) not in (int, float) or not 0 <= score <= 1:
            raise ValueError("Invalid environment score")
        result = {
            "task_id": task["id"],
            "valid": True,
            "score": score,
            "success": score == 1,
            "stop_reason": reason,
            "decisions": index + 1,
            "delivered_actions": delivered,
            "seconds": time.monotonic() - start,
        }
        save(root / "result.json", result)
        return result
    except Exception as error:
        result = {
            "task_id": task["id"],
            "valid": False,
            "score": None,
            "success": None,
            "error": str(error),
            "exception_type": type(error).__name__,
        }
        save(root / "result.json", result)
        raise
    finally:
        if initialized:
            try:
                world.request("/task/tear_down?" + query, data={})
            except Exception as error:
                save(root / "cleanup-error.json", {"error": str(error)})
                raise


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--run", type=Path, required=True)
    a = p.parse_args()
    r = a.run
    manifest = json.loads((r / "manifest.json").read_text())
    cfg = manifest["deployment"]
    stop = Stop(r)

    def progress(task, index, stage):
        save(
            r / "status.json",
            {"stage": stage, "task_id": task, "decision": index, "time": time.time()},
        )

    results = []
    with Path(cfg["lock"]).open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            world = WorldClient(cfg["world_port"], Path(cfg["token_file"]).read_text().strip())
            if world.request("/trace2task/identity")["instance"] != cfg["instance"]:
                raise RuntimeError("Pinned environment instance changed")
            progress(None, 0, "loading_model")
            runtime = load_runtime(manifest["release"], manifest["base"], manifest["bundle"])
            if manifest.get("kv_cache"):
                from trace2task.mai_swift_kv import CachedRuntime

                runtime = CachedRuntime(runtime)
            for task in manifest["tasks"]:
                stop.raise_if_requested()
                result = evaluate(
                    runtime,
                    world,
                    task,
                    r / "episodes" / task["id"].replace(":", "-"),
                    stop,
                    manifest["max_actions"],
                    progress,
                    manifest.get("task_reminder_interval", 0),
                    manifest.get("validate_actions", False),
                )
                results.append(result)
                save(
                    r / "summary.json",
                    {
                        "planned": len(manifest["tasks"]),
                        "completed": len(results),
                        "valid": sum(x["valid"] for x in results),
                        "successes": sum(x["success"] is True for x in results),
                        "success_rate": sum(x["success"] is True for x in results) / len(results),
                        "complete": len(results) == len(manifest["tasks"]),
                        "results": results,
                    },
                )
            progress(None, 0, "complete")
        except Exception as error:
            save(
                r / "status.json",
                {
                    "stage": "failed",
                    "error": str(error),
                    "completed": len(results),
                    "time": time.time(),
                },
            )
            raise


if __name__ == "__main__":
    main()
