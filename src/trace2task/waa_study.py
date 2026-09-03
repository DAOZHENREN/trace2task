from __future__ import annotations

import csv
import hashlib
import json
import random
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from trace2task.windows_agent import WINDOWS_EXPERIENCE_MODES

STUDY_SCHEMA_VERSION = "0.1"
DEFAULT_STUDY_OUTPUT = Path("evaluations/windows-agent-arena/studies")


def _load_document(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    payload = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    if not isinstance(payload, dict):
        raise TypeError("WAA study spec must be an object")
    if payload.get("schema_version") != STUDY_SCHEMA_VERSION:
        raise ValueError(
            f"WAA study spec schema_version must be {STUDY_SCHEMA_VERSION!r}"
        )
    return payload


def _positive_int(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise TypeError(f"{field} must be a positive integer")
    return value


def _string_list(value: object, *, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise TypeError(f"{field} must be a list of non-empty strings")
    return tuple(dict.fromkeys(item.strip() for item in value))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    if resolved.is_file():
        return {
            "path": str(resolved),
            "kind": "file",
            "sha256": _sha256_file(resolved),
            "file_count": 1,
            "size_bytes": resolved.stat().st_size,
        }
    if not resolved.is_dir():
        raise FileNotFoundError(f"Study artifact was not found: {resolved}")
    digest = hashlib.sha256()
    file_count = 0
    size_bytes = 0
    for child in sorted(item for item in resolved.rglob("*") if item.is_file()):
        relative = child.relative_to(resolved).as_posix()
        file_hash = _sha256_file(child)
        size = child.stat().st_size
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(file_hash.encode("ascii"))
        digest.update(b"\n")
        file_count += 1
        size_bytes += size
    return {
        "path": str(resolved),
        "kind": "directory",
        "sha256": digest.hexdigest(),
        "file_count": file_count,
        "size_bytes": size_bytes,
    }


def _git_state(project_root: Path) -> dict[str, Any]:
    def run(*args: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["git", *args],
            cwd=project_root,
            check=False,
            capture_output=True,
        )

    commit = run("rev-parse", "HEAD")
    if commit.returncode != 0:
        return {"commit": None, "dirty": None, "status": "not_a_git_checkout"}
    status = run("status", "--short", "--untracked-files=all")
    diff = run("diff", "--binary", "HEAD")
    status_bytes = status.stdout if status.returncode == 0 else b""
    diff_bytes = diff.stdout if diff.returncode == 0 else b""
    status_text = status_bytes.decode("utf-8", errors="replace")
    return {
        "commit": commit.stdout.decode("ascii", errors="replace").strip(),
        "dirty": bool(status_text.strip()),
        "status": status_text.splitlines(),
        "tracked_diff_sha256": hashlib.sha256(diff_bytes).hexdigest(),
    }


def _resolve(base: Path, raw: object) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = Path(raw.strip()).expanduser()
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def _task_list_contains(path: Path, *, app: str, task_id: str) -> bool:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"WAA task list must be an object: {path}")
    values = payload.get(app)
    return isinstance(values, list) and task_id in values


def _waa_example_path(client_root: Path, *, app: str, task_id: str) -> Path:
    app_root = client_root / "evaluation_examples_windows" / "examples" / app
    exact = app_root / f"{task_id}.json"
    if exact.is_file():
        return exact
    matches = sorted(app_root.glob(f"{task_id}*.json")) if app_root.is_dir() else []
    if len(matches) == 1:
        return matches[0]
    raise FileNotFoundError(
        f"WAA example JSON for app={app!r}, task_id={task_id!r} was not found"
    )


def _applies(condition: dict[str, Any], task: dict[str, Any]) -> bool:
    required_tags = _string_list(
        condition.get("include_task_tags"),
        field=f"condition {condition.get('id')!r} include_task_tags",
    )
    if not required_tags:
        return True
    task_tags = set(_string_list(task.get("tags"), field="task tags"))
    return bool(task_tags.intersection(required_tags))


def _powershell_quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _episode_command(
    episode: dict[str, Any],
    *,
    waa_root: Path,
    model: str,
    reasoning_effort: str,
    output_root: Path,
) -> str:
    mode = episode["runtime_mode"]
    selected_task = Path(episode["taskpack"])
    execution_task = Path(episode["execution_taskpack"])
    command = [
        "uv run --no-sync trace2task waa experiment",
        f"--waa-root {_powershell_quote(waa_root)}",
        f"--task {_powershell_quote(selected_task if mode in {'compiled', 'trace'} else execution_task)}",
        f"--reset-spec {_powershell_quote(episode['reset_spec'])}",
        f"--conditions {mode}",
        "--repetitions 1",
        f"--model {model}",
        f"--reasoning-effort {reasoning_effort}",
        f"--json-name {_powershell_quote(episode['waa_json_name'])}",
        f"--output {_powershell_quote(output_root / 'episodes' / episode['episode_id'])}",
    ]
    if mode == "narrated_compiled":
        command.append(f"--narrated-task {_powershell_quote(selected_task)}")
    elif mode == "feedback":
        command.append(f"--feedback-task {_powershell_quote(selected_task)}")
    if episode["condition_id"] == "auto_compiled":
        command.append("--allow-automatic-compiler-draft")
    return " `\n  ".join(command)


def _issue(
    issues: list[dict[str, Any]],
    code: str,
    message: str,
    *,
    task_id: str | None = None,
    condition_id: str | None = None,
) -> None:
    payload: dict[str, Any] = {"code": code, "message": message}
    if task_id is not None:
        payload["task_id"] = task_id
    if condition_id is not None:
        payload["condition_id"] = condition_id
    issues.append(payload)


def prepare_waa_study(
    spec_path: Path,
    waa_root: Path,
    *,
    output_root: Path = DEFAULT_STUDY_OUTPUT,
    strict: bool = False,
    prepared_at: datetime | None = None,
) -> dict[str, Any]:
    """Freeze a WAA study protocol and emit a deterministic, auditable run schedule."""

    source = spec_path.expanduser().resolve()
    spec = _load_document(source)
    study_id = spec.get("study_id")
    if not isinstance(study_id, str) or not study_id.strip():
        raise TypeError("WAA study_id must be a non-empty string")
    study_id = study_id.strip()
    seed = _positive_int(spec.get("seed"), field="seed")
    repetitions = _positive_int(spec.get("repetitions"), field="repetitions")
    model = str(spec.get("model") or "gpt-5.6-terra")
    reasoning_effort = str(spec.get("reasoning_effort") or "low")
    path_base = _resolve(source.parent, spec.get("path_base") or ".")
    assert path_base is not None

    raw_conditions = spec.get("conditions")
    raw_tasks = spec.get("tasks")
    targets = spec.get("targets")
    if not isinstance(raw_conditions, list) or not raw_conditions:
        raise TypeError("WAA study conditions must be a non-empty list")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise TypeError("WAA study tasks must be a non-empty list")
    if not isinstance(targets, dict):
        raise TypeError("WAA study targets must be an object")

    conditions: list[dict[str, Any]] = []
    condition_ids: set[str] = set()
    for raw in raw_conditions:
        if not isinstance(raw, dict):
            raise TypeError("Each WAA study condition must be an object")
        condition_id = raw.get("id")
        mode = raw.get("runtime_mode")
        role = raw.get("taskpack_role")
        if not isinstance(condition_id, str) or not condition_id.strip():
            raise TypeError("Each WAA study condition needs a non-empty id")
        if condition_id in condition_ids:
            raise ValueError(f"Duplicate WAA study condition id: {condition_id}")
        if mode not in WINDOWS_EXPERIENCE_MODES:
            raise ValueError(f"Unknown runtime_mode for {condition_id!r}: {mode!r}")
        if not isinstance(role, str) or not role.strip():
            raise TypeError(f"Condition {condition_id!r} needs taskpack_role")
        condition_ids.add(condition_id)
        conditions.append(raw)

    root = waa_root.expanduser().resolve()
    client_root = root / "src" / "win-arena-container" / "client"
    if not client_root.is_dir():
        raise FileNotFoundError(f"WAA client directory was not found: {client_root}")

    issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    artifacts: dict[str, dict[str, Any]] = {}

    def freeze(path: Path | None) -> dict[str, Any] | None:
        if path is None or not path.exists():
            return None
        key = str(path.resolve()).casefold()
        if key not in artifacts:
            artifacts[key] = _fingerprint(path)
        return artifacts[key]

    freeze(source)
    tasks: list[dict[str, Any]] = []
    task_ids: set[str] = set()
    episodes: list[dict[str, Any]] = []
    cost_keys = {
        key
        for condition in conditions
        for key in _string_list(
            condition.get("human_cost_keys"),
            field=f"condition {condition.get('id')!r} human_cost_keys",
        )
    }

    for raw_task in raw_tasks:
        if not isinstance(raw_task, dict):
            raise TypeError("Each WAA study task must be an object")
        task_id = raw_task.get("id")
        app = raw_task.get("app")
        family = raw_task.get("family_id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise TypeError("Each WAA study task needs a non-empty id")
        if task_id in task_ids:
            raise ValueError(f"Duplicate WAA study task id: {task_id}")
        if not isinstance(app, str) or not app.strip():
            raise TypeError(f"Task {task_id!r} needs an app")
        if not isinstance(family, str) or not family.strip():
            raise TypeError(f"Task {task_id!r} needs a family_id")
        task_ids.add(task_id)
        status = str(raw_task.get("status") or "planned")
        taskpacks = raw_task.get("taskpacks")
        if not isinstance(taskpacks, dict):
            taskpacks = {}
        resolved_taskpacks = {
            role: _resolve(path_base, value) for role, value in taskpacks.items()
        }
        execution_task = resolved_taskpacks.get("execution")
        human_cost = raw_task.get("human_cost_seconds")
        if not isinstance(human_cost, dict):
            human_cost = {}

        waa_task_id = raw_task.get("waa_task_id")
        waa_json_name = raw_task.get("waa_json_name")
        reset_path = _resolve(path_base, raw_task.get("reset_spec"))
        waa_list_path = (
            client_root.joinpath(*Path(waa_json_name).parts)
            if isinstance(waa_json_name, str) and waa_json_name.strip()
            else None
        )
        example_path: Path | None = None
        task_blockers: list[str] = []
        if status != "ready":
            _issue(
                issues,
                "task_not_ready",
                f"Task is {status!r}; finish its WAA definition and demonstration artifacts.",
                task_id=task_id,
            )
            if status == "planned":
                task_blockers.append("task_not_ready")
        if not isinstance(waa_task_id, str) or not waa_task_id.strip():
            task_blockers.append("waa_task_id_missing")
            _issue(
                issues,
                "waa_task_id_missing",
                "A WAA task id has not been assigned.",
                task_id=task_id,
            )
        if waa_list_path is None or not waa_list_path.is_file():
            task_blockers.append("waa_task_list_missing")
            _issue(
                issues,
                "waa_task_list_missing",
                "The WAA task-list JSON is missing.",
                task_id=task_id,
            )
        elif isinstance(waa_task_id, str):
            freeze(waa_list_path)
            if not _task_list_contains(waa_list_path, app=app, task_id=waa_task_id):
                task_blockers.append("waa_task_not_listed")
                _issue(
                    issues,
                    "waa_task_not_listed",
                    "The WAA task-list JSON does not contain this app/task id pair.",
                    task_id=task_id,
                )
            try:
                example_path = _waa_example_path(
                    client_root, app=app, task_id=waa_task_id
                )
            except FileNotFoundError as error:
                task_blockers.append("waa_example_missing")
                _issue(issues, "waa_example_missing", str(error), task_id=task_id)
            else:
                freeze(example_path)
        if reset_path is None or not reset_path.is_file():
            task_blockers.append("reset_spec_missing")
            _issue(
                issues,
                "reset_spec_missing",
                "The reset spec is missing.",
                task_id=task_id,
            )
        else:
            freeze(reset_path)

        if raw_task.get("parameterized") is True:
            demonstration_variant = raw_task.get("demonstration_variant")
            evaluation_variant = raw_task.get("evaluation_variant")
            if (
                not isinstance(demonstration_variant, str)
                or not isinstance(evaluation_variant, str)
                or demonstration_variant == evaluation_variant
            ):
                _issue(
                    issues,
                    "held_out_variant_missing",
                    "Parameterized tasks need distinct demonstration_variant and evaluation_variant ids.",
                    task_id=task_id,
                )

        applicable_cost_keys = {
            key
            for condition in conditions
            if _applies(condition, raw_task)
            for key in _string_list(
                condition.get("human_cost_keys"),
                field=f"condition {condition.get('id')!r} human_cost_keys",
            )
        }
        for key in sorted(applicable_cost_keys):
            value = human_cost.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                _issue(
                    issues,
                    "human_cost_missing",
                    f"Record human_cost_seconds.{key} before formal evaluation.",
                    task_id=task_id,
                )

        auto_path = resolved_taskpacks.get("auto_compiled")
        reviewed_path = resolved_taskpacks.get("reviewed_compiled")
        if auto_path is not None and reviewed_path is not None:
            auto_fingerprint = freeze(auto_path)
            reviewed_fingerprint = freeze(reviewed_path)
            if (
                auto_fingerprint is not None
                and reviewed_fingerprint is not None
                and auto_fingerprint["sha256"] == reviewed_fingerprint["sha256"]
            ):
                _issue(
                    issues,
                    "compile_variants_identical",
                    "Automatic and human-reviewed compile artifacts must be separate snapshots.",
                    task_id=task_id,
                )

        execution_trace = (
            execution_task.parent / "reference" / "trace.jsonl"
            if execution_task is not None
            else None
        )
        mismatch_task = resolved_taskpacks.get("mismatched_trace")
        mismatch_trace = (
            mismatch_task.parent / "reference" / "trace.jsonl"
            if mismatch_task is not None
            else None
        )
        mismatched_trace_invalid = (
            execution_trace is not None
            and mismatch_trace is not None
            and execution_trace.is_file()
            and mismatch_trace.is_file()
            and _sha256_file(execution_trace) == _sha256_file(mismatch_trace)
        )
        if mismatched_trace_invalid:
            _issue(
                issues,
                "mismatched_trace_is_same",
                "The negative-control Trace is identical to the matched Trace.",
                task_id=task_id,
            )

        task_record = {
            "id": task_id,
            "app": app,
            "family_id": family,
            "split": str(raw_task.get("split") or "test"),
            "parameterized": raw_task.get("parameterized") is True,
            "demonstration_variant": raw_task.get("demonstration_variant"),
            "evaluation_variant": raw_task.get("evaluation_variant"),
            "status": status,
            "tags": list(_string_list(raw_task.get("tags"), field="task tags")),
            "waa_task_id": waa_task_id,
            "waa_json_name": waa_json_name,
            "waa_example": str(example_path) if example_path is not None else None,
            "reset_spec": str(reset_path) if reset_path is not None else None,
            "taskpacks": {
                role: str(path) if path is not None else None
                for role, path in sorted(resolved_taskpacks.items())
            },
            "human_cost_seconds": human_cost,
            "blockers": sorted(set(task_blockers)),
        }
        tasks.append(task_record)

        for condition in conditions:
            if not _applies(condition, raw_task):
                continue
            condition_id = str(condition["id"])
            role = str(condition["taskpack_role"])
            selected_taskpack = resolved_taskpacks.get(role)
            blockers = list(task_blockers)
            if role == "mismatched_trace" and mismatched_trace_invalid:
                blockers.append("mismatched_trace_is_same")
            if selected_taskpack is None or not selected_taskpack.is_file():
                blockers.append(f"taskpack_missing:{role}")
                _issue(
                    issues,
                    "taskpack_missing",
                    f"Condition requires taskpacks.{role}.",
                    task_id=task_id,
                    condition_id=condition_id,
                )
            else:
                freeze(selected_taskpack.parent)
            if execution_task is None or not execution_task.is_file():
                blockers.append("execution_taskpack_missing")
                _issue(
                    issues,
                    "execution_taskpack_missing",
                    "Every condition needs taskpacks.execution for the task contract.",
                    task_id=task_id,
                    condition_id=condition_id,
                )
            else:
                freeze(execution_task.parent)
            for repetition in range(1, repetitions + 1):
                episodes.append(
                    {
                        "episode_id": f"{task_id}--{condition_id}--r{repetition:02d}",
                        "task_id": task_id,
                        "condition_id": condition_id,
                        "runtime_mode": condition["runtime_mode"],
                        "taskpack_role": role,
                        "taskpack": (
                            str(selected_taskpack) if selected_taskpack is not None else None
                        ),
                        "execution_taskpack": (
                            str(execution_task) if execution_task is not None else None
                        ),
                        "waa_task_id": waa_task_id,
                        "waa_json_name": waa_json_name,
                        "reset_spec": str(reset_path) if reset_path is not None else None,
                        "repetition": repetition,
                        "ready": not blockers,
                        "blockers": sorted(set(blockers)),
                    }
                )

    target_apps = _positive_int(targets.get("apps"), field="targets.apps")
    target_tasks = _positive_int(
        targets.get("task_instances"), field="targets.task_instances"
    )
    target_families = _positive_int(
        targets.get("parameterized_families"),
        field="targets.parameterized_families",
    )
    actual_apps = len({task["app"] for task in tasks})
    actual_tasks = len(tasks)
    actual_families = len(
        {task["family_id"] for task in tasks if task["parameterized"]}
    )
    for code, actual, target, label in (
        ("target_apps_not_met", actual_apps, target_apps, "apps"),
        ("target_tasks_not_met", actual_tasks, target_tasks, "task instances"),
        (
            "target_families_not_met",
            actual_families,
            target_families,
            "parameterized families",
        ),
    ):
        if actual < target:
            _issue(issues, code, f"Study has {actual} {label}; target is {target}.")

    rng = random.Random(seed)
    rng.shuffle(episodes)
    for index, episode in enumerate(episodes, start=1):
        episode["order"] = index

    project_root = path_base
    while project_root.parent != project_root and not (project_root / ".git").exists():
        project_root = project_root.parent
    git = _git_state(project_root)
    if git.get("dirty"):
        warnings.append(
            {
                "code": "dirty_git_checkout",
                "message": "Formal runs should use a clean committed checkout.",
            }
        )
    elif git.get("dirty") is None:
        warnings.append(
            {
                "code": "git_state_unavailable",
                "message": "The source commit could not be frozen.",
            }
        )

    ready_episodes = sum(1 for episode in episodes if episode["ready"])
    planned_task_ids = {task["id"] for task in tasks if task["status"] == "planned"}
    issues = [
        issue
        for issue in issues
        if issue.get("task_id") not in planned_task_ids
        or issue["code"] == "task_not_ready"
    ]
    unique_issues = {
        json.dumps(issue, ensure_ascii=False, sort_keys=True): issue for issue in issues
    }
    issues = list(unique_issues.values())
    now = prepared_at or datetime.now(UTC)
    destination = output_root.expanduser().resolve() / study_id
    destination.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": STUDY_SCHEMA_VERSION,
        "study_id": study_id,
        "title": str(spec.get("title") or study_id),
        "prepared_at": now.isoformat(),
        "source_spec": {
            "path": str(source),
            "sha256": _sha256_file(source),
        },
        "source_code": git,
        "protocol": {
            "seed": seed,
            "repetitions": repetitions,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "conditions": conditions,
            "primary_metrics": list(
                _string_list(spec.get("primary_metrics"), field="primary_metrics")
            ),
            "analysis_plan": spec.get("analysis_plan") or {},
            "human_cost_policy": spec.get("human_cost_policy") or {},
        },
        "targets": {
            "apps": target_apps,
            "task_instances": target_tasks,
            "parameterized_families": target_families,
        },
        "coverage": {
            "apps": actual_apps,
            "task_instances": actual_tasks,
            "parameterized_families": actual_families,
        },
        "readiness": {
            "ready": not issues,
            "ready_episodes": ready_episodes,
            "total_episodes": len(episodes),
            "blocked_episodes": len(episodes) - ready_episodes,
            "issues": issues,
            "warnings": warnings,
        },
        "tasks": tasks,
        "schedule": episodes,
        "artifacts": sorted(artifacts.values(), key=lambda item: item["path"].casefold()),
    }
    manifest_path = destination / "study-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    schedule_path = destination / "episode-schedule.csv"
    with schedule_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=(
                "order",
                "episode_id",
                "task_id",
                "condition_id",
                "runtime_mode",
                "repetition",
                "ready",
                "blockers",
            ),
        )
        writer.writeheader()
        for episode in episodes:
            writer.writerow(
                {
                    **{key: episode[key] for key in writer.fieldnames if key != "blockers"},
                    "blockers": ";".join(episode["blockers"]),
                }
            )

    cost_path = destination / "human-costs.csv"
    with cost_path.open("w", encoding="utf-8-sig", newline="") as stream:
        fields = ["task_id", "app", "family_id", *sorted(cost_keys)]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for task in tasks:
            row = {key: "" for key in fields}
            row.update(
                task_id=task["id"], app=task["app"], family_id=task["family_id"]
            )
            row.update(task["human_cost_seconds"])
            writer.writerow(row)

    runnable = [episode for episode in episodes if episode["ready"]]
    script_path = destination / "run-ready-episodes.ps1"
    manifest_sha256 = _sha256_file(manifest_path)
    script_lines = [
        "param([string]$ResumeRunRoot = '')",
        "$ErrorActionPreference = 'Stop'",
        "$PSNativeCommandUseErrorActionPreference = $true",
        f"# Frozen study: {study_id}",
        f"# Ready episodes: {len(runnable)}/{len(episodes)}",
        "# Execute only after reviewing study-manifest.json and resetting the WAA VM.",
        "$studyRunId = [DateTimeOffset]::UtcNow.ToString('yyyyMMdd-HHmmss-fffffff')",
        (
            "$studyRunRoot = if ($ResumeRunRoot) { "
            "(Resolve-Path -LiteralPath $ResumeRunRoot).Path } else { Join-Path "
            f"{_powershell_quote(destination / 'study-runs')} $studyRunId }}"
        ),
        "$null = New-Item -ItemType Directory -Force -Path $studyRunRoot",
        "$ledgerPath = Join-Path $studyRunRoot 'study-run.jsonl'",
        "$summaryPath = Join-Path $studyRunRoot 'study-run-summary.json'",
        "if (-not (Test-Path -LiteralPath $summaryPath)) {",
        (
            "  [pscustomobject]@{schema_version='0.1'; study_id="
            f"{_powershell_quote(study_id)}; study_manifest_sha256="
            f"{_powershell_quote(manifest_sha256)}; started_at="
            "[DateTimeOffset]::UtcNow.ToString('o'); total_episodes="
            f"{len(runnable)}}} | ConvertTo-Json | Set-Content -Encoding UTF8 $summaryPath"
        ),
        "}",
        "$completedEpisodeIds = @()",
        "if (Test-Path -LiteralPath $ledgerPath) {",
        "  $completedEpisodeIds = @(Get-Content $ledgerPath -Encoding UTF8 | ConvertFrom-Json | Where-Object status -eq 'completed' | Select-Object -ExpandProperty episode_id)",
        "}",
        "function Update-StudyRunSummary([string]$Status) {",
        "  $episodeRows = @(if (Test-Path $ledgerPath) { Get-Content $ledgerPath -Encoding UTF8 | ConvertFrom-Json })",
        "  $summary = Get-Content $summaryPath -Raw -Encoding UTF8 | ConvertFrom-Json",
        "  $summary | Add-Member -Force -NotePropertyName status -NotePropertyValue $Status",
        "  $summary | Add-Member -Force -NotePropertyName updated_at -NotePropertyValue ([DateTimeOffset]::UtcNow.ToString('o'))",
        "  $summary | Add-Member -Force -NotePropertyName completed -NotePropertyValue @($episodeRows | Where-Object status -eq 'completed').Count",
        "  $summary | Add-Member -Force -NotePropertyName infrastructure_failed -NotePropertyValue @($episodeRows | Where-Object status -eq 'infrastructure_failed').Count",
        "  if ($Status -in @('completed', 'aborted')) {",
        "    $summary | Add-Member -Force -NotePropertyName finished_at -NotePropertyValue ([DateTimeOffset]::UtcNow.ToString('o'))",
        "  }",
        "  $summary | ConvertTo-Json | Set-Content -Encoding UTF8 $summaryPath",
        "}",
        "",
    ]
    for episode in runnable:
        episode_id = str(episode["episode_id"])
        episode_log_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", episode_id) + ".log"
        command = _episode_command(
            episode,
            waa_root=root,
            model=model,
            reasoning_effort=reasoning_effort,
            output_root=destination,
        )
        indented_command = "\n".join(f"  {line}" for line in command.splitlines())
        script_lines.extend(
            [
                f"# {episode['order']}: {episode_id}",
                f"$episodeId = {_powershell_quote(episode_id)}",
                f"$episodeOrder = {episode['order']}",
                f"$episodeLog = Join-Path $studyRunRoot {_powershell_quote(episode_log_name)}",
                "if ($completedEpisodeIds -contains $episodeId) {",
                "  Write-Host \"[Study $episodeOrder/"
                + f"{len(runnable)}] already completed; skipping $episodeId\"",
                "} else {",
                "$episodeStarted = [DateTimeOffset]::UtcNow",
                "$episodeStatus = 'completed'",
                "$episodeError = $null",
                "Write-Host \"[Study $episodeOrder/"
                + f"{len(runnable)}] $episodeId\"",
                "try {",
                "  & {",
                indented_command,
                "  } 2>&1 | Tee-Object -FilePath $episodeLog",
                "  if ($LASTEXITCODE -ne 0) {",
                "    throw \"Episode command exited with code $LASTEXITCODE\"",
                "  }",
                "} catch {",
                "  $episodeStatus = 'infrastructure_failed'",
                "  $episodeError = $_.Exception.Message",
                "  ($_ | Out-String) | Add-Content -Encoding UTF8 $episodeLog",
                "  Write-Warning \"Infrastructure/program failure; aborting study: $episodeId :: $episodeError\"",
                "}",
                "$episodeFinished = [DateTimeOffset]::UtcNow",
                (
                    "[pscustomobject]@{schema_version='0.1'; episode_id=$episodeId; "
                    "order=$episodeOrder; status=$episodeStatus; started_at="
                    "$episodeStarted.ToString('o'); finished_at=$episodeFinished.ToString('o'); "
                    "elapsed_seconds=[math]::Round(($episodeFinished-$episodeStarted).TotalSeconds,3); "
                    "error=$episodeError; log_path=$episodeLog} | ConvertTo-Json -Compress | "
                    "Add-Content -Encoding UTF8 $ledgerPath"
                ),
                "if ($episodeStatus -ne 'completed') {",
                "  Update-StudyRunSummary 'aborted'",
                "  throw \"Study aborted after infrastructure/program failure in $episodeId\"",
                "}",
                "}",
                "",
            ]
        )
    script_lines.extend(
        [
            "Update-StudyRunSummary 'completed'",
            "Write-Host \"Study run complete: $summaryPath\"",
            "",
        ]
    )
    script_path.write_text("\n".join(script_lines), encoding="utf-8")

    report_path = destination / "README.md"
    report_lines = [
        f"# {manifest['title']}",
        "",
        f"- Formal readiness: **{'READY' if not issues else 'NOT READY'}**",
        (
            f"- Coverage: {actual_tasks}/{target_tasks} tasks, "
            f"{actual_apps}/{target_apps} apps, "
            f"{actual_families}/{target_families} parameterized families"
        ),
        (
            f"- Schedule: {ready_episodes}/{len(episodes)} runnable episodes, "
            f"{repetitions} repetitions, seed {seed}"
        ),
        f"- Model: `{model}` / `{reasoning_effort}`",
        "",
        "## Readiness issues",
        "",
    ]
    if issues:
        report_lines.extend(
            f"- `{issue['code']}`: {issue['message']}"
            + (f" (task `{issue['task_id']}`)" if issue.get("task_id") else "")
            for issue in issues
        )
    else:
        report_lines.append("- None.")
    report_lines.extend(["", "## Reproducibility warnings", ""])
    if warnings:
        report_lines.extend(
            f"- `{warning['code']}`: {warning['message']}" for warning in warnings
        )
    else:
        report_lines.append("- None.")
    report_lines.extend(
        [
            "",
            "## Files",
            "",
            "- `study-manifest.json`: frozen protocol, source hashes, blockers, and schedule",
            "- `episode-schedule.csv`: deterministic interleaved episode order",
            "- `human-costs.csv`: demonstration, review, narration, and feedback time template",
            "- `run-ready-episodes.ps1`: commands for cells whose artifacts are complete",
            "- `study-runs/<run-id>/study-run.jsonl`: append-only per-episode status and timing ledger",
            "- `study-runs/<run-id>/*.log`: complete stdout/stderr for every scheduled episode",
            "",
            "> Do not report this as a formal result until readiness is READY and the checkout is clean.",
            "",
        ]
    )
    report_path.write_text("\n".join(report_lines), encoding="utf-8")

    if strict and issues:
        raise RuntimeError(
            f"WAA study is not ready: {len(issues)} issue(s). See {manifest_path}"
        )
    return {
        "mode": "waa_study_plan",
        "study_id": study_id,
        "ready": not issues,
        "ready_episodes": ready_episodes,
        "total_episodes": len(episodes),
        "issue_count": len(issues),
        "manifest_path": str(manifest_path),
        "schedule_path": str(schedule_path),
        "human_cost_path": str(cost_path),
        "run_script_path": str(script_path),
        "report_path": str(report_path),
    }
