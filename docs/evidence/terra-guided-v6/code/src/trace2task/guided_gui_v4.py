"""Screenshot-led supervisor and stage-local executor history; v3 stays reproducible."""

from .guided_gui_v2 import GuidedConfigV2, TerraStageGuideV2, run_guided_v2
from .guided_gui_v3 import SUPERVISOR_PROMPT as V3_PROMPT
from .guided_gui_v3 import executor_instruction

PROTOCOL = "terra-guided-v4-stage-history"
SUPERVISOR_PROMPT = V3_PROMPT.replace(
    "using the screenshot and action history.",
    "using the current screenshot, subgoals and handoff reason.",
)
GuidedConfigV4 = GuidedConfigV2


def supervisor_context(context):
    return {key: context[key] for key in ("current_subgoal", "prior_subgoals") if key in context}


class TerraStageGuideV4(TerraStageGuideV2):
    def __init__(self, session):
        super().__init__(session, prompt=SUPERVISOR_PROMPT)

    def __call__(self, task, observation, context, reason):
        return super().__call__(task, observation, supervisor_context(context), reason)


def run_guided_v4(task, environment, guide, executor, validate_action, record, config=None):
    def guide_without_execution_history(task, observation, context, reason):
        return guide(task, observation, supervisor_context(context), reason)

    return run_guided_v2(
        task,
        environment,
        guide_without_execution_history,
        executor,
        validate_action,
        record,
        config,
        protocol=PROTOCOL,
        instruction_builder=executor_instruction,
        reset_history_on_stage=True,
    )
