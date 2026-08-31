from __future__ import annotations

import json
import subprocess
import threading
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from ipaddress import IPv4Address, ip_address
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from trace2task.waa_bridge import create_waa_bridge_server
from trace2task.waa_results import write_waa_report
from trace2task.windows_agent import WINDOWS_EXPERIENCE_MODES
from trace2task.windows_task import WindowsTaskContract, load_windows_task

DEFAULT_WAA_CONDITIONS = ("baseline", "trace", "compiled")
DEFAULT_WAA_RESET_SPEC = Path(
    "integrations/windows_agent_arena/reset_specs/notepad.json"
)


def _load_reset_spec(path: Path, *, expected_task_ids: set[str]) -> dict[str, Any]:
    source = path.expanduser().resolve()
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != "0.1":
        raise ValueError("WAA reset spec must be a schema_version 0.1 JSON object")
    tasks = payload.get("tasks")
    if not isinstance(tasks, dict):
        raise TypeError("WAA reset spec tasks must be an object")
    missing = expected_task_ids - set(tasks)
    if missing:
        raise ValueError(f"WAA reset spec is missing task ids: {sorted(missing)}")
    for task_id in expected_task_ids:
        task_spec = tasks[task_id]
        if not isinstance(task_spec, dict):
            raise TypeError(f"WAA reset spec for {task_id!r} must be an object")
        paths = task_spec.get("must_not_exist")
        if not isinstance(paths, list) or not paths:
            raise TypeError(
                f"WAA reset spec for {task_id!r} must define must_not_exist"
            )
        if not all(isinstance(item, str) and item.strip() for item in paths):
            raise TypeError("WAA reset paths must be non-empty strings")
    return payload


def _task_ids(client_root: Path, json_name: str) -> set[str]:
    relative = PurePosixPath(json_name)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("WAA json_name must stay inside the client directory")
    path = client_root.joinpath(*relative.parts)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("WAA task list must be a JSON object")
    task_ids = {
        task_id
        for values in payload.values()
        if isinstance(values, list)
        for task_id in values
        if isinstance(task_id, str) and task_id
    }
    if not task_ids:
        raise ValueError("WAA task list contains no task ids")
    return task_ids


def _validate_conditions(
    contract: WindowsTaskContract,
    conditions: Sequence[str],
) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(conditions))
    if not normalized:
        raise ValueError("WAA experiment requires at least one condition")
    unknown = set(normalized) - set(WINDOWS_EXPERIENCE_MODES)
    if unknown:
        raise ValueError(f"Unknown WAA experience conditions: {sorted(unknown)}")
    if contract.task.requires_confirmation:
        raise RuntimeError("WAA experiments require a confirmed task pack")
    semantic_conditions = {"compiled", "narrated_compiled", "feedback"}
    if semantic_conditions.intersection(normalized) and contract.semantic_experience is None:
        raise RuntimeError(
            "Compiler conditions require reviewed Compiler Agent semantic experience"
        )
    if "feedback" in normalized and contract.human_guidance is None:
        raise RuntimeError("The feedback condition requires reviewed human guidance")
    return normalized


def _source_trace_path(contract: WindowsTaskContract) -> Path:
    path = contract.task.source_path.parent / "reference" / "trace.jsonl"
    if not path.is_file():
        raise FileNotFoundError(f"Task pack source Trace was not found: {path}")
    return path


def _run(
    command: list[str],
    *,
    check: bool = True,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=check,
        capture_output=capture_output,
        text=True,
    )


def _wsl_command(distro: str, *command: str) -> list[str]:
    return ["wsl", "-d", distro, "--", *command]


def _container_command(
    distro: str,
    container: str,
    *command: str,
) -> list[str]:
    return _wsl_command(distro, "docker", "exec", container, *command)


def _parse_container_gateway(raw_routes: str) -> str:
    try:
        routes = json.loads(raw_routes)
    except json.JSONDecodeError as error:
        raise RuntimeError("WAA container routes were not valid JSON") from error
    if not isinstance(routes, list):
        raise TypeError("WAA container routes must be a JSON list")
    for route in routes:
        if not isinstance(route, dict) or route.get("dst") != "default":
            continue
        gateway = route.get("gateway")
        if not isinstance(gateway, str):
            continue
        try:
            address = ip_address(gateway)
        except ValueError:
            continue
        if isinstance(address, IPv4Address):
            return str(address)
    raise RuntimeError("Could not determine the WAA container IPv4 gateway")


def _bridge_host(bridge_url: str) -> str:
    host = urlsplit(bridge_url).hostname
    if host is None:
        raise ValueError("WAA bridge URL must contain a host")
    return host


def _require_running_vm(distro: str, container: str) -> str:
    inspected = _run(
        _wsl_command(
            distro,
            "docker",
            "inspect",
            "-f",
            "{{.State.Running}}",
            container,
        ),
        capture_output=True,
    )
    if inspected.stdout.strip() != "true":
        raise RuntimeError(f"WAA container {container!r} is not running")
    platform = _run(
        _container_command(
            distro,
            container,
            "curl",
            "--noproxy",
            "20.20.20.21",
            "-fsS",
            "--max-time",
            "5",
            "http://20.20.20.21:5000/platform",
        ),
        capture_output=True,
    )
    if platform.stdout.strip() != "Windows":
        raise RuntimeError("WAA Windows VM server is not ready")
    raw_routes = _run(
        _container_command(
            distro,
            container,
            "ip",
            "-j",
            "route",
            "show",
            "default",
        ),
        capture_output=True,
    ).stdout
    return _parse_container_gateway(raw_routes)


def _ensure_relay(distro: str, *, bridge_port: int, relay_port: int) -> int | None:
    probe = _run(
        _wsl_command(
            distro,
            "bash",
            "-lc",
            f"ss -ltn 'sport = :{relay_port}' | grep -q LISTEN",
        ),
        check=False,
    )
    if probe.returncode == 0:
        return None
    script = (
        f"nohup socat TCP-LISTEN:{relay_port},reuseaddr,fork "
        f"TCP:127.0.0.1:{bridge_port} "
        f">/tmp/trace2task-waa-relay-{relay_port}.log 2>&1 & echo $!"
    )
    started = _run(
        _wsl_command(distro, "bash", "-lc", script),
        capture_output=True,
    )
    try:
        return int(started.stdout.strip())
    except ValueError as error:
        raise RuntimeError("WAA bridge relay did not return a process id") from error


def _stop_relay(distro: str, relay_pid: int | None) -> None:
    if relay_pid is None:
        return
    _run(_wsl_command(distro, "kill", str(relay_pid)), check=False)


def _check_bridge_health(
    distro: str,
    container: str,
    *,
    bridge_url: str,
    condition: str,
) -> None:
    bridge_host = _bridge_host(bridge_url)
    last_error = "bridge did not respond"
    for _ in range(20):
        result = _run(
            _container_command(
                distro,
                container,
                "curl",
                "--noproxy",
                bridge_host,
                "-fsS",
                "--max-time",
                "2",
                f"{bridge_url}/health",
            ),
            check=False,
            capture_output=True,
        )
        if result.returncode == 0:
            try:
                payload = json.loads(result.stdout)
            except json.JSONDecodeError:
                last_error = "bridge health response was not JSON"
            else:
                if payload.get("experience_mode") == condition:
                    return
                last_error = "bridge health response reported the wrong condition"
        else:
            last_error = result.stderr.strip() or last_error
        time.sleep(0.25)
    raise RuntimeError(f"WAA container cannot reach the Trace2Task bridge: {last_error}")


def _client_run_command(
    *,
    distro: str,
    container: str,
    bridge_url: str,
    token: str,
    reset_spec_path: str,
    result_dir: str,
    json_name: str,
) -> list[str]:
    no_proxy = f"{_bridge_host(bridge_url)},20.20.20.21,localhost,127.0.0.1"
    return _wsl_command(
        distro,
        "docker",
        "exec",
        "-e",
        f"TRACE2TASK_WAA_BRIDGE_URL={bridge_url}",
        "-e",
        f"TRACE2TASK_WAA_TOKEN={token}",
        "-e",
        f"TRACE2TASK_WAA_RESET_SPEC={reset_spec_path}",
        "-e",
        f"NO_PROXY={no_proxy}",
        "-e",
        f"no_proxy={no_proxy}",
        container,
        "/start_client.sh",
        "--agent",
        "trace2task",
        "--model",
        "unused",
        "--clean-results",
        "true",
        "--result-dir",
        result_dir,
        "--json-name",
        json_name,
    )


def run_waa_experiment(
    waa_root: Path,
    task_path: Path,
    *,
    reset_spec: Path = DEFAULT_WAA_RESET_SPEC,
    conditions: Sequence[str] = DEFAULT_WAA_CONDITIONS,
    narrated_task_path: Path | None = None,
    repetitions: int = 3,
    model: str = "gpt-5.6-terra",
    reasoning_effort: str = "low",
    codex_bin: str = "codex",
    plan_horizon: int = 12,
    distro: str = "Trace2Task-WAA",
    container: str = "winarena",
    json_name: str = "evaluation_examples_windows/test_trace2task.json",
    token: str = "trace2task-local-eval",
    bridge_port: int = 8776,
    relay_port: int = 8876,
    output_root: Path = Path("evaluations/windows-agent-arena"),
) -> dict[str, Any]:
    if repetitions <= 0:
        raise ValueError("WAA experiment repetitions must be positive")
    root = waa_root.expanduser().resolve()
    client_root = root / "src" / "win-arena-container" / "client"
    if not client_root.is_dir():
        raise FileNotFoundError(f"WAA client directory was not found: {client_root}")
    resolved_task_path = task_path.expanduser().resolve()
    contract = load_windows_task(resolved_task_path)
    selected_conditions = _validate_conditions(contract, conditions)
    condition_task_paths = {
        condition: resolved_task_path for condition in selected_conditions
    }
    experience = contract.semantic_experience
    if (
        "compiled" in selected_conditions
        and experience is not None
        and experience.narration_available
    ):
        raise RuntimeError(
            "The compiled condition requires an experience compiled without narration"
        )
    if "narrated_compiled" in selected_conditions:
        resolved_narrated_task = (
            narrated_task_path.expanduser().resolve()
            if narrated_task_path is not None
            else resolved_task_path
        )
        narrated_contract = load_windows_task(resolved_narrated_task)
        _validate_conditions(narrated_contract, ("narrated_compiled",))
        narrated_experience = narrated_contract.semantic_experience
        if narrated_experience is None or narrated_experience.narration_kind != "human":
            raise RuntimeError(
                "The narrated_compiled condition requires an experience compiled with human narration"
            )
        if "compiled" in selected_conditions and (
            _source_trace_path(contract).read_bytes()
            != _source_trace_path(narrated_contract).read_bytes()
        ):
            raise RuntimeError(
                "Compiled and narrated_compiled conditions must derive from the same Trace"
            )
        condition_task_paths["narrated_compiled"] = resolved_narrated_task
    reset_payload = _load_reset_spec(
        reset_spec,
        expected_task_ids=_task_ids(client_root, json_name),
    )
    installed_reset_spec = client_root / "trace2task_reset_spec.json"
    installed_reset_spec.write_text(
        json.dumps(reset_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    gateway = _require_running_vm(distro, container)
    bridge_url = f"http://{gateway}:{relay_port}"
    experiment_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    relative_results_root = PurePosixPath(
        "results", "trace2task-experiments", experiment_id
    )
    host_results_root = client_root.joinpath(*relative_results_root.parts)
    host_results_root.mkdir(parents=True)
    report_root = output_root.expanduser().resolve() / experiment_id
    manifest_path = host_results_root / "experiment.json"
    manifest: dict[str, Any] = {
        "schema_version": "0.1",
        "experiment_id": experiment_id,
        "task": str(resolved_task_path),
        "condition_tasks": {
            condition: str(path) for condition, path in condition_task_paths.items()
        },
        "conditions": list(selected_conditions),
        "repetitions": repetitions,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "reset_spec": str(reset_spec.expanduser().resolve()),
        "status": "running",
        "completed_episodes": [],
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    relay_pid = _ensure_relay(
        distro,
        bridge_port=bridge_port,
        relay_port=relay_port,
    )
    try:
        for condition in selected_conditions:
            for repetition in range(1, repetitions + 1):
                run_name = f"trace2task-{condition}-r{repetition:02d}"
                result_dir = str(relative_results_root / run_name)
                print(
                    f"[WAA experiment] {condition} repetition "
                    f"{repetition}/{repetitions}: resetting and running."
                )
                server = create_waa_bridge_server(
                    condition_task_paths[condition],
                    experience_mode=condition,
                    model=model,
                    reasoning_effort=reasoning_effort,
                    token=token,
                    host="127.0.0.1",
                    port=bridge_port,
                    codex_bin=codex_bin,
                    plan_horizon=plan_horizon,
                )
                server_thread = threading.Thread(
                    target=server.serve_forever,
                    name=f"trace2task-waa-{condition}-r{repetition:02d}",
                    daemon=True,
                )
                server_thread.start()
                try:
                    _check_bridge_health(
                        distro,
                        container,
                        bridge_url=bridge_url,
                        condition=condition,
                    )
                    _run(
                        _client_run_command(
                            distro=distro,
                            container=container,
                            bridge_url=bridge_url,
                            token=token,
                            reset_spec_path="/client/trace2task_reset_spec.json",
                            result_dir=str(PurePosixPath("/client") / result_dir),
                            json_name=json_name,
                        )
                    )
                finally:
                    server.shutdown()
                    server_thread.join(timeout=5)
                    server.server_close()
                    server.bridge.close()
                manifest["completed_episodes"].append(run_name)
                manifest_path.write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
        manifest["status"] = "completed"
        report = write_waa_report(host_results_root, report_root)
        manifest["report"] = report
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as error:
        manifest["status"] = "failed"
        manifest["error"] = f"{type(error).__name__}: {error}"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        raise
    finally:
        _stop_relay(distro, relay_pid)
    return {
        "mode": "waa_experiment",
        "experiment_id": experiment_id,
        "status": manifest["status"],
        "conditions": list(selected_conditions),
        "repetitions": repetitions,
        "results_root": str(host_results_root),
        "report_path": str(report_root / "waa-ablation-report.json"),
        "manifest_path": str(manifest_path),
    }
