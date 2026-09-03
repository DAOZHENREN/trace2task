# Trace2Task

<p align="right">
  <strong>English</strong> | <a href="README.zh-CN.md">简体中文</a>
</p>

**Teach a Windows agent with a human demonstration, then improve it through reviewable feedback.**

Trace2Task records how a person completes a task and turns that evidence into a versioned experience
for a multimodal desktop Agent. The Agent does not blindly replay the original clicks or coordinates.
It observes the current screen, retrieves the relevant parts of the reviewed experience, plans a
bounded sequence of actions, executes them through a guarded local motor layer, and replans when the
visible state changes.

The current release is **v0.18.1**. It is a Windows-first research prototype with a local web console.

## Release progression

| Release | Main change |
|---|---|
| v0.14.6 | Established the adaptive experience console: reviewed semantic experience, iterative human guidance, and visible task details. |
| v0.15.x | Added an independent Effect Verifier and a repeatable reset-run-evaluate protocol so experience quality can be measured rather than judged only by observation. |
| v0.16.x | Added the Windows Agent Arena bridge and controlled `baseline` / `trace` / `compiled` / `feedback` ablations with aggregated success and efficiency metrics. |
| v0.17.0-v0.17.3 | Added synchronized WAA action recording and optional human narration, task-catalog-driven verified reset, recording cancellation, and residue cleanup. |
| v0.17.4 | Added a lightweight Compiler connection preflight, fail-fast paired compilation, and retry from preserved recordings without recording again. |
| v0.17.5 | Replaced the fixed Compiler response deadline with progress-aware streaming, a 90-second inactivity deadline, a 600-second hard limit, and precise retryable failure categories. |
| v0.18.0 | Added a paper-grade WAA study protocol: frozen artifact hashes, held-out variant checks, deterministic interleaved schedules, automatic-versus-reviewed Compiler controls, mismatched-Trace controls, feedback learning curves, and human-cost accounting. |
| v0.18.1 | Added the first leakage-controlled parameterized WAA family: one recordable D0 demonstration, three hidden held-out variants, self-contained setup/evaluation, and immutable pre-review Compiler snapshots. |

The architectural direction remains unchanged across these releases: immutable human Trace is the
primary evidence; Compiler output is reviewable derived knowledge; feedback is versioned; and the
runtime Agent must be evaluated behind an independent reset and effect-verification boundary.

The proposed research path for moving model inference off the runtime critical path is documented in
[Trace-Guided Low-Latency Desktop Agent](docs/research/trace-guided-low-latency-agent.md). It separates
current observations from hypotheses and defines measurable follow-up experiments for guarded action
programs, compact runtime context, and hierarchical model routing.

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

<p align="center">
  <img src="docs/research/assets/trace-guided-runtime-architecture-v2.png" alt="Trace2Task architecture: human evidence is compiled into guarded runtime knowledge, independently verified, and revised through a versioned learning loop" width="100%">
</p>

<details>
<summary><strong>Detailed control flow</strong></summary>

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
    J[Independent Effect Verifier]
    K[Human feedback]
    L[Guidance or task-model revision draft]
    M[Verification receipt]
    N[Repeatable evaluation suite]

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
    I -->|Agent claims completion| J
    J --> M
    M --> K
    N -->|reset + repeat| F
    M --> N
    K --> L
    L -->|human review and confirmation| G
    L -->|structural correction| E
```

</details>

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

### Verify effects and run repeatable evaluations

The runtime Agent may propose that a task is complete, but it no longer owns the final verification
label. A separate Effect Verifier writes `verification.json` for every executed run with an explicit
outcome:

- `verified`: an independent configured verifier accepted the effect;
- `completed_unverified`: the Agent visually judged the task complete, but no independent effect
  verifier was configured;
- `reconciliation_required`: the Agent and independent evidence disagree, so execution stops before
  a blind retry;
- `failed_execution` or `canceled`: execution did not reach an accepted completion.

Existing task packs use `reviewed_reference_frame` and therefore remain compatible, but their result
is honestly labeled `completed_unverified`. A task can opt into the deterministic
`pixel_reference` verifier with a reviewed threshold. The verifier registry is intentionally small:
application-specific UIA, API, file, database, or OSWorld evaluators can implement the same interface
without replacing the runtime Agent.

Evaluation suites define task cases, reset adapters, instructions, and repetition counts. Each
attempt preserves its Agent trace and verification receipt; the suite writes `attempts.jsonl` and a
machine-readable `summary.json` containing verified/completion rates, outcome counts, latency, model
turns, and action counts. This protocol follows the reset-run-evaluate separation used by desktop
benchmarks while remaining usable for local Trace2Task tasks.

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
2. Select either a local target window or **Windows Agent Arena VM**, then enter a unique
   experience name.
3. Choose the Compiler Agent model and reasoning effort.
4. Leave narrated recording enabled if spoken intent or task tricks would help the compiler.
5. Start recording, complete the task normally, and press `F8` at the successful state.
6. Review or correct the Turbo transcript, then start compilation.

For WAA narration recording, the console waits for the VM recorder to report `READY`, starts the
browser microphone, and then sends `GO` so the microphone and Trace share one timeline. `F8` stops
both sides after the WAA evaluator confirms success. After transcript review, the console generates
two task packs from the same immutable Trace: `· 纯Trace` and `· 人工讲解`.

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

Run a repeatable evaluation suite:

```yaml
# evaluations/fgo-smoke.yaml
schema_version: "0.1"
id: fgo-smoke
cases:
  - id: one-battle
    task: ../taskpacks/generated/<task-pack>/task.yaml
    instruction: 完成一次当前副本
    repetitions: 5
    reset:
      type: command
      argv: ["powershell", "-NoProfile", "-File", "reset-fgo.ps1"]
      timeout_seconds: 60
```

```powershell
uv run trace2task eval run `
  --suite evaluations\fgo-smoke.yaml `
  --model gpt-5.6-terra `
  --reasoning-effort low `
  --execute
```

Use `reset: {type: none}` only when the application is already self-resetting or when a dry-run suite
does not mutate it. New reset backends, including an OSWorld environment adapter, can be registered
without changing the evaluation runner. A `command` reset executes the listed local program, so only
run evaluation suites you have reviewed and trust.

### Windows Agent Arena controlled ablations (experimental)

The WAA adapter keeps the benchmark and Trace2Task responsibilities separate:

- Windows Agent Arena resets the Windows VM, supplies the task and screenshot, executes `pyautogui`
  actions, and owns the independent task evaluator.
- Trace2Task runs Codex on the Windows host using the existing subscription login and injects exactly
  one controlled evidence condition: `baseline`, `trace`, `compiled`, `narrated_compiled`, or
  `feedback`.
- Every condition uses the same fixed general motor-action policy. Trace-derived allowed-skill lists
  are deliberately not exposed to the baseline.

Keep WAA in a separate checkout because it owns a large VM image and generated benchmark results. A
checkout on `D:\MyProject\WindowsAgentArena` keeps those artifacts off the system drive. Download the
official Windows 11 Enterprise Evaluation x64 ISO from the
[Microsoft Evaluation Center](https://www.microsoft.com/en-us/evalcenter/download-windows-11-enterprise),
rename it to `setup.iso`, and place it under
`src\win-arena-container\vm\image`. Then install the small Trace2Task overlay:

```powershell
uv run python integrations\windows_agent_arena\install_overlay.py `
  D:\MyProject\WindowsAgentArena
```

On the first machine setup, build the development container and let WAA create its Windows golden
image. This is a one-time, disk- and network-heavy step; keep the checkout, Docker data, ISO, and VM
storage on `D:`:

```powershell
wsl -d Trace2Task-WAA -- bash -lc "cd /mnt/d/MyProject/WindowsAgentArena/scripts && \
  export TRACE2TASK_DOCKER_BUILD_ARGS='--network=host' && \
  ./run.sh --mode dev --prepare-image true --start-client false \
  --openai-api-key trace2task-bridge-unused"
```

If the WSL distribution needs the Windows host's local HTTP proxy while building, append proxy
build arguments to `TRACE2TASK_DOCKER_BUILD_ARGS`, for example
`--build-arg HTTP_PROXY=http://127.0.0.1:7892 --build-arg HTTPS_PROXY=http://127.0.0.1:7892`.
If that proxy rejects Debian package traffic, leave those proxy arguments empty and pass a reachable
mirror instead, for example `--build-arg TRACE2TASK_DEBIAN_MIRROR=https://mirrors.ustc.edu.cn/debian`
and `--build-arg TRACE2TASK_DEBIAN_SECURITY_MIRROR=https://mirrors.ustc.edu.cn/debian-security`.

Wait for the preparation container to finish and shut down cleanly. The overlay also installs a
VM-native human recorder. For later runs, start the prepared WAA VM without an Agent:

```powershell
wsl -d Trace2Task-WAA -- bash -lc "cd /mnt/d/MyProject/WindowsAgentArena/scripts && \
  ./run.sh --mode dev --skip-build true --start-client false \
  --openai-api-key trace2task-bridge-unused"
```

Open `http://localhost:8006`. The recommended recording path is now the Trace2Task web console:

1. Open **Record experience** and choose **Windows Agent Arena VM**.
2. Keep the default WAA root or select another checkout, and choose a WAA example JSON under its
   `client` directory.
3. Ensure the task id is covered by one JSON file under
   `integrations/windows_agent_arena/reset_specs`. Recording is blocked when no deterministic reset
   contract exists.
4. Enable narration and click **Start recording**. Before `READY`, the console closes task apps,
   applies the matching reset spec, verifies every invariant, and writes `reset-receipt.json`. It then
   performs a `READY → microphone → GO` handshake; no second PowerShell recorder command is needed.
5. Operate inside the VM and press `F8`. Review the Turbo transcript before compilation.

The archived `narration.json` stores `audio_start_trace_elapsed_ms`, so each speech segment is moved
onto the Trace clock before Compiler alignment. A bounded four-second forward window handles the
common case where the demonstrator explains an action shortly before performing it.

The following container command remains available as a narration-free/manual fallback:

```powershell
wsl -d Trace2Task-WAA -- bash -lc "docker exec -it winarena bash -lc 'cd /client && \
  python trace2task_human_trace.py \
  --example evaluation_examples_windows/examples/notepad/366de66e-cbae-4d72-b042-26390db2b145-WOS.json \
  --task-id waa-notepad-draft'"
```

Operate only inside the VM and press `F8` when done (`F9` cancels). A recording allows 30 minutes by
default and reports its remaining time every 30 seconds. `F8` now runs the independent WAA evaluator
before stopping: if validation fails, the same Trace remains active so the human can correct the
task and press `F8` again. The recorder saves raw key and mouse edges plus the corresponding VM
screenshots under `client\trace2task_recordings`, and marks the Trace successful only when the WAA
score is `1.0`.
Compile the saved `trace.jsonl` in the normal Trace2Task workflow, review the Compiler Agent state
graph, and add feedback revisions before using the `compiled` and `feedback` conditions. For a
narration ablation, generate two task packs from the same immutable Trace. Compile the first while
explicitly ignoring narration and compile the second with the reviewed human transcript:

```powershell
uv run trace2task windows compile-experience `
  --task taskpacks\generated\<plain-task-pack>\task.yaml `
  --ignore-narration `
  --model gpt-5.6-sol `
  --reasoning-effort high

uv run trace2task windows compile-experience `
  --task taskpacks\generated\<narrated-task-pack>\task.yaml `
  --model gpt-5.6-sol `
  --reasoning-effort high
```

`narrated_compiled` accepts only actual human narration. The WAA recorder's synthetic
`waa_task_instruction` transcript is labelled separately and does not qualify as human narration.

The bundled experimental task list selects one standard WAA Notepad task with a deterministic file
evaluator. Before an experiment, confirm the task pack and review its semantic experience. Then the
recommended one-command runner executes the same task three times per condition:

```powershell
uv run trace2task waa experiment `
  --waa-root D:\MyProject\WindowsAgentArena `
  --task taskpacks\generated\<plain-task-pack>\task.yaml `
  --narrated-task taskpacks\generated\<narrated-task-pack>\task.yaml `
  --feedback-task taskpacks\generated\<narrated-task-pack>\task.yaml `
  --reset-spec integrations\windows_agent_arena\reset_specs\notepad.json `
  --conditions baseline trace compiled narrated_compiled feedback `
  --repetitions 3 `
  --model gpt-5.6-terra `
  --reasoning-effort low
```

The fourth and fifth conditions can deliberately point to the same narrated task pack. In
`narrated_compiled` mode the Agent receives the narrated Compiler experience but ignores
`guidance.yaml`; in `feedback` mode it receives that same experience plus the reviewed guidance.
This keeps the original Trace and Compiler output fixed, so the fifth condition isolates the effect
of human feedback. `--feedback-task` defaults to `--narrated-task` when it is omitted.

The WAA VM and `winarena` container must already be running, but no second bridge terminal is needed.
The runner keeps the model, reasoning effort, task list, action policy, and plan horizon fixed. Before
every repetition it closes benchmark applications, moves the declared output files to the Windows
Recycle Bin, resets WAA, and verifies that the reset invariant still holds. A failed reset aborts the
episode before any model action. The Notepad reset declaration is intentionally explicit:

```json
{
  "schema_version": "0.1",
  "tasks": {
    "366de66e-cbae-4d72-b042-26390db2b145-WOS": {
      "must_not_exist": ["C:\\Users\\Docker\\Documents\\draft.txt"]
    }
  }
}
```

Reset paths must be absolute files below `C:\Users\Docker`; directories and the profile root are
rejected. WAA writes an independent `result.txt`, trajectory screenshots, actions, and timestamps
beneath `client\results\trace2task-experiments\<experiment-id>`. Trace2Task writes the aggregated
JSON and Markdown report under `evaluations\windows-agent-arena\<experiment-id>`. This makes the
baseline/Trace/compiled comparison use WAA's evaluator rather than model self-reports.

For diagnostics, a single condition can still be served manually with `trace2task waa bridge` and
existing result trees can be re-aggregated with:

```powershell
uv run trace2task waa report `
  --results-root D:\MyProject\WindowsAgentArena\src\win-arena-container\client\results `
  --output evaluations\windows-agent-arena
```

The report contains evaluator success rate, success-rate delta versus baseline, executed actions,
model plan calls, model round-trip seconds, and wall-clock task time for every condition.

### Stage 1 paper study protocol

`waa experiment` remains the small runner for one task and several runtime modes. Formal paper
experiments need another layer that freezes the research question before any result is observed. The
Stage 1 protocol lives at
`integrations\windows_agent_arena\studies\stage1.yaml` and currently defines:

- 22 task slots across Notepad, File Explorer, LibreOffice Writer, LibreOffice Calc, and Paint;
- matched `baseline`, raw Trace, `Trace Compile`, and `Narrated Trace Compile` arms on every task;
- four cumulative feedback revisions on a five-task subset;
- a mismatched-Trace negative control on that same subset;
- three repetitions, deterministic randomized interleaving, and evaluator, latency, planning,
  action, recovery, and human-effort metrics.

Prepare and audit the study without running an Agent:

```powershell
uv run trace2task waa study-plan `
  --spec integrations\windows_agent_arena\studies\stage1.yaml `
  --waa-root D:\MyProject\WindowsAgentArena
```

The command writes a frozen `study-manifest.json`, human-readable `episode-schedule.csv`,
`human-costs.csv`, a readiness report, and `run-ready-episodes.ps1` under
`evaluations\windows-agent-arena\studies\trace2task-stage1`. Every present task pack, reset declaration,
WAA task JSON, example JSON, and source specification is content-hashed. The source Git commit, dirty
status, and tracked-diff hash are recorded as well.

The first parameterized family is `count-token-occurrences`. It contains one visible demonstration
variant, D0, and three held-out evaluator variants, E1-E3. Only D0 appears in the web recorder. The
held-out variants use different file names, search tokens, document contents, output names, and exact
counts; their setup and evaluator run locally inside the WAA VM without a network dependency. A direct
recording request for E1-E3 is rejected by the server as well as hidden by the UI.

The second parameterized family is `find-file-by-content`. D0 asks the demonstrator to inspect opaque
text files, identify the one containing a requested marker, copy it into another Documents subfolder,
rename the copy, and preserve the source. E1-E3 vary the folders, marker, source position, and output
name. Only D0 is recordable; the held-out variants have deterministic VM setup and exact-content
evaluators.

Record D0 from the web console with a new experience name such as
`WAA count-token D0`. Perform the task shown by WAA and optionally narrate the reusable method rather
than the literal answer. Compiler snapshots remain available for provenance, but confirmation by itself
is a quality gate rather than a separate experimental method. Current studies report one `Trace Compile`
condition; create a separate human-edited condition only when a reviewer materially changes the semantic
graph or runtime guidance.

The checked-in Stage 1 file remains a research backlog rather than a fabricated completed dataset.
The count-token family has completed its first formal run. The three find-file evaluation rows remain
`planned` until its D0 recording, confirmed Compiler artifacts, and human costs have been attached. Use
`--strict` in CI or before a formal run to turn any remaining gap into a failing command.

When the manifest reaches `READY`, review its hashes and schedule, commit a clean checkout, and run
the generated PowerShell script in order. Do not manually regroup conditions: interleaving them is
part of the protocol and reduces time/model-drift bias.

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
- `reviewed_reference_frame` is model-assisted and is deliberately reported as unverified. A
  `pixel_reference` match is independent of the model but still proves pixels, not a backend business
  transaction. High-impact tasks need an application-specific effect verifier.
- Supervise new task packs until their graph, Guidance, and verifier behavior have been reviewed
  across multiple reset variants.

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
graphs, Guidance fusion and migration, Windows action validation, runtime recovery, effect receipts,
repeatable evaluation aggregation, web APIs, and voice transcription integration.

## Project status

Trace2Task is an evolving research prototype. V0.18.1 adds the first D0-to-held-out WAA task family
and freezes automatic Compiler output before review; V0.18.0 added a frozen, auditable WAA study-design layer
for measuring whether matched Trace and iterative experience improve success or efficiency. It does
not claim paper evidence before the declared held-out task slots are recorded and the readiness gate
passes. V0.17.5 replaced the Compiler's fixed absolute response
deadline with progress-aware streaming, a separate inactivity deadline, and a bounded hard limit, while
reporting first-token, stalled-response, hard-timeout, and connectivity failures separately. V0.17.4 added
a lightweight Codex connectivity preflight, fail-fast paired Compiler behavior, and retryable compilation
from preserved recordings. V0.17.3 added
explicit narrated-recording cancellation and
residue-free deletion for reset-spec benchmark artifacts. V0.17.2 added a reset-gated WAA task catalog and verified
task-level reset receipts to synchronized Windows Agent Arena Trace plus human-narration recording while retaining the controlled
four-condition experience ablations on top of the V0.15 verification boundary; it does not claim
that pixel verification proves every application effect. The experimental bridge provides an independent reset,
execution, and evaluator boundary for controlled experience ablations. Near-term work includes
repeated ablation reports, application-specific effect adapters,
better learned motor-skill boundaries, and finer narration/action alignment.

The verification contract is inspired by [OpenAdapt](https://github.com/OpenAdaptAI/OpenAdapt), and
the reset-run-evaluate separation is inspired by
[Windows Agent Arena](https://github.com/microsoft/WindowsAgentArena) and
[OSWorld](https://github.com/xlang-ai/OSWorld).
Trace2Task reimplements these boundaries around its own Trace-to-experience runtime rather than
embedding either project's control loop.

## License

Apache-2.0. See [LICENSE](LICENSE).
