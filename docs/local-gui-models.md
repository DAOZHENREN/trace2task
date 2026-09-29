# Local 2B GUI models

The desktop application's **本地模型 · 本机 GPU** menu includes:

- Qwen3-VL-2B-Instruct: unmodified base, fixed revision `89644892e4d85e24eaac8bacfd4f463576704203`.
- GUI-Owl-1.5-2B-Instruct: `mPLUG/GUI-Owl-1.5-2B-Instruct`, revision `528ceaec795bbfbe6103bd79e03db849feadfb24`.
- MAI-UI-2B: `Tongyi-MAI/MAI-UI-2B`, revision `503050934809558c8dfd2ddedaf9621fa74ac2de`.

These use a shared resident service at **127.0.0.1:8768**, separate from D-5970 (8767)
and llama.cpp Qwen 8B (8081). Switching between these three unloads the previous
model. Starting a new task on the same model does not reload it. Other GPU services
must be stopped manually if they leave insufficient free VRAM.

## Download without proxy

```powershell
.venv\Scripts\python.exe scripts/local_gui/download.py
```

Default location: `D:\Models\Trace2Task-GUI`. All curl requests explicitly specify
`--noproxy '*'`, including HTTPS redirects. No changes to system proxy configuration.
Downloads use hf-mirror.com, immutable revisions resolved into download-manifest.json,
SHA256 for LFS and Git blob checksums for other files. `verified.json` records every
file SHA256. Existing matching Qwen base weights are reused, not LoRA/action heads.
Failed downloads remain `.part` and resume on rerun; a checksum failure is not accepted.

The service uses the already installed CUDA Python environment:
`D:\Models\Trace2Task-D-5970\.venv\Scripts\python.exe`.
Override with `TRACE2TASK_GUI_PYTHON`; no training environment is modified.
Reference dependencies: torch 2.7.1+cu128, transformers 4.57.1, Pillow, safetensors.

## Usage and scope

Select the model, enter a harmless task, and check the backend and target before starting.
The current execution page no longer exposes a separate plan-only button.
**开始执行** runs continuously after initial confirmation, with F9/stop and a 40-action
limit. Win32 binds the current desktop foreground; Cua binds only selected windows.
Confirmed experience is optional where the model input format supports it. Native
model replies are normalized to `ActionPlan`; the execution core checks the target,
then the selected backend adapter checks its own capabilities before dispatch.
An unsupported action produces no-input feedback for a fresh observation, while
authorization failures remain fatal. `done`/terminate is not proof of success.

These are **local adaptation profiles, not reproductions of official benchmark scores**:
BF16/SDPA, one screenshot, maximum 1,048,576 image pixels, 6,144 input tokens,
512 generated tokens and last four actually executed steps. Qwen can return up to
eight actions per response; GUI-Owl and MAI keep their native single tool call.
The core executes a valid batch in order without treating ordinary redraws as
an interruption. It rechecks the authorized target before every action and
reobserves after the batch; target/geometry changes and uncertain delivery stop
the remainder. Every delivered action gets its own receipt and history entry.
Over-budget, truncated, unknown or unsupported output is rejected, not executed.
If a fully generated answer fails action-format validation, the loop archives its
raw output, sends no input, and asks the model to regenerate from a fresh screenshot
with the parser error as feedback. It permits at most two correction attempts;
generation/transport errors and uncertain post-dispatch outcomes are not retried.

GUI-Owl uses the official desktop cookbook system prompt (MIT attribution in
`local_gui_owl_prompt.py`) without a backend-specific suffix. Its
`computer_use` coordinates are 0..1000. The intermediate adapter converts native
click, key, type, wait, scroll, drag, and pointer-move actions to the unified
protocol; the execution core then validates the bound target before dispatch.
An omitted click position or drag start uses only a cursor position established
by a previously delivered action in that target. It is never guessed from the
user's live pointer. Unsupported or unfaithful mappings produce an explicit
no-input feedback and a fresh observation, rather than silently changing the
requested action. Cua's window cursor move is currently overlay-only, so native
background hover is one such explicit capability gap. GUI-Owl `answer`,
`interact`, and failure termination are non-input control outcomes.
The driver checks its own action routes independently of model identity;
backend capabilities are not inserted into the model prompt. An authorized
window/app catalog can be supplied as neutral task data when applicable, and
unsupported routes receive explicit no-input feedback from the execution core.

In the desktop program, local GUI models expose an editable system prompt and
per-round user prompt template under Advanced Options. Profiles are saved per
model and Win32/Cua backend in `runs/local-gui/prompt-profiles.json` in the
selected data directory. The template must keep the task, executed history,
experience and execution-feedback placeholders. The old context placeholders
remain accepted but are optional; only authorized target references are filled.
Profiles are frozen when a task starts; the read-only
completion review keeps its separate fixed prompt. The model's actual system
and user messages, raw reply, and executor receipt are archived per round and
shown as a conversation in Agent Activity. Editing a prompt does not bypass
the action decoder, authorization scope, or execution core.
MAI's official navigation tool is `mobile_use`, coordinates 0..999. MAI's supplied
navigation prompt is mobile-oriented: our clearly labelled experimental Windows
adaptation removes the Android launcher and navigation commands. It is NOT an
official MAI desktop navigation recipe. Both use tool_call JSON, never Python eval.
MAI remains a separate, narrower Windows adaptation; its mobile launcher, swipe,
system buttons, and unsupported pointer actions fail explicitly rather than being
guessed or converted into unrelated Windows operations.

Qwen uses a direct structured JSON next-action prompt. This differs from D-5970's
frozen structured-head record format and should be disclosed in experiments.

## Audit

`runs/local-gui/<prediction-id>/` contains the screenshot, full messages,
formatted chat-template prompt, generation configuration, raw text/token output,
parsed executor actions, annotated screenshot, load/inference times and peak VRAM.
Run-level trace.jsonl records only delivered actions in subsequent history.
The authenticated local service has no screenshot upload path to external services.

## Upstream references

### Native service buttons

Under the local model selector, **启动本地模型服务** clears recognized old
Trace2Task model processes and loads the selected model. **关闭本地模型服务**
stops recognized GUI, D and Qwen 8B services, including previous-build remnants.
Neither button deletes weights or logs. Active tasks block these operations.
Unrelated Python processes and unknown port occupants are not terminated.
The status line reports loading, ready, stopped or failure; startup logs are in
`runs/local-gui/lifecycle.log`. Qwen 8B currently uses the locally installed
`D:/Tools/llama-b11026/bin/llama-server.exe` and `D:/Models/Qwen3-VL-8B-Instruct`.


- https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct
- https://huggingface.co/mPLUG/GUI-Owl-1.5-2B-Instruct (MIT)
- https://github.com/X-PLUG/MobileAgent/blob/main/Mobile-Agent-v3.5/cookbook/end2end_usage_computer.ipynb
- https://huggingface.co/Tongyi-MAI/MAI-UI-2B (Apache-2.0)
- https://github.com/Tongyi-MAI/MAI-UI/blob/main/MAI-UI/src/prompt.py
- https://github.com/Tongyi-MAI/MAI-UI/blob/main/MAI-UI/src/mai_naivigation_agent.py
