param([string]$Iscc = 'D:\Tools\InnoSetup\ISCC.exe')
$ErrorActionPreference = 'Stop'
$codeRoot = Split-Path -Parent $PSScriptRoot
Push-Location $codeRoot
try {
    # Run uv sync --extra desktop --extra dev and install pyinstaller==6.16.0 first.
    & .venv\Scripts\python.exe -m PyInstaller --noconfirm packaging\windows\Trace2Task.spec
    if ($LASTEXITCODE -ne 0) { throw 'Application build failed' }
    & $Iscc packaging\windows\installer.iss
    if ($LASTEXITCODE -ne 0) { throw 'Installer build failed' }
    Get-FileHash dist\installer\Trace2Task-Setup-*-win64.exe -Algorithm SHA256
} finally { Pop-Location }
