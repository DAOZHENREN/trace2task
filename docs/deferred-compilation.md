# Save a recording and compile later

In the recording panel, enable **仅保存录制，稍后编译** before starting a
Windows or WAA recording. The option is off by default; existing automatic
compilation remains unchanged.

With narration enabled, review and confirm the transcript first. Confirmation
archives the narration and marks the recording job complete without starting the
Compiler Agent. Without narration, a successful recording is saved directly.
Cancellation and recording failure still follow their existing paths.

Open the original recording in **本地经验** to compile it later. A completed
recording job is not a compiled or reviewed TaskPack and is not agent-task success.
The API exposes `defer_compilation: true` and `result.compilation.status: deferred`.
This option skips compilation, not the recording, VM preparation or optional local
speech transcription work. It does not provide a batch-compilation scheduler.

Validation uses fake recorders, model hooks and synthetic files. Live Windows/VM
recording and browser interaction have not been re-run for this maintenance PR.
The focused CI covers recording/narration routes and served UI content. Two
unrelated failures also reproduce on the base revision in this Linux environment:
`test_web_controller_opens_only_local_experience_directories` (Windows API mock)
and `test_locked_recording_is_copied_to_trash_and_hidden_until_cleanup` (directory
iteration during cleanup). This PR does not change or suppress those tests.
