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
- `agent --provider visual` detects fixed pixel colors and uses A* as a deterministic baseline.
- `agent --provider codex` sends the current screenshot and task contract to a general multimodal
  model in one persistent session, then executes only actions allowed by the task pack.
- `verifier` checks the rendered success state rather than trusting the action sequence.
- `reset` creates deterministic but different layouts from integer seeds.

The original visual agent remains local and model-free. The Codex adapter is the first model-backed
implementation of the same control loop and consumes the existing task pack rather than reading
`GameState` or using the hard-coded color detector and A* planner.

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

The interaction is detected from physical `E` key events, text input, the live Pygame keyboard
state, and a Windows virtual-key fallback. The Windows fallback bypasses IMEs that swallow letter
events before they reach Pygame. `Enter` or `Space` can also trigger the interaction if needed.

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

## Multimodal Agent with a ChatGPT subscription

An API key is not required for the first model-backed adapter. It calls the installed Codex CLI,
which can reuse a saved ChatGPT subscription login. Verify the local login first:

```bash
codex login status
```

If needed, run `codex login` and complete the browser sign-in. Then start the multimodal agent:

```bash
uv run trace2task agent --provider codex --model gpt-5.6-terra --seed 19 --relocate-after 4
```

Each command starts one local Codex App Server process and one ephemeral conversation thread.
Every later replan sends a new screenshot into that same thread, so the model retains task context
without paying process and conversation startup cost again. The process is closed automatically
when the run finishes.

On Windows, Trace2Task also discovers the versioned `codex.exe` bundled inside the ChatGPT/Codex
desktop app, so an older terminal `PATH` does not break after an app update. If Codex is installed
somewhere else, pass it explicitly:

```powershell
uv run trace2task agent --provider codex --codex-bin "C:\path\to\codex.exe" --seed 19
```

The App Server thread is ephemeral and read-only. The planner prompt forbids tool use, and the
client rejects model-initiated approval/tool requests. Every final response must match a JSON Schema
generated from the task pack, so the model can only return one of the declared actions. By default,
the model plans up to 12 actions and the local motor controller executes them at 20 FPS. A blocked
action or environment change immediately discards the remaining batch and requests a fresh
screenshot-based plan.

Tune the planning/execution split if a later task needs shorter cautious plans or faster animation:

```bash
uv run trace2task agent --provider codex --plan-horizon 8 --motor-fps 30 --seed 19
```

On the seed-19 relocation scenario, the persistent implementation completed in 23.3 seconds with
2 model replans in one reference run. The earlier fresh-process implementation took about 93 seconds
and 5 replans on the same scenario. Model latency varies, so treat this as a local reference rather
than a fixed benchmark.

This bridge uses ChatGPT/Codex subscription limits rather than API billing. It deliberately keeps
the model at low-frequency decision points; frame-by-frame movement stays local because even a
persistent model turn can take several seconds. A later API or local-model adapter can implement
the same `AgentAdapter` contract without changing the recorder, executor, task pack, or verifier.

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
- Move the Codex prompt inputs entirely into compiler-generated task packs.
- Add pop-up recovery and changed-obstacle scenarios.
- Extend the local motor layer from grid moves to reusable desktop/game skills.
- Add adapters for desktop applications, browser tasks, and real game test environments.
