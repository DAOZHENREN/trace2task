# Trace2Task

**Record once. Benchmark every agent.**

Trace2Task turns a successful workflow into a resettable, verifiable task that an agent must solve from the current state. It does not require the agent to replay the original path.

This repository currently contains a deliberately small game prototype that proves the core loop:

```text
human demonstration -> recorded trace -> changed environment
                                      -> fixed replay: FAIL
                                      -> visual agent: replan -> PASS
```

## What the prototype does

The task is simple: move a blue player to a gold daily-task marker and press `E`.

- `record` captures each human input, timestamp, screenshot, and final outcome.
- `replay` applies the same input sequence to another seeded layout.
- `agent` observes rendered pixels, detects the player, goal, and obstacles, then replans after every action.
- `verifier` checks the rendered success state rather than trusting the action sequence.
- `reset` creates deterministic but different layouts from integer seeds.

The visual agent is intentionally local and model-free for the first milestone. Pixel perception and A* provide a fast, reproducible motor layer; a multimodal model can later replace or augment perception and high-level planning without changing the task format.

## Quick start

Requires Python 3.11+ and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev
uv run trace2task demo
```

The automated demo records a reference solution on seed `7`, replays it on the changed layout from seed `19`, and runs the visual replanning agent while relocating the target once. Artifacts are written to `runs/demo/`.

To watch the comparison:

```bash
uv run trace2task demo --show
```

`demo --show` is an automatic comparison; it intentionally ignores manual WASD input. Use
the `record` command below for keyboard control.

## Record your own demonstration

```bash
uv run trace2task record --seed 7
```

Use `WASD` or the arrow keys to move. Stand on the gold marker or in an adjacent cell and press `E`. If the player is too far away, the status bar turns red and explains why the interaction failed. The command prints the generated `trace.jsonl` path.

After a successful interaction, a large `TASK COMPLETE` panel remains visible so you can confirm
that the demonstration was recorded. Press `Enter` or `Esc` to close the window and print the trace
path in the terminal.

The interaction is detected from physical `E` key events, text/IME input, and the live keyboard
state. `Enter` or `Space` can also trigger the interaction if needed.

You can tap a movement key for one grid cell or hold it for continuous movement. Every seeded
recording starts with all four neighboring cells clear, so each WASD direction gives immediate
feedback.

Replay the exact recorded actions on a different layout:

```bash
uv run trace2task replay runs/<run>/trace.jsonl --seed 19
```

Run the visual agent on that layout and move the target after four actions:

```bash
uv run trace2task agent --seed 19 --relocate-after 4
```

## Task pack

The first task definition lives in [`taskpacks/daily-reward/task.yaml`](taskpacks/daily-reward/task.yaml). A run produces:

```text
runs/<run-id>/
├── metadata.json
├── trace.jsonl
└── frames/
    ├── 0000.png
    ├── 0001.png
    └── ...
```

## Development

```bash
uv run pytest
uv run ruff check .
```

## Next milestones

- Compile raw demonstrations into candidate goals and verifiers with user confirmation.
- Add pop-up recovery and changed-obstacle scenarios.
- Separate reusable motor skills from model-driven high-level decisions.
- Add adapters for desktop applications, browser tasks, and real game test environments.
