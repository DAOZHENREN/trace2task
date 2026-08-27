import json

import pygame

from trace2task.game import WINDOW_SIZE, GameRenderer, GameState
from trace2task.recording import TraceWriter, load_actions


def test_trace_writer_round_trip(tmp_path) -> None:
    pygame.init()
    surface = pygame.Surface(WINDOW_SIZE)
    state = GameState.reset(3)
    GameRenderer().render(surface, state)
    writer = TraceWriter(tmp_path / "run", task_id="daily-reward", seed=3, source="test")
    writer.record("start", surface)
    writer.record("human_input", surface, action="move_right", details={"key": "d"})
    trace = writer.finish(success=False)

    assert load_actions(trace.trace_path) == ["move_right"]
    metadata = json.loads((tmp_path / "run" / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["event_count"] == 2
    assert (tmp_path / "run" / "frames" / "0001.png").exists()
