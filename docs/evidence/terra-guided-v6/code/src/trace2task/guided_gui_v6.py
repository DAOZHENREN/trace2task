"""Keep every prior subgoal plus two executed action/image pairs across stages."""

import json

from .guided_gui_v2 import run_guided_v2
from .guided_gui_v3 import executor_instruction
from .guided_gui_v4 import GuidedConfigV4, TerraStageGuideV4, supervisor_context

PROTOCOL = "terra-guided-v6-all-subgoals"
GuidedConfigV6 = GuidedConfigV4
TerraStageGuideV6 = TerraStageGuideV4


def run_guided_v6(task, environment, guide, executor, validate_action, record, config=None):
    revision = 0
    seen_revision = 0
    history_start = 0
    stage_goals = []
    carried_goals = []

    def supervise(task, observation, context, reason):
        nonlocal revision
        raw = guide(task, observation, supervisor_context(context), reason)
        if isinstance(raw, dict) and raw.get("decision") == "stage":
            revision += 1
            stage_goals.append(raw["goal"])
        return raw

    def execute(goal, observation, history):
        nonlocal seen_revision, history_start, carried_goals
        if revision != seen_revision:
            seen_revision = revision
            history_start = max(0, len(history) - 2)
            carried_goals = stage_goals[:-1].copy()
            record(
                {
                    "kind": "executor_history_carryover",
                    "stage_revision": revision,
                    "history_start": history_start,
                    "retained_actions": len(history) - history_start,
                    "previous_subgoals": carried_goals,
                    "observation_sequence": observation.sequence,
                }
            )
        instruction = goal
        if carried_goals:
            instruction = (
                "Previous subgoals (history context, not current instructions): "
                + json.dumps(carried_goals, ensure_ascii=False)
                + "\nCurrent subgoal: "
                + goal
            )
        return executor(instruction, observation, history[history_start:])

    return run_guided_v2(
        task,
        environment,
        supervise,
        execute,
        validate_action,
        record,
        config,
        protocol=PROTOCOL,
        instruction_builder=executor_instruction,
    )
