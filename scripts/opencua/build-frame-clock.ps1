param(
    [string]$Zig = 'D:/Tools/zig-python-0.15.2/ziglang/zig.exe',
    [string]$Cache = 'D:/Tools/zig-cache',
    [string]$Output = 'build/obs-frame-clock'
)
$ErrorActionPreference = 'Stop'
if ((& $Zig version) -ne '0.15.2') { throw 'Expected Zig 0.15.2' }
$env:ZIG_GLOBAL_CACHE_DIR = $Cache
$env:ZIG_LOCAL_CACHE_DIR = Join-Path $Cache 'local'
New-Item -ItemType Directory -Force $Output | Out-Null
& $Zig cc -target x86_64-windows-gnu -std=c11 -O2 -Wall -Wextra -shared `
    (Join-Path $PSScriptRoot 'obs_frame_clock.c') -o (Join-Path $Output 'trace2task-frame-clock.dll')
if ($LASTEXITCODE -ne 0) { throw 'OBS frame-clock build failed' }
Get-FileHash (Join-Path $Output 'trace2task-frame-clock.dll') -Algorithm SHA256
