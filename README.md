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

## Local web console and semantic Trace compiler (v0.7.2)

Version 0.7.0 adds the offline multimodal Compiler Agent. Version 0.7.1 makes manual compilation a
visible background job: the activity panel responds immediately, concurrent duplicate requests are
rejected, and recompiling the same raw Trace reuses its latest task pack instead of creating another
copy. The console also provides recoverable delete buttons for task experiences, candidate
experiences, and raw recordings; deleted directories move under `taskpacks/.trash` or `runs/.trash`.
Version 0.7.2 separates the teacher Compiler Agent from the runtime Agent. Compiler model and
reasoning controls are visible in **Local experience**, shared with the recording page, and default
to Sol + high for a stronger one-time interpretation. Runtime planning and execution keep their own
Terra + low defaults. Each semantic experience card records and displays the model and effort that
compiled it, so a stronger teacher can guide a faster student without coupling their settings.

The loopback-only browser console runs the Compiler Agent after a successful Windows recording. The
recording is still compiled deterministically into motor evidence,
then the selected Codex model interprets the preserved human Trace as reviewable stages, grounded
states, action intents, visible preconditions, expected effects, and explicitly uncertain dynamic
decisions. Double-click
`start-console.cmd`, or start it once from a terminal:

```powershell
uv run trace2task web
```

The console opens at `http://127.0.0.1:8765/`. Its only business input is one natural-language
instruction, such as `给文件传输助手发送：Trace2Task 网页控制台测试`. The selected task pack remains
the reviewed demonstration experience: its original wording, action sequence, and reference frame
are structural hints rather than literal names, text, or coordinates to replay.

The default **自动选择经验** mode scores the instruction against confirmed local Trace metadata,
intent examples, target application, and required capabilities. The match is local, deterministic,
and explainable; it adds no second model request. A low-confidence or ambiguous match is rejected
before planning or execution, while the task-pack selector remains available as a manual override.
The selected Trace is mandatory planning guidance: the Agent preserves the demonstrated stage order
and checkpoints unless the current screenshot requires a reasoned deviation.

Windows recording samples keyboard and mouse transitions on a dedicated high-frequency thread.
Screenshots are still attached as visual evidence, but a slow target capture (notably Android
emulators) can no longer inflate click duration or move the recorded mouse-up coordinate to the
next interaction. The compiler uses the independent sample timestamp when it is present and remains
backward-compatible with older traces.
Stationary presses longer than one second compile to a bounded `hold_mouse` action, while pointer
movement remains a `drag`; the compiler no longer has to mislabel either behavior as a click.

The V0.7 artifact separates evidence from interpretation:

- `reference/trace.jsonl`, its frames, and `demonstration.json` remain immutable strong human
  evidence.
- `experience.yaml` is a replaceable Compiler Agent interpretation. Every stage covers a contiguous
  range of demonstrated actions and cites preserved evidence frames.
- Strategy that one demonstration cannot prove is marked `runtime_agent_decides` or `unknown`
  instead of becoming a fixed rule.
- Confirming a task pack confirms both its deterministic motor contract and the reviewed semantic
  experience. Runtime planning receives the semantic stages and their evidence, while the current
  screenshot still controls each Agent decision.

- **只生成计划** captures the selected target and asks the Agent for a read-only plan.
- **执行 Agent 模型 / 执行思考强度** selects the Codex model and reasoning effort for this run. Terra + low
  remains the default; Sol prioritizes capability, Luna prioritizes speed, and higher effort can
  improve difficult visual planning at the cost of a longer wait.
- **经验编译模型（教师）** independently selects the one-time semantic Compiler Agent. Sol + high
  is the default; changing the runtime Agent never changes this setting or an existing experience.
- **开始执行** requires a confirmed task pack, shows an explicit confirmation, and retains F9 as
  the emergency stop.
- **录制经验** lists visible local windows, selects the Compiler Agent model and effort, starts a
  human demonstration, and uses `F8` to mark success or `F9` to cancel. A successful trace is
  compiled automatically into a draft motor task and semantic experience.
- **本地经验** lists both task packs and raw recordings. It can open their folders in Explorer,
  confirm reviewed drafts, and upgrade legacy WeChat examples with reusable messaging actions.
- A successful executed run is saved under `runs/candidates/` as a **待审核候选经验**, including
  its source Trace, runtime instruction, execution trace, selection rationale, and run metrics. It
  never changes or promotes a confirmed task pack automatically.
- Only one desktop-control job can run at a time. Job state and progress logs are polled in the
  browser while the Python service owns execution.
- The server always binds to `127.0.0.1`; it has no LAN-facing mode or remote authentication.

The console detects legacy WeChat demonstrations that lack a text-entry motor skill. Those packs
remain available for planning, but execution is disabled until the template is upgraded; this
prevents a free-form message instruction from silently degrading into coordinate-only clicks.
The added `type_text` motor skill accepts bounded Unicode text, including Chinese and emoji, and
injects it independently of the active keyboard layout. It intentionally rejects newlines and does
not press Enter: sending or submitting remains a separate, screenshot-verified Agent step.

Messaging recordings are compiled semantically rather than as literal keyboard replay. Fast Pinyin
typing often contains harmless key rollover, so the compiler groups each text-entry burst into a
reserved `<runtime-text-N>` demonstration marker while preserving clicks, control keys, timing, and
visual evidence. At run time the Agent must resolve each marker from the one natural-language
instruction and the visibly focused field. Both the planner and motor executor reject an unresolved
marker, so it can never be typed literally. A saved recording can also be retried from **本地经验 →
编译为经验** without recording again.

If recording succeeds but automatic compilation fails for another reason, the console now reports
**录制成功** and keeps the raw trace, while showing the compilation error separately instead of
mislabeling the entire recording as failed.

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
├── experience.yaml          # V0.7 replaceable semantic interpretation
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

GPU-rendered applications that reject `PrintWindow` can explicitly request foreground capture:

```powershell
uv run trace2task windows capture `
  --process "game.exe" `
  --focus `
  --output runs\game.png
```

The command requests foreground focus and then waits up to ten seconds. If Windows blocks the focus
request, switch to the target manually while it waits; only then may it fall back to screen pixels.

The capture backend asks the selected process to render its client area through `PrintWindow`, so a
covered window does not leak pixels from the app placed over it. If an app cannot render that way,
screen-pixel fallback is permitted only while that exact target is foreground; otherwise capture
fails closed. Some GPU-rendered games may still return a black `PrintWindow` image and will require
foreground capture in a later adapter.

Record raw keyboard/mouse transitions and a target screenshot after every event:

```powershell
uv run trace2task windows record --handle 123456 --task-id external-daily
```

If a game or keyboard intercepts the function keys, choose different recorder controls:

```powershell
uv run trace2task windows record --handle 123456 --task-id external-daily `
  --success-key home --cancel-key end
```

The recorder first tries the normal Windows focus request. If Windows declines it, switch to the
target window yourself; recording begins only after the selected handle becomes visible,
unminimized, and foreground.

- Press `F8` to save a final success frame and finish successfully.
- Press `F9` to cancel the recording.
- `--success-key` and `--cancel-key` accept letters, digits, function keys, and supported special
  keys. The selected controls are reserved and are not written into the task trace.
- Recorder controls use both registered global hotkeys and physical key-state edges so GPU games
  that suppress `WM_HOTKEY` can still finish or cancel a recording.
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

## Game drag compilation (v0.5.7)

Mouse movement between a recorded button-down and button-up is compiled as a bounded `drag`
action. The action preserves normalized start/end coordinates, mouse button, and demonstrated
duration. Foreground execution interpolates the physical cursor while holding the button;
background execution sends held-button mouse-move messages directly to compatible windows. Both
paths release the button in a `finally` guard if execution is interrupted.

## Visible batched game execution (v0.5.8)

Foreground keyboard actions use Windows scan-code `SendInput`, which is recognized by more game
input stacks than virtual-key injection. Windows Agent execution now prints each model request,
received batch, action start, action completion, and exact failure message as it happens. The
default plan horizon is four actions so unambiguous adjacent steps can run locally after one model
decision, while loading screens and uncertain branches still force a new screenshot and replan.

Trace2Task does not attempt to bypass software that deliberately rejects synthetic input.

### Synchronous window-message input probe

When physical keyboard input works but the normal foreground scan-code path and background
`PostMessage` path do not, test the remaining synchronous HWND-message variant in isolation:

```powershell
uv run trace2task windows input-probe `
  --process "NRC-Win64-Shipping.exe" `
  --key f `
  --hold-ms 650
```

If keyboard synthesis is ignored but foreground mouse input works, temporarily bind the target
game action to the middle mouse button and test that distinct input route:

```powershell
uv run trace2task windows input-probe `
  --process "NRC-Win64-Shipping.exe" `
  --method send-input-mouse `
  --button middle `
  --hold-ms 120
```

The command focuses the selected window, waits one second, resolves its focused child HWND, and
sends paired `WM_KEYDOWN`/`WM_KEYUP` messages through `SendMessageTimeoutW`. A successful result
means Windows processed the messages; it does not prove that a game accepted the action. This
diagnostic does not change the Windows Agent's normal input method.

The next low-cost compatibility probe uses the virtual-key form of foreground `SendInput`
instead of scan codes:

```powershell
uv run trace2task windows input-probe `
  --process "NRC-Win64-Shipping.exe" `
  --method send-input-vk `
  --key f `
  --hold-ms 650
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

## Background Windows execution (v0.5.5)

An unminimized target may now remain behind another app during execution. Add `--background` to a
confirmed task-pack run:

```powershell
uv run trace2task windows agent `
  --task taskpacks\generated\<pack>\task.yaml `
  --execute `
  --background
```

Background mode keeps the current foreground app active and does not move the physical cursor. It
captures the target through `PrintWindow`, maps normalized mouse coordinates into the target client
area, and posts paired mouse/keyboard messages to the target HWND (or the matching child control).
The planner excludes `focus_window` from its active schema in this mode. Visibility, minimized
state, task confirmation, action bounds, per-action replanning, trace recording, and the `F9`
emergency stop remain enforced.

This is a best-effort Windows compatibility path. Apps that consume normal `WM_MOUSE*` and
`WM_KEY*` messages generally work; games using Raw Input, DirectInput, anti-cheat protection, an
elevated integrity level, or GPU-only rendering may ignore directed input or return unusable
`PrintWindow` frames. Run those targets in the default foreground mode. Minimized execution is not
supported yet.

## Foreground game capture (v0.5.6)

Games and other GPU-rendered targets often cannot render through `PrintWindow`. Use `--focus` on a
read-only Agent dry-run so the safe screen-pixel fallback can observe the actual foreground game:

```powershell
uv run trace2task windows agent `
  --task taskpacks\generated\<pack>\task.yaml `
  --focus
```

`--focus` changes only window focus during dry-run and still injects no input. It cannot be combined
with `--background`. Foreground `--execute` runs already request focus and now also wait up to ten
seconds for a manual switch when Windows refuses the programmatic request.

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
