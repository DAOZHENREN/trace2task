param(
    [string]$Zig = 'D:/Tools/zig-python-0.15.2/ziglang/zig.exe',
    [string]$Output = 'build/dxgi-clock-probe'
)
$ErrorActionPreference = 'Stop'
if ((& $Zig version) -ne '0.15.2') { throw 'Expected Zig 0.15.2' }
$env:ZIG_GLOBAL_CACHE_DIR = 'D:/Tools/zig-cache'
$env:ZIG_LOCAL_CACHE_DIR = 'D:/Tools/zig-cache/local'
New-Item -ItemType Directory -Force $Output | Out-Null
& $Zig c++ -target x86_64-windows-gnu -std=c++17 -O2 -Wall -Wextra -shared `
    (Join-Path $PSScriptRoot 'dxgi_clock_probe.cpp') -ld3d11 -ldxgi -ldxguid -luuid `
    -o (Join-Path $Output 'dxgi-clock-probe.dll')
if ($LASTEXITCODE -ne 0) { throw 'DXGI probe build failed' }
Get-FileHash (Join-Path $Output 'dxgi-clock-probe.dll') -Algorithm SHA256
