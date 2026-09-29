from trace2task.experience_runtime import project_experience


def test_projection_selects_only_current_state_rules_and_transitions():
    source = {
        "task_id": "draft",
        "semantic": {
            "goal": "Save the draft",
            "completion": {"mode": "state", "success_condition": "saved"},
            "state_graph": {
                "entry_state_id": "editing",
                "states": [
                    {"id": "editing", "name": "Editing", "visual_anchors": ["editor"]},
                    {"id": "saving", "name": "Saving", "visual_anchors": ["dialog"]},
                ],
                "transitions": [{"id": "save", "source_state_id": "editing",
                                 "target_type": "state", "target_id": "saving"}],
                "terminals": [],
            },
        },
        "human_guidance": {"revision": 1, "rules": [
            {"id": "global", "scope": {"type": "global", "id": "global"}},
            {"id": "edit", "scope": {"type": "state", "id": "editing"}},
            {"id": "save", "scope": {"type": "transition", "id": "save"}},
            {"id": "dialog", "scope": {"type": "state", "id": "saving"}},
        ]},
    }
    editing = project_experience(source)
    saving = project_experience(source, "saving")
    unknown = project_experience(source, "unknown")
    assert [rule["id"] for rule in editing["human_guidance"]["rules"]] == [
        "global", "edit", "save"
    ]
    assert [rule["id"] for rule in saving["human_guidance"]["rules"]] == [
        "global", "dialog"
    ]
    assert unknown["candidate_state"] is None
    assert [rule["id"] for rule in unknown["human_guidance"]["rules"]] == ["global"]
    assert "source_scope" not in editing and "applicability" not in editing
