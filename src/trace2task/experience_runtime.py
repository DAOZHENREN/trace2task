"""Task-scoped, bounded projection of a reviewed experience for one model turn.

The stored graph is not an execution grant. A previous state report is only a
candidate: after an action the screenshot may already show a different state.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def project_experience(
    source: Mapping[str, Any] | None,
    state_hint: str | None = None,
) -> dict[str, Any] | None:
    if source is None:
        return None
    semantic = source["semantic"]
    graph = semantic["state_graph"]
    states = {state["id"]: state for state in graph["states"]}
    candidate = None if state_hint == "unknown" else (
        state_hint if state_hint in states else graph["entry_state_id"]
    )
    outgoing = [
        transition for transition in graph["transitions"]
        if transition["source_state_id"] == candidate
    ]
    neighbor_ids = {
        transition["target_id"] for transition in outgoing
        if transition["target_type"] == "state"
    }
    nearby = [state for state in graph["states"] if state["id"] in neighbor_ids]
    terminal_ids = {
        transition["target_id"] for transition in outgoing
        if transition["target_type"] == "terminal"
    }
    guidance = source.get("human_guidance")
    rules = []
    if guidance is not None:
        transition_ids = {transition["id"] for transition in outgoing}
        for rule in guidance["rules"]:
            scope = rule["scope"]
            if (
                scope["type"] == "global"
                or (scope["type"] == "state" and scope["id"] == candidate)
                or (scope["type"] == "transition" and scope["id"] in transition_ids)
                or (scope["type"] == "terminal" and scope["id"] in terminal_ids)
            ):
                rules.append(rule)
    return {
        "task_id": source["task_id"],
        "goal": semantic["goal"],
        "completion": semantic["completion"],
        "state_index": [
            {"id": state["id"], "name": state["name"],
             "visual_anchors": state["visual_anchors"]}
            for state in graph["states"]
        ],
        "candidate_state": states[candidate] if candidate is not None else None,
        "candidate_is_unverified": True,
        "nearby_states": nearby,
        "outgoing_transitions": outgoing,
        "candidate_terminals": [
            terminal for terminal in graph["terminals"]
            if terminal["id"] in terminal_ids
        ],
        "human_guidance": (
            {"revision": guidance["revision"],
             "summary": guidance.get("summary", ""), "rules": rules}
            if guidance is not None else None
        ),
    }


def known_state_ids(source: Mapping[str, Any] | None) -> set[str]:
    if source is None:
        return set()
    return {
        state["id"] for state in source["semantic"]["state_graph"]["states"]
    }
