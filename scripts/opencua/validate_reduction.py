"""Offline synthetic contract probes for the pinned official Reducer; no GUI input."""
import argparse
import tempfile
from pathlib import Path

from derive import reduce_events
from worker import upstream


def click(t, pressed, x=100, y=100):
    return {"time_stamp": t, "action": "click", "button": "left", "pressed": pressed, "x": x, "y": y}


def key(t, action, name):
    return {"time_stamp": t, "action": action, "name": name, "pynput_key": name}


def validate(root):
    upstream(root)
    from core.action_reduction import Reducer
    from core.logger import logger
    logger.remove()
    cases = {
        "single": ([click(1, True), click(1.1, False)], "click"),
        "double": ([click(1, True), click(1.1, False), click(1.2, True), click(1.3, False)], "click"),
        "drag": ([click(1, True), {"time_stamp": 1.1, "action": "move", "x": 100, "y": 100},
                  {"time_stamp": 1.3, "action": "move", "x": 400, "y": 400}, click(1.5, False, 400, 400)], "drag"),
        # Pinned official version labels a stationary hold as click; duration survives.
        "hold": ([click(1, True), click(3, False)], "click"),
        "ctrl_c": ([key(1, "press", "ctrl_l"), key(1.1, "press", "c"),
                    key(1.2, "release", "c"), key(1.3, "release", "ctrl_l")], "press"),
        "typing": ([key(1, "press", "a"), key(1.1, "release", "a"),
                    key(1.2, "press", "b"), key(1.3, "release", "b")], "type"),
        "scroll": ([{"time_stamp": 1, "action": "scroll", "x": 100, "y": 100, "dx": 0, "dy": -1},
                    {"time_stamp": 1.1, "action": "scroll", "x": 100, "y": 100, "dx": 0, "dy": -1},
                    click(2, True), click(2.1, False)], "scroll"),
    }
    with tempfile.TemporaryDirectory(prefix="opencua-reduction-") as directory:
        for name, (events, expected) in cases.items():
            for i, event in enumerate(events): event["event_idx"] = i
            reducer = reduce_events(Reducer, Path(directory),
                                    {"screen_width": 1920, "screen_height": 1080}, events)
            actions = [action.complete_dump() for action in reducer.reduced_actions]
            assert actions, name
            actual = actions[0]["action"]
            # Ctrl+C may be represented as a parent press or long_press with children.
            assert actual == expected or name == "ctrl_c" and actual == "long_press", (name, actual)
            # Known upstream boundary bug: the first pair remains two single clicks.
            if name == "double":
                assert len(actions) == 2 and all(a["click_type"] == 1 for a in actions)
            if name == "hold": assert actions[0]["end_time"] - actions[0]["start_time"] == 2
            if name == "ctrl_c": assert actions[0].get("children")
            assert all("complete" not in event for event in events), "Input mutated"
            print(f"{name}: {actual} OK")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", required=True)
    validate(parser.parse_args().upstream)
