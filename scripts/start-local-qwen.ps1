param(
    [Parameter(Mandatory = $true)][string]$ServerPath,
    [Parameter(Mandatory = $true)][string]$ModelDirectory,
    [ValidateRange(4096, 32768)][int]$ContextSize = 16384
)
$ErrorActionPreference = 'Stop'
$server = (Resolve-Path -LiteralPath $ServerPath).Path
$model = Join-Path $ModelDirectory 'Qwen3VL-8B-Instruct-Q4_K_M.gguf'
$vision = Join-Path $ModelDirectory 'mmproj-Qwen3VL-8B-Instruct-F16.gguf'
foreach ($file in @($server, $model, $vision)) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing file: $file" }
}
if (Get-NetTCPConnection -LocalPort 8081 -State Listen -ErrorAction SilentlyContinue) {
    throw 'Port 8081 is occupied. Stop the existing service before starting another.'
}
# Foreground process: Ctrl+C stops the service and releases GPU memory.
& $server -m $model --mmproj $vision --host 127.0.0.1 --port 8081 `
    --alias qwen3-vl-8b-instruct -ngl 99 -c $ContextSize `
    -ctk q8_0 -ctv q8_0 -np 1 -b 512 -ub 128 -fa on `
    --image-min-tokens 1024 --image-max-tokens 1280 `
    --cors-origins http://127.0.0.1:8081 --no-cors-credentials
if ($LASTEXITCODE -ne 0) { throw "Local model server exited with code $LASTEXITCODE" }
