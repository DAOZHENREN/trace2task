from __future__ import annotations

import csv
import itertools
import json
import math
import re
from collections.abc import Callable, Iterable
from pathlib import Path
from statistics import NormalDist, mean, median
from typing import Any

import numpy as np

_REPORT_PATH_PATTERN = re.compile(
    r'"report_path"\s*:\s*(?P<value>"(?:\\.|[^"\\])*")'
)

CONDITION_LABELS = {
    "baseline": "Baseline",
    "raw_trace": "Raw Trace",
    "trace_compile": "Trace Compile",
    "narrated_trace_compile": "Narrated Trace Compile",
    # Historical condition ids remain readable in frozen earlier studies.
    "auto_compiled": "Auto-compiled",
    "reviewed_compiled": "Reviewed compile",
    "narrated_compiled": "Narrated compile",
}

_PLOT_SCRIPT = r'''from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
with (HERE.parent / "condition-summary.csv").open(encoding="utf-8-sig", newline="") as handle:
    rows = list(csv.DictReader(handle))

preferred_order = [
    "baseline",
    "raw_trace",
    "trace_compile",
    "narrated_trace_compile",
    "auto_compiled",
    "reviewed_compiled",
    "narrated_compiled",
]
by_id = {row["condition_id"]: row for row in rows}
order = [value for value in preferred_order if value in by_id]
order.extend(value for value in by_id if value not in order)
rows = [by_id[value] for value in order]
label_map = {
    "baseline": "Baseline",
    "raw_trace": "Raw\nTrace",
    "trace_compile": "Trace\nCompile",
    "narrated_trace_compile": "Narrated Trace\nCompile",
    "auto_compiled": "Auto\nCompile",
    "reviewed_compiled": "Reviewed\nCompile",
    "narrated_compiled": "Narrated\nCompile",
}
short_label_map = {
    "baseline": "Base",
    "raw_trace": "Trace",
    "trace_compile": "Compile",
    "narrated_trace_compile": "Narr.+",
    "auto_compiled": "Auto",
    "reviewed_compiled": "Review",
    "narrated_compiled": "Narr.",
}
color_map = {
    "baseline": "#B0BEC5",
    "raw_trace": "#56B4E9",
    "trace_compile": "#E69F00",
    "narrated_trace_compile": "#D55E00",
    "auto_compiled": "#E69F00",
    "reviewed_compiled": "#009E73",
    "narrated_compiled": "#D55E00",
}
hatch_map = {
    "baseline": "",
    "raw_trace": "//",
    "trace_compile": "..",
    "narrated_trace_compile": "\\\\",
    "auto_compiled": "..",
    "reviewed_compiled": "xx",
    "narrated_compiled": "\\\\",
}
labels = [label_map.get(value, value) for value in order]
short_labels = [short_label_map.get(value, value) for value in order]
colors = [color_map.get(value, "#999999") for value in order]
hatches = [hatch_map.get(value, "") for value in order]

plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "DejaVu Serif"],
    "font.size": 9,
    "axes.labelsize": 9,
    "axes.titlesize": 10,
    "axes.titleweight": "bold",
    "figure.dpi": 300,
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.alpha": 0.16,
    "grid.linestyle": "-",
})


def values(name: str) -> np.ndarray:
    return np.asarray([float(row[name]) for row in rows], dtype=float)


def bars(ax, y, low, high, ylabel, title, value_format):
    x = np.arange(len(rows))
    errors = np.vstack([np.maximum(0, y - low), np.maximum(0, high - y)])
    patches = ax.bar(
        x,
        y,
        yerr=errors,
        capsize=3,
        color=colors,
        edgecolor="#404040",
        linewidth=0.55,
        error_kw={"elinewidth": 0.8, "capthick": 0.8},
        zorder=3,
    )
    for patch, hatch, value in zip(patches, hatches, y):
        patch.set_hatch(hatch)
        ax.text(
            patch.get_x() + patch.get_width() / 2,
            patch.get_height(),
            value_format(value),
            ha="center",
            va="bottom",
            fontsize=7.2,
        )
    ax.set_xticks(x, labels)
    ax.set_ylabel(ylabel)
    ax.set_title(title, loc="left")
    ax.set_axisbelow(True)


success = values("success_rate") * 100
success_low = values("success_ci_low") * 100
success_high = values("success_ci_high") * 100
elapsed = values("elapsed_median_seconds")
elapsed_low = values("elapsed_ci_low")
elapsed_high = values("elapsed_ci_high")

fig, axes = plt.subplots(1, 2, figsize=(6.75, 2.65), constrained_layout=True)
bars(axes[0], success, success_low, success_high, "Success rate (%)", "a  Task success", lambda x: f"{x:.0f}%")
axes[0].set_ylim(0, 112)
bars(axes[1], elapsed, elapsed_low, elapsed_high, "Wall time (s)", "b  End-to-end latency", lambda x: f"{x:.0f}")
fig.savefig(HERE / "fig_success_efficiency.pdf")
fig.savefig(HERE / "fig_success_efficiency.png", dpi=300)
plt.close(fig)

metrics = [
    ("model_time_median_seconds", "model_time_ci_low", "model_time_ci_high", "Model time (s)", "a  Model latency"),
    ("plan_calls_median", "plan_calls_ci_low", "plan_calls_ci_high", "Plan calls", "b  Planning calls"),
    ("actions_median", "actions_ci_low", "actions_ci_high", "Actions", "c  Executed actions"),
]
fig, axes = plt.subplots(1, 3, figsize=(6.75, 2.4), constrained_layout=True)
for ax, (mid, low, high, ylabel, title) in zip(axes, metrics):
    bars(ax, values(mid), values(low), values(high), ylabel, title, lambda x: f"{x:.1f}")
    ax.set_xticklabels(short_labels)
fig.savefig(HERE / "fig_planning_efficiency.pdf")
fig.savefig(HERE / "fig_planning_efficiency.png", dpi=300)
plt.close(fig)
(HERE / "figure-metadata.json").write_text(
    json.dumps(
        {
            "matplotlib_version": matplotlib.__version__,
            "numpy_version": np.__version__,
            "dpi": 300,
            "font_family": "Times New Roman with DejaVu Serif fallback",
            "palette": "Okabe-Ito with gray baseline",
            "outputs": [
                "fig_success_efficiency.pdf",
                "fig_success_efficiency.png",
                "fig_planning_efficiency.pdf",
                "fig_planning_efficiency.png",
            ],
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
'''


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    if not isinstance(payload, dict):
        raise TypeError(f"Expected a JSON object: {path}")
    return payload


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _report_path_from_log(log_path: Path) -> Path:
    match = _REPORT_PATH_PATTERN.search(log_path.read_text(encoding="utf-8", errors="replace"))
    if match is None:
        raise ValueError(f"Study episode log has no report_path: {log_path}")
    return Path(json.loads(match.group("value"))).expanduser().resolve()


def _condition_definitions(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    protocol = manifest.get("protocol")
    values = protocol.get("conditions") if isinstance(protocol, dict) else None
    if not isinstance(values, list):
        raise TypeError("Study manifest has no protocol conditions")
    return {
        str(item["id"]): item
        for item in values
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }


def collect_study_episodes(study_root: Path, run_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    study = study_root.expanduser().resolve()
    run = run_root.expanduser().resolve()
    manifest = _read_json(study / "study-manifest.json")
    schedule = _read_csv(study / "episode-schedule.csv")
    definitions = _condition_definitions(manifest)
    scheduled = {row["episode_id"]: row for row in schedule}
    ledger = [
        json.loads(line)
        for line in (run / "study-run.jsonl").read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    if len(ledger) != len(schedule):
        raise ValueError(f"Study run is incomplete: {len(ledger)}/{len(schedule)} ledger rows")
    if any(row.get("status") != "completed" for row in ledger):
        raise ValueError("Study run contains a non-completed episode")

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ledger_row in sorted(ledger, key=lambda row: int(row["order"])):
        episode_id = str(ledger_row["episode_id"])
        if episode_id in seen or episode_id not in scheduled:
            raise ValueError(f"Unexpected or duplicate episode id: {episode_id}")
        seen.add(episode_id)
        schedule_row = scheduled[episode_id]
        condition_id = schedule_row["condition_id"]
        definition = definitions[condition_id]
        report_path = _report_path_from_log(Path(str(ledger_row["log_path"])))
        report = _read_json(report_path)
        report_episodes = report.get("episodes")
        if not isinstance(report_episodes, list) or len(report_episodes) != 1:
            raise ValueError(f"Expected one WAA episode in {report_path}")
        episode = report_episodes[0]
        if not isinstance(episode, dict):
            raise TypeError(f"Invalid WAA episode in {report_path}")
        expected_mode = str(definition.get("runtime_mode") or condition_id)
        if episode.get("condition") != expected_mode:
            raise ValueError(
                f"Condition mismatch for {episode_id}: {episode.get('condition')} != {expected_mode}"
            )
        elapsed = episode.get("elapsed_seconds")
        if not isinstance(elapsed, int | float):
            raise TypeError(f"Episode has no elapsed time: {episode_id}")
        score = float(episode.get("score") or 0.0)
        results.append(
            {
                "order": int(schedule_row["order"]),
                "episode_id": episode_id,
                "task_id": schedule_row["task_id"],
                "condition_id": condition_id,
                "condition_label": CONDITION_LABELS.get(condition_id, condition_id),
                "runtime_mode": expected_mode,
                "repetition": int(schedule_row["repetition"]),
                "score": score,
                "success": int(score >= 1.0),
                "completed": int(bool(episode.get("completed"))),
                "elapsed_seconds": float(elapsed),
                "model_time_seconds": float(episode.get("model_roundtrip_seconds") or 0.0),
                "plan_calls": int(episode.get("plan_calls") or 0),
                "actions": int(episode.get("actions") or 0),
                "recovery_count": None,
                "report_path": str(report_path),
                "trajectory_path": str(episode.get("path") or ""),
                "log_path": str(ledger_row["log_path"]),
            }
        )
    return manifest, results


def _quantile(values: Iterable[float], probability: float) -> float:
    return float(np.quantile(np.asarray(list(values), dtype=float), probability))


def _iqr(values: Iterable[float]) -> tuple[float, float]:
    data = list(values)
    return _quantile(data, 0.25), _quantile(data, 0.75)


def _wilson_interval(successes: int, total: int, confidence: float = 0.95) -> tuple[float, float]:
    if total <= 0:
        raise ValueError("Wilson interval requires at least one observation")
    z = NormalDist().inv_cdf(0.5 + confidence / 2)
    proportion = successes / total
    denominator = 1 + z**2 / total
    center = (proportion + z**2 / (2 * total)) / denominator
    radius = (
        z
        * math.sqrt(proportion * (1 - proportion) / total + z**2 / (4 * total**2))
        / denominator
    )
    return max(0.0, center - radius), min(1.0, center + radius)


def _cluster_bootstrap(
    rows: list[dict[str, Any]],
    statistic: Callable[[list[dict[str, Any]]], float],
) -> tuple[float, float]:
    tasks = sorted({str(row["task_id"]) for row in rows})
    by_task = {task: [row for row in rows if row["task_id"] == task] for task in tasks}
    estimates: list[float] = []
    for sample in itertools.product(tasks, repeat=len(tasks)):
        sampled: list[dict[str, Any]] = []
        for task in sample:
            sampled.extend(by_task[task])
        estimates.append(float(statistic(sampled)))
    return _quantile(estimates, 0.025), _quantile(estimates, 0.975)


def _paired_cluster_bootstrap(
    baseline: list[dict[str, Any]],
    condition: list[dict[str, Any]],
    statistic: Callable[[list[dict[str, Any]]], float],
) -> tuple[float, float]:
    tasks = sorted({str(row["task_id"]) for row in baseline})
    if tasks != sorted({str(row["task_id"]) for row in condition}):
        raise ValueError("Condition and baseline do not contain the same task variants")
    baseline_by_task = {task: [row for row in baseline if row["task_id"] == task] for task in tasks}
    condition_by_task = {task: [row for row in condition if row["task_id"] == task] for task in tasks}
    estimates: list[float] = []
    for sample in itertools.product(tasks, repeat=len(tasks)):
        baseline_sample: list[dict[str, Any]] = []
        condition_sample: list[dict[str, Any]] = []
        for task in sample:
            baseline_sample.extend(baseline_by_task[task])
            condition_sample.extend(condition_by_task[task])
        estimates.append(float(statistic(condition_sample) - statistic(baseline_sample)))
    return _quantile(estimates, 0.025), _quantile(estimates, 0.975)


def _mean_field(field: str) -> Callable[[list[dict[str, Any]]], float]:
    return lambda rows: mean(float(row[field]) for row in rows)


def _median_field(field: str) -> Callable[[list[dict[str, Any]]], float]:
    return lambda rows: median(float(row[field]) for row in rows)


def _condition_summary(
    rows: list[dict[str, Any]],
    condition_order: list[str],
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    metrics = {
        "elapsed": "elapsed_seconds",
        "model_time": "model_time_seconds",
        "plan_calls": "plan_calls",
        "actions": "actions",
    }
    for condition_id in condition_order:
        group = [row for row in rows if row["condition_id"] == condition_id]
        if not group:
            continue
        success_stat = _mean_field("success")
        successes = sum(int(row["success"]) for row in group)
        success_low, success_high = _wilson_interval(successes, len(group))
        cluster_low, cluster_high = _cluster_bootstrap(group, success_stat)
        summary: dict[str, Any] = {
            "condition_id": condition_id,
            "condition_label": group[0]["condition_label"],
            "episodes": len(group),
            "successes": successes,
            "success_rate": success_stat(group),
            "success_ci_low": success_low,
            "success_ci_high": success_high,
            "success_cluster_ci_low": cluster_low,
            "success_cluster_ci_high": cluster_high,
        }
        for prefix, field in metrics.items():
            values = [float(row[field]) for row in group]
            q1, q3 = _iqr(values)
            stat = _median_field(field)
            low, high = _cluster_bootstrap(group, stat)
            summary.update(
                {
                    f"{prefix}_median_seconds" if prefix in {"elapsed", "model_time"} else f"{prefix}_median": stat(group),
                    f"{prefix}_q1": q1,
                    f"{prefix}_q3": q3,
                    f"{prefix}_ci_low": low,
                    f"{prefix}_ci_high": high,
                }
            )
        summaries.append(summary)
    return summaries


def _contrasts(
    rows: list[dict[str, Any]],
    condition_order: list[str],
) -> list[dict[str, Any]]:
    baseline = [row for row in rows if row["condition_id"] == "baseline"]
    metrics = {
        "success_rate": _mean_field("success"),
        "elapsed_median_seconds": _median_field("elapsed_seconds"),
        "model_time_median_seconds": _median_field("model_time_seconds"),
        "plan_calls_median": _median_field("plan_calls"),
        "actions_median": _median_field("actions"),
    }
    contrasts: list[dict[str, Any]] = []
    for condition_id in condition_order:
        if condition_id == "baseline":
            continue
        group = [row for row in rows if row["condition_id"] == condition_id]
        contrast: dict[str, Any] = {
            "condition_id": condition_id,
            "condition_label": group[0]["condition_label"],
            "reference": "baseline",
        }
        for name, statistic in metrics.items():
            estimate = statistic(group) - statistic(baseline)
            low, high = _paired_cluster_bootstrap(baseline, group, statistic)
            contrast[f"{name}_delta"] = estimate
            contrast[f"{name}_ci_low"] = low
            contrast[f"{name}_ci_high"] = high
        contrasts.append(contrast)
    return contrasts


def _human_cost_summary(
    manifest: dict[str, Any],
    condition_order: list[str],
) -> list[dict[str, Any]]:
    protocol = manifest.get("protocol")
    if not isinstance(protocol, dict):
        return []
    policy = protocol.get("human_cost_policy")
    if not isinstance(policy, dict):
        return []
    definitions = _condition_definitions(manifest)
    component_fields = {
        "demonstration": "demonstration_wall_seconds_total",
        "narration": "narration_session_seconds_total",
        "compiler_review": "compiler_review_availability_to_confirmation_seconds_total",
    }
    results: list[dict[str, Any]] = []
    for condition_id in condition_order:
        keys = definitions[condition_id].get("human_cost_keys")
        selected = set(keys) if isinstance(keys, list) else set()
        row: dict[str, Any] = {
            "condition_id": condition_id,
            "condition_label": CONDITION_LABELS.get(condition_id, condition_id),
        }
        for component, field in component_fields.items():
            row[f"{component}_seconds"] = (
                float(policy.get(field) or 0.0) if component in selected else 0.0
            )
        row["components_are_additive"] = False
        results.append(row)
    return results


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"Cannot write empty CSV: {path}")
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _paper_markdown(
    manifest: dict[str, Any],
    summaries: list[dict[str, Any]],
    contrasts: list[dict[str, Any]],
    human_costs: list[dict[str, Any]],
) -> str:
    lines = [
        "# Trace2Task WAA held-out study results",
        "",
        "## Analysis contract",
        "",
        "- Primary endpoint: evaluator success over all scheduled episodes (intent-to-treat).",
        "- Independent sampling unit: held-out task variant (3 variants, 3 repetitions each).",
        "- Absolute success intervals: Wilson 95% intervals over the 9 scheduled episodes per condition.",
        "- Paired effects: complete enumerated variant-cluster bootstrap (3^3 = 27 resamples).",
        "- Efficiency: median and IQR over all episodes; successful-only latency is not used as the primary efficiency result.",
        "- Recovery count: not collected by this WAA runner and therefore not imputed.",
        "- These intervals are coarse because there are only three independent task variants.",
        "",
        "## Condition results",
        "",
        "| Condition | Success | Rate [95% Wilson CI] | Wall time, median [IQR] s | Model time, median [IQR] s | Plans | Actions |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for item in summaries:
        lines.append(
            f"| {item['condition_label']} | {item['successes']}/{item['episodes']} | "
            f"{item['success_rate']:.1%} [{item['success_ci_low']:.1%}, {item['success_ci_high']:.1%}] | "
            f"{item['elapsed_median_seconds']:.1f} [{item['elapsed_q1']:.1f}, {item['elapsed_q3']:.1f}] | "
            f"{item['model_time_median_seconds']:.1f} [{item['model_time_q1']:.1f}, {item['model_time_q3']:.1f}] | "
            f"{item['plan_calls_median']:.1f} | {item['actions_median']:.1f} |"
        )
    lines.extend(
        [
            "",
            "## Paired effects versus baseline",
            "",
            "Positive success deltas favor the condition; negative latency and count deltas favor the condition.",
            "",
            "| Condition | Success delta [95% CI] | Wall-time delta s [95% CI] | Model-time delta s [95% CI] | Plan delta [95% CI] | Action delta [95% CI] |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for item in contrasts:
        lines.append(
            f"| {item['condition_label']} | {item['success_rate_delta']:+.1%} "
            f"[{item['success_rate_ci_low']:+.1%}, {item['success_rate_ci_high']:+.1%}] | "
            f"{item['elapsed_median_seconds_delta']:+.1f} "
            f"[{item['elapsed_median_seconds_ci_low']:+.1f}, {item['elapsed_median_seconds_ci_high']:+.1f}] | "
            f"{item['model_time_median_seconds_delta']:+.1f} "
            f"[{item['model_time_median_seconds_ci_low']:+.1f}, {item['model_time_median_seconds_ci_high']:+.1f}] | "
            f"{item['plan_calls_median_delta']:+.1f} "
            f"[{item['plan_calls_median_ci_low']:+.1f}, {item['plan_calls_median_ci_high']:+.1f}] | "
            f"{item['actions_median_delta']:+.1f} "
            f"[{item['actions_median_ci_low']:+.1f}, {item['actions_median_ci_high']:+.1f}] |"
        )
    protocol = manifest.get("protocol")
    policy = protocol.get("human_cost_policy") if isinstance(protocol, dict) else None
    if isinstance(policy, dict) and human_costs:
        lines.extend(
            [
                "",
                "## Human acquisition cost",
                "",
                "| Condition | Demonstration s | Narration s | Compiler review s |",
                "|---|---:|---:|---:|",
            ]
        )
        for item in human_costs:
            lines.append(
                f"| {item['condition_label']} | {item['demonstration_seconds']:.3f} | "
                f"{item['narration_seconds']:.3f} | {item['compiler_review_seconds']:.3f} |"
            )
        lines.extend(
            [
                "",
                f"- Demonstration: {float(policy.get('demonstration_wall_seconds_total') or 0):.3f} s total.",
                f"- Narration: {float(policy.get('narration_session_seconds_total') or 0):.3f} s total.",
                f"- Compiler review: {float(policy.get('compiler_review_availability_to_confirmation_seconds_total') or 0):.3f} s total.",
                "- Narration overlaps the demonstration, so these components must not be added into a single wall-clock total.",
            ]
        )
    lines.extend(
        [
            "",
            "## Reporting caution",
            "",
            "This is one application and one parameterized task family. The study estimates within-family transfer; it does not yet establish broad desktop-agent generalization.",
            "",
        ]
    )
    return "\n".join(lines)


def _paper_latex(
    summaries: list[dict[str, Any]],
    contrasts: list[dict[str, Any]],
) -> str:
    lines = [
        r"\begin{table*}[t]",
        r"\centering",
        r"\caption{Trace2Task ablation on the held-out WAA count-token family. Intervals on absolute success rates are Wilson 95\% intervals; efficiency values are medians with interquartile ranges.}",
        r"\label{tab:waa-count-token}",
        r"\begin{tabular}{lrrrrrr}",
        r"\toprule",
        r"Condition & Success & Rate (95\% CI) & Wall time (s) & Model time (s) & Plans & Actions \\",
        r"\midrule",
    ]
    for item in summaries:
        rate = f"{item['success_rate']:.1%}".replace("%", r"\%")
        rate_low = f"{item['success_ci_low']:.1%}".replace("%", r"\%")
        rate_high = f"{item['success_ci_high']:.1%}".replace("%", r"\%")
        lines.append(
            f"{item['condition_label']} & {item['successes']}/{item['episodes']} & "
            f"{rate} [{rate_low}, {rate_high}] & "
            f"{item['elapsed_median_seconds']:.1f} [{item['elapsed_q1']:.1f}, {item['elapsed_q3']:.1f}] & "
            f"{item['model_time_median_seconds']:.1f} [{item['model_time_q1']:.1f}, {item['model_time_q3']:.1f}] & "
            f"{item['plan_calls_median']:.1f} & {item['actions_median']:.1f} \\\\"
        )
    lines.extend(
        [
            r"\bottomrule",
            r"\end{tabular}",
            r"\end{table*}",
            "",
            r"\begin{table*}[t]",
            r"\centering",
            r"\caption{Paired effects relative to the no-Trace baseline. Intervals are complete enumerated held-out-variant cluster-bootstrap 95\% intervals. Positive success differences and negative efficiency differences favor Trace2Task.}",
            r"\label{tab:waa-count-token-deltas}",
            r"\begin{tabular}{lrrrrr}",
            r"\toprule",
            r"Condition & $\Delta$ success & $\Delta$ wall time & $\Delta$ model time & $\Delta$ plans & $\Delta$ actions \\",
            r"\midrule",
        ]
    )
    for item in contrasts:
        delta = f"{item['success_rate_delta']:+.1%}".replace("%", r"\%")
        delta_low = f"{item['success_rate_ci_low']:+.1%}".replace("%", r"\%")
        delta_high = f"{item['success_rate_ci_high']:+.1%}".replace("%", r"\%")
        lines.append(
            f"{item['condition_label']} & {delta} [{delta_low}, {delta_high}] & "
            f"{item['elapsed_median_seconds_delta']:+.1f} "
            f"[{item['elapsed_median_seconds_ci_low']:+.1f}, {item['elapsed_median_seconds_ci_high']:+.1f}] & "
            f"{item['model_time_median_seconds_delta']:+.1f} "
            f"[{item['model_time_median_seconds_ci_low']:+.1f}, {item['model_time_median_seconds_ci_high']:+.1f}] & "
            f"{item['plan_calls_median_delta']:+.1f} "
            f"[{item['plan_calls_median_ci_low']:+.1f}, {item['plan_calls_median_ci_high']:+.1f}] & "
            f"{item['actions_median_delta']:+.1f} "
            f"[{item['actions_median_ci_low']:+.1f}, {item['actions_median_ci_high']:+.1f}] \\\\"
        )
    lines.extend([r"\bottomrule", r"\end{tabular}", r"\end{table*}", ""])
    return "\n".join(lines)


def write_waa_study_report(
    study_root: Path,
    run_root: Path,
    *,
    output_root: Path | None = None,
) -> dict[str, Any]:
    study = study_root.expanduser().resolve()
    run = run_root.expanduser().resolve()
    output = (output_root or run / "analysis").expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest, episodes = collect_study_episodes(study, run)
    definitions = _condition_definitions(manifest)
    condition_order = list(definitions)
    summaries = _condition_summary(episodes, condition_order)
    contrasts = _contrasts(episodes, condition_order)
    human_costs = _human_cost_summary(manifest, condition_order)
    task_summaries: list[dict[str, Any]] = []
    for task_id in sorted({str(row["task_id"]) for row in episodes}):
        for condition_id in condition_order:
            group = [
                row
                for row in episodes
                if row["task_id"] == task_id and row["condition_id"] == condition_id
            ]
            task_summaries.append(
                {
                    "task_id": task_id,
                    "condition_id": condition_id,
                    "episodes": len(group),
                    "successes": sum(int(row["success"]) for row in group),
                    "success_rate": mean(float(row["success"]) for row in group),
                    "elapsed_median_seconds": median(float(row["elapsed_seconds"]) for row in group),
                    "model_time_median_seconds": median(float(row["model_time_seconds"]) for row in group),
                    "plan_calls_median": median(float(row["plan_calls"]) for row in group),
                    "actions_median": median(float(row["actions"]) for row in group),
                }
            )

    _write_csv(output / "episodes.csv", episodes)
    _write_csv(output / "condition-summary.csv", summaries)
    _write_csv(output / "contrasts-vs-baseline.csv", contrasts)
    _write_csv(output / "task-condition-summary.csv", task_summaries)
    if human_costs:
        _write_csv(output / "human-cost-summary.csv", human_costs)
    analysis = {
        "schema_version": "0.1",
        "study_id": manifest.get("study_id"),
        "study_root": str(study),
        "run_root": str(run),
        "episode_count": len(episodes),
        "independent_task_variants": len({row["task_id"] for row in episodes}),
        "analysis": {
            "primary_endpoint": "evaluator_success",
            "intent_to_treat": True,
            "absolute_success_interval": "Wilson 95% interval over scheduled episodes",
            "paired_effect_interval": "complete enumerated held-out-variant cluster bootstrap",
            "bootstrap_resamples": 27,
            "recovery_count": "not_collected",
        },
        "conditions": summaries,
        "contrasts_vs_baseline": contrasts,
        "human_costs": human_costs,
    }
    json_path = output / "study-analysis.json"
    json_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path = output / "paper-results.md"
    markdown_path.write_text(
        _paper_markdown(manifest, summaries, contrasts, human_costs),
        encoding="utf-8",
    )
    latex_path = output / "paper-tables.tex"
    latex_path.write_text(_paper_latex(summaries, contrasts), encoding="utf-8")
    figures = output / "figures"
    figures.mkdir(exist_ok=True)
    plot_script = figures / "gen_fig_condition_ablation.py"
    plot_script.write_text(_PLOT_SCRIPT, encoding="utf-8")
    return {
        "mode": "waa_study_report",
        "status": "completed",
        "episodes": len(episodes),
        "conditions": len(summaries),
        "analysis_path": str(json_path),
        "paper_results_path": str(markdown_path),
        "paper_tables_path": str(latex_path),
        "condition_summary_path": str(output / "condition-summary.csv"),
        "contrasts_path": str(output / "contrasts-vs-baseline.csv"),
        "plot_script_path": str(plot_script),
    }
