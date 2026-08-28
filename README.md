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
- `compile` validates a successful trace and turns it into a self-contained draft task pack.
- `confirm` marks a reviewed generated task pack as executable.
- `windows list/capture/record/agent` discovers, records, plans, and explicitly executes Windows
  targets through guarded parameterized actions.
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

## Compile a recording into a task pack

Compile one successful recording instead of writing `task.yaml` by hand:

```powershell
uv run trace2task compile runs\<successful-human-run>\trace.jsonl
```

The compiler dispatches from the trace source. Mini-game traces require both successful metadata
and the rendered visual success signal; Windows traces require the human `F8` success marker,
balanced raw input, and DPI-safe physical coordinates. It then creates a self-contained directory
under `taskpacks/generated/` containing:

```text
<generated-taskpack>/
├── task.yaml
├── compiler-report.json
├── demonstration.json       # Windows task packs
└── reference/
    ├── metadata.json
    ├── trace.jsonl
    └── frames/*.png
```

Version 0.4 supports the built-in mini-game adapter. It segments the demonstration into
`navigate`, `interact`, and `verify` stages. The complete movement vocabulary comes from the
adapter rather than only the demonstrated directions, so the resulting agent can solve changed
layouts.

Generated packs start as drafts and cannot execute accidentally. Review the instruction, declared
actions, compiler report, and final reference frame, then confirm the exact printed task path:

```powershell
uv run trace2task confirm taskpacks\generated\<generated-taskpack>\task.yaml
```

For a mini-game pack, the confirmed artifact can drive either agent implementation:

```powershell
uv run trace2task agent --task taskpacks\generated\<generated-taskpack>\task.yaml --seed 19
uv run trace2task agent --task taskpacks\generated\<generated-taskpack>\task.yaml --provider codex --seed 19
```

## Windows motor foundation (v0.5.1)

Version 0.5.1 introduces the safe execution foundation for controlling an external Windows app.
List visible top-level windows without changing focus or sending input:

```powershell
uv run trace2task windows list
uv run trace2task windows list --title "part of the window title"
uv run trace2task windows list --process "game.exe"
```

Each result includes the stable window handle, process ID/name, client-area bounds, DPI, visibility,
minimized state, and foreground state. A selector must resolve to exactly one window; ambiguous or
missing targets fail closed.

The new parameterized action contract supports:

- `focus_window`
- `click` and `double_click` with normalized client coordinates
- `press_key` and bounded `hold_key`
- two-to-four-key `hotkey`
- bounded `wait`

For example, a future compiled Windows task can request:

```json
{
  "skill": "hold_key",
  "args": {"key": "w", "duration_ms": 420}
}
```

The Windows motor executor validates the payload, requires the selected target to be visible,
unminimized, and foreground, maps normalized coordinates into its current client area, then uses
Win32 `SendInput`. Key-up and mouse-up events are sent from `finally` blocks so an interrupted skill
does not intentionally leave a key or button held.

The existing mini-game agent is not wired to this executor yet. Version 0.5.2 records the raw
Windows evidence consumed by the deterministic compiler in v0.5.3.

## Windows target recording (v0.5.2)

After finding an unambiguous target, capture its client area without changing focus:

```powershell
uv run trace2task windows capture --handle 123456 --output runs\target.png
```

The capture backend asks the selected process to render its client area through `PrintWindow`, so a
covered window does not leak pixels from the app placed over it. If an app cannot render that way,
screen-pixel fallback is permitted only while that exact target is foreground; otherwise capture
fails closed. Some GPU-rendered games may still return a black `PrintWindow` image and will require
foreground capture in a later adapter.

Record raw keyboard/mouse transitions and a target screenshot after every event:

```powershell
uv run trace2task windows record --handle 123456 --task-id external-daily
```

The recorder first tries the normal Windows focus request. If Windows declines it, switch to the
target window yourself; recording begins only after the selected handle becomes visible,
unminimized, and foreground.

- Press `F8` to save a final success frame and finish successfully.
- Press `F9` to cancel the recording.
- Input is polled but never injected by the recorder.
- Input from another foreground app is discarded while recording is paused.
- Mouse events include screen coordinates and target-client normalized coordinates.
- Window movement, resizing, and DPI changes are recorded as `window_changed` events.
- Window geometry, captures, cursor polling, and replay coordinates use physical pixels under
  per-monitor DPI awareness, so 125%/150% display scaling does not crop frames or shift clicks.

A Windows recording contains raw `down/up` transitions rather than prematurely guessed motor
skills:

```json
{
  "type": "windows_input",
  "details": {
    "raw_input": {"device": "keyboard", "event": "down", "key": "w"}
  }
}
```

## Windows trace compiler (v0.5.3)

Compile the successful Windows recording with the same top-level command:

```powershell
uv run trace2task compile runs\<windows-human-run>\trace.jsonl
```

The deterministic compiler produces `demonstration.json` and a draft Windows task pack. It:

- pairs every keyboard and mouse `down/up` transition;
- turns key intervals below 300ms into `press_key` and longer intervals into `hold_key`;
- recognizes Ctrl/Alt/Shift chords as `hotkey`;
- recognizes two nearby clicks within 500ms as `double_click`;
- inserts a bounded `wait` for observed idle gaps of at least 500ms;
- rejects drags, outside-target clicks, unreleased input, excessive holds, and concurrent gestures
  that the current motor vocabulary cannot reproduce safely.

Every generated action points back to its source sequence numbers, elapsed-time range, inference
rule, and evidence frame. The pack deliberately uses a generic instruction and a human-marked
reference-frame verifier, so it remains a draft until you inspect `task.yaml`,
`demonstration.json`, and `compiler-report.json`.

Version 0.5.3 compiles and reviews the Windows workflow. Version 0.5.4 connects the resulting pack
to the guarded multimodal Windows Agent loop below.

## Guarded Windows Agent (v0.5.4)

Start with the default read-only dry-run. It captures only the selected target client area, sends
the current image and the task pack's successful reference image to the Codex model, and prints a
strict parameterized action plan without changing focus or injecting input:

```powershell
uv run trace2task windows agent --task taskpacks\generated\<pack>\task.yaml
```

The dry-run may use a draft pack so you can inspect the proposed action before confirmation. The
images are sent to the configured Codex service through the existing ChatGPT subscription login;
do not run it on a window whose contents you do not want the model to process.

Before execution, edit the generic instruction and target selector if needed, inspect
`demonstration.json` and the reference frame, then confirm the exact pack:

```powershell
uv run trace2task confirm taskpacks\generated\<pack>\task.yaml
```

Execution requires both that confirmation and the explicit `--execute` flag:

```powershell
uv run trace2task windows agent --task taskpacks\generated\<pack>\task.yaml --execute
```

- `F9` is reserved as the emergency stop; waits and key holds poll it every 50ms.
- The target is focused before planning, and every non-focus action fails closed if focus changes.
- Model output is constrained by the task pack's parameterized JSON Schema and validated again
  locally before `SendInput`.
- The default plan horizon is one action, so the model sees a fresh screenshot after each action.
- The Codex session is persistent, read-only, approval-free, and network-disabled for tool use.
- Each executed action and resulting target screenshot is written to a new trace under `runs/`.

For v0.5.4, completion is a constrained model comparison between the live target image and the
human-reviewed final reference image. This is useful for the integration loop but is not yet a
deterministic region verifier; keep action limits small and supervise the first executions.

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

- Add deterministic reference-region verification and model-assisted semantic labels.
- Add approval checkpoints for high-impact actions and application-specific policies.
- Infer reusable motor-skill boundaries from longer demonstrations.
- Add pop-up recovery and changed-obstacle scenarios.
- Extend the local motor layer from grid moves to reusable desktop/game skills.
- Add adapters for desktop applications, browser tasks, and real game test environments.
