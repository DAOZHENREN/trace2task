# Trace2Task

<p align="right"><strong>English</strong> | <a href="README.zh-CN.md">简体中文</a></p>

**Turn human demonstrations into reviewable, reusable knowledge for desktop agents.**

Record a workflow, optionally explain it aloud, compile it into a task model, and improve it through human feedback. At execution time, the agent uses the current screen and instruction—not a blind replay of recorded coordinates.

Trace2Task combines a **Windows desktop application**, a local web console, multiple model backends, and a research workflow for measuring whether demonstrations and experience actually help.

> **Status:** actively developed research software. This README describes the September 2026 source tree; package metadata remains `0.18.1`. An older installer does not necessarily contain every feature on `main`. Native model adapters and Cua are experimental. A valid action or model-reported completion is not proof of task success.

[Get started](#get-started) · [Capabilities](#current-capabilities) · [Models](#model-support) · [Evaluation](#evaluate-trace-and-experience) · [Docs](#documentation)

## The core idea

A macro preserves **where someone clicked**. A general GUI model sees **what is on screen now**. Trace2Task adds a third layer: **what a person demonstrated, why it worked, and what later feedback corrected**.

```mermaid
flowchart LR
    A[Human demonstration<br/>actions + screenshots + optional narration] --> B[Preserved raw Trace]
    B --> C[Compiler Agent]
    C --> D[Reviewed task model<br/>states, transitions, evidence]
    D --> E[Runtime Agent<br/>instruction + current screen]
    E --> F[Validate and execute<br/>observe again]
    F --> E
    F --> G[Run evidence + human feedback]
    G --> H[Review and merge revisions]
    H --> D
```

Three artifacts remain distinct:

- **Trace:** original screenshots, input events, timing, and optional narration. Revisions do not rewrite this evidence.
- **Task model / `experience.yaml`:** the Compiler's interpretation of states, intent, preconditions, transitions, and terminal outcomes. Humans can correct it.
- **Guidance / `guidance.yaml`:** versioned execution advice derived from reviewed feedback. Rules accumulate through explicit merges, not silent replacement of the previous round.

Baseline mode can execute without experience. The original WASD mini-game remains a regression fixture, not the primary product workflow.

## Current capabilities

### Record, compile, and revise

- Record one Windows application or the primary desktop across applications; record WAA VM demonstrations through the benchmark integration.
- Preserve raw mouse/keyboard events and screenshots. **F8** marks a demonstration complete; **F9** cancels.
- Record optional microphone narration, transcribe locally with Whisper Turbo, and correct the transcript before compilation. Natural-language fields also offer voice input.
- Select Compiler model and reasoning effort independently of the execution model.
- Compile evidence into a **directed task graph** with branches, loops, recovery edges, and separate terminal conditions—not just a linear numbered script.
- Review task details, evidence images, states, transitions, and active rules before confirming an experience.
- Revise either **task structure** or **execution tricks**. Guidance uses stable IDs and `add / update / keep / deprecate / conflict` operations with revision history.
- Edit summaries, inspect merged rules, and delete human guidance independently of its task. Supported asset deletion flows preserve recoverable copies.

### Execute from one instruction

```text
Open Notepad, type "hello", and save it as greeting.txt in Documents.
```

- Choose a target window or the primary desktop, with experience guidance or no-experience **Baseline**.
- General-agent workflows support manual experience selection and compatible automatic routing. Desktop experience mode needs an appropriate reviewed task model.
- Preview plans before input. General agents can return bounded multi-action batches; visual checkpoints, focus changes, or failures can discard remaining actions and trigger a new observation.
- Inspect progress, stop reasons, no-progress protection, and **F9 / Stop** controls. Cancellation latency depends on the backend.
- Optionally enable **LangGraph subgoal memory and SQLite checkpoints** on the general desktop path. Resume re-observes the desktop; it does not replay old coordinates or blindly retry uncertain effects.

Native 2B and D adapters currently run without compiled experience or LangGraph. They are not feature-equivalent to the general agent path.

### Desktop software and browser console

Both frontends use the same Python backend and task data.

- **Native window:** WebView2, data-folder selection, a per-data-directory instance guard, startup logs, and guarded exit during active jobs.
- **Installer tooling:** PyInstaller + Inno Setup produce a per-user Windows x64 application with bundled Python, shortcuts, and an uninstaller. Program files, data, and weights stay separate.
- **Local model controls:** start/stop recognized Trace2Task services, inspect loading/error state, and reuse loaded models across tasks.
- **Console:** recording, task/experience details, review, feedback, model selection, execution status, and per-round model I/O.

The installer does **not** provision model weights, Codex CLI, Cua Driver, or a benchmark VM. Closing the application does not automatically unload independent model services.

## Model support

| Backend | Use | Current boundary |
|---|---|---|
| **Codex subscription / CLI** | General execution, Compiler and Revision Agents | Separately installed/authenticated CLI required. Compilation, revision and WAA model calls still use this path. |
| **OpenAI-compatible model API** | General visual execution with your provider | Image input and compatible JSON output required. Custom IDs, reasoning controls, `json_schema` / `json_object`; provider capabilities vary. |
| **Qwen3-VL-8B-Instruct · Q4_K_M** | General local agent via llama.cpp | Separate runtime; uses the API-based experience workflow. Memory/context limits depend on configuration. |
| **Qwen3-VL-2B / GUI-Owl-1.5-2B / MAI-UI-2B** | Resident native GUI baselines | BF16 adapters, usually one next action per response, recent executed history, no experience/LangGraph. MAI is an experimental Windows adaptation of a mobile protocol. |
| **Trace2Task D / step 5970** | Custom structured-action research model | Fixed Qwen3-VL-2B base + language-attention LoRA + action head. Frozen validated records, not Chat Completions. Requires a separately provisioned trusted bundle; weights are not published here. |

The three native 2B models share a resident service; switching models unloads the previous one. D and llama.cpp use separate services and can compete for GPU memory.

Native outputs are parsed into supported action whitelists. Unsupported actions fail explicitly. D supports predicted action groups and continuous execution; other native adapters generally emit one next action. Their `done` / terminate output is a **model stop signal**, not independent success verification.

See [Qwen 8B](docs/local-model.md), [native 2B models](docs/local-gui-models.md), and [D integration](docs/trained-model-local.md).

## Control backends and task memory

| Mode | Scope | Limitation |
|---|---|---|
| **Win32 single window** | Selected application; task experience and configured verification | Background messages/capture depend on the app. No universal game or minimized-window support. |
| **Win32 primary desktop** | Foreground interaction across applications | Uses your desktop; human input/focus changes can invalidate a plan. Secondary monitors are not the current target. |
| **Cua Driver — experimental** | Explicitly selected windows/app launch targets for native models | Only the authorized scope reaches the model. No silent foreground fallback; compatibility depends on driver, app, and model protocol. |
| **LangGraph — optional** | Working subgoals/checkpoints for the general desktop agent | Not another model, a VM snapshot, long-term learning, or a guarantee of correct grounding. Uncertain action outcomes block automatic resume. |

Cua accepts up to 12 explicitly selected targets. Qwen 2B has a Cua-specific routing/scroll/drag protocol; do not assume D, GUI-Owl or MAI supports the same operations. An in-flight driver call can take approximately 20 seconds before stop is observed. Start with disposable documents.

## Inspect actual model inputs and actions

- Per-round timing, load/preprocess/generate/decode measurements where available, token counts, and native-model GPU allocation/reservation.
- Actual application-level inputs: task, system/user messages or frozen records, screenshots, generation settings, and applicable action schemas.
- Raw returned outputs, decoded actions, executor inputs/results, errors, cancellation, and discarded late responses.
- Run traces, screenshots, annotated predictions, and local paths for review and feedback.

General-agent audits use `io-audit/`; native local runs also provide `model-io.json` and per-round `model-io/` artifacts. **These are application-boundary logs, not TLS packet captures or access to a provider's hidden prompts/reasoning.** Old logs cannot recover fields never saved.

Logs may contain private screenshots, typed text, and experience. Redacting authentication fields does not make the remaining content safe to publish.

## Get started

### Run from source

Use Windows 10/11, Python 3.11+, and [uv](https://docs.astral.sh/uv/). The native window additionally needs Microsoft Edge WebView2 Runtime.

```powershell
git clone https://github.com/DAOZHENREN/trace2task.git
cd trace2task
uv sync --extra desktop
uv run --extra desktop trace2task desktop
```

Or launch the browser console:

```powershell
uv run trace2task web
```

Default URL: `http://127.0.0.1:8765/`; use `--port 8766` if occupied. Backend updates require a server restart, not just a page refresh.

Reuse an existing data directory from another checkout:

```powershell
uv run --extra desktop trace2task desktop --project-root "D:\Trace2TaskData"
```

Replace the example with your data directory. Do not run desktop-control tasks from multiple console instances simultaneously.

### Try a harmless task

1. Open **Execute task**, choose the scope, and start with blank Notepad or a disposable test page.
2. Select **Codex**, **Model API**, or **Local model**. Local runtimes and weights require separate setup; selection is not an automatic download.
3. Choose **Baseline / no experience**, or an experience supported by that backend.
4. Enter one instruction and inspect **Plan only**.
5. Confirm execution after checking the scope; use **Stop / F9** when needed.

For Codex-backed work, authenticate separately with `codex login`. A ChatGPT subscription does not supply credits for arbitrary API endpoints.

For APIs, configure endpoint, model ID, key, reasoning, and JSON format. Saved keys use current-user Windows DPAPI and are reused only for the same endpoint. If strict schema is unsupported, try `json_object`. Unsupported protocols remain errors, not silent model substitutions.

### Build an installer

The repository contains installer **source and build scripts**, not committed executables or weights. A local test installer is not necessarily a published GitHub Release.

```powershell
uv sync --extra desktop --extra dev
uv pip install -r packaging\windows\requirements-build.txt
.\scripts\build-desktop.ps1 -Iscc "D:\Tools\InnoSetup\ISCC.exe"
```

Install Inno Setup separately and replace its example path. Output: `dist/installer/`. The installed app bundles Python/dependencies, selects a separate data folder, and preserves data/weights on uninstall. Test builds are unsigned. [Packaging guide](docs/desktop-app.md).

## A complete experience workflow

1. **Demonstrate:** record a reusable method, optionally explaining choices. Preserve the Trace even if compilation fails.
2. **Compile:** interpret screenshots, actions, and reviewed narration into states, transitions, and expected effects.
3. **Review:** correct the graph, inspect evidence, and confirm the task pack.
4. **Execute:** use a new instruction/current screen, validate a bounded plan, execute, and re-observe.
5. **Inspect:** examine actual inputs/outputs, action results, screenshots, and stop reason.
6. **Improve:** review an incremental Guidance merge or structural revision, then activate it for later runs.

For example, demonstrate searching a contact and composing a message, then supply a different contact/text in a later instruction. Feedback such as “verify the conversation title before sending” becomes reviewed Guidance rather than another fixed coordinate script. Message sending is a real external action—test in a safe conversation first.

## Evaluate Trace and experience

The [Windows Agent Arena integration](integrations/windows_agent_arena/) separates reset, execution, and evaluation: VM recording, task catalogs, verified task-level reset receipts, compiler snapshots, schedules, and machine-readable reports.

| Condition | Information available to the runtime |
|---|---|
| **Baseline** | Instruction/current observation, no demonstration experience |
| **Raw Trace** | Demonstration evidence |
| **Trace Compile** | Semantics compiled from Trace |
| **Narrated Compile** | Semantics compiled from Trace plus human explanation |
| **Feedback** | Applicable compiled experience plus reviewed feedback |

Declare conditions and compatible frozen artifacts in the experiment specification. Human confirmation alone is **not** another method called “Reviewed compile”; only material edits justify a separate comparison. Not every task has all conditions or completed measurements.

Study tooling includes held-out variants, hashes, repetitions, and latency/action/model-call reports. A remote WAA VM's **task reset is not a full VM snapshot restore**. WAA/VM setup is separate from the app. OSWorld is a design reference, not a shipped integration.

Configured window-task Effect Verifiers can produce independent receipts. Screenshot/model-only completion is unverified; desktop/native runs must not be reported as independently successful merely because the model returns `done`.

## Privacy and practical limits

- Loopback control interfaces, not a hosted multi-user service.
- Codex/cloud APIs receive planning inputs. Local inference stays on this computer; initial dependency/weight downloads still need network access.
- Unknown actions, stale focus, limits, and uncertain outcomes can stop execution. Guards do not make arbitrary unattended actions risk-free.
- Multi-action support differs between adapters. Predicting a group from one screenshot does not imply intermediate observation inside that group.
- Background control is not universal; do not assume game, minimized-window, elevated-app, or anti-cheat compatibility.
- Local paths/GPU presets reflect a Windows development environment. Configure your own paths and measure available memory.
- Installer/unit tests do not establish model accuracy or broad application compatibility. No automatic updater or tray workflow yet.
- Do not commit `runs/`, generated task packs, checkpoints, weights, keys, or unredacted recordings.

## Documentation

| Topic | Guide |
|---|---|
| App installation and data lifecycle | [Desktop app](docs/desktop-app.md) |
| Recording, Baseline and experience | [Desktop execution](docs/desktop-baseline.md) |
| Qwen 8B via llama.cpp | [Local model setup](docs/local-model.md) |
| Qwen 2B, GUI-Owl, MAI and services | [Native GUI models](docs/local-gui-models.md) |
| Structured-action D model | [D integration](docs/trained-model-local.md) |
| Subgoals and recovery | [LangGraph](docs/langgraph-desktop.md) |
| Authorized background targets | [Cua backend](docs/cua-experimental-backend.md) |
| Model/executor logs | [I/O audit](docs/model-io-audit.md) · [Local runtime measurements](docs/local-model-runtime-validation.md) |
| Release review and remaining limits | [2026-09-22 audit](docs/release-audit-2026-09-22.md) |
| Speech and evidence | [Narration](docs/narration-evidence.md) |
| Reproducible research | [Compiler snapshots](docs/compiler-snapshots.md) · [WAA reports](docs/waa-report-format.md) |
| Research proposals, not shipped guarantees | [Low-latency direction](docs/research/trace-guided-low-latency-agent.md) |

<details>
<summary>Detailed research architecture</summary>

<img src="docs/research/assets/trace-guided-runtime-architecture-v2.png" alt="Trace-guided runtime and reviewed experience iteration" width="100%">

This describes architectural direction, not implementation of every verification boundary in every backend.

</details>

## Development

```powershell
uv sync --extra desktop --extra dev
uv run --extra desktop --extra dev pytest
uv run --extra dev ruff check .
node --check src\trace2task\web\app.js
node --test tests/*.test.cjs
```

Some tests need optional dependencies/platform capabilities. Run real model/application trials separately; unit tests are not cloud-provider validation or benchmark success measurements.

## Acknowledgements and license

Trace2Task draws on demonstration/verification ideas explored by [OpenAdapt](https://github.com/OpenAdaptAI/OpenAdapt), evaluation boundaries in [Windows Agent Arena](https://github.com/microsoft/WindowsAgentArena) and [OSWorld](https://github.com/xlang-ai/OSWorld), and components including LangGraph, pywebview, PyInstaller and Inno Setup. Model/prompt attribution is documented in the relevant guides and source.

Project code: [Apache-2.0](LICENSE). External models, drivers, and tools retain their own licenses and distribution requirements.
