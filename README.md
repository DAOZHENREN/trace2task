# Trace2Task

**Teach a Windows agent with a human demonstration, then improve it through reviewable feedback.**

Trace2Task records how a person completes a task and turns that evidence into a versioned experience
for a multimodal desktop Agent. The Agent does not blindly replay the original clicks or coordinates.
It observes the current screen, retrieves the relevant parts of the reviewed experience, plans a
bounded sequence of actions, executes them through a guarded local motor layer, and replans when the
visible state changes.

The current release is **v0.14.6**. It is a Windows-first research prototype with a local web console.

## Why Trace2Task?

A fixed macro is fast but brittle: a moved button, popup, loading delay, or changed task parameter can
break the whole sequence. A general desktop Agent is flexible but may lack the task-specific tricks a
human already knows.

Trace2Task keeps the useful middle layer:

- the original Trace is immutable evidence of what a human actually did;
- the Compiler Agent turns that evidence into reviewable task semantics;
- the runtime Agent adapts those semantics to the current screen and instruction;
- human feedback accumulates as versioned rules instead of silently overwriting earlier knowledge.

## Current workflow

```mermaid
flowchart LR
    A[Human demonstration<br/>actions + frames + optional narration]
    B[Immutable Trace evidence]
    C[Deterministic motor compiler]
    D[Multimodal Compiler Agent]
    E[Reviewed task pack<br/>episodes + directed state graph + terminals]
    F[Runtime instruction + current screenshot]
    G[Relevant active Guidance]
    H[Multimodal Agent<br/>multi-action plan]
    I[Guarded local execution]
    J[Run Trace + outcome]
    K[Human feedback]
    L[Guidance or task-model revision draft]

    A --> B
    B --> C
    B --> D
    C --> E
    D --> E
    E --> F
    G --> H
    F --> H
    H --> I
    I -->|visual checkpoint or exception| H
    I --> J
    J --> K
    K --> L
    L -->|human review and confirmation| G
    L -->|structural correction| E
```

## What the current version can do

### Record demonstrations

- Discover and select visible Windows application windows.
- Record keyboard and mouse down/up transitions on a dedicated input thread.
- Capture the target window after input events and preserve physical/DPI-safe coordinates.
- Mark a successful demonstration with `F8` or cancel with `F9`.
- Record an optional spoken explanation alongside the demonstration.
- Transcribe speech locally with Whisper Turbo and let the user correct it before compilation.

### Compile Trace evidence into an experience

- Convert raw input intervals into validated motor evidence such as clicks, key presses, hotkeys,
  text entry, holds, drags, and bounded waits.
- Use a separately selected teacher model to infer a canonical task instruction, Trace-backed
  episodes, action intent, visible preconditions, expected effects, uncertainty, and completion.
- Build a directed state graph with branches, loops, backward recovery edges, and separate terminal
  outcomes. Runtime control is not forced to follow the demonstrated episode order linearly.
- Bind evidence images to the exact Trace action ranges selected by the Compiler Agent.
- Keep recorded coordinates out of runtime semantic Guidance so a new run does not copy stale screen
  positions.

### Execute with a multimodal Agent

- Accept one natural-language instruction; task parameters are inferred from that sentence.
- Automatically route the instruction to a compatible confirmed local experience, or let the user
  select one manually.
- Select runtime model and reasoning effort independently from the teacher model.
- Ask the model for an ordered multi-action plan when the visible continuation is predictable.
- Execute validated actions locally, use adaptive visual waits, and stop a batch immediately when an
  expected visual response does not appear.
- Replan from the latest screenshot in the same task session after recoverable exceptions.
- Preserve successful, failed, and manually stopped runs as feedback evidence when a run Trace exists.
- Support foreground execution and a best-effort background mode for compatible applications.

### Improve experience over multiple runs

Trace2Task separates two kinds of correction:

1. **Guidance revision** improves task-specific tricks without changing the task graph. The Revision
   Agent proposes `keep`, `add`, `update`, `deprecate`, or `conflict` operations against stable rule
   IDs. Confirmed rules accumulate across rounds; unresolved conflicts cannot be activated.
2. **Task Model Revision** corrects states, transitions, branches, recovery paths, or terminal
   conditions when the Compiler interpretation itself is wrong. The immutable raw Trace remains
   untouched, and every confirmed graph version is preserved.

Guidance rules can be scoped globally or to one state, transition, or terminal. The first runtime
planning turn receives the active merged summary and active rules. Later turns retrieve only the
global rules plus rules relevant to the current graph neighborhood. Older revisions, raw feedback,
and merge-operation explanations remain audit history and are not repeatedly injected into the
runtime model.

### Use voice throughout the console

Every natural-language field has a reusable **Voice input** control, including the runtime
instruction, experience name, narration correction, merged experience summary, run feedback, and
task-graph feedback. These controls reuse the same cached local Whisper Turbo model.

Ordinary dictation audio is written only to a temporary file and deleted after transcription.
Narrated demonstration audio follows the separate, explicit Trace archive flow.

## Requirements

- Windows 10 or Windows 11.
- Python 3.11 or newer.
- [`uv`](https://docs.astral.sh/uv/).
- A local Codex installation signed in with a ChatGPT subscription for model-backed compilation and
  execution. An OpenAI API key is not required for this adapter.
- A microphone only if narration or voice dictation is needed.

Whisper Turbo is downloaded on first use into `.cache/faster-whisper/` and then reused. The model is
approximately 1.6 GB. CUDA FP16 is preferred when compatible CUDA 12/cuDNN 9 libraries are available;
otherwise transcription falls back to CPU INT8.

## Quick start

```powershell
git clone https://github.com/DAOZHENREN/trace2task.git
cd trace2task
uv sync --extra dev
codex login status
uv run trace2task web
```

The console opens at [http://127.0.0.1:8765/](http://127.0.0.1:8765/). It binds only to the loopback
interface.

If Codex has not been authenticated yet:

```powershell
codex login
```

Trace2Task also discovers the versioned `codex.exe` bundled with the ChatGPT/Codex Windows desktop
app when the normal terminal `PATH` entry is unavailable.

## Recommended web-console flow

### 1. Record an experience

1. Open **Record experience**.
2. Select the target window and enter a unique experience name.
3. Choose the Compiler Agent model and reasoning effort.
4. Leave narrated recording enabled if spoken intent or task tricks would help the compiler.
5. Start recording, complete the task normally, and press `F8` at the successful state.
6. Review or correct the Turbo transcript, then start compilation.

The recording and compilation are separate outcomes. If semantic compilation fails, the raw Trace is
still retained and can be compiled again from **Local experience**.

### 2. Review and confirm

Open the generated task detail page and review:

- target process/window selection;
- allowed motor actions;
- canonical task instruction and completion condition;
- Trace-backed episodes and their before/after evidence images;
- directed states, legal transitions, recovery edges, and terminal outcomes;
- narration claims accepted or rejected by visual/action evidence.

A generated task pack remains a draft until it is explicitly confirmed.

### 3. Plan and execute

1. Open **Execute task**.
2. Use automatic experience selection or choose a confirmed experience.
3. Enter one natural-language instruction, for example:

   ```text
   给文件传输助手发送：Trace2Task 当前版本测试完成
   ```

4. Select the runtime Agent model, effort, and input mode.
5. Use **Plan only** to inspect the Agent's interpretation without injecting input.
6. Use **Start execution** and confirm the target. Press `F9` at any time for an emergency stop.

### 4. Feed a run back into the experience

Every run with a saved Trace appears under **Feedback runs**. Write concrete behavioral feedback,
generate a Guidance fusion draft, inspect the rule-level diff, and confirm it only when correct.

If the problem is structural—for example, a state needs a backward edge or success was modeled as an
ordinary numbered stage—use the separate task-graph feedback field and review the Task Model Revision
draft.

## How the runtime Agent uses a task pack

The runtime model does not receive the entire raw recording on every turn.

On the first planning turn it receives a compact task contract containing:

- the user's current instruction;
- the current screenshot;
- the canonical task description and completion policy;
- the directed state graph and current candidate state neighborhood;
- selected Trace evidence images and coordinate-free action categories;
- the active merged Guidance summary and applicable active rules;
- the system-level multi-action planning and safety contract.

After each batch, local visual checkpoints decide whether execution can continue, should wait, or
must return to the model. Later model turns use the latest screenshot, observed outcomes, compact
session history, and only Guidance relevant to the current state, eligible outgoing transitions, and
candidate terminals.

This split keeps the human Trace authoritative without turning it into a literal replay script.

## Task-pack artifacts

A current Windows task pack can contain:

```text
<task-pack>/
├── task.yaml                    # target, actions, limits, review state
├── demonstration.json          # deterministic motor evidence with provenance
├── compiler-report.json        # compiler decisions and source audit
├── experience.yaml             # active semantic episodes and directed state graph
├── experience-revisions/       # confirmed task-model history
├── guidance.yaml               # active merged human Guidance
├── guidance-revisions/         # confirmed Guidance history
└── reference/
    ├── metadata.json
    ├── trace.jsonl              # immutable raw human Trace
    ├── narration.json           # optional reviewed transcript and alignment
    └── frames/*.png             # preserved Trace evidence
```

Generated task packs, run traces, model caches, and deleted-item trash are local data and are ignored
by Git when stored in their normal generated locations.

## Advanced CLI

The web console is the recommended workflow. The lower-level commands remain useful for diagnostics
and automation.

List or capture Windows targets:

```powershell
uv run trace2task windows list
uv run trace2task windows list --process "Weixin.exe"
uv run trace2task windows capture --process "Weixin.exe" --focus --output runs\capture.png
```

Record without narrated web review:

```powershell
uv run trace2task windows record `
  --process "Weixin.exe" `
  --task-id "wechat-send-message"
```

Compile and confirm a saved recording:

```powershell
uv run trace2task compile runs\<recording>\trace.jsonl
uv run trace2task confirm taskpacks\generated\<task-pack>\task.yaml
```

Generate a read-only plan from a task pack:

```powershell
uv run trace2task windows agent `
  --task taskpacks\generated\<task-pack>\task.yaml
```

Execute a confirmed task pack:

```powershell
uv run trace2task windows agent `
  --task taskpacks\generated\<task-pack>\task.yaml `
  --model gpt-5.6-terra `
  --reasoning-effort low `
  --execute
```

Add `--background` only when the target accepts directed Win32 window messages and can render through
`PrintWindow`. Add `--focus` to a dry run when a GPU-rendered target requires foreground screen-pixel
capture.

## Safety model and limitations

- Model output is constrained by a task-specific JSON Schema and validated again before execution.
- Only actions declared by the reviewed task pack can run.
- Foreground execution verifies the selected target before every input action.
- `F9` is reserved as an emergency stop; interrupted holds release keys and mouse buttons in cleanup
  paths.
- Background mode requires a visible, unminimized window and is application-dependent. Raw Input,
  DirectInput, elevated processes, GPU-only rendering, or deliberate rejection of synthetic input may
  make it unavailable.
- Trace2Task does not attempt to bypass anti-cheat or software that intentionally rejects synthetic
  input.
- Screenshots used for model planning are sent through the signed-in Codex service. Do not run tasks
  on content you do not want that model to process.
- Visual completion and dynamic UI interpretation are still model-assisted. Supervise new task packs
  until their graph, Guidance, and verifier behavior have been reviewed across multiple runs.

## Legacy mini-game demo

The original WASD daily-reward mini-game remains only as a deterministic regression fixture for the
record/replay/replan loop. It is no longer the primary Trace2Task use case.

```powershell
uv run trace2task demo --show
```

## Development

```powershell
uv run pytest
uv run ruff check .
node --check src\trace2task\web\app.js
```

The current test suite covers the deterministic compiler, semantic experience loading, directed task
graphs, Guidance fusion and migration, Windows action validation, runtime recovery, web APIs, and
voice transcription integration.

## Project status

Trace2Task is an evolving research prototype. Near-term work includes stronger deterministic visual
verification, better learned motor-skill boundaries, finer narration/action alignment, explicit
approval policies for higher-impact actions, and reproducible quality/latency evaluation across task
families.

## License

Apache-2.0. See [LICENSE](LICENSE).
