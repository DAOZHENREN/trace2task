# Local GUI runtime correction — 2026-09-22

Scope: resident Qwen 2B / GUI-Owl / MAI generative service, Cua and Win32 local-model run audit. Model weights, precision, image budget and action adapter semantics are unchanged. This is not a claim of improved GUI grounding accuracy.

## Findings and changes

The failed calculator run had 5,927 / 5,976 / 6,027 input tokens and only 30 / 29 / 29 output tokens. Generation took 7.44 / 54.29 / 81.33 seconds. PyTorch reserved-memory peaks rose from 10.41 to 14.67 to 19.00 GiB on a nominal 12 GB GPU. Historical logs did not include post-request allocator measurements, so they cannot alone prove a live tensor leak or OS paging mechanism.

Current environment: torch 2.7.1+cu128, Transformers 4.57.1; no static generation cache configured. Each request now releases temporary input/generated tensor references (including output views), collects garbage and returns unused allocator blocks while keeping model weights resident. Before/peak/after-cleanup allocated and reserved memory are recorded separately.

Cancellation uses authenticated, request-scoped `/cancel`, including a short-lived marker for cancel-before-request races. Active requests do not expire mid-generation. Generation checks cancellation between decoding steps and before generation; a running GPU kernel/prefill cannot be interrupted instantly. Cancelled partial output is archived, never dispatched. If a response arrives concurrently with stop it is discarded. If cancellation is not acknowledged within the client's bounded wait, the UI reports pending cancellation, not a false success. D-5970 uses a separate frozen structured predictor; it discards late results but does not use this autoregressive cancel endpoint.

## Actual GPU replay (no desktop input)

Source: `runs/20260922-053343-3ec722a5-cua/trace.jsonl`.

The test replayed the same archived screenshots, task, histories and full application catalog. It did **not** shorten the prompts or send mouse/keyboard input.

| Round | Input tokens | Generation | Reserved peak | Reserved after cleanup |
| --- | ---: | ---: | ---: | ---: |
| 1 | 5927 | 4.96 s | 10.41 GiB | 3.99 GiB |
| 2 | 5976 | 4.46 s | 10.50 GiB | 3.99 GiB |
| 3 | 6027 | 4.41 s | 10.61 GiB | 3.99 GiB |

First model load took an additional 7.75 s; later requests retained the loaded weights. A second warm replay measured 4.48 / 4.16 / 4.51 s with the same post-cleanup reserved memory. These are local replay observations, not a general latency guarantee.

Two direct cancel tests returned `cancelled` about 1.47 / 1.39 s after requesting stop. The application-client cancellation/archival test also passed: partial raw text, actual prompt, metrics and cancelled response were saved together; zero desktop actions were dispatched.

Evidence on this machine:

- `D:/MyProject/trace2task/runs/local-gui-runtime-validation-20260922/results.json`
- `D:/MyProject/trace2task/runs/local-gui-runtime-validation-20260922-integrated/results.json`
- `D:/MyProject/trace2task/runs/local-gui-runtime-validation-20260922-integrated/client-cancel/result.json`

## Audit layout

Each new run contains `trace.jsonl`, `result.json`, `model-io.json`, and `model-io/0000/` etc. Per-round directories contain:

- `request.json`: local API body, including the actual image data; authentication token excluded.
- `input.json`: actual system/user messages, image dimensions and generation configuration.
- `formatted-prompt.txt`, `tokenized-input.json`, `screenshot.png`.
- `raw-output.json`, `response.json`, `outcome.json`, `round.json`; `error.log` when applicable.

The application displays model round trips, load/preprocess/generate/decode/total durations, input/output token counts, GPU allocation/reservation and raw/decoded answers. Actual prompts and screenshot/log files are linked from the same round. It distinguishes generation cancellation, pending cancellation, failure and discarded late output. Logs contain private tasks/screenshots and should not be published unredacted.

Application selection/multi-select and the model's incorrect calculator clicks are separate work; this change does not silently alter those behaviors.
