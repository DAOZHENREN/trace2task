# OpenCUA recording (Windows, local archive)

Choose OpenCUA in Record. Wait for readiness, demonstrate on the primary screen,
then press F8 to finish or F9/Stop to cancel. F8 records a human completion mark,
not independently verified success. No audio or remote screenshot upload.

## Official files, separate application metadata

New recordings use `runs/<UUID>/`. AgentNetTool revision
`05068e93ea8110694e96d62d6c6d485bb4e958f0` (MIT) supplies the input callbacks,
A11yListener, KeyFrameDetector, MetadataManager and browser FileService.
Upstream source remains unchanged. Trace2Task owns worker/OBS lifecycle and the
local HTTP transport, not a replacement raw-data schema or the full official UI.

- `video.mp4`: primary-screen video, native resolution, 30 fps, cursor, no audio.
- `events.jsonl`: official raw mouse/key events, including releases and movement.
- `a11y.jsonl`: official accessibility-tree samples.
- `element.jsonl`: official clicked native-control samples.
- `top_window.jsonl`: official foreground-window observations.
- `html.jsonl`: official FileService output: HTML, DOM, URL and axtree field.
- `html_element.jsonl`: browser clicked-element records, created when received.
- `metadata.json`: official MetadataManager output, including system/screen,
  scrolling direction, recording times and OBS state timestamps.
- `runtime.log`: official component diagnostics.
- **`trace2task.json`**: application-only task ID, completion/cancellation,
  provenance, video diagnostics and browser-receipt counts.

No custom Win32 metadata fallback or combined `observations.jsonl` is added.
Official window/control information is retained. Old archives are not rewritten.
Capture is asynchronous, not a claim of frame-exact input/video alignment.
Native trees can be incomplete; official browser AXTree collection is disabled
in extension 1.1, so an empty browser axtree is not a successful AXTree capture.

## Browser extension

Load the official htmlsniffer extension from
`D:\Trace2Task-deps\htmlsniffer-official\htmlsniffer` in Chrome developer mode.
It is linked by the [official Windows setup guide](https://agentnet-tool.xlang.ai/quickstart/windows_quick_start/).
The downloaded archive SHA256 is
`a9c72fda059d0c665f44c8fcce5e959a9256a76079ce233620041c02106918c3`.

During recording, localhost:5328 receives the official append_html and
append_element requests and delegates serialization to official FileService.
Requests from webpage origins are rejected; the listener is closed after capture.
Refresh a nonsensitive page after readiness. The result explicitly reports
received HTML/click counts or warns that no HTML arrived. The extension itself
can remain active in Chrome; disable it when not needed. Video, keys, HTML and
trees can contain private data: avoid passwords and sensitive pages.

## Components and boundaries

- Source: `D:\Trace2Task-deps\AgentNetTool`
- Python: `D:\Trace2Task-deps\opencua-runtime\Scripts\python.exe`
- Dedicated OBS portable: `D:\Apps\Trace2Task-OBS`
- Dependencies: `scripts/opencua/requirements.txt`

Override with TRACE2TASK_OPENCUA_SOURCE, TRACE2TASK_OPENCUA_PYTHON and
TRACE2TASK_OPENCUA_OBS. Git must be available for pinned-source integrity checks.
The installer bundles the worker, not the optional source/runtime/OBS packages.
OBS uses a random authenticated WebSocket port and its own portable profile;
unrelated OBS processes are not taken over.

After a successful F8 recording, an isolated local worker calls the official
Reducer's `compress → reduce_all → transform → finish`, then its official
complete/visual serializers. No custom click/drag/keyboard classification rules
replace these stages. Nested actions and releases remain in the complete output.

Derived output goes in `derived/<UUID>/`, never overwriting the official archive:
`reduced_events_complete.jsonl`, `reduced_events_vis.jsonl`,
`frames/*.png`, `manifest.json`, and `index.html`. Open the action report directory
from the recording card, then open `index.html`. Current Compiler conversion
is still not enabled; this step does not call a model or execute any actions.

Evidence schema v2 no longer generates `actions.json`. The report reads official
`reduced_events_vis.jsonl` directly; `manifest.json` stores only the frame catalog
and action-ID-to-before/after frame references, with no copied action payload.
The official complete export remains for details omitted by the visual export.
Older derived exports are retained unchanged.

### Trace2Task adaptations (not official functionality)

- We call the pure reduction stages rather than `reduce_pipeline`. Upstream
  `match_element` rewrites HTML and may invoke remote target prediction; both
  are deliberately excluded. Native/browser raw evidence stays available in
  its original files but is not yet attached to each reduced action.
- Upstream video code makes annotated clips; its standalone frame helper crops
  around clicks. New recordings now archive `obs-frame-clock.jsonl` via the pinned
  OBS 32.2.2 plugin. PyAV matches actual decoded PTS to callback PTS, then chooses
  the latest frame whose CTS precedes the action boundary. CTS is composition
  time, NOT physical capture time: images may already show that action's effect.
  The UI and manifest label this `obs_cts_approximate`, never strict prestate.
  Missing/invalid expected CTS fails extraction, with no silent fallback.
  Older recordings without CTS retain explicitly labelled start-time subtraction.
- The next action's boundary image is the preceding action's effect reference. A final
  frame is selected before F8/stop, using the sidecar's monotonic stop timestamp.
  Older recordings fall back to the OBS stopping timestamp with a visible warning.
  An effect reference does not prove animation completion or task success.
- Human-readable HTML, raw event interval references, file hashes before/after,
  explicit unaccounted non-movement event warnings, and bounded/cancellable
  processing are local adapters. No raw files are altered. Unfinished attempts
  remain separate and do not overwrite a successful previous export.
- IME composition and pasted content cannot be reconstructed from keyboard
  events alone; official grouped typing preserves key names, not inferred Chinese.
- Offline synthetic probes expose two pinned-upstream limitations: an initial
  double-click may remain two clicks (`_find_last_close_complete_identical_click`
  returns early at index 1); a stationary long hold can remain `click` because
  `_is_long_press` excludes a lone release child. We preserve official labels,
  show duration and warn instead of silently claiming perfect action semantics.
  `scripts/opencua/validate_reduction.py` checks seven synthetic cases without
sending input to the desktop.

Manual reprocessing (also works on a completed older official archive):

```powershell
& D:\Trace2Task-deps\opencua-runtime\Scripts\python.exe scripts/opencua/derive.py `
  --upstream D:\Trace2Task-deps\AgentNetTool --recording <recording-directory>
```

## Local verification, 2026-09-28

Manual Chrome example.com → Learn more → F8 validation captured 5 HTML records,
1 browser click (A / Learn more), zero browser write errors, official metadata
and a finalized video. Evidence:
`D:\Trace2Task-deps\opencua-official-browser-validation-02`.

## CTS integration, 2026-09-29

The user accepted approximate CTS alignment; the DXGI probe is not integrated.
The source worker enables timing by default. Official raw metadata/events/video
remain unchanged; our timing goes into an additional sidecar. Requires PyAV
16.0.1 and `obs-plugins/64bit/trace2task-frame-clock.dll` in the dedicated OBS
directory (already installed locally). The plugin requires OBS 32.2.2, rejects
pause/missing timing/overflow evidence. Build instructions: `scripts/opencua/build-frame-clock.ps1`.

25 relevant tests passed. Real existing video regression checked all 24 marker
requests against the previous CTS analysis, including decoded image content;
469 final video frames matched, one un-muxed callback packet explicitly excluded.
Report: `D:\Trace2Task-deps\opencua-cts-integration-validation\bc7a15f5-cca9-4a14-aec3-528006d0feb5\report.json`.
This confirms implementation consistency, not strict pre-action pixels or an
error bound. No new human recording or installer replacement was performed.
