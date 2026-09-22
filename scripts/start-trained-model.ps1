param([string]$Bundle = 'D:\Models\Trace2Task-D-5970')
$ErrorActionPreference = 'Stop'
if (Get-NetTCPConnection -LocalPort 8767 -State Listen -ErrorAction SilentlyContinue) {
    Write-Host 'Preview is already running: http://127.0.0.1:8767/ (do not start a second model)'
    return
}
$python = Join-Path $Bundle '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Missing dedicated Python environment; see docs/trained-model-local.md' }
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
& $python (Join-Path $PSScriptRoot 'trained_model\preview.py') --bundle $Bundle --serve
if ($LASTEXITCODE -ne 0) { throw "D preview exited: $LASTEXITCODE" }
