param(
    [string]$ProjectRoot,
    [switch]$Development,
    [switch]$SmokeTest,
    [switch]$Wait
)
$ErrorActionPreference = 'Stop'
$codeRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $codeRoot '.venv\Scripts\pythonw.exe'
try {
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Install desktop dependencies first: uv sync --extra desktop'
}
$env:PYTHONUTF8 = '1'
# Prefer this checkout even when another editable installation is present.
$env:PYTHONPATH = Join-Path $codeRoot 'src'
$arguments = @('-m', 'trace2task.desktop_app')
if ($ProjectRoot) { $arguments += @('--project-root', ('"' + $ProjectRoot + '"')) }
if ($Development) { $arguments += '--development' }
if ($SmokeTest) { $arguments += '--smoke-test' }
$logRoot = Join-Path $env:LOCALAPPDATA 'Trace2Task\logs'
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-ffff'
$stderr = Join-Path $logRoot "source-launch-$stamp.stderr.log"
$stdout = Join-Path $logRoot "source-launch-$stamp.stdout.log"
# pythonw has no console to hide. SW_HIDE also hides its first native GUI window.
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $codeRoot -WindowStyle Normal -PassThru -RedirectStandardError $stderr -RedirectStandardOutput $stdout
if ($Wait -or $SmokeTest) {
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) { throw "Desktop launch failed. Log: $stderr" }
} else {
    # Catch missing dependencies/import errors that precede the app's error dialog.
    if ($process.WaitForExit(2000) -and $process.ExitCode -ne 0) {
        throw "Desktop launch failed. Log: $stderr"
    }
}
} catch {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'Trace2Task launcher') | Out-Null
    exit 1
}
