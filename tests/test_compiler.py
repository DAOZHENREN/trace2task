from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from trace2task.compiler import MINI_GAME_ACTIONS, compile_trace, confirm_taskpack
from trace2task.runner import create_reference_trace, run_agent
from trace2task.taskpack import load_taskpack


def test_compiler_builds_self_contained_reviewable_taskpack(tmp_path: Path) -> None:
    trace = create_reference_trace(7, tmp_path / "source")

    result = compile_trace(trace.trace_path, tmp_path / "compiled")
    task_path = Path(result.task_path)
    task = load_taskpack(task_path)
    report = json.loads(Path(result.report_path).read_text(encoding="utf-8"))
    task_yaml = yaml.safe_load(task_path.read_text(encoding="utf-8"))

    assert task.task_id == "daily-reward"
    assert task.environment_adapter == "trace2task.mini_game"
    assert task.verifier_type == "visual_status"
    assert task.actions == MINI_GAME_ACTIONS
    assert task.review_status == "draft"
    assert task.requires_confirmation
    assert result.demonstration_actions == len(trace.actions)
    assert result.stages == 3
    assert task_yaml["compiler"]["source_trace"] == "reference/trace.jsonl"
    assert task_yaml["verifier"]["reference_frame"].startswith("reference/frames/")
    assert (task_path.parent / "reference" / "trace.jsonl").is_file()
    assert (task_path.parent / "reference" / "metadata.json").is_file()
    assert len(list((task_path.parent / "reference" / "frames").glob("*.png"))) == len(
        trace.actions
    ) + 1
    assert [stage["id"] for stage in report["inference"]["stages"]] == [
        "navigate",
        "interact",
        "verify",
    ]
    for stage in report["inference"]["stages"]:
        for frame in stage["evidence_frames"]:
            assert (task_path.parent / frame).is_file()


def test_draft_must_be_confirmed_before_agent_execution(tmp_path: Path) -> None:
    trace = create_reference_trace(7, tmp_path / "source")
    compiled = compile_trace(trace.trace_path, tmp_path / "compiled")
    task_path = Path(compiled.task_path)

    with pytest.raises(RuntimeError, match="still a draft"):
        run_agent(19, provider="visual", task_path=task_path, show=False)

    confirmation = confirm_taskpack(task_path)
    result = run_agent(
        19,
        provider="visual",
        task_path=task_path,
        relocate_after=4,
        show=False,
        output_root=tmp_path / "runs",
    )

    assert confirmation.review_status == "confirmed"
    assert not confirmation.requires_confirmation
    assert load_taskpack(task_path).review_status == "confirmed"
    report = json.loads((task_path.parent / "compiler-report.json").read_text(encoding="utf-8"))
    assert report["review"]["status"] == "confirmed"
    assert not report["review"]["requires_confirmation"]
    assert "confirmed_at" in report["review"]
    assert result.success
    assert result.goal_changes == 1


def test_compiler_rejects_unsuccessful_metadata(tmp_path: Path) -> None:
    trace = create_reference_trace(7, tmp_path / "source")
    metadata_path = trace.trace_path.parent / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["success"] = False
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(ValueError, match="reports success"):
        compile_trace(trace.trace_path, tmp_path / "compiled")


def test_compiler_checks_final_visual_success_evidence(tmp_path: Path) -> None:
    trace = create_reference_trace(7, tmp_path / "source")
    events = [
        json.loads(line)
        for line in trace.trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    first_frame = trace.trace_path.parent / events[0]["frame"]
    final_frame = trace.trace_path.parent / events[-1]["frame"]
    shutil.copyfile(first_frame, final_frame)

    with pytest.raises(ValueError, match="visual success signal"):
        compile_trace(trace.trace_path, tmp_path / "compiled")
