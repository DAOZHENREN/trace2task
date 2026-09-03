from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from trace2task.waa_results import (
    _plan_timings,
    collect_waa_results,
    materialize_waa_feedback_candidates,
    write_waa_report,
)


def _episode(root: Path, condition: str, repetition: int, score: float) -> Path:
    episode = (
        root
        / f"trace2task-{condition}-r{repetition:02d}"
        / "pyautogui"
        / "screenshot"
        / "unused"
        / "0"
        / "notepad"
        / "task-id"
    )
    episode.mkdir(parents=True)
    (episode / "result.txt").write_text(f"{score}\n", encoding="utf-8")
    (episode / "traj.html").write_text(
        "<h1>Elapsed Time: 0:01:30.500000</h1>",
        encoding="utf-8",
    )
    rows = [
        {"step_num": 0, "action": None},
        {"step_num": 1, "action": "pyautogui.click(1, 2)"},
        {"step_num": 2, "action": "DONE"},
    ]
    (episode / "traj.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    (episode / "plan_result-step_0.txt").write_text(
        json.dumps({"timing": {"model_roundtrip_ms": 2500}}),
        encoding="utf-8",
    )
    (episode / "plan_result-step_0_duplicated.txt").write_text(
        json.dumps({"timing": {"model_roundtrip_ms": 2500}}),
        encoding="utf-8",
    )
    return episode


def test_collect_waa_results_uses_independent_evaluator_scores(tmp_path: Path) -> None:
    _episode(tmp_path, "baseline", 1, 0.0)
    _episode(tmp_path, "feedback", 1, 1.0)

    episodes = collect_waa_results(tmp_path)

    assert [episode.condition for episode in episodes] == ["baseline", "feedback"]
    assert episodes[0].actions == 1
    assert episodes[0].plan_calls == 1
    assert episodes[0].model_roundtrip_seconds == 2.5
    assert episodes[0].elapsed_seconds == 90.5


def test_write_waa_report_compares_every_condition_to_baseline(tmp_path: Path) -> None:
    _episode(tmp_path, "baseline", 1, 0.0)
    _episode(tmp_path, "trace", 1, 1.0)
    _episode(tmp_path, "compiled", 1, 1.0)
    _episode(tmp_path, "feedback", 1, 1.0)

    result = write_waa_report(tmp_path, tmp_path / "report")

    assert result["conditions"]["baseline"]["success_rate"] == 0.0
    assert result["conditions"]["feedback"]["success_rate_delta_vs_baseline"] == 1.0
    assert Path(result["report_path"]).is_file()
    markdown = Path(result["markdown_path"]).read_text(encoding="utf-8")
    assert "| feedback | 1 | 100.0%" in markdown


def test_completed_waa_experiment_becomes_a_feedback_candidate(tmp_path: Path) -> None:
    task_path = tmp_path / "taskpacks" / "notepad" / "task.yaml"
    task_path.parent.mkdir(parents=True)
    task_path.write_text(yaml.safe_dump({"id": "waa-notepad"}), encoding="utf-8")
    results_root = tmp_path / "external-waa-results" / "experiment-1"
    episode = _episode(results_root, "compiled", 1, 1.0)
    screenshot = b"fake-png"
    (episode / "reset.png").write_bytes(screenshot)
    (episode / "after.png").write_bytes(screenshot)
    plan_name = "plan_result-step_0.txt"
    (episode / plan_name).write_text(
        json.dumps(
            {
                "structured_actions": [
                    {"skill": "click", "args": {"x": 0.5, "y": 0.75}}
                ],
                "reason": "Open Notepad.",
                "stage_id": "open_app",
                "stage_goal": "Open the editor.",
                "timing": {"model_roundtrip_ms": 2500},
            }
        ),
        encoding="utf-8",
    )
    rows = [
        {
            "step_num": 0,
            "action": None,
            "instruction": "Create draft.txt",
            "screenshot": "reset.png",
        },
        {
            "step_num": 1,
            "action": "pyautogui.click(10, 20)",
            "instruction": "Create draft.txt",
            "screenshot": "after.png",
            "plan_result": plan_name,
        },
        {"step_num": 2, "action": "DONE", "instruction": "Create draft.txt"},
    ]
    (episode / "traj.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    report_output = tmp_path / "evaluations" / "windows-agent-arena" / "smoke" / "experiment-1"
    write_waa_report(results_root, report_output)
    (results_root / "experiment.json").write_text(
        json.dumps(
            {
                "experiment_id": "experiment-1",
                "status": "completed",
                "task": str(task_path),
                "condition_tasks": {"compiled": str(task_path)},
                "model": "gpt-5.6-terra",
                "reasoning_effort": "low",
            }
        ),
        encoding="utf-8",
    )

    manifests = materialize_waa_feedback_candidates(
        tmp_path,
        tmp_path / "runs" / "candidates",
    )

    assert len(manifests) == 1
    candidate = yaml.safe_load(manifests[0].read_text(encoding="utf-8"))
    assert candidate["task_id"] == "waa-notepad"
    assert candidate["source_kind"] == "waa_experiment"
    assert candidate["waa"]["condition"] == "compiled"
    assert candidate["outcome"]["task_complete"] is True
    review = json.loads((manifests[0].parent / "trajectory-review.json").read_text())
    assert review["round_count"] == 2
    assert review["action_count"] == 1
    assert review["rounds"][0]["actions"][0]["skill"] == "click"
    assert review["rounds"][1]["decision"] == "DONE"
    assert (manifests[0].parent / "frames" / "initial.png").read_bytes() == screenshot
    assert (manifests[0].parent / "trace.jsonl").is_file()

    assert materialize_waa_feedback_candidates(
        tmp_path,
        tmp_path / "runs" / "candidates",
    ) == manifests

    trash = tmp_path / "runs" / ".trash" / "candidates" / "deleted-waa-run"
    trash.parent.mkdir(parents=True)
    manifests[0].parent.replace(trash)
    assert materialize_waa_feedback_candidates(
        tmp_path,
        tmp_path / "runs" / "candidates",
    ) == []


@pytest.mark.skipif(os.name != "nt", reason="Win32 extended paths are Windows-specific")
def test_plan_timings_reads_a_plan_beyond_the_legacy_win32_path_limit(
    tmp_path: Path,
) -> None:
    episode = tmp_path
    while len(str(episode / "plan_result-step_0.txt")) <= 265:
        episode /= "deep-waa-result-segment"
    extended_episode = Path(f"\\\\?\\{episode.resolve()}")
    extended_episode.mkdir(parents=True)
    (extended_episode / "plan_result-step_0.txt").write_text(
        json.dumps({"timing": {"model_roundtrip_ms": 4321}}),
        encoding="utf-8",
    )

    assert _plan_timings(episode) == (1, 4.321)
