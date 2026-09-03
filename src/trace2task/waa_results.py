from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Any

import yaml

from trace2task.windows_agent import WINDOWS_EXPERIENCE_MODES

_ELAPSED_PATTERN = re.compile(r"Elapsed Time:\s*([^<\r\n]+)")
_PLAN_STEP_PATTERN = re.compile(r"plan_result-step_([^_.]+)(?:_|\.txt)")
_REPETITION_PATTERN = re.compile(r"^trace2task-(?P<condition>.+)-r(?P<repetition>\d+)$")

WAA_CONDITION_LABELS = {
    "baseline": "无 Trace 基线",
    "trace": "原始 Trace",
    "compiled": "Compiler 经验",
    "narrated_compiled": "人工讲解 Compiler",
    "feedback": "人工反馈经验",
}
WAA_FEEDBACK_IMPORT_VERSION = 2


@dataclass(frozen=True)
class WaaEpisodeResult:
    condition: str
    path: str
    score: float
    completed: bool
    actions: int
    plan_calls: int
    model_roundtrip_seconds: float
    elapsed_seconds: float | None


def _filesystem_path(path: Path) -> Path:
    """Use Win32's extended path form for deeply nested WAA artifacts."""

    if os.name != "nt":
        return path
    value = str(path.resolve())
    if value.startswith("\\\\?\\"):
        return Path(value)
    if value.startswith("\\\\"):
        return Path(f"\\\\?\\UNC\\{value[2:]}")
    return Path(f"\\\\?\\{value}")


def _read_text(path: Path, *, errors: str | None = None) -> str:
    return _filesystem_path(path).read_text(encoding="utf-8", errors=errors)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(_read_text(path, errors="replace"))
    if not isinstance(payload, dict):
        raise TypeError(f"Expected a JSON object: {path}")
    return payload


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with (
        _filesystem_path(source).open("rb") as input_file,
        destination.open("wb") as output_file,
    ):
        shutil.copyfileobj(input_file, output_file)


def _elapsed_seconds(path: Path) -> float | None:
    html_path = path / "traj.html"
    if not html_path.is_file():
        return None
    match = _ELAPSED_PATTERN.search(_read_text(html_path, errors="replace"))
    if match is None:
        return None
    parts = match.group(1).strip().split(":")
    if len(parts) != 3:
        return None
    hours, minutes, seconds = parts
    return timedelta(
        hours=int(hours),
        minutes=int(minutes),
        seconds=float(seconds),
    ).total_seconds()


def _trajectory_actions(path: Path) -> int:
    trajectory = path / "traj.jsonl"
    if not trajectory.is_file():
        return 0
    actions = 0
    for line in _read_text(trajectory, errors="replace").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        action = payload.get("action") if isinstance(payload, dict) else None
        if isinstance(action, str) and action not in {"DONE", "FAIL"}:
            actions += 1
    return actions


def _plan_timings(path: Path) -> tuple[int, float]:
    plans = 0
    model_ms = 0.0
    plans_by_step: dict[str, Path] = {}
    for plan_path in sorted(_filesystem_path(path).glob("plan_result*.txt")):
        match = _PLAN_STEP_PATTERN.search(plan_path.name)
        step = match.group(1) if match is not None else plan_path.name
        plans_by_step.setdefault(step, plan_path)
    for plan_path in plans_by_step.values():
        try:
            payload = json.loads(_read_text(plan_path))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(payload, dict):
            continue
        plans += 1
        timing = payload.get("timing")
        if isinstance(timing, dict):
            value = timing.get("model_roundtrip_ms", 0)
            if isinstance(value, int | float):
                model_ms += float(value)
    return plans, model_ms / 1000


def _condition_episode_dirs(results_root: Path, condition: str) -> list[Path]:
    roots = sorted(
        path
        for path in results_root.glob(f"trace2task-{condition}*")
        if path.is_dir()
    )
    episodes: set[Path] = set()
    for root in roots:
        episodes.update(path.parent for path in root.rglob("traj.jsonl"))
        episodes.update(path.parent for path in root.rglob("result.txt"))
    return sorted(episodes)


def _episode_repetition(episode_path: Path, condition: str) -> int:
    for part in reversed(episode_path.parts):
        match = _REPETITION_PATTERN.match(part)
        if match is not None and match.group("condition") == condition:
            return int(match.group("repetition"))
    return 1


def _plan_step(raw_path: object, fallback: int) -> str:
    if isinstance(raw_path, str):
        match = _PLAN_STEP_PATTERN.search(Path(raw_path).name)
        if match is not None:
            return match.group(1)
    return f"unplanned-{fallback}"


def _episode_rows(episode_path: Path) -> list[dict[str, Any]]:
    trajectory_path = episode_path / "traj.jsonl"
    rows: list[dict[str, Any]] = []
    for line in _read_text(trajectory_path, errors="replace").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _load_plan(episode_path: Path, raw_path: object) -> dict[str, Any]:
    if not isinstance(raw_path, str) or not raw_path:
        return {}
    path = (episode_path / raw_path).resolve()
    if path.parent != episode_path.resolve() or not _filesystem_path(path).is_file():
        return {}
    try:
        return _read_json(path)
    except (OSError, TypeError, json.JSONDecodeError):
        return {}


def _source_screenshot(episode_path: Path, row: dict[str, Any]) -> Path | None:
    raw_path = row.get("screenshot")
    if not isinstance(raw_path, str) or not raw_path:
        return None
    path = (episode_path / raw_path).resolve()
    if path.parent != episode_path.resolve() or not _filesystem_path(path).is_file():
        return None
    return path


def _materialize_episode_evidence(
    episode_path: Path,
    candidate_dir: Path,
) -> tuple[dict[str, Any], int]:
    rows = _episode_rows(episode_path)
    if not rows:
        raise ValueError(f"WAA trajectory contains no readable rows: {episode_path}")

    instruction = next(
        (str(row["instruction"]) for row in rows if row.get("instruction")),
        "",
    )
    initial_source = _source_screenshot(episode_path, rows[0])
    groups: list[dict[str, Any]] = []
    groups_by_step: dict[str, dict[str, Any]] = {}
    previous_source = initial_source
    for row_index, row in enumerate(rows):
        action = row.get("action")
        if not isinstance(action, str):
            next_source = _source_screenshot(episode_path, row)
            if next_source is not None:
                previous_source = next_source
            continue
        step = _plan_step(row.get("plan_result"), row_index)
        group = groups_by_step.get(step)
        if group is None:
            plan = _load_plan(episode_path, row.get("plan_result"))
            group = {
                "step": step,
                "plan": plan,
                "actions": [],
                "decision": None,
                "before_source": previous_source,
                "after_source": None,
            }
            groups_by_step[step] = group
            groups.append(group)
        if action in {"DONE", "FAIL"}:
            group["decision"] = action
            after_source = _source_screenshot(episode_path, row)
            if after_source is not None:
                group["after_source"] = after_source
                previous_source = after_source
            continue
        group["actions"].append(action)
        after_source = _source_screenshot(episode_path, row)
        if after_source is not None:
            group["after_source"] = after_source
            previous_source = after_source

    copied: dict[str, str] = {}

    def copy_frame(source: Path | None, name: str) -> str | None:
        if source is None:
            return None
        key = str(source)
        if key in copied:
            return copied[key]
        suffix = source.suffix.casefold() or ".png"
        relative = f"frames/{name}{suffix}"
        _copy_file(source, candidate_dir / relative)
        copied[key] = relative
        return relative

    initial_frame = copy_frame(initial_source, "initial")
    review_rounds: list[dict[str, Any]] = []
    trace_events: list[dict[str, Any]] = []
    action_sequence = 0
    for round_index, group in enumerate(groups, start=1):
        plan = group["plan"] if isinstance(group["plan"], dict) else {}
        before_frame = copy_frame(group["before_source"], f"round-{round_index:02d}-before")
        after_frame = copy_frame(group["after_source"], f"round-{round_index:02d}-after")
        structured = plan.get("structured_actions")
        if not isinstance(structured, list):
            structured = []
        review_actions: list[dict[str, Any]] = []
        for action_index, raw_action in enumerate(group["actions"]):
            structured_action = (
                structured[action_index]
                if action_index < len(structured) and isinstance(structured[action_index], dict)
                else {}
            )
            skill = structured_action.get("skill")
            args = structured_action.get("args")
            review_actions.append(
                {
                    "index": action_sequence + 1,
                    "skill": skill if isinstance(skill, str) else None,
                    "args": args if isinstance(args, dict) else {},
                    "raw": raw_action,
                }
            )
            trace_events.append(
                {
                    "seq": action_sequence,
                    "type": "windows_action",
                    "frame": after_frame or before_frame,
                    "details": {
                        "parameterized_action": structured_action or raw_action,
                        "model_reason": plan.get("reason"),
                        "model_confidence": plan.get("confidence"),
                        "stage_id": plan.get("stage_id"),
                    },
                }
            )
            action_sequence += 1
        timing = plan.get("timing") if isinstance(plan.get("timing"), dict) else {}
        review_rounds.append(
            {
                "round": round_index,
                "step": group["step"],
                "stage_id": plan.get("stage_id"),
                "stage_goal": plan.get("stage_goal"),
                "reason": plan.get("reason"),
                "expected_end_state": plan.get("expected_end_state"),
                "confidence": plan.get("confidence"),
                "decision": group["decision"],
                "model_roundtrip_ms": timing.get("model_roundtrip_ms"),
                "before_frame": before_frame,
                "after_frame": after_frame,
                "actions": review_actions,
            }
        )

    final_frame = next(
        (round_data["after_frame"] for round_data in reversed(review_rounds) if round_data["after_frame"]),
        initial_frame,
    )
    review = {
        "schema_version": "0.1",
        "source": "windows_agent_arena",
        "instruction": instruction,
        "initial_frame": initial_frame,
        "final_frame": final_frame,
        "round_count": len(review_rounds),
        "action_count": action_sequence,
        "rounds": review_rounds,
    }
    (candidate_dir / "trajectory-review.json").write_text(
        json.dumps(review, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (candidate_dir / "trace.jsonl").write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in trace_events),
        encoding="utf-8",
    )
    return review, action_sequence


def _deleted_waa_candidate_ids(candidate_root: Path) -> set[str]:
    trash_root = candidate_root.parent / ".trash" / "candidates"
    if not trash_root.is_dir():
        return set()
    candidate_ids: set[str] = set()
    for path in trash_root.rglob("candidate.yaml"):
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(payload, dict) and payload.get("source_kind") == "waa_experiment":
            value = payload.get("candidate_id")
            if isinstance(value, str):
                candidate_ids.add(value)
    return candidate_ids


def materialize_waa_feedback_candidates(
    project_root: Path,
    candidate_root: Path,
) -> list[Path]:
    """Normalize completed WAA episodes into the existing feedback-candidate contract."""

    project = project_root.resolve()
    candidate_root = candidate_root.resolve()
    reports_root = project / "evaluations" / "windows-agent-arena"
    if not reports_root.is_dir():
        return []
    deleted_ids = _deleted_waa_candidate_ids(candidate_root)
    manifests: list[Path] = []
    for report_path in reports_root.rglob("waa-ablation-report.json"):
        try:
            report = _read_json(report_path)
            results_root = Path(str(report.get("results_root") or "")).expanduser().resolve()
            experiment_path = results_root / "experiment.json"
            experiment = _read_json(experiment_path)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if experiment.get("status") != "completed":
            continue
        experiment_id = str(experiment.get("experiment_id") or results_root.name)
        condition_tasks = experiment.get("condition_tasks")
        if not isinstance(condition_tasks, dict):
            condition_tasks = {}
        episodes = report.get("episodes")
        if not isinstance(episodes, list):
            continue
        for episode in episodes:
            if not isinstance(episode, dict):
                continue
            condition = str(episode.get("condition") or "")
            raw_episode_path = episode.get("path")
            raw_task_path = condition_tasks.get(condition) or experiment.get("task")
            if not isinstance(raw_episode_path, str) or not isinstance(raw_task_path, str):
                continue
            episode_path = Path(raw_episode_path).expanduser().resolve()
            task_path = Path(raw_task_path).expanduser().resolve()
            task_root = (project / "taskpacks").resolve()
            if not task_path.is_relative_to(task_root) or not task_path.is_file():
                continue
            try:
                task_payload = yaml.safe_load(task_path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                continue
            if not isinstance(task_payload, dict) or not isinstance(task_payload.get("id"), str):
                continue
            repetition = _episode_repetition(episode_path, condition)
            candidate_id = f"waa-{experiment_id}-{condition}-r{repetition:02d}"
            candidate_dir = candidate_root / candidate_id
            manifest_path = candidate_dir / "candidate.yaml"
            if candidate_id in deleted_ids:
                continue
            if manifest_path.is_file():
                try:
                    existing = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
                except (OSError, yaml.YAMLError):
                    existing = None
                if isinstance(existing, dict) and (
                    existing.get("import_version") == WAA_FEEDBACK_IMPORT_VERSION
                    or existing.get("status") == "feedback_applied"
                    or isinstance(existing.get("revision"), dict)
                    or isinstance(existing.get("task_model_revision"), dict)
                ):
                    manifests.append(manifest_path)
                    continue
                shutil.rmtree(candidate_dir, ignore_errors=True)
            try:
                candidate_dir.mkdir(parents=True, exist_ok=False)
                review, action_count = _materialize_episode_evidence(
                    episode_path,
                    candidate_dir,
                )
                created_at = datetime.fromtimestamp(
                    _filesystem_path(episode_path / "traj.jsonl").stat().st_mtime,
                    tz=UTC,
                ).isoformat()
                model_seconds = float(episode.get("model_roundtrip_seconds") or 0.0)
                elapsed_seconds = episode.get("elapsed_seconds")
                elapsed_ms = (
                    float(elapsed_seconds) * 1000
                    if isinstance(elapsed_seconds, int | float)
                    else None
                )
                plan_calls = int(episode.get("plan_calls") or review["round_count"])
                metrics = {
                    "executed_actions": int(episode.get("actions") or action_count),
                    "replans": plan_calls,
                    "planning_ms": model_seconds * 1000,
                    "batch_count": plan_calls,
                    "planned_actions": action_count,
                    "average_batch_size": round(action_count / plan_calls, 2) if plan_calls else 0,
                    "performance": {
                        "total_elapsed_ms": elapsed_ms,
                        "planning_ms": model_seconds * 1000,
                        "model_roundtrip_ms": model_seconds * 1000,
                    },
                }
                candidate = {
                    "schema_version": "0.2",
                    "candidate_id": candidate_id,
                    "status": "pending_review",
                    "created_at": created_at,
                    "source_kind": "waa_experiment",
                    "import_version": WAA_FEEDBACK_IMPORT_VERSION,
                    "task_id": task_payload["id"],
                    "runtime_instruction": review["instruction"],
                    "source_task": task_path.relative_to(project).as_posix(),
                    "execution_trace": (candidate_dir / "trace.jsonl").relative_to(project).as_posix(),
                    "review_timeline": "trajectory-review.json",
                    "outcome": {
                        "task_complete": float(episode.get("score") or 0.0) >= 1.0,
                        "stop_reason": "waa_evaluator_complete",
                        "verified": bool(episode.get("completed")),
                        "verification_outcome": "verified" if episode.get("completed") else "incomplete",
                    },
                    "metrics": metrics,
                    "waa": {
                        "experiment_id": experiment_id,
                        "condition": condition,
                        "condition_label": WAA_CONDITION_LABELS.get(condition, condition),
                        "repetition": repetition,
                        "score": float(episode.get("score") or 0.0),
                        "model": experiment.get("model"),
                        "reasoning_effort": experiment.get("reasoning_effort"),
                        "episode_path": str(episode_path),
                        "report_path": str(report_path),
                    },
                }
                manifest_path.write_text(
                    yaml.safe_dump(candidate, sort_keys=False, allow_unicode=True, width=100),
                    encoding="utf-8",
                )
                manifests.append(manifest_path)
            except (OSError, TypeError, ValueError, json.JSONDecodeError, yaml.YAMLError):
                shutil.rmtree(candidate_dir, ignore_errors=True)
                continue
    return manifests


def collect_waa_results(results_root: Path) -> list[WaaEpisodeResult]:
    root = results_root.expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"WAA results directory was not found: {root}")
    episodes: list[WaaEpisodeResult] = []
    for condition in WINDOWS_EXPERIENCE_MODES:
        for episode_dir in _condition_episode_dirs(root, condition):
            result_path = episode_dir / "result.txt"
            try:
                score = float(_read_text(result_path).strip())
                completed = True
            except (FileNotFoundError, ValueError):
                score = 0.0
                completed = False
            plan_calls, model_seconds = _plan_timings(episode_dir)
            episodes.append(
                WaaEpisodeResult(
                    condition=condition,
                    path=str(episode_dir),
                    score=score,
                    completed=completed,
                    actions=_trajectory_actions(episode_dir),
                    plan_calls=plan_calls,
                    model_roundtrip_seconds=model_seconds,
                    elapsed_seconds=_elapsed_seconds(episode_dir),
                )
            )
    return episodes


def _condition_summary(episodes: list[WaaEpisodeResult]) -> dict[str, Any]:
    elapsed = [episode.elapsed_seconds for episode in episodes if episode.elapsed_seconds is not None]
    return {
        "runs": len(episodes),
        "completed_runs": sum(episode.completed for episode in episodes),
        "successes": sum(episode.score >= 1.0 for episode in episodes),
        "success_rate": mean(episode.score >= 1.0 for episode in episodes) if episodes else 0.0,
        "mean_score": mean(episode.score for episode in episodes) if episodes else 0.0,
        "mean_actions": mean(episode.actions for episode in episodes) if episodes else 0.0,
        "mean_plan_calls": mean(episode.plan_calls for episode in episodes) if episodes else 0.0,
        "mean_model_roundtrip_seconds": (
            mean(episode.model_roundtrip_seconds for episode in episodes) if episodes else 0.0
        ),
        "mean_elapsed_seconds": mean(elapsed) if elapsed else None,
    }


def write_waa_report(results_root: Path, output_root: Path) -> dict[str, Any]:
    episodes = collect_waa_results(results_root)
    grouped = {
        condition: [episode for episode in episodes if episode.condition == condition]
        for condition in WINDOWS_EXPERIENCE_MODES
    }
    conditions = {
        condition: _condition_summary(condition_episodes)
        for condition, condition_episodes in grouped.items()
    }
    baseline = conditions["baseline"]
    for condition, summary in conditions.items():
        summary["success_rate_delta_vs_baseline"] = (
            summary["success_rate"] - baseline["success_rate"]
        )
    payload = {
        "schema_version": "0.1",
        "results_root": str(results_root.expanduser().resolve()),
        "conditions": conditions,
        "episodes": [asdict(episode) for episode in episodes],
    }

    output = output_root.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "waa-ablation-report.json"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Windows Agent Arena experience ablation",
        "",
        "| condition | runs | success rate | mean actions | mean plans | model seconds | elapsed seconds | delta vs baseline |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for condition in WINDOWS_EXPERIENCE_MODES:
        summary = conditions[condition]
        elapsed_value = summary["mean_elapsed_seconds"]
        elapsed_text = "n/a" if elapsed_value is None else f"{elapsed_value:.1f}"
        lines.append(
            f"| {condition} | {summary['runs']} | {summary['success_rate']:.1%} | "
            f"{summary['mean_actions']:.1f} | {summary['mean_plan_calls']:.1f} | "
            f"{summary['mean_model_roundtrip_seconds']:.1f} | {elapsed_text} | "
            f"{summary['success_rate_delta_vs_baseline']:+.1%} |"
        )
    markdown_path = output / "waa-ablation-report.md"
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "report_path": str(json_path),
        "markdown_path": str(markdown_path),
        "episodes": len(episodes),
        "conditions": conditions,
    }
