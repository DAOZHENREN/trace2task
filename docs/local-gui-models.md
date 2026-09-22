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

Select the model, enter a task and use **只生成计划** first (screenshot preview, no input).
**开始执行** runs continuously after initial confirmation, with F9/stop and a 40-action
limit. Primary desktop only, Baseline without experience in this first integration.
Outputs go through existing ActionCall validation, focus/frame guards and no-progress
protection. `done`/terminate remains unverified, never proof of success.

These are **local adaptation profiles, not reproductions of official benchmark scores**:
BF16/SDPA, one screenshot, maximum 1,048,576 image pixels, 6,144 input tokens,
512 generated tokens, last four actually executed steps, one next action per response.
Over-budget, truncated, unknown or unsupported output is rejected, not executed.

GUI-Owl preserves the official desktop cookbook system prompt (MIT attribution in
local_gui_owl_prompt.py), then appends explicit host capability restrictions.
Its official desktop tool is `computer_use`, coordinates 0..1000.
MAI's official navigation tool is `mobile_use`, coordinates 0..999. MAI's supplied
navigation prompt is mobile-oriented: our clearly labelled experimental Windows
adaptation removes the Android launcher and navigation commands. It is NOT an
official MAI desktop navigation recipe. Both use tool_call JSON, never Python eval.
Supported subset: click/double click, type, validated keys, wait, terminate.
Scroll, swipe, mobile open/system_button, mouse_move and implicit-start drag fail
explicitly; they are not guessed or converted into unrelated Windows operations.

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
