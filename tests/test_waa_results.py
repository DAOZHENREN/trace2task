from __future__ import annotations

import json
from pathlib import Path

from trace2task.waa_results import collect_waa_results, write_waa_report


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
