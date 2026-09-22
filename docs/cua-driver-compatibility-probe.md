# Cua Driver Windows compatibility probe — 2026-09-21

## Scope

Exploratory test, not production integration or a general compatibility claim.
Cua Driver 0.28.2 official Windows x64 release, downloaded directly without proxy.
Binary: `D:/Tools/cua-driver-probe/v0.28.2/cua-driver-rs-0.28.2-windows-x86_64/cua-driver.exe`.
Private daemon pipe: `\\.\pipe\trace2task-cua-probe`. Telemetry disabled via
`CUA_DRIVER_RS_TELEMETRY_ENABLED=false`. No PATH or autostart changes, no production
executor changes. All input targeted a disposable Notepad document or Calculator.

## Observed results

| Check | Actual result |
| --- | --- |
| doctor | Windows interactive session and UI Automation available |
| Notepad background Chinese text | `effect: confirmed`, accessibility route; fresh readback contains the requested Chinese text |
| Foreground/cursor during that input | HWND 1772446 unchanged; cursor (975,254) unchanged; target HWND 2494438 was not foreground |
| Background Notepad screenshot | Valid 1000×685 PNG; visually verified Chinese text; foreground unchanged |
| Stale element token | `status: refused`, `stale_element_token`; no foreground escalation |
| Calculator background button | Invoked observed button 七; fresh snapshot reports 显示为 7 |
| Foreground/cursor during Calculator click | HWND 1772446 unchanged; cursor (947,856) unchanged |
| Calculator action receipt | `effect: unverifiable`; subsequent observation was needed to verify the effect |

Recorded CLI-inclusive times, single samples only: Notepad input 52 ms,
Notepad UIA readback 618 ms, screenshot 914 ms, Calculator click 1276 ms,
Calculator readback 570 ms. These are not model inference times or benchmark averages.

## Important integration issues

- A stale-token refusal and a failed app-name lookup returned CLI exit code 0.
  Always parse the result, including refusal/error and effect fields.
- English `Calculator` lookup failed on this system; launching `calc.exe`
  returned an initial PID with no windows. Enumeration found the actual window
  hosted by ApplicationFrameHost. Bind fresh `(pid, window_id)`, not launcher PID.
- Notepad launch reused its existing tabbed app. Only the dedicated test document
  was edited; the shared process must not be terminated during cleanup.
- Notepad launch coincided with foreground and cursor changes; the user was also
  moving the mouse. Do not claim launch never steals focus based on this test.
- The text call appended the Chinese text to the existing test document. Do not
  assume all type_text routes mean replace-all merely because UIA is involved.
- Screenshot coordinates are window-local screenshot pixels; D emits normalized
  desktop coordinates. An explicit target/capture transform remains necessary.
- Foreground/cursor samples are before/after only, not continuous monitoring.
  They cannot prove absence of every transient focus change or input leakage.

Not tested: browser, QQ Music, games, minimized targets, scroll, drag, raw key
streams, multi-monitor/DPI changes, recovery, stop under load, or repeated trials.
Background UIA success does not establish universal background pixel input.

## Evidence and reproduction

Local artifacts (not committed):
`D:/MyProject/trace2task/runs/cua-driver-probe-20260921/calls.jsonl`
and `notepad-background.png`. Logs preserve arguments, complete replies, timings,
foreground HWND and cursor positions. The UIA logs include existing tab titles;
keep these artifacts local rather than committing or publishing them.

`scripts/probe_cua_driver.py` is a manual CLI wrapper, not an automatic test suite.
Never reuse the recorded PIDs or tokens: enumerate and observe again before input.

Recommendation: proceed with a feature-flagged backend prototype after adding
structured result classification and explicit snapshot/target binding. Do not
replace the current Win32 executor on the strength of these two-app samples.

Sources: https://github.com/trycua/cua/tree/main/libs/cua-driver and
https://cua.ai/docs/reference/cua-driver/platform-support
