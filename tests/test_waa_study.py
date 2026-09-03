from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trace2task.waa_study import _episode_command, prepare_waa_study


def _write_taskpack(root: Path, name: str, trace: str) -> Path:
    task_root = root / "taskpacks" / name
    reference = task_root / "reference"
    reference.mkdir(parents=True)
    task = task_root / "task.yaml"
    task.write_text(f"id: {name}\n", encoding="utf-8")
    (reference / "trace.jsonl").write_text(trace, encoding="utf-8")
    return task


def _write_waa_root(root: Path, task_ids: list[str]) -> Path:
    client = root / "waa" / "src" / "win-arena-container" / "client"
    examples = client / "evaluation_examples_windows" / "examples" / "notepad"
    examples.mkdir(parents=True)
    for task_id in task_ids:
        (examples / f"{task_id}.json").write_text(
            json.dumps({"id": task_id}), encoding="utf-8"
        )
    task_list = client / "evaluation_examples_windows" / "study.json"
    task_list.write_text(json.dumps({"notepad": task_ids}), encoding="utf-8")
    return root / "waa"


def _ready_spec(root: Path) -> Path:
    task_ids = ["task-a", "task-b"]
    _write_waa_root(root, task_ids)
    reset = root / "reset.json"
    reset.write_text("{}", encoding="utf-8")
    task_a = _write_taskpack(root, "task-a", "trace-a")
    task_b = _write_taskpack(root, "task-b", "trace-b")
    tasks = []
    for index, (task_id, task) in enumerate(zip(task_ids, (task_a, task_b)), start=1):
        tasks.append(
            {
                "id": task_id,
                "app": "notepad",
                "family_id": f"family-{index}",
                "split": "test",
                "parameterized": True,
                "demonstration_variant": f"demo-{index}",
                "evaluation_variant": f"eval-{index}",
                "status": "ready",
                "waa_task_id": task_id,
                "waa_json_name": "evaluation_examples_windows/study.json",
                "reset_spec": "reset.json",
                "taskpacks": {"execution": str(task.relative_to(root))},
                "human_cost_seconds": {"demonstration": 12.5},
            }
        )
    spec = {
        "schema_version": "0.1",
        "study_id": "unit-study",
        "path_base": ".",
        "seed": 17,
        "repetitions": 2,
        "targets": {"apps": 1, "task_instances": 2, "parameterized_families": 2},
        "conditions": [
            {
                "id": "baseline",
                "runtime_mode": "baseline",
                "taskpack_role": "execution",
            },
            {
                "id": "trace",
                "runtime_mode": "trace",
                "taskpack_role": "execution",
                "human_cost_keys": ["demonstration"],
            },
        ],
        "tasks": tasks,
    }
    path = root / "study.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


def test_study_plan_freezes_and_randomizes_deterministically(tmp_path: Path) -> None:
    spec = _ready_spec(tmp_path)
    fixed_time = datetime(2026, 9, 1, tzinfo=UTC)

    first = prepare_waa_study(
        spec,
        tmp_path / "waa",
        output_root=tmp_path / "out-a",
        prepared_at=fixed_time,
    )
    second = prepare_waa_study(
        spec,
        tmp_path / "waa",
        output_root=tmp_path / "out-b",
        prepared_at=fixed_time,
    )

    assert first["ready"] is True
    assert first["total_episodes"] == 8
    first_manifest = json.loads(Path(first["manifest_path"]).read_text(encoding="utf-8"))
    second_manifest = json.loads(Path(second["manifest_path"]).read_text(encoding="utf-8"))
    first_order = [episode["episode_id"] for episode in first_manifest["schedule"]]
    second_order = [episode["episode_id"] for episode in second_manifest["schedule"]]
    assert first_order == second_order
    assert all(episode["ready"] for episode in first_manifest["schedule"])
    assert first_manifest["source_spec"]["sha256"]
    assert any(item["kind"] == "directory" for item in first_manifest["artifacts"])
    assert Path(first["run_script_path"]).read_text(encoding="utf-8").count(
        "uv run --no-sync trace2task waa experiment"
    ) == 8
    run_script = Path(first["run_script_path"]).read_text(encoding="utf-8")
    assert "study-run.jsonl" in run_script
    assert "Tee-Object -FilePath $episodeLog" in run_script
    assert "param([string]$ResumeRunRoot = '')" in run_script
    assert "already completed; skipping" in run_script
    assert "Infrastructure/program failure; aborting study" in run_script
    assert "$episodeStatus = 'infrastructure_failed'" in run_script
    assert "Update-StudyRunSummary 'aborted'" in run_script
    assert "Study aborted after infrastructure/program failure" in run_script


def test_study_plan_reports_missing_artifacts_and_strict_fails(tmp_path: Path) -> None:
    waa_root = _write_waa_root(tmp_path, ["task-a"])
    spec = {
        "schema_version": "0.1",
        "study_id": "blocked-study",
        "path_base": ".",
        "seed": 1,
        "repetitions": 1,
        "targets": {"apps": 1, "task_instances": 1, "parameterized_families": 1},
        "conditions": [
            {
                "id": "mismatch",
                "runtime_mode": "trace",
                "taskpack_role": "mismatched_trace",
            }
        ],
        "tasks": [
            {
                "id": "task-a",
                "app": "notepad",
                "family_id": "family-a",
                "parameterized": True,
                "status": "planned",
                "taskpacks": {},
            }
        ],
    }
    spec_path = tmp_path / "blocked.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    result = prepare_waa_study(
        spec_path, waa_root, output_root=tmp_path / "out"
    )
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    codes = {issue["code"] for issue in manifest["readiness"]["issues"]}

    assert result["ready"] is False
    assert "task_not_ready" in codes
    assert manifest["readiness"]["ready_episodes"] == 0
    assert "taskpack_missing:mismatched_trace" in manifest["schedule"][0]["blockers"]
    with pytest.raises(RuntimeError, match="WAA study is not ready"):
        prepare_waa_study(
            spec_path,
            waa_root,
            output_root=tmp_path / "strict-out",
            strict=True,
        )


def test_study_plan_rejects_identical_auto_and_reviewed_snapshots(
    tmp_path: Path,
) -> None:
    task = _write_taskpack(tmp_path, "same", "trace")
    waa_root = _write_waa_root(tmp_path, ["task-a"])
    reset = tmp_path / "reset.json"
    reset.write_text("{}", encoding="utf-8")
    spec = {
        "schema_version": "0.1",
        "study_id": "compile-control",
        "path_base": ".",
        "seed": 3,
        "repetitions": 1,
        "targets": {"apps": 1, "task_instances": 1, "parameterized_families": 1},
        "conditions": [
            {
                "id": "auto",
                "runtime_mode": "compiled",
                "taskpack_role": "auto_compiled",
            },
            {
                "id": "reviewed",
                "runtime_mode": "compiled",
                "taskpack_role": "reviewed_compiled",
            },
        ],
        "tasks": [
            {
                "id": "task-a",
                "app": "notepad",
                "family_id": "family-a",
                "parameterized": True,
                "demonstration_variant": "demo",
                "evaluation_variant": "eval",
                "status": "ready",
                "waa_task_id": "task-a",
                "waa_json_name": "evaluation_examples_windows/study.json",
                "reset_spec": "reset.json",
                "taskpacks": {
                    "execution": str(task.relative_to(tmp_path)),
                    "auto_compiled": str(task.relative_to(tmp_path)),
                    "reviewed_compiled": str(task.relative_to(tmp_path)),
                },
            }
        ],
    }
    spec_path = tmp_path / "compile.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    result = prepare_waa_study(
        spec_path, waa_root, output_root=tmp_path / "out"
    )
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))

    assert "compile_variants_identical" in {
        issue["code"] for issue in manifest["readiness"]["issues"]
    }


def test_only_auto_compiled_study_arm_enables_the_frozen_draft() -> None:
    common = {
        "runtime_mode": "compiled",
        "taskpack": r"D:\snapshots\task.yaml",
        "execution_taskpack": r"D:\reviewed\task.yaml",
        "reset_spec": r"D:\reset.json",
        "waa_json_name": "evaluation_examples_windows/test.json",
        "episode_id": "episode",
    }

    automatic = _episode_command(
        {**common, "condition_id": "auto_compiled"},
        waa_root=Path(r"D:\WAA"),
        model="gpt-5.6-terra",
        reasoning_effort="low",
        output_root=Path(r"D:\out"),
    )
    reviewed = _episode_command(
        {**common, "condition_id": "reviewed_compiled"},
        waa_root=Path(r"D:\WAA"),
        model="gpt-5.6-terra",
        reasoning_effort="low",
        output_root=Path(r"D:\out"),
    )

    assert "--allow-automatic-compiler-draft" in automatic
    assert "--allow-automatic-compiler-draft" not in reviewed
