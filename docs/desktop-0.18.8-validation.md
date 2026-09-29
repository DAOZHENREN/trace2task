# Windows independent installer — 0.18.8

Validation date: 2026-09-26. This is a local release candidate, not a universal compatibility claim.

## Implemented

- PyInstaller bundles the application interpreter and dependencies; no user Python, uv, Git, or checkout is required to launch it.
- Inno Setup checks the documented WebView2 runtime registry keys. If absent, it runs the bundled Microsoft-signed online bootstrapper and verifies installation before proceeding.
- Component management is protected by the existing loopback/CSRF boundary. Installation is asynchronous, cancellable, and logs errors. A new GPU environment becomes active only after a real CUDA matrix multiplication succeeds.
- The bundled uv binary installs an isolated CPython 3.11.13 without PATH/registry registration. Torch 2.7.1 cu128 and torchvision 0.22.1 cu128 come from the official CUDA index. Primary application dependencies are pinned in `scripts/local_gui/managed-requirements.txt`; transitive resolved versions appear in the install log.
- Three public model downloads use fixed revisions and file integrity checks. D-5970 import checks the known checkpoint SHA256 without moving user files. Public-model and D clients can use the managed interpreter.
- Failed/cancelled installation does not replace a previous configuration. Downloads/caches and incomplete environments are retained; there is no automatic recursive user-data deletion.

## Verified here

- 196 targeted Python tests and one component UI Node test passed, including configuration publication gates, cancellation, CSRF, legacy service compatibility, and rejecting an untrusted D checkpoint.
- Build and actual per-user installation to `D:\Apps\Trace2Task` succeeded. Registered version: 0.18.8.
- Installed EXE launched from a temporary working directory with PATH limited to Windows directories and PYTHONPATH/PYTHONHOME/VIRTUAL_ENV cleared. DOM receipt: `{"ready":"complete","console":true,"components":true}`.
- A newly provisioned managed Python/GPU environment imported torch, transformers, peft and pygame, then successfully computed and checked a CUDA matrix multiplication on NVIDIA GeForce RTX 5070 Ti Laptop GPU. Driver: 610.88.
- The initial large-wheel network transfer was stopped and retried using the existing official wheel after its SHA256 matched `138c66dcd0ed2f07aafba3ed8b7958e2bed893694990e0b4b55b6b2b4a336aa6`. This did not reuse the old Python environment.

## Local evidence

- `D:\Trace2Task-standalone-validation\installer.log`
- `D:\Trace2Task-standalone-validation\desktop-smoke.json`
- `D:\Trace2Task-standalone-validation\runs\desktop\desktop.log`
- `D:\Models\Trace2Task-managed-validation\runs\components\install.log`
- `D:\Models\Trace2Task-managed-validation\components.json`

The validation environment remains on D: and consumes disk space; existing task packs, model weights and user Python environments were not removed or replaced.

## Still not verified / limits

- No clean Windows VM was available. Clearing PATH is not equivalent to a machine without any preinstalled runtimes.
- The missing-WebView2 branch was compiled but not exercised by uninstalling the host's shared runtime.
- This pass did not validate every model's end-to-end desktop task, real provider API authentication, or all microphone paths.
- Codex CLI, the legacy llama.cpp 8B service, CUA driver and WAA VMs remain separately provisioned. API mode and the supported managed GPU path do not require them.
- NVIDIA drivers are system prerequisites. First component installation requires network access. The installer remains unsigned.

Reproduce the independent-path startup check with `scripts/validate-desktop-independent.ps1`.
