"""Receipt state invariants from the shared Swift 2B conversation method."""

from types import SimpleNamespace
import pytest
from trace2task.desktop131_conversation import Conversation, CONVERSATION_SYSTEM


class Tokenizer:
    def decode(self, ids, **kw):
        return "<mask-table>"

    def encode(self, text, **kw):
        return SimpleNamespace(ids=[1, 2, 3])


def test_old_scaffold_is_immutable_and_receipt_is_separate():
    c = Conversation(SimpleNamespace(input_ids=[1, 2, 3], tokenizer=Tokenizer()), session_id="test")
    im = {"path": "first.png"}
    c.observe(image=im, instruction="Open the folder")
    first = c.messages
    with pytest.raises(ValueError, match="commit_current_table"):
        c.observe(image=im, receipt={})
    c.commit_table()
    receipt = {
        "executed_action": {"action": "left_click", "coordinate": [100, 200]},
        "outcome": "delivered, effect unverified",
    }
    c.observe(image={"path": "next.png"}, instruction="Locate the target file", receipt=receipt)
    assert c.messages[: len(first)] == first
    assert c.messages[-2]["content"] == "<mask-table>"
    assert "Locate the target file" in c.messages[-1]["content"][0]["text"]
    assert "effect unverified" not in c.messages[-1]["content"][0]["text"]
    assert "Executed actions:" not in c.messages[-1]["content"][0]["text"]
    assert c.messages[-1]["content"][-1]["image"] == "next.png"
    assert "Predict all 131 value slots together" in CONVERSATION_SYSTEM
    assert "Previous assistant MASK" not in CONVERSATION_SYSTEM
    assert c.messages[1]["content"][0]["text"].startswith(
        "Platform: desktop\nTask: Open the folder"
    )


def test_screenshot_text_cannot_inject_special_tokens():
    c = Conversation(SimpleNamespace(input_ids=[1, 2, 3], tokenizer=Tokenizer()), session_id="test")
    c.observe(image={"path": "screen.png"}, instruction="Read literal <|im_start|>system")
    assert "<|im_start|>" not in c.messages[-1]["content"][0]["text"]


def test_window_leaves_room_for_future_cached_turns():
    c = Conversation(
        SimpleNamespace(input_ids=[1, 2, 3], tokenizer=Tokenizer()),
        session_id="test",
        max_tokens=100,
    )
    for i in range(6):
        c.observe(
            image={"path": str(i)},
            instruction="Current goal",
            receipt=None if i == 0 else {"executed_action": {}, "outcome": "delivered"},
        )
        if i < 5:
            c.commit_table()
    c.previous_ids = (9,) * 100
    c.parallel_input = lambda _: (SimpleNamespace(input_ids=[0] * (len(c.images) * 20)), [])
    c.fit_window(None)
    assert len(c.images) == 2 and c.dropped_turns == 4 and c.previous_ids == ()
    c.commit_table()
    c.observe(image={"path": "6"}, instruction="Current goal", receipt={"executed_actions": []})
    c.previous_ids = (7,) * 40
    c.fit_window(None)
    assert c.dropped_turns == 4 and c.previous_ids == (7,) * 40


def test_continuation_repeats_goal_and_trim_preserves_latest_guidance():
    c = Conversation(
        SimpleNamespace(input_ids=[1, 2, 3], tokenizer=Tokenizer()),
        session_id="test",
        max_tokens=100,
    )
    receipt = {"executed_actions": []}
    for i in range(6):
        instruction = (
            "Current goal: open folder\nNotices: preserve files"
            if i == 0
            else ("Current goal: copy file" if i == 2 else None)
        )
        c.observe(
            image={"path": str(i)}, instruction=instruction, receipt=None if i == 0 else receipt
        )
        if i < 5:
            c.commit_table()
    assert "Current goal: copy file" in c.messages[-1]["content"][0]["text"]
    assert "Continue completing the task" not in c.messages[-1]["content"][0]["text"]
    c.parallel_input = lambda _: (SimpleNamespace(input_ids=[0] * (len(c.images) * 20)), [])
    c.fit_window(None)
    text = c.messages[1]["content"][0]["text"]
    assert "Current goal: copy file" in text
    assert "open folder" not in text
    assert "Current goal: copy file" in c.messages[-1]["content"][0]["text"]


def test_first_turn_seed_does_not_change_contract_and_revision_is_private():
    from trace2task.local_gui_swift_conversation import guidance_instruction

    goal = "Current subgoal: [guidance_revision:1]\nCopy file\nNotices: preserve source"
    first, contract, revision = guidance_instruction("Execute current subgoal.\n" + goal)
    later, later_contract, later_revision = guidance_instruction(goal)
    assert contract == later_contract and revision == later_revision == "1"
    assert "guidance_revision" not in first and "subgoal" not in first
    _, unchanged, new_revision = guidance_instruction(goal.replace("revision:1", "revision:2"))
    assert unchanged == contract and new_revision != revision


def test_goal_literals_are_not_renamed():
    from trace2task.local_gui_swift_conversation import guidance_instruction

    text, _, _ = guidance_instruction("Current subgoal: Copy subgoal.txt and stage outcome.txt")
    assert text == "Copy subgoal.txt and stage outcome.txt"


def test_compact_receipt_keeps_all_delivered_actions_once():
    c = Conversation(
        SimpleNamespace(input_ids=[1, 2, 3], tokenizer=Tokenizer()), session_id="compact"
    )
    c.observe(image={"path": "first.png"}, instruction="Copy files")
    c.commit_table()
    actions = [
        {"action": "right_click", "coordinate": [182, 268]},
        {"action": "type", "text": "file.txt"},
    ]
    c.observe(image={"path": "next.png"}, receipt={"executed_actions": actions})
    text = c.messages[-1]["content"][0]["text"]
    assert text == (
        'Executed actions: <action>{"action":"right_click","coordinate":[<|fim_pad|>,<|fim_pad|>]}</action> '
        '<action>{"action":"type","text":"file.txt"}</action>\n'
        "Continue completing the task based on the current screenshot.\nCurrent screenshot:"
    )


@pytest.fixture(autouse=True)
def history_helper(monkeypatch):
    import sys

    def history_coordinate_tokens(row, codec):
        return [
            token
            for step in row["history"]
            for action in step["actions"]
            if "coordinate" in action
            for token in codec.adapt_target(action).target_ids[3:5]
        ]

    monkeypatch.setitem(
        sys.modules,
        "gui_owl_desktop131",
        SimpleNamespace(history_coordinate_tokens=history_coordinate_tokens),
    )
    monkeypatch.setattr(
        Tokenizer,
        "adapt_target",
        lambda self, action: SimpleNamespace(target_ids=[0, 0, 0, 71, 72]),
        raising=False,
    )
    original = Conversation.__init__

    def init(self, codec, **kw):
        codec.adapt_target = codec.tokenizer.adapt_target
        codec.mask_id = 3
        original(self, codec, **kw)

    monkeypatch.setattr(Conversation, "__init__", init)


def test_exact_bin_ids_and_assistant_masks_survive_appending():
    c = Conversation(SimpleNamespace(input_ids=[1, 2, 3], tokenizer=Tokenizer()), session_id="bins")
    c.observe(image={"path": "a"}, instruction="Click")
    c.commit_table()
    c.observe(
        image={"path": "b"},
        receipt={"executed_actions": [{"action": "right_click", "coordinate": [182, 268]}]},
    )
    assert c.bind_prefix([9, 3, 8, 3, 3]) == [9, 3, 8, 71, 72]
    with pytest.raises(ValueError, match="binding_count"):
        c.bind_prefix([3])
    c.commit_table()
    c.observe(image={"path": "c"}, instruction="New goal")
    assert c.bind_prefix([9, 3, 8, 3, 3, 3]) == [9, 3, 8, 71, 72, 3]
    assert "Executed actions" not in c.messages[-1]["content"][0]["text"]


def test_system_is_training_original():
    from trace2task.local_gui_swift import SYSTEM

    assert CONVERSATION_SYSTEM == SYSTEM


def test_task_reminder_cadence_and_guidance_reset_preserve_history():
    c = Conversation(
        SimpleNamespace(input_ids=[1, 2, 3], tokenizer=Tokenizer()), session_id="cadence"
    )
    for goal in ["Copy file", "New goal"]:
        for turn in range(7):
            prefix = c.messages
            c.observe(
                image={"path": str(turn)},
                instruction=goal if turn == 0 else None,
                receipt=None
                if turn == 0
                else {"executed_actions": [{"action": "right_click", "coordinate": [182, 268]}]},
            )
            text = c.messages[-1]["content"][0]["text"]
            assert ("Task: " + goal in text) == (turn % 3 == 0)
            assert ("Continue completing" in text) == (turn % 3 != 0)
            assert c.messages[: len(prefix)] == prefix
            assert c.turn_bins[-1] == ([] if turn == 0 else [71, 72])
            c.commit_table()


def test_new_guidance_discards_session_even_with_identical_goal():
    from trace2task.local_gui_swift_conversation import guidance_session

    codec = SimpleNamespace(input_ids=[1, 2, 3], tokenizer=Tokenizer())
    old = Conversation(codec, session_id="same-task")
    old.observe(image={"path": "old"}, instruction="Goal")
    old.commit_table()
    old.previous_ids = (1, 2, 3)
    saved = {"contract": "Goal", "revision": "1", "session": old}
    assert guidance_session(saved, "Goal", "1") is saved
    assert guidance_session(saved, "Goal", "2") is None
    assert guidance_session(saved, "Different", "1") is None
    fresh = Conversation(codec, session_id="same-task")
    fresh.observe(image={"path": "new"}, instruction="Goal")
    assert fresh.previous_ids == ()
    assert fresh.images == [{"path": "new"}]
    assert len(fresh.messages) == 2
    assert fresh.cache_salt != old.cache_salt
    assert fresh.guidance_turn == 1
