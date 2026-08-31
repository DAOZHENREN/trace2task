from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from statistics import mean
from typing import Any

from trace2task.windows_agent import WINDOWS_EXPERIENCE_MODES

_ELAPSED_PATTERN = re.compile(r"Elapsed Time:\s*([^<\r\n]+)")
_PLAN_STEP_PATTERN = re.compile(r"plan_result-step_([^_.]+)(?:_|\.txt)")


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


def _elapsed_seconds(path: Path) -> float | None:
    html_path = path / "traj.html"
    if not html_path.is_file():
        return None
    match = _ELAPSED_PATTERN.search(html_path.read_text(encoding="utf-8", errors="replace"))
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
    for line in trajectory.read_text(encoding="utf-8", errors="replace").splitlines():
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
    for plan_path in sorted(path.glob("plan_result*.txt")):
        match = _PLAN_STEP_PATTERN.search(plan_path.name)
        step = match.group(1) if match is not None else plan_path.name
        plans_by_step.setdefault(step, plan_path)
    for plan_path in plans_by_step.values():
        try:
            payload = json.loads(plan_path.read_text(encoding="utf-8"))
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


def collect_waa_results(results_root: Path) -> list[WaaEpisodeResult]:
    root = results_root.expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"WAA results directory was not found: {root}")
    episodes: list[WaaEpisodeResult] = []
    for condition in WINDOWS_EXPERIENCE_MODES:
        for episode_dir in _condition_episode_dirs(root, condition):
            result_path = episode_dir / "result.txt"
            try:
                score = float(result_path.read_text(encoding="utf-8").strip())
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
