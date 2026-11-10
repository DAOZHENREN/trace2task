"""Workbench adapter to the pinned official AndroidWorld service.

Reuse the admitted GUI boundary and the mainline loop/audits; never replay actions.
"""

import json
import math
import os
import subprocess
import time
from pathlib import Path
from urllib.parse import urlencode

from trace2task.android_world_benchmark import SCOPE, WorldClient, WorldBackend
from trace2task.osworld_execution import OSWorldObservation


def deployment():
    return json.loads(Path(os.environ["TRACE2TASK_ANDROIDWORLD_CONFIG"]).read_text())


def client(port_key="world_port"):
    cfg = deployment()
    return WorldClient(cfg[port_key], Path(cfg["token_file"]).read_text().strip())


def catalog():
    try:
        cfg = deployment()
        value = json.loads(Path(cfg["catalog"]).read_text())
        identity = client().request("/trace2task/identity")
        if identity.get("scope") != SCOPE or identity["instance"] != value["instance"]:
            raise ValueError("AndroidWorld instance changed; refresh the server-owned task catalog")
        return dict(value, configured=True)
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        return {"configured": False, "tasks": [], "error": str(error)}


def selection(value):
    if not isinstance(value, dict) or set(value) != {"task_id", "max_actions"}:
        raise ValueError("AndroidWorld requires task_id and max_actions")
    if type(value["max_actions"]) is not int or not 1 <= value["max_actions"] <= 500:
        raise ValueError("AndroidWorld action budget must be 1..500")
    task = next((t for t in catalog()["tasks"] if t["id"] == value["task_id"]), None)
    if task is None:
        raise ValueError("AndroidWorld task unavailable in the configured environment")
    return dict(value), task


def predict(instruction, **request):
    return client("motor_port").request(
        "/predict", data={"instruction": instruction, "request": request}
    )


class AndroidWorldDriver:
    def __init__(self, root, record, stop, config):
        self.root, self.record, self.stop = Path(root), record, stop
        self.config, self.task = selection(config)
        self.client = client()
        self.failed = False
        self.initialized = self.motor_started = False
        self.counter = 0
        self.lease = None
        self.cfg = deployment()
        self.target = {"kind": "androidworld", "instance": self.cfg["instance"]}
        self.query = urlencode(
            {"task_type": self.task["task_type"], "task_idx": self.task["task_index"]}
        )

    def start(self):
        import fcntl

        self.lease = Path(self.cfg["lock"]).open("a+")
        try:
            fcntl.flock(self.lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lease.close()
            raise RuntimeError(
                "AndroidWorld is busy; another workbench run holds its lease"
            ) from None
        identity = self.client.request("/trace2task/identity")
        if identity != {"scope": SCOPE, "instance": self.target["instance"]}:
            raise ValueError("AndroidWorld service identity changed")
        # Own only the configured motor container. Never interrupt another GPU user.
        memory = int(
            subprocess.check_output(
                [
                    "nvidia-smi",
                    "--id=" + self.cfg["gpu_uuid"],
                    "--query-gpu=memory.used",
                    "--format=csv,noheader,nounits",
                ],
                text=True,
            ).strip()
        )
        if memory >= 256:
            raise RuntimeError("AndroidWorld motor GPU is occupied; retry when it is idle")
        subprocess.run(
            ["docker", "start", self.cfg["motor_container"]], check=True, capture_output=True
        )
        self.motor_started = True
        deadline = time.monotonic() + 600
        while True:
            self.stop.raise_if_requested()
            try:
                client("motor_port").request("/")
                break
            except (OSError, RuntimeError):
                if time.monotonic() > deadline:
                    raise TimeoutError("AndroidWorld motor failed to become ready")
                self.stop.sleep(2)
        # Mark before request: an interrupted initialize may already have mutated the emulator.
        self.initialized = True
        self.client.request("/task/initialize?" + self.query, data={})
        goal = self.client.request("/task/goal?" + self.query)["goal"]
        if goal != self.task["instruction"]:
            raise ValueError("AndroidWorld goal differs from selected task")
        self.record(
            "androidworld_ready",
            task=self.task,
            instance=identity,
            source="official AndroidWorld 55ddaf5; mainline hybrid controller",
        )

    def observe(self):
        from PIL import Image

        self.stop.raise_if_requested()
        self.counter += 1
        pixels = self.client.screenshot(
            wait_to_stabilize=True, expected_instance=self.target["instance"]
        )
        path = self.root / f"androidworld-screen-{self.counter:04d}.png"
        Image.fromarray(pixels).save(path)
        return {"path": str(path), "instance": self.target["instance"]}

    def evaluate(self):
        score = self.client.request("/task/score?" + self.query)["score"]
        if type(score) not in (int, float) or not math.isfinite(score):
            raise ValueError("Invalid AndroidWorld score")
        value = {
            "valid": True,
            "raw_score": score,
            "success": score == 1,
            "binary_reward": int(score == 1),
            "benchmark": "AndroidWorld",
            "task_id": self.task["id"],
        }
        self.record("environment_evaluation", **value)
        return value

    def close(self):
        try:
            if self.initialized:
                self.client.request("/task/tear_down?" + self.query, data={})
        finally:
            try:
                if self.motor_started:
                    subprocess.run(
                        ["docker", "stop", "-t", "20", self.cfg["motor_container"]],
                        capture_output=True,
                        check=True,
                    )
            finally:
                if self.lease is not None:
                    self.lease.close()


class AndroidWorldObservation(OSWorldObservation):
    pass


class AndroidWorldExecutionBackend(WorldBackend):
    default_delivery_mode = "isolated_androidworld_emulator"

    def __init__(self, driver):
        super().__init__(driver.client, driver.target["instance"])
        self.driver = driver
