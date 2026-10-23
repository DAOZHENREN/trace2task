# MobileForge integration — Android, local models and Codex subscription

This is a pinned policy bridge into Trace2Task's bounded Android pixel loop,
not the upstream DFS explorer or an end-to-end trained system. The desktop
workbench uses the same run history, screenshots, action receipts and stop path
as the general Android agent. See [Android setup and limits](android-backend.md).

## Workbench setup

1. Start the source desktop app. An installed package does not automatically
   receive source changes.
2. Select **Android · 指定手机与应用**, choose an existing connected device and
   authorized app, then select **MobileForge + ADB** in the advanced settings.
3. Supply a clean checkout of the pinned upstream source. Choose either a
   loopback vision API (including an explicitly configured SSH tunnel) or the
   supported Codex subscription bridge.
4. Review the current device, package, model destination and action/time budget.
   Start explicitly. Stop/F9 cancels subsequent input; unknown delivery stops
   instead of replaying the action.

The general Android agent defaults to 80 actions / 20 minutes; workbench budgets
also apply to the MobileForge route. This is a limit, not a promised task length.
MobileForge does not support automatic B selection, desktop experience,
human-takeover controls, or the general agent's new navigation actions.

### Optional 3090 / Qwen3-VL preset

The **使用 3090 控制云手机** preset selects Android, the MobileForge policy and
`http://127.0.0.1:18998/v1` with model ID `Qwen3-VL-8B-Instruct`.
Selecting it does not launch a task or operate the phone. Its default local
ADB/source paths are examples and can be changed in advanced settings.

The model service must already be deployed separately. Its loopback
`/health` must return `{"status":"ok","model":"Qwen3-VL-8B-Instruct"}`.
No model weights, remote runtime or credentials are distributed with this repo.

Configure the SSH destination locally using either:

- `TRACE2TASK_ANDROID_SSH_HOST` with an SSH alias or `user@host`; or
- a copy of [android-model-profile.example.json](../android-model-profile.example.json)
  saved as `android-model-profile.json` in the source root. This file is ignored
  by Git. `TRACE2TASK_ANDROID_MODEL_PROFILE` may point to a different local file
  (also useful for a packaged app).

The environment host takes precedence. No server address is embedded in public
source. SSH key authentication and a pretrusted host are required.
**连接 / 复用 SSH 隧道** creates only the fixed forward
`127.0.0.1:18998 → remote 127.0.0.1:8098`, with no remote command.
Healthy existing connections are reused; an unhealthy occupied port is not
replaced. Only a newly owned failed connection is stopped.

Although the endpoint is loopback, inference runs on the configured remote
server: task text and screenshots leave the PC over SSH. The preset uses a local
placeholder API key, never a saved cloud-provider key. Connection checks send no
screenshots, inference requests or phone input.

Every task submission repeats device/application preflight. The worker binds
the transport and boot identity. Optional, explicit app-launch consent opens
only a launcher belonging to the authorized package; it does not reset app data.
There is no desktop fallback, automatic reconnect or multi-touch support.

## Pinned source and implementation boundary

Upstream: [kwai/MobileForge](https://github.com/kwai/MobileForge), commit
`a4168aa2709a6a2350811591054d3579884dd357`. The upstream checkout is not modified.
The official Qwen3-VL prompt and hint reader are SHA-256 checked before reuse.

Implemented stages: policy decoding, task proposals, critic/reflection,
official-format export, and a guarded training launcher. The upstream
`run.py` / DFS explorer is **not** invoked by this bridge. In particular, its
all-app stop operation must not be treated as authorization to stop unrelated apps.

The MobileForge policy supports click, bounded hold/swipe/wait, and Back.
There is no arbitrary shell, typing, Home or policy-controlled app switching.
Login, purchases, download confirmation and legal acceptance are forbidden by
the prompt and require user supervision; coordinate checks are not semantic
page-level safety enforcement.

Local-provider endpoints must be loopback, including SSH tunnels. The separate
Codex bridge validates subscription authentication, uses `gpt-6-luna`, disables
execution tools, and expects a serialized `mobile_use` prediction. It is not an
interchangeable API key or a fallback to a third-party model service.

## CLI stages

Use a separately provisioned, verified model and your current device identity.
All output paths below must be new; examples do not authorize device input.

1. Check the upstream source without model or device calls:

~~~powershell
.venv/Scripts/python.exe scripts/run_mobileforge.py --upstream <MobileForge-checkout>
~~~

2. Propose tasks from a saved screenshot. This invokes the selected model but
   does not instantiate a device driver:

~~~powershell
.venv/Scripts/python.exe scripts/mobileforge_pipeline.py propose --upstream <MobileForge-checkout> --base-url http://127.0.0.1:8098/v1 --model Qwen3-VL-8B-Instruct --screenshot <saved-screen.png> --package <authorized-package> --output <new-proposals.json>
~~~

For the subscription path, replace the endpoint options with
`--provider codex --model gpt-6-luna`. Generated tasks require human selection;
they are not automatically executed.

3. For an explicitly approved task, use `scripts/run_mobileforge.py --execute`
   with `--adb`, `--serial`, `--server-port`, `--package`, `--instruction`,
   `--max-actions`, `--output`, `--upstream`, and the provider/model options.
   Recheck device identity; do not reuse a stale serial blindly. Later attempts
   may use `--hints-root <exports> --task-id <id> --attempt 2`. There is no app reset.

4. Evaluate and export an existing attempt without device input:

~~~powershell
.venv/Scripts/python.exe scripts/mobileforge_pipeline.py critic --upstream <MobileForge-checkout> --base-url http://127.0.0.1:8098/v1 --model Qwen3-VL-8B-Instruct --run <run-directory> --task <exact-original-instruction> --output <new-feedback.json>
.venv/Scripts/python.exe scripts/mobileforge_pipeline.py export --run <run-directory> --feedback <feedback.json> --destination <exports> --task-id <stable-task-id> --attempt 1
~~~

Feedback is bound to the result and screenshot bytes; existing outputs are not
overwritten. Critic labels are model judgments, not independent ground truth.
Missing evidence is quarantined, not converted into a negative training label.
Repeated stateful attempts are not reset-independent benchmark trials.

5. The pinned native processor can convert exports under Linux:

~~~bash
cd <MobileForge-checkout>/rollout
python data_process_verl/mobileforge_data_processor.py --rollout_dir <exports> --output_dir <new-conversion-dir> --format grpo --filter_sr_max 1.0
~~~

`metadata.overall_success` represents terminal-answer status, while
`metadata.final_result` reflects critic success. No terminal action is invented
to make these flags agree.

6. Prepare disjoint train/validation exports grouped by stable task ID. Preview
   the guarded smoke launcher in a new isolated Linux environment:

~~~bash
python scripts/mobileforge_train.py --upstream <MobileForge-checkout> --model <verified-local-model> --train <train-exports> --val <val-exports>
~~~

Only `--execute` invokes native conversion and `verl.trainer.main`. The launcher
rejects missing dependencies/weights, synthetic or ineligible data, overlapping
task IDs, dirty/unpinned source, occupied GPUs and insufficient disk. It rechecks
resources after conversion, but does not reserve GPUs. Never interrupt unrelated
jobs or install into their environments.

The smoke configuration uses four GPUs, one update, batch four, group two,
4096/512 token limits, a reduced image budget, frozen vision tower and no
checkpoints. This is neither the paper's eight-GPU recipe nor a proven memory-fit
or useful trained-model release. Longer training needs a separate reviewed plan.

## Verification status

Historical local checks are narrower than task-success or training claims:

- Fake ADB/model tests cover routing, app/device identity, bounded actions,
  unknown delivery, stops, provenance and export. They do not control a phone.
- The native CPU data converter accepted a synthetic fixture. Synthetic evidence
  must not enter real training data.
- A saved-image Qwen3-VL 8B BF16 smoke check produced valid click predictions.
  A later live screenshot reached that service and decoded a prediction; its
  callback stopped before action dispatch. This was inference validation, not a
  successful game-task run.
- A Clock smoke run started a stopwatch. A later pause action was corroborated
  by read-only captures, but the automatic completion review did not pass.
  That failed review was not relabeled as success.
- Native DFS exploration, feedback quality, complete GRPO dependencies and GPU
  training remain unvalidated. No improvement or benchmark score is claimed.

Raw screenshots, requests, deployment logs, network identities and machine
inventories stay in ignored local evidence directories, not in source releases.
