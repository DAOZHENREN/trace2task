<p align="center">
  <img src="docs/assets/readme-hero.svg" alt="Trace2Task — Demonstrate once. Inspect every step." width="100%">
</p>

<p align="center">
  <strong>English</strong> · <a href="README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#from-demonstration-to-experience">The workflow</a> ·
  <a href="#choose-your-runtime">Models & runtimes</a> ·
  <a href="#documentation">Documentation</a>
</p>

**A Windows-first workbench for turning human demonstrations into inspectable GUI-agent experience.**

Record a workflow, choose what the model receives, and review what actually happened. At execution time, the agent uses the **current instruction and current screen**; recorded coordinates are reference data, not a replay script.

> [!NOTE]
> **Research preview, not unattended automation.** This README describes the source tree; older installers may differ. A delivered action or a model saying “done” is not independently verified task success.

## The workbench

<p align="center">
  <img src="docs/assets/workbench.png" alt="Trace2Task workbench with task instruction, model selection, experience selection and explicit execution scope" width="100%">
</p>

<sub>Actual interface in an isolated, read-only preview with a synthetic task. No model call or desktop action was run for this image. The current UI is Chinese.</sub>

- **See the exact input.** Inspect the complete saved experience text and the historical images that will be sent.
- **Keep versions, not mysteries.** Generation timestamps, duplicate-generation confirmation, preserved originals, and a restorable library trash.
- **Separate the choices.** Model provider, inference engine, execution backend, and authorized targets are visible before a task starts.
- **Review the run.** Follow incremental rounds, model requests and replies, dispatched actions, receipts, and stop reasons.

The React / TypeScript / Fluent UI workbench runs in a WebView2 desktop window or a local browser, backed by the same Python controller and data.

## Quick start

**Windows 10/11 · Python 3.11+ · [uv](https://docs.astral.sh/uv/) · WebView2 Runtime for the desktop window**

~~~powershell
git clone https://github.com/DAOZHENREN/trace2task.git
cd trace2task
uv sync --locked --extra desktop
uv run --extra desktop trace2task desktop
~~~

The desktop launcher requests Windows administrator approval (UAC); cancelling stops startup. Choose a data directory separate from the installation. Built workbench assets are included, so Node.js is not required just to launch the app.

Prefer a browser?

~~~powershell
uv run trace2task web
~~~

Open [localhost:8765](http://127.0.0.1:8765/). Add <code>--port 8766</code> if occupied. Do not run desktop-control tasks from multiple instances at once.

**Try it safely:** open an empty Notepad document → choose a model and **no experience** → enter a harmless instruction → check the scope and data destination → confirm. Use **Stop / F9** when needed. Model credentials, local weights, and optional recording/driver components require separate setup.

<details>
<summary>Existing data, development launcher, and installer builds</summary>

Reuse a data directory without moving or replacing it:

~~~powershell
uv run --extra desktop trace2task desktop --project-root "D:\Trace2TaskData"
~~~

After initial dependency setup, Windows source users can double-click <code>Start Trace2Task Dev.vbs</code>. Python changes require an app restart; frontend changes require a rebuild. See [source development](docs/desktop-development.md).

The repository provides [installer build scripts](docs/desktop-app.md), not bundled model weights or a promise of a published installer. Building uses Node.js, PyInstaller, and Inno Setup in addition to Python. Program files, recordings, and weights remain separate.

</details>

## From demonstration to experience

**Record → Generate → Inspect → Select → Execute → Review**

1. **Record.** OpenCUA captures a demonstration on the primary display. Finish with **F8**, cancel with **F9**; required components and recording limitations are [documented separately](docs/opencua-recording.md).
2. **Generate locally.** After official action derivation, create **D** for actions alone or **A** for actions with visual evidence. Neither generator calls a model.
3. **Inspect.** Open **查看模型原文** (“View model input”) to read the exact saved text and, for A, see every selected image. Raw files and audit metadata are separate.
4. **Select explicitly.** Choose a version as reference for a new task, or run without experience. Generating a version never silently activates it.
5. **Execute and review.** Observe the current screen, plan within the chosen scope, execute, and observe again. Review the run evidence instead of treating a stop signal as proof of success.

### What reaches the model?

| Representation | Model-facing experience | Status |
| --- | --- | --- |
| **N · No experience** | No demonstration; current task and observations only | Available |
| **D · Action sequence** | Ordered action descriptions, durations, visible child actions, and click coordinates | Available |
| **A · Actions + visual evidence** | The same D-style actions, with references to **at most 8** selected historical screenshots | Available |
| **B · Procedure** | A semantically compiled, frozen task method | Planned |
| **C · Evidence + Procedure** | The same A evidence plus the exact same B procedure | Planned |

A selects available images uniformly in temporal order, including the first and last; fewer than eight means all are selected. File paths, hashes, video clocks, and raw event logs stay in the **audit archive**, not the new A model text. Image alignment is approximate, not guaranteed pre-action state or proof of success.

Original recordings and compiled versions are independent. Re-generating the same representation asks for confirmation and preserves prior versions. Old A versions are not rewritten automatically.

**Scope matters:** legacy Windows/WAA task graphs, narrated compilation, and reviewed Guidance remain a separate workflow. They are not relabeled as B/C or silently inserted into the new A/D execution path. [Representation and versioning details →](docs/workbench.md)

## Choose your runtime

| Route | Implemented support | Important boundary |
| --- | --- | --- |
| **Codex CLI / subscription** | Visual execution; legacy compilation and revision | Separate CLI installation and authentication |
| **Vision model API** | User-configured Chat Completions endpoint | Must support images and the requested JSON format |
| **Qwen3-VL 8B** | Official Q4_K_M + F16 vision projection via llama-server | Separately provisioned and verified weights |
| **Qwen3-VL 2B / GUI-Owl 1.5 2B** | llama-server or Transformers | GGUF conversion/setup required for the llama route |
| **MAI-UI 2B** | Transformers | Experimental Windows adaptation of a mobile protocol |
| **D-5970 research model** | Frozen structured-action model protocol | Separate trusted model bundle; **no A/D experience input** |

The **D representation** and the **D-5970 model** are different things.

llama-server is the default local engine when no explicit setting exists. Existing choices are preserved; unsupported model/engine combinations fail rather than silently switching. Registered GUI models share a resident service and task conversation. [Local model setup →](docs/llama-gui-backend.md)

**Execution:** Win32 controls the foreground desktop; the experimental Cua path supports explicitly selected windows/apps. Background input is not universally supported. Neither model choice nor experience grants access to additional targets. [Execution contracts](docs/unified-execution-core.md) · [Cua limitations](docs/cua-experimental-backend.md)

## Evidence, privacy, and limits

- **Review before sharing.** Screenshots, typed text, traces, and model I/O can contain private information. Authentication-field redaction does not make an entire run safe to publish.
- **Know the destination.** Codex and remote APIs receive task inputs, screenshots, and selected experience. Local inference runs locally; initial dependency/weight downloads still require a network.
- **Treat control as real.** Start with disposable files. Stop cancels future work but cannot undo an action already delivered; latency depends on the backend.
- **Keep claims narrow.** Unit tests are not model-quality benchmarks. Application-level audits do not expose a provider’s hidden prompts or reasoning.
- **Keep data out of Git.** Do not commit recordings, generated experience, checkpoints, model weights, API keys, or unredacted run artifacts.

### Android preview

The separate [Android project](android/README.md) records accessibility events and sampled screenshots, compiles experience through a user-configured cloud vision API, and operates **one selected app**. It supports versioned feedback and emergency stop.

This is a developer preview: recording is not complete raw-touch capture, PC pairing is not implemented, and real-device/provider acceptance remains separate from JVM tests and APK builds.

### Research extensions

[Windows Agent Arena](integrations/windows_agent_arena/) separates task reset, execution, and evaluation. [RSIAgent](docs/rsi-integration.md) supports practice in a separately configured Linux VM. Legacy [LangGraph checkpoints](docs/langgraph-desktop.md) belong to their documented execution route, not every model backend.

Report the frozen artifacts and actual verification method used. A task reset is not a full VM restore; confirmation alone is not a distinct “reviewed compile” method. These integrations are tooling, not published benchmark results.

## Documentation

| Start here | Go deeper |
| --- | --- |
| [Workbench & A/D experience](docs/workbench.md) | [Action projection contract](docs/research-D-visual-projection.md) |
| [Desktop setup & packaging](docs/desktop-app.md) | [Source development](docs/desktop-development.md) |
| [llama-server model setup](docs/llama-gui-backend.md) | [Native model profiles](docs/local-gui-models.md) · [D-5970](docs/trained-model-local.md) |
| [OpenCUA recording](docs/opencua-recording.md) | [Narration & evidence](docs/narration-evidence.md) |
| [Run history & model I/O](docs/model-io-audit.md) | [Task conversations](docs/task-conversations.md) · [Context management](docs/local-prefix-cache.md) |
| [Android preview](android/README.md) | [WAA reports](docs/waa-report-format.md) · [Compiler snapshots](docs/compiler-snapshots.md) |

## Development

Use Node.js 24 for frontend work (the version used in CI).

~~~powershell
uv sync --locked --extra desktop --extra dev
npm ci --prefix frontend
npm run build --prefix frontend

uv run --extra dev pytest
uv run --extra dev ruff check .
node --test tests/*.test.cjs

# Browser regression tests; uses an isolated, write-disabled preview
cd frontend
npx playwright install
npm test
~~~

The offline suites do not call real models or drive the desktop. Platform-specific tests may skip when required capabilities are unavailable. Frontend bundles are committed for source startup; rebuild them whenever frontend sources change.

## Acknowledgements & license

Built with Python, React, Fluent UI, pywebview, and model/runtime integrations documented in their respective guides. Thanks to [OpenCUA](https://github.com/xlang-ai/OpenCUA), [OpenAdapt](https://github.com/OpenAdaptAI/OpenAdapt), [Windows Agent Arena](https://github.com/microsoft/WindowsAgentArena), and [OSWorld](https://github.com/xlang-ai/OSWorld) for open research and tooling.

Code is licensed under [Apache-2.0](LICENSE). Models, drivers, and external tools retain their own licenses and distribution requirements.
