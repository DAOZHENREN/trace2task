from __future__ import annotations

import ctypes
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import pygame

from trace2task import __version__
from trace2task.agent import VisualReplanningAgent
from trace2task.game import WINDOW_SIZE, GameRenderer, GameState
from trace2task.planner import a_star, path_to_actions
from trace2task.recording import RecordedTrace, TraceWriter, load_actions, make_run_dir
from trace2task.vision import VisualVerifier

HUMAN_KEY_ACTIONS = {
    pygame.K_w: "move_up",
    pygame.K_UP: "move_up",
    pygame.K_s: "move_down",
    pygame.K_DOWN: "move_down",
    pygame.K_a: "move_left",
    pygame.K_LEFT: "move_left",
    pygame.K_d: "move_right",
    pygame.K_RIGHT: "move_right",
    pygame.K_e: "interact",
    pygame.K_RETURN: "interact",
    pygame.K_SPACE: "interact",
}


@dataclass(frozen=True)
class RunResult:
    mode: str
    seed: int
    success: bool
    actions: int
    replans: int = 0
    goal_changes: int = 0
    relocations: int = 0
    trace_path: str | None = None


def _prepare_pygame(show: bool) -> pygame.Surface:
    if show:
        if not pygame.get_init():
            pygame.init()
        if not pygame.display.get_init():
            pygame.display.init()
        pygame.display.set_caption(f"Trace2Task {__version__} - Daily Reward")
        return pygame.display.set_mode(WINDOW_SIZE)
    if not pygame.font.get_init():
        pygame.font.init()
    return pygame.Surface(WINDOW_SIZE)


def _present(show: bool, fps: int, clock: pygame.time.Clock) -> bool:
    if not show:
        return True
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            return False
    pygame.display.flip()
    clock.tick(fps)
    return True


def _record_decision_details(decision_path: list[tuple[int, int]], reason: str) -> dict:
    return {"reason": reason, "path": [list(cell) for cell in decision_path]}


def human_action_for_event(event: pygame.event.Event) -> str | None:
    """Resolve physical keys and text/IME events to one task action."""
    if event.type == pygame.KEYDOWN:
        action = HUMAN_KEY_ACTIONS.get(event.key)
        if action is not None:
            return action
        if getattr(event, "unicode", "").casefold() == "e":
            return "interact"
    if event.type == pygame.TEXTINPUT and getattr(event, "text", "").casefold() == "e":
        return "interact"
    return None


def _pygame_interaction_key_is_pressed() -> bool:
    pressed = pygame.key.get_pressed()
    return bool(pressed[pygame.K_e] or pressed[pygame.K_RETURN] or pressed[pygame.K_SPACE])


def _win32_key_was_pressed(virtual_key: int) -> bool:
    """Read a physical key outside the IME/Pygame text event path."""
    try:
        state = ctypes.windll.user32.GetAsyncKeyState(virtual_key)
    except (AttributeError, OSError):
        return False
    # The high bit means currently down. The low bit means it was pressed
    # since the previous call, which catches short taps between frames.
    return bool(state & 0x8001)


def interaction_key_is_pressed() -> bool:
    if _pygame_interaction_key_is_pressed():
        return True
    # Windows virtual-key codes: E, Enter, Space.
    return any(_win32_key_was_pressed(key) for key in (0x45, 0x0D, 0x20))


def record_human(seed: int, output_root: Path, fps: int = 30) -> RunResult:
    surface = _prepare_pygame(show=True)
    state = GameState.reset(seed)
    renderer = GameRenderer()
    renderer.render(surface, state, mode="record")
    pygame.display.flip()
    pygame.key.set_repeat(180, 90)

    run_dir = make_run_dir(output_root, "human")
    writer = TraceWriter(run_dir, task_id="daily-reward", seed=seed, source="human")
    writer.record("start", surface)
    clock = pygame.time.Clock()
    running = True
    action_count = 0
    interact_was_pressed = False

    def apply_human_action(action: str, key_label: str, source: str) -> None:
        nonlocal action_count
        applied = state.apply(action)
        action_count += 1
        renderer.render(surface, state, mode="record")
        writer.record(
            "human_input",
            surface,
            action=action,
            details={"key": key_label, "source": source, "applied": applied},
        )

    while running:
        interaction_handled = False
        for event in pygame.event.get():
            if (
                event.type == pygame.QUIT
                or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE)
                or (
                    state.completed
                    and event.type == pygame.KEYDOWN
                    and event.key in {pygame.K_RETURN, pygame.K_SPACE}
                )
            ):
                running = False
            elif not state.completed:
                action = human_action_for_event(event)
                if action is not None:
                    key = getattr(event, "key", None)
                    key_label = pygame.key.name(key) if isinstance(key, int) else "text:e"
                    apply_human_action(action, key_label, "event")
                    interaction_handled = action == "interact"

        interact_is_pressed = interaction_key_is_pressed()
        if (
            not state.completed
            and interact_is_pressed
            and not interact_was_pressed
            and not interaction_handled
        ):
            apply_human_action("interact", "e/enter/space", "physical_key_poll")
        interact_was_pressed = interact_is_pressed

        renderer.render(surface, state, mode="record")
        pygame.display.flip()
        clock.tick(fps)

    trace = writer.finish(success=state.completed)
    pygame.key.set_repeat()
    pygame.display.quit()
    return RunResult(
        mode="record",
        seed=seed,
        success=state.completed,
        actions=action_count,
        trace_path=str(trace.trace_path),
    )


def create_reference_trace(seed: int, output_root: Path) -> RecordedTrace:
    surface = _prepare_pygame(show=False)
    state = GameState.reset(seed)
    renderer = GameRenderer()
    renderer.render(surface, state, mode="record")
    run_dir = make_run_dir(output_root, "reference")
    writer = TraceWriter(run_dir, task_id="daily-reward", seed=seed, source="reference")
    writer.record("start", surface)
    path = a_star(state.player, state.target, state.obstacles)
    for action in path_to_actions(path):
        state.apply(action)
        renderer.render(surface, state, mode="record")
        writer.record("human_input", surface, action=action, details={"generated": True})
    return writer.finish(success=state.completed)


def replay_trace(
    trace_path: Path,
    *,
    seed: int,
    show: bool = False,
    fps: int = 8,
    output_root: Path | None = None,
) -> RunResult:
    surface = _prepare_pygame(show=show)
    state = GameState.reset(seed)
    renderer = GameRenderer()
    renderer.render(surface, state, mode="replay")
    verifier = VisualVerifier()
    clock = pygame.time.Clock()
    actions = load_actions(trace_path)
    writer: TraceWriter | None = None
    if output_root is not None:
        writer = TraceWriter(
            make_run_dir(output_root, "replay"),
            task_id="daily-reward",
            seed=seed,
            source="fixed_replay",
        )
        writer.record("start", surface)

    applied_actions = 0
    for action in actions:
        state.apply(action)
        applied_actions += 1
        renderer.render(surface, state, mode="replay")
        if writer:
            writer.record("replay_action", surface, action=action)
        if not _present(show, fps, clock):
            break

    success = verifier.completed(surface)
    recorded = writer.finish(success=success) if writer else None
    if show:
        pygame.time.wait(700)
        pygame.display.quit()
    return RunResult(
        mode="replay",
        seed=seed,
        success=success,
        actions=applied_actions,
        trace_path=str(recorded.trace_path) if recorded else None,
    )


def run_visual_agent(
    seed: int,
    *,
    relocate_after: int | None = None,
    show: bool = False,
    fps: int = 10,
    max_actions: int = 300,
    output_root: Path | None = None,
) -> RunResult:
    surface = _prepare_pygame(show=show)
    state = GameState.reset(seed)
    renderer = GameRenderer()
    renderer.render(surface, state, mode="agent")
    agent = VisualReplanningAgent()
    verifier = VisualVerifier()
    clock = pygame.time.Clock()
    relocated = False
    writer: TraceWriter | None = None
    if output_root is not None:
        writer = TraceWriter(
            make_run_dir(output_root, "agent"),
            task_id="daily-reward",
            seed=seed,
            source="visual_agent",
        )
        writer.record("start", surface)

    action_count = 0
    while action_count < max_actions and not verifier.completed(surface):
        decision = agent.decide(surface)
        if decision is None:
            break
        state.apply(decision.action)
        action_count += 1
        if (
            relocate_after is not None
            and not relocated
            and action_count >= relocate_after
            and not state.completed
        ):
            state.relocate_target()
            relocated = True
        renderer.render(surface, state, mode="agent")
        if writer:
            writer.record(
                "agent_action",
                surface,
                action=decision.action,
                details=_record_decision_details(decision.path, decision.reason),
            )
        if not _present(show, fps, clock):
            break

    success = verifier.completed(surface)
    recorded = (
        writer.finish(
            success=success,
            extra={
                "replans": agent.replans,
                "goal_changes": agent.goal_changes,
                "relocations": state.relocations,
            },
        )
        if writer
        else None
    )
    if show:
        pygame.time.wait(700)
        pygame.display.quit()
    return RunResult(
        mode="agent",
        seed=seed,
        success=success,
        actions=action_count,
        replans=agent.replans,
        goal_changes=agent.goal_changes,
        relocations=state.relocations,
        trace_path=str(recorded.trace_path) if recorded else None,
    )


def run_demo(
    *,
    record_seed: int,
    changed_seed: int,
    relocate_after: int,
    output_root: Path,
    show: bool = False,
) -> dict:
    reference = create_reference_trace(record_seed, output_root)
    replay = replay_trace(
        reference.trace_path,
        seed=changed_seed,
        show=show,
        output_root=output_root,
    )
    agent = run_visual_agent(
        changed_seed,
        relocate_after=relocate_after,
        show=show,
        output_root=output_root,
    )
    summary = {
        "reference_trace": str(reference.trace_path),
        "fixed_replay": asdict(replay),
        "visual_agent": asdict(agent),
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "latest-demo.json").write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary
