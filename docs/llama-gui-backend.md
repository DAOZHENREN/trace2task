# Registered GUI models via llama-server

This is an inference backend, not a replacement for the action executor or agent
framework. The existing task conversation, official Owl prompt, action decoder,
authorization, completion review, cancellation and per-round audit remain in use.
No streaming UI or durable execution is introduced. Historical text summarization
now uses the official LangChain middleware (see below), without migrating the executor.

## Select and start

In **模型与连接**, select **GUI-Owl 2B**, **Qwen3-VL 2B**, or **Qwen3-VL 8B**, select **llama-server**,
then click **启动所选服务**. Changing the dropdown alone
does not switch a resident service. Restart the desktop app after installing this
code update. The backend choice is saved in the data directory's
`runs/local-gui/backend.json`; automatic service startup reads the same file.
Without that file, the default is **llama-server**. An existing explicit setting
is preserved. New local model integrations prioritize llama-server: verify the
official checkpoint, GGUF/vision compatibility and native action protocol first;
do not register unverified compatibility or silently fall back to another engine.
Explicitly select **Transformers** and start the service to use it for the two 2B profiles. MAI
remains Transformers-only; Qwen 8B uses its official Q4_K_M GGUF and is llama-only.
See [workbench contracts](workbench.md).

The authenticated GUI service stays on `127.0.0.1:8768`. It owns one authenticated
llama-server child on `127.0.0.1:8769`, with a profile-specific `trace2task-<model-id>` alias.
An occupied port is an error, never permission to stop an unrelated process.
Closing the GUI service stops its process tree. Cancelling active generation
stops its owned llama child, discards any late response, and reloads on the next
request. The Python GUI service and task conversation stay alive during cancellation.
Do not run another task against the service during validation.

## Reproducible local artifacts

Validated version: llama.cpp **b11026**, commit
`b49650adb31f2e49a0d76113aeb1792134fd8413`, Windows CUDA 13.4 build.
Default executable: `D:/Tools/llama-b11026/bin/llama-server.exe`; override with
`TRACE2TASK_LLAMA_SERVER`. The GUI model root follows the existing component
configuration / `TRACE2TASK_GUI_MODELS` setting.

The original checkpoint is **mPLUG/GUI-Owl-1.5-2B-Instruct**, revision
`528ceaec795bbfbe6103bd79e03db849feadfb24`, not the Qwen base checkpoint.
Use the official `convert_hf_to_gguf.py` from the pinned llama.cpp source checkout:

```powershell
# Run in a Python environment with the official converter's dependencies installed.
D:/Models/Trace2Task-D-5970/.venv/Scripts/python.exe scripts/local_gui/convert_llama.py `
  --llama-source D:/Tools/llama-b11026/source --model gui-owl-2b
# Qwen uses the same pinned converter but its own registry revision / output directory:
# replace --model gui-owl-2b with --model qwen3-vl-2b
```

For the validated local setup, the extra `sentencepiece==0.2.1` dependency was
installed into `D:/Tools/llama-b11026/converter-deps` and exposed through
`PYTHONPATH` for conversion only; the existing Python environment was not modified.
The conversion helper does not download/install dependencies or code. It checks
the original `verified.json` hashes, calls the upstream converter, and records
output hashes and converter revision in `gguf/gui-owl-2b/verified-gguf.json`.
It refuses to overwrite existing GGUFs. `--record-existing` is only for recording
outputs already made by the specified converter; it checks input/output files,
but the operator is responsible for asserting that conversion provenance.

Outputs under the model root:

- `gguf/gui-owl-2b/model-BF16.gguf`: BF16 language weights.
- `gguf/gui-owl-2b/mmproj-F16.gguf`: F16 vision projection/encoder.
- `gguf/gui-owl-2b/verified-gguf.json`: checksums checked before loading.

Original safetensors and model configuration are retained. GGUF conversion uses
the checkpoint's embedded chat template, not a hand-written substitute.

### Qwen3-VL 8B: reuse official quantized weights

The 8B profile pins [Qwen's official GGUF repository](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF/tree/f982a07559d4a2f6c8744d840bf6fccab30eea96),
revision `f982a07559d4a2f6c8744d840bf6fccab30eea96`, and the publisher SHA-256
of `Qwen3VL-8B-Instruct-Q4_K_M.gguf` and `mmproj-Qwen3VL-8B-Instruct-F16.gguf`.
Startup checks those hashes directly; it needs no safetensors copy or local
conversion manifest. Both existing files on this machine matched the publisher
hashes; no weights were downloaded, copied, or overwritten for this integration.

Path selection: explicit `TRACE2TASK_QWEN8B_GGUF_DIR`, then a complete pair under
`<GUI model root>/gguf/qwen3-vl-8b-instruct`, then the existing pair in
`D:/Models/Qwen3-VL-8B-Instruct`. A bad explicit directory does not silently fall
back. On machines without the files, the component model downloader fetches only
the two pinned GGUF files into the managed model root. It refuses to overwrite a
different existing GGUF. The conversion helper remains for the 2B checkpoints.

The new UI no longer sends 8B tasks through the independent `8081` API route.
8B uses the Qwen normalized-action adapter, shared task conversation, execution
capability checks, cancellation, experience, completion review and run journal.
Model-specific prompts are editable in **设置与工具 → 模型提示词 → Qwen3-VL · 8B**;
saved generic API guidance is retained but is not silently copied into this profile.
The old `start-local-qwen.ps1` script remains a manual compatibility entrypoint.
An already-running independent 8081 process is not stopped or reused by the new
GUI lifecycle; stop it explicitly if it occupies needed GPU memory.

Read-only acceptance (2026-10-03): the real 8B worker returned a valid click at
`(0.71, 0.71)`, inside SAVE on a synthetic 800×500 screenshot. Load was about
8.93 s, generation 1.90 s, input 1,410 tokens and output 29 tokens. A separate
real text-summary round trip returned `OK` and restored the 8B vision worker.
Both owned test workers were stopped. No desktop input was dispatched; these
checks establish the runtime/adapter path, not complex-task success rates.

## Context and audit boundaries

The configured native context is **32,768 tokens**, one slot, F16 K/V cache for
the two 2B profiles and Q8 K/V cache for 8B,
Flash Attention, GPU offload, batch 512 / microbatch 128, and 512 maximum output
tokens. Each image uses a 1,024-token budget (the pinned runtime warns against
smaller grounding budgets). This is not a benchmark-equivalent official recipe.

Every request sends the full current conversation to llama-server. Its own
multimodal prefix cache determines reuse; no conversation-ID cache assumption and
no custom Transformers KV heuristic is applied. `cache_prompt=true` is explicit.
`--no-context-shift` prevents silent native text shifting. An explicit native
context-size error still supports an explicitly recorded oldest-image eviction
fallback. Automatic summarization normally intervenes earlier. Complete original
experience, original task and current screenshot are never summarized. If no old
image remains and the context still does not fit, it fails clearly. Transport errors, arbitrary server failures and
truncated output do not become action retries. Output must end with `stop` before
the common action parser is called.

The audit saves exact HTTP message bodies and inline image bytes in
`attempt-*-input.json`, image ordering/source steps, `llama-response.json`, raw
text, finish reason, `usage`, cache counts and engine timings. Authentication is
not archived. `prefix-cache.json` reports **engine-measured cached tokens**.
There is no fabricated tokenizer output or PyTorch allocator peak for this external
engine: those fields are unavailable/null. The validation report can separately
sample **whole-device** NVIDIA memory, including other applications.

## Read-only acceptance (2026-09-30)

The following 29-round measurements are the **pre-summarization baseline**.

On the local RTX 5070 Ti Laptop GPU (12 GB), synthetic 1920x1080 screenshots plus
the full recorded task experience passed 29 consecutive prediction rounds:

- Round 12: 16,988 input tokens, 15,922 cached, ~1.75 seconds.
- Round 26: 32,416 input tokens, 31,350 cached, ~3.01 seconds.
- Rounds 27–29: explicit native-window errors evicted old images only; all text
  remained. These rounds took ~19 seconds after reduced prefix reuse.
- Round 29: 32,692 input tokens, 25 retained history images, valid parsed output.
- 397 whole-device memory samples ranged from 11,151 to 11,588 MiB. This includes
  desktop/other processes, is not a per-model allocator peak, and leaves limited
  headroom. Do not assume another GPU model can run concurrently.
- Cancelling a live long-prefill request returned `cancelled` in ~0.53 seconds;
  the next request reloaded the worker and returned a valid prediction.
- One archived real desktop screenshot also produced a valid native tool call.
  Its chosen action differed from Transformers; this is a transport/parser smoke
  check, not evidence of grounding/task-quality equivalence.

These checks sent **zero desktop actions** and do not establish task success,
grounding accuracy parity, or guaranteed memory headroom on other machines.
The local report is `D:/MyProject/trace2task/runs/llama-acceptance-20260930.json`.

```powershell
$env:TRACE2TASK_DATA_ROOT = 'D:/MyProject/trace2task'
.venv/Scripts/python.exe scripts/local_gui/validate_conversations.py gui-owl-2b `
  --turns 29 --width 1920 --height 1080 `
  --experience-file <full-experience.txt> --report <report.json>
```

Upstream: [llama.cpp](https://github.com/ggml-org/llama.cpp/tree/b11026),
[server API](https://github.com/ggml-org/llama.cpp/blob/b11026/tools/server/README.md),
[GUI-Owl checkpoint](https://huggingface.co/mPLUG/GUI-Owl-1.5-2B-Instruct).

## LangChain history compaction

`langchain==1.4.3` supplies `SummarizationMiddleware`. The adapter invokes its public
`before_model` hook inside the existing GUI service; it does not introduce a second
agent loop. The trigger is 24,576 projected context tokens (75% of 32K), based on
the last engine-reported input+output usage plus an explicitly approximate new-turn
allowance. This is a trigger estimate, not an exact tokenizer measurement or a new
hard input cap. LangChain decides the cutoff and retains the latest eight messages
(four complete GUI rounds). The pinned task and complete experience are outside
this state; the current user turn and screenshot are also excluded from compaction.

Older user/assistant text and any previous summary are summarized. Old images are
omitted from the summary model and marked unavailable rather than described by
guesswork. The recent four rounds retain their original text and image objects.
`trim_tokens_to_summarize=None` prevents the middleware's default pre-summary
trimming. Summary output must be nonempty and finish normally; overflow or invalid
output fails with the original history intact. Semantic accuracy remains a model
limitation: summaries are fallible memory, not verified execution receipts.

The chosen summary model is the existing **Qwen3-VL-8B-Instruct Q4_K_M**, used as a
text-only model (no vision encoder). The current GUI model is unloaded, the summary model starts
on the same owned 8769 worker port with alias `trace2task-gui-summary-qwen8b`, then
it is unloaded and the original GUI profile (including Qwen 8B) is restored. The independent Qwen service on 8081 is
never repurposed or stopped. The summary model uses a 32K text window, Q8 KV and
at most 1,536 output tokens. `TRACE2TASK_SUMMARY_GGUF` can override the file path;
the default follows the Qwen 8B runtime paths listed above. No new model download is needed on this machine.

Summary, restore and cancellation are transactional with respect to conversation
history. Failed/empty/truncated summaries, failed model restore and cancellation do
not replace the original turns. A cancelled summary leaves the GPU worker unloaded;
the next request loads the selected GUI model normally. Successful compaction invalidates old KV
cache; the next GUI inference repopulates it, and later rounds reuse that prefix.
LangSmith tracing is explicitly disabled around summarization so tracing environment
variables cannot send the local conversation to a remote tracing service.

Each event archives `compaction-before.json`, `summary-request.json`,
`summary-response.json` and `compaction.json`, including exact summary input/output,
replaced step IDs, usage, estimated trigger size, measured post-compaction input size,
status and elapsed time. No authentication token is included. Existing per-round
archives are retained. The chat view shows incremental turns/current images, one
compaction event when it occurs, and folded complete-input records for inspection.

Restart the GUI model service after this code/dependency update. Existing managed
Python installations need the newly pinned `langchain` requirement; newly installed
managed runtimes receive it through `managed-requirements.txt`. This version enables
compaction for all registered llama-server task conversations. Transformers/API/Codex
paths are unchanged. Persistent restoration after app restart remains out of scope.

[Official middleware API](https://reference.langchain.com/python/langchain/agents/middleware/summarization/SummarizationMiddleware)

### Compaction acceptance (2026-09-30)

36 read-only synthetic 1920x1080 rounds with complete task experience passed,
including repeated summaries and resumed prefix-cache hits:

- Round 19: projected trigger 24,785 tokens; measured GUI input after compaction
  9,510 tokens. Summary/model switch 19.10 s, full round 22.90 s. Next round 1.36 s,
  with 9,546 of 10,612 input tokens cached.
- Round 33: projected trigger 25,021; measured post-compaction input 9,461.
  Summary/model switch 16.96 s, full round 19.78 s. Next round 1.04 s, with 9,497
  of 10,563 input tokens cached.
- Each event replaced 14 older rounds with summary memory and retained the latest
  four complete rounds; the second summary included the first summary as input.
- Original task/experience were checked unchanged on every round. No desktop input
  was dispatched. The Windows worker shutdown path now waits for owned-process exit
  before reusing the port; a failed first switch left all original history intact.
- Live cancellation during Qwen summary generation returned in 0.94 s. The cancelled
  attempt's pre-summary history exactly matched the retry's history; the retry then
  summarized successfully and continued GUI prediction. Separate report:
  `D:/MyProject/trace2task/runs/summary-cancellation-acceptance.json`.

Report: `D:/MyProject/trace2task/runs/llama-summary-acceptance-20260930.json`.
These are context/transport/cache tests, not a benchmark of summary fidelity on
complex desktop tasks. Summary omissions remain possible and the raw audit is retained.
