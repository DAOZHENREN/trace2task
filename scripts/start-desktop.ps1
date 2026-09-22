param([string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference = 'Stop'
$codeRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $codeRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Install desktop dependencies first: uv sync --extra desktop'
}
$env:PYTHONUTF8 = '1'
Start-Process -FilePath $python -ArgumentList @('-m', 'trace2task.desktop_app', '--project-root', ('"' + $ProjectRoot + '"')) -WorkingDirectory $codeRoot -WindowStyle Hidden
