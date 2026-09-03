from __future__ import annotations

import ast
import json
from pathlib import Path

import yaml

INTEGRATION_ROOT = Path("integrations/windows_agent_arena")

VARIANTS = {
    "c05b680d-bda5-48db-984b-c1024496d088-WOS": {
        "variant": "D0",
        "role": "demonstration",
        "recordable": True,
        "marker": "ORCHID-742",
        "source_dir": r"C:\Users\Docker\Documents\Project Inbox",
        "source_name": "item-c.txt",
        "output": r"C:\Users\Docker\Documents\Selected Files\orchid_brief.txt",
        "task_list": "test_trace2task_find_file_d0.json",
    },
    "01b0e251-121d-4918-b36d-290447122566-WOS": {
        "variant": "E1",
        "role": "held_out_evaluation",
        "recordable": False,
        "marker": "QUARTZ-918",
        "source_dir": r"C:\Users\Docker\Documents\Research Inbox",
        "source_name": "research-a.txt",
        "output": r"C:\Users\Docker\Documents\Research Results\quartz_record.txt",
        "task_list": "test_trace2task_find_file_e1.json",
    },
    "576d7689-7577-40ae-ba79-93efa9504343-WOS": {
        "variant": "E2",
        "role": "held_out_evaluation",
        "recordable": False,
        "marker": "NEBULA-305",
        "source_dir": r"C:\Users\Docker\Documents\Operations Queue",
        "source_name": "operation-d.txt",
        "output": r"C:\Users\Docker\Documents\Operations Results\nebula_report.txt",
        "task_list": "test_trace2task_find_file_e2.json",
    },
    "9cb5bec7-c4f9-4301-b95d-2294d04a31a9-WOS": {
        "variant": "E3",
        "role": "held_out_evaluation",
        "recordable": False,
        "marker": "LANTERN-664",
        "source_dir": r"C:\Users\Docker\Documents\Review Batch",
        "source_name": "review-b.txt",
        "output": r"C:\Users\Docker\Documents\Review Results\lantern_note.txt",
        "task_list": "test_trace2task_find_file_e3.json",
    },
}


def _literal_assignment(command: str, name: str):
    tree = ast.parse(command)
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"WAA setup command did not assign {name}")


def test_find_file_family_has_one_demo_and_three_held_out_variants() -> None:
    reset = json.loads(
        (INTEGRATION_ROOT / "reset_specs" / "file_explorer.json").read_text(
            encoding="utf-8"
        )
    )
    recordable = []
    for task_id, expected in VARIANTS.items():
        example = json.loads(
            (
                INTEGRATION_ROOT
                / "examples"
                / "file_explorer"
                / f"{task_id}.json"
            ).read_text(encoding="utf-8")
        )
        metadata = example["trace2task"]
        setup_command = example["config"][0]["parameters"]["command"][2]
        evaluator_command = example["evaluator"]["result"]["command"][2]
        files = _literal_assignment(setup_command, "files")
        source_content = files[expected["source_name"]]

        assert metadata == {
            "family_id": "find-file-by-content",
            "variant_id": expected["variant"],
            "variant_role": expected["role"],
            "recordable": expected["recordable"],
        }
        assert len(files) == 4
        assert expected["marker"] in source_content
        assert sum(text.count(expected["marker"]) for text in files.values()) == 1
        assert expected["source_name"] not in example["instruction"]
        assert expected["marker"] in example["instruction"]
        assert example["config"][1]["parameters"]["command"][1] == expected[
            "source_dir"
        ]
        assert expected["source_dir"] in evaluator_command
        assert expected["source_name"] in evaluator_command
        assert expected["output"] in evaluator_command
        assert "source.read_bytes() == result.read_bytes()" in evaluator_command
        assert example["evaluator"]["expected"]["rules"]["expected"] == "true"
        assert json.loads(
            (INTEGRATION_ROOT / expected["task_list"]).read_text(encoding="utf-8")
        ) == {"file_explorer": [task_id]}
        assert reset["tasks"][task_id]["must_not_exist"] == [expected["output"]]
        if expected["recordable"]:
            recordable.append(task_id)

    assert recordable == ["c05b680d-bda5-48db-984b-c1024496d088-WOS"]


def test_find_file_study_uses_four_nonredundant_conditions() -> None:
    family = yaml.safe_load(
        (INTEGRATION_ROOT / "studies" / "find_file_family.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert [condition["id"] for condition in family["conditions"]] == [
        "baseline",
        "raw_trace",
        "trace_compile",
        "narrated_trace_compile",
    ]
    assert family["targets"] == {
        "apps": 1,
        "task_instances": 3,
        "parameterized_families": 1,
    }
    assert [task["evaluation_variant"] for task in family["tasks"]] == [
        "01b0e251-121d-4918-b36d-290447122566-WOS",
        "576d7689-7577-40ae-ba79-93efa9504343-WOS",
        "9cb5bec7-c4f9-4301-b95d-2294d04a31a9-WOS",
    ]
    assert all(task["status"] == "ready" for task in family["tasks"])
    assert all(
        set(task["taskpacks"])
        == {"execution", "trace_compile", "narrated_trace_compile"}
        for task in family["tasks"]
    )

    stage1 = yaml.safe_load(
        (INTEGRATION_ROOT / "studies" / "stage1.yaml").read_text(encoding="utf-8")
    )
    condition_ids = [condition["id"] for condition in stage1["conditions"]]
    assert "trace_compile" in condition_ids
    assert "auto_compiled" not in condition_ids
    assert "reviewed_compiled" not in condition_ids
