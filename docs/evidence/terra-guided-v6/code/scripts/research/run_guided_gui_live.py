"""Bounded serial guided GUI smoke run; reuse pinned official guests and SFT runtime."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import time
from dataclasses import asdict
from pathlib import Path

DATA = Path("<research-workspace>")
PRIOR = DATA / "runs/sampo-desktop131-test-20261029-v1"


def save(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("x") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def wait(path, timeout=600, process=None):
    deadline = time.monotonic() + timeout
    while not path.exists():
        if process is not None and process.poll() is not None:
            raise RuntimeError("executor worker exited; inspect gpu.log")
        if time.monotonic() > deadline:
            raise TimeoutError(str(path))
        time.sleep(0.1)
    return json.loads(path.read_text())


class CliSession:
    def __init__(self, root):
        self.root = root
        root.mkdir()
        (root / "empty").mkdir()
        (root / "tmp").mkdir()
        self.index = 0
        self.env = {
            k: os.environ[k]
            for k in ("HOME", "PATH", "USER", "LOGNAME", "LANG", "CODEX_HOME")
            if k in os.environ
        }
        self.env["TMPDIR"] = str(root / "tmp")
        login = subprocess.run(
            ["codex", "login", "status"], env=self.env, capture_output=True, text=True, check=True
        )
        if "Logged in using ChatGPT" not in login.stdout + login.stderr:
            raise ValueError("existing ChatGPT login required")

    def run_turn(self, *, prompt, image_path, output_schema, model, reasoning_effort):
        stem = self.root / f"{self.index:03d}"
        self.index += 1
        stem.with_suffix(".prompt.txt").write_text(prompt)
        save(stem.with_suffix(".schema.json"), output_schema)
        cmd = [
            "codex",
            "exec",
            "--ignore-user-config",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--model",
            model,
            "-c",
            f'model_reasoning_effort="{reasoning_effort}"',
            "-c",
            'web_search="disabled"',
            "-c",
            "project_doc_max_bytes=0",
            "-c",
            "features.shell_tool=false",
            "-c",
            "features.memories=false",
            "-c",
            "features.apps=false",
            "--cd",
            str(self.root / "empty"),
            "--json",
            "--output-schema",
            str(stem.with_suffix(".schema.json")),
            "--output-last-message",
            str(stem.with_suffix(".response.json")),
            "--image",
            str(image_path),
            "-",
        ]
        start = time.monotonic()
        with (
            stem.with_suffix(".events.jsonl").open("x") as out,
            stem.with_suffix(".stderr").open("x") as err,
        ):
            result = subprocess.run(
                cmd,
                input=prompt,
                text=True,
                env=self.env,
                stdout=out,
                stderr=err,
                timeout=600,
                check=False,
            )
        save(
            stem.with_suffix(".timing.json"),
            {"seconds": time.monotonic() - start, "returncode": result.returncode},
        )
        result.check_returncode()
        return stem.with_suffix(".response.json").read_text()


class LiveEnvironment:
    def __init__(self, episode, root):
        self.episode, self.root, self.sequence = episode, root, 0
        self.size = None

    def observe(self):
        from PIL import Image

        from trace2task.guided_gui import Observation

        png = self.episode.screenshot()
        with Image.open(io.BytesIO(png)) as img:
            self.size = img.size
            img.verify()
        path = self.root / f"screen-{self.sequence:03d}.png"
        with path.open("xb") as f:
            f.write(png)
        obs = Observation(self.sequence, str(path), hashlib.sha256(png).hexdigest())
        self.sequence += 1
        return obs

    def act(self, action):
        if self.size is None:
            raise ValueError("observation required before action")
        result = self.episode.act(action, *self.size, close_on_policy_failure=False)
        return {"executed": result.executed, "reason": result.reason}

    def close_policy(self):
        self.episode.close_policy()

    def evaluate(self):
        return asdict(self.episode.evaluate())


def worker(root):
    from trace2task.guided_gui import Observation
    from trace2task.guided_gui_adapters import Desktop131StageExecutor
    from trace2task.sampo_desktop131 import Desktop131Policy
    from trace2task.sampo_runtime import Desktop131Runtime

    prior = json.loads((PRIOR / "plan.json").read_text())
    runtime = Desktop131Runtime(
        prior["bundle"], "/workspace/artifacts/sampo-desktop131-20261029/runtime-manifest.json"
    )
    policy = Desktop131Policy(
        runtime.c, allowed_actions=prior["actions"], key_sequences=prior["keys"]
    )
    executor = Desktop131StageExecutor(runtime, policy)
    save(root / "ready.json", {"checkpoint": runtime.manifest["checkpoint_sha256"]})
    index = 0
    while not (root / "stop.json").exists():
        request = root / f"request-{index:04d}.json"
        if not request.exists():
            time.sleep(0.1)
            continue
        row = json.loads(request.read_text())
        start = time.monotonic()
        try:
            action = executor(row["instruction"], Observation(**row["observation"]), row["history"])
            if action is not None:
                action = executor.validate(action)
            response = {
                "action": action,
                "seconds": time.monotonic() - start,
                "request_sha256": hashlib.sha256(request.read_bytes()).hexdigest(),
            }
        except Exception as error:  # noqa: BLE001 - retain infrastructure evidence without synthetic scores
            response = {"error": f"{type(error).__name__}: {error}"}
        save(root / f"response-{index:04d}.json", response)
        index += 1


def run(root):
    import trace2task

    trace2task.__path__.append(str(PRIOR / "code/src/trace2task"))
    from trace2task.gui_owl_rlvr_adapter import load_rlvr_archive

    from trace2task.guided_gui import GuidedConfig, run_guided
    from trace2task.guided_gui_adapters import TerraStageGuide
    from trace2task.hybridcua_official_episode import OfficialEpisodeFactory
    from trace2task.hybridcua_official_guest import OfficialGuestBaseBinding

    plan = json.loads((root / "plan.json").read_text())
    protocol = plan.get("protocol", "terra-guided-v1")
    if protocol == "terra-guided-v2":
        from trace2task.guided_gui_v2 import (
            GuidedConfigV2,
            TerraStageGuideV2,
            run_guided_v2,
        )

        config_type, guide_type, runner = GuidedConfigV2, TerraStageGuideV2, run_guided_v2
    elif protocol == "terra-guided-v3-concise":
        from trace2task.guided_gui_v3 import GuidedConfigV3, TerraStageGuideV3, run_guided_v3

        config_type, guide_type, runner = GuidedConfigV3, TerraStageGuideV3, run_guided_v3
    elif protocol == "terra-guided-v4-stage-history":
        from trace2task.guided_gui_v4 import GuidedConfigV4, TerraStageGuideV4, run_guided_v4

        config_type, guide_type, runner = GuidedConfigV4, TerraStageGuideV4, run_guided_v4
    elif protocol == "terra-guided-v5-history-carryover":
        from trace2task.guided_gui_v5 import GuidedConfigV5, TerraStageGuideV5, run_guided_v5

        config_type, guide_type, runner = GuidedConfigV5, TerraStageGuideV5, run_guided_v5
    elif protocol == "terra-guided-v6-all-subgoals":
        from trace2task.guided_gui_v6 import GuidedConfigV6, TerraStageGuideV6, run_guided_v6

        config_type, guide_type, runner = GuidedConfigV6, TerraStageGuideV6, run_guided_v6
    elif protocol == "terra-guided-v1":
        config_type, guide_type, runner = GuidedConfig, TerraStageGuide, run_guided
    else:
        raise ValueError("unknown guidance protocol")
    run_config = config_type(**plan["config"])
    prior = json.loads((PRIOR / "plan.json").read_text())
    receipt = json.loads(Path(prior["base_receipt"]).read_text())
    if receipt["status"] != "passed" or len(receipt["boots"]) != 2:
        raise ValueError("base admission missing")
    base = receipt["base"]
    binding = OfficialGuestBaseBinding(
        Path(base["path"]), base["sha256"], tuple(base["identity"]), base["verified_at_unix_ns"]
    )
    archive = load_rlvr_archive(prior["archive"], expected_sha256=prior["archive_sha256"])
    (root / "guests").mkdir()
    (root / "rpc").mkdir()
    (root / "tmp").mkdir()
    factory = OfficialEpisodeFactory(
        archive=archive,
        allowed_task_ids=[t["task_id"] for t in plan["tasks"]],
        base_binding=binding,
        runtime_root=root / "guests",
        max_slots=1,
        readiness_policy="upstream",
    )
    gpu = prior["gpu_uuids"][0]
    memory = subprocess.check_output(
        ["nvidia-smi", "-i", gpu, "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        text=True,
    )
    if int(memory.strip()) > 100:
        raise RuntimeError("selected GPU busy")
    repo = Path(__file__).resolve().parents[2]
    cmd = [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--name",
        root.name + "-executor",
        "--network",
        "none",
        "--read-only",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--shm-size",
        "4g",
        "--gpus",
        f"device={gpu}",
    ]
    for mount in (
        f"{DATA}:{DATA}:ro",
        f"{repo}:/workspace:ro",
        f"{root}:{root}:rw",
        f"{root}/tmp:/tmp:rw",
    ):
        cmd += ["-v", mount]
    env = {
        "PYTHONPATH": f"/workspace/src:{DATA}/runtime/parallel4075-trl024-peft017-v1/site-packages:{DATA}/runtime/gui-fill/torchvision-0.24.1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "OMP_NUM_THREADS": "4",
        "HF_HOME": "/tmp/hf",
        "XDG_CACHE_HOME": "/tmp/xdg",
        "CUDA_CACHE_PATH": "/tmp/cuda",
        "TMPDIR": "/tmp",
    }
    for k, v in env.items():
        cmd += ["-e", k + "=" + v]
    cmd += [
        "--entrypoint",
        "python",
        prior["gpu_image"],
        "/workspace/scripts/research/run_guided_gui_live.py",
        "--output",
        str(root / "rpc"),
        "--worker",
    ]
    save(
        root / "launch.json",
        {
            "command": cmd,
            "gpu_uuid": gpu,
            "archive_sha256": archive.sha256,
            "base_sha256": binding.sha256,
            "readiness": "upstream",
            "wall_time_unix": time.time(),
        },
    )
    rows = []
    with (root / "gpu.log").open("x") as log:
        process = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
        try:
            wait(root / "rpc/ready.json", process=process)
            counter = 0

            def execute(instruction, observation, history):
                nonlocal counter
                request = root / "rpc" / f"request-{counter:04d}.json"
                save(
                    request,
                    {
                        "instruction": instruction,
                        "observation": asdict(observation),
                        "history": history,
                    },
                )
                response = wait(root / "rpc" / f"response-{counter:04d}.json", process=process)
                counter += 1
                if "error" in response:
                    raise RuntimeError(response["error"])
                if response["request_sha256"] != hashlib.sha256(request.read_bytes()).hexdigest():
                    raise ValueError("worker response binding mismatch")
                return response["action"]

            for index, task in enumerate(plan["tasks"]):
                directory = root / f"task-{index:02d}"
                directory.mkdir()
                episode = None
                start = time.monotonic()
                row = {"task_id": task["task_id"], "app_type": task["app_type"]}
                try:
                    if shutil.disk_usage(root).free < 3 * 1024**3:
                        raise RuntimeError("less than 3GiB free; preserve remaining evidence")
                    episode = factory(task["task_id"])
                    save(
                        directory / "environment.json",
                        {
                            "provider_dir": str(episode._runtime_dir),
                            "task": episode.config.instruction,
                        },
                    )
                    episode.setup()
                    session = CliSession(directory / "teacher")
                    with (directory / "events.jsonl").open("x") as events:

                        def record(event):
                            events.write(json.dumps(event, ensure_ascii=False) + "\n")
                            events.flush()
                            os.fsync(events.fileno())

                        result = runner(
                            episode.config.instruction,
                            LiveEnvironment(episode, directory),
                            guide_type(session),
                            execute,
                            lambda action: action,
                            record,
                            run_config,
                        )
                    row.update(status="scored", **result)
                except Exception as error:  # noqa: BLE001 - retain infrastructure evidence without synthetic scores
                    row.update(
                        status="infrastructure_invalid", error=f"{type(error).__name__}: {error}"
                    )
                finally:
                    if episode is not None:
                        try:
                            episode.cleanup()
                        except Exception as error:  # noqa: BLE001 - retain infrastructure evidence without synthetic scores
                            row.update(status="infrastructure_invalid", cleanup_error=str(error))
                row["seconds"] = time.monotonic() - start
                save(directory / "result.json", row)
                rows.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
            save(root / "result.json", {"tasks": rows, "training": False, "heldout": False})
        finally:
            save(root / "rpc/stop.json", {})
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                subprocess.run(
                    ["docker", "stop", "--time", "10", root.name + "-executor"], check=True
                )
                process.wait(timeout=30)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    root = args.output.resolve(strict=True)
    if not root.is_relative_to(DATA) or (not args.worker and not os.path.ismount("/mnt/sda")):
        raise ValueError("verified data mount required")
    (worker if args.worker else run)(root)
