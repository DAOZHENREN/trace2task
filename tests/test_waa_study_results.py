from __future__ import annotations

import csv
import json
from pathlib import Path

from trace2task.waa_study_results import CONDITION_LABELS, write_waa_study_report


def _write_report(path: Path, *, condition: str, score: float, task_marker: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "episodes": [
                    {
                        "condition": condition,
                        "path": str(path.parent / task_marker),
                        "score": score,
                        "completed": True,
                        "actions": 4,
                        "plan_calls": 2,
                        "model_roundtrip_seconds": 5.0,
                        "elapsed_seconds": 10.0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def test_write_waa_study_report_collects_formal_ledger_only(tmp_path: Path) -> None:
    study = tmp_path / "study"
    run = study / "study-runs" / "formal"
    study.mkdir(parents=True)
    run.mkdir(parents=True)
    manifest = {
        "study_id": "example-study",
        "protocol": {
            "conditions": [
                {"id": "baseline", "runtime_mode": "baseline"},
                {"id": "raw_trace", "runtime_mode": "trace"},
            ]
        },
    }
    (study / "study-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    schedule_rows: list[dict[str, object]] = []
    ledger_rows: list[dict[str, object]] = []
    order = 0
    for task_index in range(1, 4):
        for condition_id, runtime_mode, score in (
            ("baseline", "baseline", 0.0),
            ("raw_trace", "trace", 1.0),
        ):
            order += 1
            episode_id = f"task-{task_index}--{condition_id}--r01"
            report = tmp_path / "reports" / episode_id / "waa-ablation-report.json"
            _write_report(report, condition=runtime_mode, score=score, task_marker=episode_id)
            log = run / f"{episode_id}.log"
            log.write_text(
                json.dumps({"report_path": str(report)}),
                encoding="utf-8",
            )
            schedule_rows.append(
                {
                    "order": order,
                    "episode_id": episode_id,
                    "task_id": f"task-{task_index}",
                    "condition_id": condition_id,
                    "runtime_mode": runtime_mode,
                    "repetition": 1,
                    "ready": True,
                    "blockers": "",
                }
            )
            ledger_rows.append(
                {
                    "episode_id": episode_id,
                    "order": order,
                    "status": "completed",
                    "log_path": str(log),
                }
            )
    with (study / "episode-schedule.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(schedule_rows[0]))
        writer.writeheader()
        writer.writerows(schedule_rows)
    (run / "study-run.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in ledger_rows),
        encoding="utf-8",
    )

    result = write_waa_study_report(study, run)

    assert result["episodes"] == 6
    analysis = json.loads(Path(result["analysis_path"]).read_text(encoding="utf-8"))
    assert analysis["independent_task_variants"] == 3
    assert analysis["conditions"][0]["success_rate"] == 0.0
    assert analysis["conditions"][1]["success_rate"] == 1.0
    assert analysis["contrasts_vs_baseline"][0]["success_rate_delta"] == 1.0
    assert Path(result["paper_results_path"]).is_file()
    plot_script = Path(result["plot_script_path"])
    assert plot_script.is_file()
    assert '"trace_compile": "Trace\\nCompile"' in plot_script.read_text(
        encoding="utf-8"
    )


def test_current_condition_labels_merge_draft_and_confirmation_arm() -> None:
    assert CONDITION_LABELS["trace_compile"] == "Trace Compile"
    assert CONDITION_LABELS["narrated_trace_compile"] == "Narrated Trace Compile"
