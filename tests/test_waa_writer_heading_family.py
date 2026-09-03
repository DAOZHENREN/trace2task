from __future__ import annotations

import json
from pathlib import Path


INTEGRATION_ROOT = Path("integrations/windows_agent_arena")

VARIANTS = {
    "7cfca519-892e-4179-af85-819924deeb16-WOS": {
        "variant": "D0",
        "role": "demonstration",
        "recordable": True,
        "heading": "Quarterly Research Summary",
        "path": r"C:\Users\Docker\Documents\Writer Tasks\research_overview.odt",
        "reset": r"C:\Users\Docker\Documents\Writer Tasks\research_overview.heading1.complete",
        "task_list": "test_trace2task_writer_heading_d0.json",
    },
    "0c37db62-b4a6-4f32-ba96-d73666b2601a-WOS": {
        "variant": "E1",
        "role": "held_out_evaluation",
        "recordable": False,
        "heading": "Project Aurora Update",
        "path": r"C:\Users\Docker\Documents\Writer Tasks\project_update.odt",
        "reset": r"C:\Users\Docker\Documents\Writer Tasks\project_update.heading1.complete",
        "task_list": "test_trace2task_writer_heading_e1.json",
    },
    "4374bb59-4a83-4c6e-9997-ae5d24a6ace1-WOS": {
        "variant": "E2",
        "role": "held_out_evaluation",
        "recordable": False,
        "heading": "Operations Readiness Brief",
        "path": r"C:\Users\Docker\Documents\Writer Tasks\operations_brief.odt",
        "reset": r"C:\Users\Docker\Documents\Writer Tasks\operations_brief.heading1.complete",
        "task_list": "test_trace2task_writer_heading_e2.json",
    },
    "9be63c39-3065-4ad9-a419-b359b3517294-WOS": {
        "variant": "E3",
        "role": "held_out_evaluation",
        "recordable": False,
        "heading": "Policy Review Memorandum",
        "path": r"C:\Users\Docker\Documents\Writer Tasks\policy_review.odt",
        "reset": r"C:\Users\Docker\Documents\Writer Tasks\policy_review.heading1.complete",
        "task_list": "test_trace2task_writer_heading_e3.json",
    },
}


def test_writer_heading_family_has_one_demo_and_three_held_out_variants() -> None:
    reset = json.loads(
        (INTEGRATION_ROOT / "reset_specs" / "libreoffice_writer.json").read_text(
            encoding="utf-8"
        )
    )
    recordable = []
    for task_id, expected in VARIANTS.items():
        example = json.loads(
            (
                INTEGRATION_ROOT
                / "examples"
                / "libreoffice_writer"
                / f"{task_id}.json"
            ).read_text(encoding="utf-8")
        )
        metadata = example["trace2task"]
        setup_command = example["config"][0]["parameters"]["command"][2]
        evaluator_command = example["evaluator"]["result"]["command"][2]

        assert metadata == {
            "family_id": "writer-heading-style",
            "variant_id": expected["variant"],
            "variant_role": expected["role"],
            "recordable": expected["recordable"],
        }
        assert expected["heading"] in example["instruction"]
        assert expected["heading"] in setup_command
        assert r"C:\Program Files\LibreOffice\program\soffice.exe" in setup_command
        assert "taskkill" in setup_command
        assert "-env:UserInstallation=" in setup_command
        assert ".~lock." in setup_command
        assert "assert out.is_file()" in setup_command
        assert "--convert-to','odt'" in setup_command
        assert r"AppData\Roaming\LibreOffice\4\user" not in setup_command
        assert expected["heading"] in evaluator_command
        assert expected["path"] in evaluator_command
        assert expected["reset"] in evaluator_command
        assert len(example["config"]) == 1
        assert example["config"][0]["type"] == "command"
        assert expected["path"] in example["instruction"]
        assert example["evaluator"]["expected"]["rules"]["expected"] == "true"
        assert json.loads(
            (INTEGRATION_ROOT / expected["task_list"]).read_text(encoding="utf-8")
        ) == {"libreoffice_writer": [task_id]}
        assert reset["tasks"][task_id]["must_not_exist"] == [expected["reset"]]
        assert reset["tasks"][task_id]["must_exist_after_setup"] == [
            expected["path"]
        ]
        if expected["recordable"]:
            recordable.append(task_id)

    assert recordable == ["7cfca519-892e-4179-af85-819924deeb16-WOS"]
