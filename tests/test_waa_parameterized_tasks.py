from __future__ import annotations

import ast
import json
import re
from pathlib import Path

INTEGRATION_ROOT = Path("integrations/windows_agent_arena")

VARIANTS = {
    "351f1d5e-f3f7-4efe-8fda-e8a8e9eacf4c-WOS": {
        "variant": "D0",
        "role": "demonstration",
        "recordable": True,
        "token": "example",
        "count": 7,
        "input": r"C:\Users\Docker\Documents\demo_corpus.txt",
        "output": r"C:\Users\Docker\Documents\example_count.txt",
        "task_list": "test_trace2task_count_token_d0.json",
    },
    "ea5daf01-d830-475d-863a-7863c618e489-WOS": {
        "variant": "E1",
        "role": "held_out_evaluation",
        "recordable": False,
        "token": "banana",
        "count": 11,
        "input": r"C:\Users\Docker\Documents\alpha_corpus.txt",
        "output": r"C:\Users\Docker\Documents\banana_count.txt",
        "task_list": "test_trace2task_count_token_e1.json",
    },
    "ef2d1903-cc63-46a7-9c1d-d510ab1960ee-WOS": {
        "variant": "E2",
        "role": "held_out_evaluation",
        "recordable": False,
        "token": "window",
        "count": 5,
        "input": r"C:\Users\Docker\Documents\beta_notes.txt",
        "output": r"C:\Users\Docker\Documents\window_count.txt",
        "task_list": "test_trace2task_count_token_e2.json",
    },
    "9d3ab930-5956-4b7c-9592-ffc3c0ded517-WOS": {
        "variant": "E3",
        "role": "held_out_evaluation",
        "recordable": False,
        "token": "agent",
        "count": 13,
        "input": r"C:\Users\Docker\Documents\gamma_log.txt",
        "output": r"C:\Users\Docker\Documents\agent_count.txt",
        "task_list": "test_trace2task_count_token_e3.json",
    },
}


def _source_text(command: str) -> str:
    tree = ast.parse(command)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "write_text"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            return node.args[0].value
    raise AssertionError("WAA setup command did not contain source.write_text")


def test_count_token_family_has_one_demo_and_three_held_out_variants() -> None:
    reset = json.loads(
        (INTEGRATION_ROOT / "reset_specs" / "notepad.json").read_text(
            encoding="utf-8"
        )
    )
    recordable = []
    for task_id, expected in VARIANTS.items():
        example = json.loads(
            (
                INTEGRATION_ROOT
                / "examples"
                / "notepad"
                / f"{task_id}.json"
            ).read_text(encoding="utf-8")
        )
        metadata = example["trace2task"]
        setup_command = example["config"][0]["parameters"]["command"][2]
        evaluator_command = example["evaluator"]["result"]["command"][2]
        source_text = _source_text(setup_command)
        observed_count = len(
            re.findall(
                rf"(?<!\w){re.escape(str(expected['token']))}(?!\w)",
                source_text,
            )
        )

        assert metadata == {
            "family_id": "count-token-occurrences",
            "variant_id": expected["variant"],
            "variant_role": expected["role"],
            "recordable": expected["recordable"],
        }
        assert observed_count == expected["count"]
        assert f"== '{expected['count']}'" in evaluator_command
        assert example["evaluator"]["expected"]["rules"]["expected"] == "true"
        assert json.loads(
            (INTEGRATION_ROOT / str(expected["task_list"])).read_text(
                encoding="utf-8"
            )
        ) == {"notepad": [task_id]}
        assert reset["tasks"][task_id]["must_not_exist"] == [expected["output"]]
        assert expected["input"] in setup_command
        if expected["recordable"]:
            recordable.append(task_id)

    assert recordable == ["351f1d5e-f3f7-4efe-8fda-e8a8e9eacf4c-WOS"]
