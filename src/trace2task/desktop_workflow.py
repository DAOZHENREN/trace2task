"""LangGraph planning checkpoints; real desktop effects are never auto-replayed."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context


class TaskState(TypedDict, total=False):
    identity: str
    instruction: str
    progress: dict
    proposed_progress: dict
    recent_actions: list
    pending_action: dict | None
    last_outcome: dict
    observation: str
    prompt: str
    raw: str
    status: str
    total_actions: int


PROGRESS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "current_subgoal": {"type": "string", "maxLength": 500},
        **{name: {"type": "array", "maxItems": 8,
                  "items": {"type": "string", "maxLength": 500}}
           for name in ("remaining_subgoals", "observed_evidence", "pending_checks")},
    },
    "required": ["current_subgoal", "remaining_subgoals", "observed_evidence", "pending_checks"],
}


def validate_progress(value):
    if not isinstance(value, dict) or set(value) != set(PROGRESS_SCHEMA["required"]):
        raise ValueError("Invalid task progress fields")
    for key, item in value.items():
        values = [item] if key == "current_subgoal" else item
        if not isinstance(values, list) or len(values) > 8 or any(
            not isinstance(text, str) or len(text) > 500 for text in values
        ):
            raise ValueError("Invalid task progress value")
    return value


def identity_for(instruction, experience):
    return hashlib.sha256(json.dumps([instruction, experience], ensure_ascii=False,
                                    sort_keys=True).encode()).hexdigest()


def load_resume(root: Path, identity: str):
    database = root / "checkpoints.sqlite"
    if not database.is_file():
        raise ValueError("所选运行没有 LangGraph 检查点")
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as conn:
        checkpoint = SqliteSaver(conn).get({"configurable": {"thread_id": "desktop"}})
    if not checkpoint:
        raise ValueError("检查点为空")
    state = checkpoint["channel_values"]
    if state.get("identity") != identity:
        raise ValueError("恢复时必须使用原任务指令及同一版本经验；请新建任务或恢复原设置")
    if state.get("status") == "complete":
        raise ValueError("该任务已声明完成，请新建任务")
    if state.get("pending_action"):
        raise ValueError("上次动作的执行结果不确定，不能自动恢复。请核对桌面后新建任务，说明已完成的操作")
    return {key: state[key] for key in ("identity", "instruction", "progress", "recent_actions",
                                        "last_outcome", "total_actions") if key in state}


class DesktopWorkflow:
    def __init__(self, root: Path, instruction: str, experience, resume_from=None):
        identity = identity_for(instruction, experience)
        initial = load_resume(Path(resume_from).resolve(), identity) if resume_from else {}
        self.root = root
        self.conn = sqlite3.connect(root / "checkpoints.sqlite", check_same_thread=False)
        self.request = None
        self.parser = None
        builder = StateGraph(TaskState)
        builder.add_node("observe", lambda state: {"status": "planning", "raw": ""})
        builder.add_node("plan", self._plan)
        builder.add_node("review", self._review)
        builder.add_edge(START, "observe")
        builder.add_edge("observe", "plan")
        builder.add_edge("plan", "review")
        builder.add_edge("review", END)
        self.graph = builder.compile(checkpointer=SqliteSaver(self.conn))
        self.config = {"configurable": {"thread_id": "desktop"}, "callbacks": []}
        self.graph.update_state(self.config, {
            "identity": identity, "instruction": instruction, "progress": {},
            "recent_actions": [], "pending_action": None, "last_outcome": {},
            "total_actions": 0, **initial, "status": "reobserve",
        }, as_node="review")
        self.export()

    @property
    def state(self):
        return self.graph.get_state(self.config).values

    def _plan(self, state):
        return {"raw": self.request()}

    def _review(self, state):
        payload = json.loads(state["raw"])
        progress = validate_progress(payload.pop("progress", None))
        self.parser(json.dumps(payload))
        return {"proposed_progress": progress, "status": "planned"}

    def plan(self, request, parser, prompt, observation):
        self.request, self.parser = request, parser
        try:
            # Always provide fresh input. Never invoke(None) to replay pending work.
            # Screenshots and task context remain local even if the user's shell
            # has enabled LangSmith tracing for another project.
            with tracing_context(enabled=False):
                value = self.graph.invoke({"prompt": prompt, "observation": observation}, self.config)
            self.export()
            payload = json.loads(value["raw"])
            payload.pop("progress")
            return value["raw"], parser(json.dumps(payload))
        finally:
            self.request = self.parser = None

    def update(self, **values):
        self.graph.update_state(self.config, values, as_node="review")
        self.export()

    def accept_observation(self):
        self.update(progress=self.state["proposed_progress"])

    def before_action(self, action):
        self.update(pending_action=action.to_payload(), status="executing")

    def after_action(self, action, result):
        self.update(pending_action=None, status="needs_observation",
                    recent_actions=(self.state["recent_actions"] + [action.to_payload()])[-8:],
                    last_outcome={"action": action.to_payload(), "delivery": result,
                                  "effect": "unverified_until_next_observation"},
                    total_actions=self.state["total_actions"] + 1)

    def context(self):
        state = self.state
        return json.dumps({"model_reported_progress": state["progress"],
                           "last_executor_outcome": state["last_outcome"],
                           "total_actions": state["total_actions"]}, ensure_ascii=False)

    def export(self):
        # Human-readable mirror, not the authoritative checkpoint database.
        state = {key: value for key, value in self.state.items() if key not in {"prompt", "raw"}}
        target = self.root / "task-state.json"
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(target)

    def close(self):
        self.conn.close()
