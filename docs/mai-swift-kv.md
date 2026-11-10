# MAI-Swift native105 inference cache

User requested cross-turn KV caching and model-speed recording on 2026-11-09.
Implementation: `src/trace2task/mai_swift_kv.py`; enabled by the standalone AndroidWorld run manifest's `kv_cache: true`. The released adapter, input codec, model weights, BF16 precision and 16384-token window remain unchanged. This is Transformers caching, not vLLM.

The adapter reuses pinned Transformers 4.57.3 Qwen3-VL forward and its DynamicCache. Reference documentation: https://huggingface.co/docs/transformers/v4.57.3/en/cache_explanation . The released official slot reader hardcodes use_cache=False, so a small adapter supplies use_cache=True while retaining the same 105 `slot_positions - 1` readouts. It performs one model forward, without autoregressive generation. Published release files are untouched.

Cache reuse requires identical old token IDs, full MRoPE positions, image grids and preprocessed image pixels. The complete old MASK tables remain cached. Only new suffix IDs, images and positions are forwarded; attention mask covers past plus new tokens. A new task, changed prefix/image, or oldest-turn window eviction triggers a cold rebuild. No KV rotation or approximation is used to conceal window changes. The upstream input builder's history_offset avoids retokenizing already evicted turns, without changing its window selection.

Each prediction JSON and trace model_timing event stores input_seconds, synchronized GPU forward_seconds, decode_seconds, total_inference_seconds, cached/new/total tokens, new image count, cache reason, dropped-history count and allocated/peak GPU bytes. Inference time excludes screenshot acquisition, action execution and prediction-file persistence. Each episode's speed-summary.json reports count, mean/median/nearest-rank p95/max and cache-hit mean latency. First/cold calls are identified, not silently omitted.

Evidence was recorded in the private deployment runtime; it is not bundled with this repository.
Initial BF16 reference comparison was NOT bitwise/action-identical: 8/14 decoded actions exact,14/14 same action type; six coordinate differences, largest71/1000. This is material numerical sensitivity and must remain visible. FP32 diagnostic (not the evaluation precision) gave identical actions and105/105 argmax on three steps; max logit error <0.001. This supports correct cache indexing but does not prove BF16 equivalence. Cold/reset BF16 logits were exact. Cached and uncached scores must not be pooled. Follow-up full-input/window and changed-image invalidation checks are saved separately.

A full context window eventually rebuilds every turn under the unchanged oldest-turn policy; the cache-hit forward timing is not a guarantee for all long tasks. Input preprocessing is timed separately and can dominate short cached forward passes.

## Optional periodic task reminders

Standalone manifests with task_reminder_interval=8 use the unchanged System and original task on rounds1/9/17/... only. Every turn uses `Complete the task based on the current screenshot.`; full-task turns append `Task: <goal>`. Receipts/images remain each turn. If the retained window contains no full reminder, the new turn gets one early, logged as last_task_reminder_evicted. Only the new message changes; cached old messages are preserved. Default interval0 keeps the historical full-goal-every-turn behavior. Prediction files store reminder interval/reason, actual turns and token IDs. This is a separately versioned prompting configuration.

## Invalid-action receipts (2026-11-09)

User requires invalid proposals to be discarded and reported to the model for fresh inference. Standalone manifests with validate_actions=true call the owned server's pure `/trace2task/validate-action` endpoint before dispatch. It reuses UnifiedAction validation and the pinned official adb_utils app resolver, without reading state or sending device commands. Rejected proposals consume one decision, leave executed_action=null, include proposed_action/reason/instruction in the next user receipt, and cause a fresh screenshot and model prediction. Repeated invalid proposals are bounded by the original task budget, not a hidden three-rejection limit. No action is silently rewritten or retried.

Only an explicit valid=false, executed=false response from the expected instance is a known invalid action. HTTP errors, malformed replies and actual dispatch failures still halt with invalid/null evidence. This avoids labeling uncertain executed actions as safe to retry. The execution endpoint also runs the same preflight before any device state access. System/prompt cadence/KV/model/scorer are unchanged.
