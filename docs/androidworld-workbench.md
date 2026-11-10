# AndroidWorld in the mainline workbench

Select **AndroidWorld · 手机环境测试** under the task composer scope. The first supported executor is **MAI-Swift 2B / native105 in hybrid mode**; select the cloud director independently. This is distinct from GUI-Swift-Mobile-2B. The workbench uses the existing mainline hybrid controller and UI run history, not a separate benchmark subprocess UI.

The server supplies immutable task instructions from its configured official AndroidWorld instance. The deployed pinned service has 116 task types / 232 generated instances. These are development tasks, not a claim of benchmark success or leaderboard coverage. Original task source: AndroidWorld revision55ddaf5. GUI actuation and scoring reuse the already admitted official-service adapter. The environment service must already be ready; this adapter does not install or rebuild AndroidWorld.

`TRACE2TASK_ANDROIDWORLD_CONFIG` points to a server-owned deployment JSON containing catalog, instance, world_port, motor_port, token_file, lock, gpu_uuid and motor_container. No client-supplied commands, paths, ports or credentials are accepted. The dedicated motor container is created separately from the pinned image and released MAI model runtime. It starts only if its GPU is idle and stops in cleanup. A nonblocking exclusive lease prevents concurrent workbench tasks on the same emulator; do not run the standalone research runner concurrently. The emulator itself remains running and is not destroyed. Each selected task is initialized and torn down through official routes. Actions are never automatically retried.

Model IO, screenshots, action receipts and official environment_evaluation appear in the normal mainline run artifacts. A model completion claim is separate from the score. Errors, cancellation and timeouts have no valid score. Context overflow removes oldest complete turns with an explicit input audit while preserving current goal/image and absolute receipt count. Current mainline controller/prompt fixes remain enabled: this is a new execution condition, not a replay of the older 48-action research run.

Validation on 2026-11-09: 4 new integration tests plus80 OSWorld/hybrid regression tests passed; TypeScript/Vite build passed. Firefox UI verified232 selectable instances and the MAI hybrid configuration. Real official environment smoke used a synthetic director, real MAI inference and one delivered wait; valid official score0, clean teardown. This smoke is an integration test, not a model success-rate measurement.

Deployment and run evidence are stored in the configured local data root and are not included in this repository.

## Lossless PNG observation transport (2026-11-09)

The maintained server/client now use `/trace2task/screenshot.png`, preserving official stabilization and full RGB resolution, with SHA256 pixel and instance verification. The legacy JSON endpoint remains available. Independent evaluation uses the new source immediately; an already-running workbench process requires its next idle restart to load the client. Do not interrupt an active OSWorld job to reload it. Deployment provenance and verification are saved by the operator alongside the deployment manifest. The two wall-clock-generated Markor folder names are restored from the frozen catalog at service startup.
