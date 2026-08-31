from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from trace2task.evaluation import load_evaluation_suite, run_evaluation_suite


def _write_suite(tmp_path: Path, *, repetitions: int = 2) -> Path:
    task_path = tmp_path / "task.yaml"
    task_path.write_text("id: placeholder\n", encoding="utf-8")
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "0.1",
                "id": "smoke",
                "cases": [
                    {
                        "id": "case-one",
                        "task": "task.yaml",
                        "instruction": "Complete the task once.",
                        "repetitions": repetitions,
                        "reset": {"type": "none"},
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return suite_path


@dataclass(frozen=True)
class FakeRunResult:
    task_complete: bool
    verified: bool
    verification_outcome: str
    stop_reason: str = "model_complete"
    planning_ms: float = 100.0
    replans: int = 2
    executed_actions: int = 3


def test_evaluation_suite_repeats_cases_and_aggregates_verifier_outcomes(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, object]] = []

    def runner(task_path: Path, **kwargs: object) -> FakeRunResult:
        calls.append({"task_path": task_path, **kwargs})
        verified = len(calls) == 1
        return FakeRunResult(
            task_complete=True,
            verified=verified,
            verification_outcome=("verified" if verified else "completed_unverified"),
        )

    result = run_evaluation_suite(
        _write_suite(tmp_path),
        execute=True,
        output_root=tmp_path / "evaluations",
        runner=runner,
        status_callback=lambda message: None,
    )
    rows = [
        json.loads(line)
        for line in Path(result.attempts_path).read_text(encoding="utf-8").splitlines()
    ]

    assert len(calls) == 2
    assert all(call["execute"] is True for call in calls)
    assert result.total_attempts == 2
    assert result.verified_attempts == 1
    assert result.completed_attempts == 2
    assert result.verified_rate == 0.5
    assert result.completion_rate == 1.0
    assert result.outcomes == {"completed_unverified": 1, "verified": 1}
    assert result.mean_planning_ms == 100.0
    assert result.mean_model_turns == 2.0
    assert result.mean_executed_actions == 3.0
    assert rows[0]["reset"]["status"] == "skipped"
    assert Path(result.summary_path).is_file()


def test_evaluation_suite_rejects_unknown_reset_adapter(tmp_path: Path) -> None:
    suite_path = _write_suite(tmp_path, repetitions=1)
    payload = yaml.safe_load(suite_path.read_text(encoding="utf-8"))
    payload["cases"][0]["reset"] = {"type": "osworld"}
    suite_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    try:
        load_evaluation_suite(suite_path)
    except ValueError as error:
        assert "Unknown evaluation reset adapter" in str(error)
    else:
        raise AssertionError("Unknown reset adapters must be rejected")
