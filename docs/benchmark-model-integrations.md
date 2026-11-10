# Optional benchmark and native-model integrations

These integrations add explicit workbench targets and inference adapters. They do
not replace existing Windows/Android workflows or automatically install weights
and benchmark images.

## Prompt compatibility

The existing GitHub director and chat-executor prompts are retained, including
stage/continue/finish/blocked and notices/fail_conditions. Experimental server
delegate/takeover/guidance revisions are not included. The original first-turn
guided-executor instruction is also retained. New native models use their own
training-aligned input formats.

## Model prerequisites

GUI-Swift-4B uses gui-swift-native131. Set TRACE2TASK_SWIFT_RELEASE,
TRACE2TASK_SWIFT_BASE and TRACE2TASK_SWIFT_UPSTREAM to the local release, base
model and upstream codec. The release must include infer.py, adapter weights and
SHA256SUMS.json. The dedicated service is expected on loopback port 8769.

MAI-Swift-2B uses mai-swift-native105. Set TRACE2TASK_MAI_RELEASE,
TRACE2TASK_MAI_BASE and TRACE2TASK_MAI_BUNDLE to the local release, base model and
pinned inference bundle. See [MAI-Swift cache notes](mai-swift-kv.md).

Release revisions are recorded in the model registry. Weights and deployment
manifests are not bundled. GPU dependencies belong in a separate runtime.
The optional GUI-Swift vLLM adapter checks version 0.11.0 and reference-source
hashes. Its fixed-slot pooler is not ordinary autoregressive text generation.
Export and verification helpers are in scripts/local_gui/.

## OSWorld

TRACE2TASK_OSWORLD_CONFIG points to an operator-owned JSON manifest.
The adapter uses catalog, python, lock and optional runtime_bin_dirs fields.
The worker also requires capsule, fallback, source_files, archive,
archive_sha256 and base_receipt. These identify the pinned environment sources,
task archive and accepted guest image.

This adapts an existing verified environment; it is not a one-command fresh
OSWorld installation. The environment capsule and guest image are external
prerequisites. Missing configuration disables task selection. A guest lease
prevents concurrent workbench resets.

Setup and transport failures produce invalid/null evaluation evidence, never
synthetic zero scores. The cursor-lifetime patch matches known source hashes
and changes only the owned guest overlay. Terminal-close recovery remains
limited to the supported shortcut; arbitrary shell actions are not enabled.

## AndroidWorld

See [AndroidWorld workbench](androidworld-workbench.md). Configure the pinned
runtime, owned emulator, service token and catalog identity separately.
TRACE2TASK_ANDROIDWORLD_CONFIG identifies a local manifest with instance,
catalog, token_file, service ports, named motor_container and GPU binding.
Never commit the manifest or token.

Benchmark text and cross-app capabilities use the AndroidWorld action schema.
The ordinary Android workflow remains unchanged. Services and emulators are
external prerequisites; the web UI does not silently download or replace them.

## Trajectory and reliability changes

The inspector displays recorded model input/output, collapsible subtask details
and normalized action markers. Markers show proposed positions, not proof of
execution. Local inference evidence is resolved only inside the run archive.
Historical records remain readable.

Horizontal tabs are preserved in type_text, including director receipts converted
to native-model history. Other control characters remain restricted.
After changing a shared validator, reload both the workbench and GPU services;
restarting only the web process leaves the GPU process's imported code stale.

Browser storage preserves execution preferences, excluding API credentials.
Restoring preferences never launches a task.
