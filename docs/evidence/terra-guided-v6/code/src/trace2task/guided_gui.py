"""Bounded supervisor/executor orchestration, independent of live VM transport.

The caller supplies an already-authorized environment, codec validator and durable
event sink. A stage-completion claim never closes the task or supplies its score.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass

from .sampo_desktop131 import GUI_ACTIONS


@dataclass(frozen=True)
class Observation:
    sequence: int
    image_path: str
    image_sha256: str


@dataclass(frozen=True)
class Stage:
    decision: str  # stage, finish, blocked
    goal: str
    completion_hint: str
    constraints: tuple[str, ...]
    allowed_actions: tuple[str, ...]
    action_budget: int

    @classmethod
    def parse(cls, value, max_actions):
        expected = {
            "decision",
            "goal",
            "completion_hint",
            "constraints",
            "allowed_actions",
            "action_budget",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError("invalid supervisor schema")
        if value["decision"] not in {"stage", "finish", "blocked"}:
            raise ValueError("invalid supervisor decision")
        if any(
            not isinstance(value[k], str) or not value[k].strip()
            for k in ("goal", "completion_hint")
        ):
            raise ValueError("stage needs goal and completion hint")
        if not isinstance(value["constraints"], list) or any(
            not isinstance(x, str) for x in value["constraints"]
        ):
            raise ValueError("invalid stage constraints")
        actions = value["allowed_actions"]
        if not isinstance(actions, list) or any(not isinstance(x, str) for x in actions):
            raise ValueError("invalid stage action list")
        if len(set(actions)) != len(actions) or not set(actions) <= GUI_ACTIONS:
            raise ValueError("stage may not widen GUI action space")
        budget = value["action_budget"]
        if type(budget) is not int or not 0 <= budget <= max_actions:
            raise ValueError("stage budget exceeds local limit")
        if value["decision"] == "stage" and (budget == 0 or not actions):
            raise ValueError("executable stage needs budget and actions")
        return cls(
            value["decision"],
            value["goal"],
            value["completion_hint"],
            tuple(value["constraints"]),
            tuple(actions),
            budget,
        )


@dataclass(frozen=True)
class GuidedConfig:
    max_decisions: int = 60
    max_guide_calls: int = 8
    max_stage_actions: int = 6
    repeated_no_change_limit: int = 3

    def __post_init__(self):
        if any(type(v) is not int or v < 1 for v in asdict(self).values()):
            raise ValueError("positive integer budgets required")


def executor_instruction(task, stage):
    return (
        f"Overall task: {task}\nCurrent stage: {stage.goal}\n"
        f"Stage completion hint: {stage.completion_hint}\n"
        f"Constraints: {json.dumps(stage.constraints, ensure_ascii=False)}\n"
        f"Allowed GUI actions: {', '.join(stage.allowed_actions)}\n"
        "Choose one GUI action from the CURRENT screenshot. If the stage is complete, "
        "return terminate(success) to hand control to the supervisor. If blocked, return "
        "terminate(failure). These are stage reports, not overall task success."
    )


def run_guided(task, environment, guide, executor, validate_action, record, config=None):
    """Run through injected ports; no VM reset, transport or reward-model code.

    environment: observe()->Observation, act(action)->{executed:bool, reason:str},
    close_policy(), evaluate()->{valid:bool, raw_score:number, ...}.
    guide(task, observation, recent_events, reason)->Stage JSON;
    executor(instruction, observation, executed_history)->GUI action or None.
    All infrastructure exceptions propagate after closing the policy; never zero.
    """
    config = config or GuidedConfig()
    history, events = [], []
    calls = decisions = executed = 0
    reason, last_sequence, last_key, repetitions = "task_start", -1, None, 0
    closed = False

    def emit(kind, **values):
        event = {"kind": kind, **values}
        record(event)  # Must succeed before a corresponding environment action.
        events.append(event)

    def observe():
        nonlocal last_sequence
        obs = environment.observe()
        if not isinstance(obs, Observation) or obs.sequence <= last_sequence:
            raise ValueError("missing or stale observation")
        if not obs.image_path or len(obs.image_sha256) != 64:
            raise ValueError("observation needs screenshot provenance")
        last_sequence = obs.sequence
        emit("observation", observation=asdict(obs))
        return obs

    try:
        obs = observe()
        while decisions < config.max_decisions and calls < config.max_guide_calls:
            calls += 1
            emit("guide_request", index=calls, reason=reason, observation=asdict(obs))
            raw = guide(task, obs, tuple(events[-24:]), reason)
            emit("guide_response", index=calls, raw=raw)
            stage = Stage.parse(raw, config.max_stage_actions)
            emit("stage", index=calls, stage=asdict(stage))
            if stage.decision != "stage":
                reason = "guide_" + stage.decision
                break
            last_key, repetitions = None, 0
            for _ in range(min(stage.action_budget, config.max_decisions - decisions)):
                decisions += 1
                action = executor(executor_instruction(task, stage), obs, tuple(history))
                emit("executor_decision", index=decisions, action=action, observation=asdict(obs))
                # Terminal actions are handoffs, never forwarded to environment.act.
                invalid = None
                try:
                    if not isinstance(action, dict) or action.get("action") not in GUI_ACTIONS:
                        raise ValueError("not a GUI action")
                    action = validate_action(action)
                    if (
                        action["action"] not in {"terminate", "answer"}
                        and action["action"] not in stage.allowed_actions
                    ):
                        raise ValueError("action outside current stage allowlist")
                except (ValueError, TypeError, KeyError) as exc:
                    invalid = str(exc)
                if invalid is not None:
                    emit("invalid_action", reason=invalid)
                    reason = "invalid_action"
                    obs = observe()
                    break
                if action["action"] in {"terminate", "answer"}:
                    reason = "executor_stage_report"
                    emit("handoff", reason=reason, report=action)
                    obs = observe()
                    break
                emit("execution_intent", action=action)
                result = environment.act(action)
                if not isinstance(result, dict) or type(result.get("executed")) is not bool:
                    raise ValueError("invalid environment action receipt")
                emit("execution_result", result=result)
                previous = obs
                obs = observe()
                if not result["executed"]:
                    reason = "execution_rejected"
                    break
                executed += 1
                history.append(
                    {
                        "image": {"path": previous.image_path, "sha256": previous.image_sha256},
                        "raw_output": "<tool_call>"
                        + json.dumps({"name": "computer_use", "arguments": action})
                        + "</tool_call>",
                    }
                )
                if result.get("handoff_reason"):
                    if not isinstance(result["handoff_reason"], str):
                        raise ValueError("invalid environment handoff reason")
                    reason = "environment_event:" + result["handoff_reason"]
                    break
                key = (previous.image_sha256, json.dumps(action, sort_keys=True))
                if obs.image_sha256 == previous.image_sha256:
                    repetitions = repetitions + 1 if key == last_key else 1
                else:
                    repetitions = 0
                last_key = key
                if repetitions >= config.repeated_no_change_limit:
                    reason = "repeated_action_without_visual_change"
                    break
                reason = "stage_budget_exhausted"
        if decisions >= config.max_decisions:
            reason = "total_decision_budget_exhausted"
        elif calls >= config.max_guide_calls and reason not in {"guide_finish", "guide_blocked"}:
            reason = "guide_call_budget_exhausted"
        closed = True  # Do not retry an irreversible close if it raises.
        environment.close_policy()
        verdict = environment.evaluate()
        emit("verifier", verdict=verdict)
        score = verdict.get("raw_score")
        if (
            verdict.get("valid") is not True
            or type(score) not in (int, float)
            or not math.isfinite(score)
            or not 0 <= score <= 1
        ):
            raise ValueError("invalid environment evidence; no synthetic reward")
        result = {
            "stop_reason": reason,
            "guide_calls": calls,
            "executor_decisions": decisions,
            "executed_actions": executed,
            "environment_score": score,
            "success": score == 1,
            "config": asdict(config),
        }
        emit("complete", result=result)
        return result
    finally:
        if not closed:
            environment.close_policy()
