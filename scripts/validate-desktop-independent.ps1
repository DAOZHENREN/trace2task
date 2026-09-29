param(
    [string]$Exe = (Join-Path $PSScriptRoot '../dist/Trace2Task/Trace2Task.exe'),
    [string]$Data = 'D:\Trace2Task-standalone-validation'
)
$ErrorActionPreference = 'Stop'
$application = (Resolve-Path -LiteralPath $Exe).Path
New-Item -ItemType Directory -Force -Path $Data | Out-Null
$oldPath = $env:PATH
$oldPythonPath = $env:PYTHONPATH
$oldPythonHome = $env:PYTHONHOME
$oldVirtualEnv = $env:VIRTUAL_ENV
try {
    # Do not uninstall or rename the user's development tools.
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
    $env:PYTHONPATH = ''
    $env:PYTHONHOME = ''
    $env:VIRTUAL_ENV = ''
    $process = Start-Process -FilePath $application -ArgumentList @('--project-root', "`"$Data`"", '--smoke-test') -WorkingDirectory $env:TEMP -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(60000)) { throw 'Desktop smoke test did not exit; inspect its window and logs.' }
    if ($process.ExitCode -ne 0) { throw "Desktop failed: $($process.ExitCode)" }
    $receipt = Get-Item -LiteralPath (Join-Path $Data 'desktop-smoke.json')
    if ($receipt.LastWriteTime -lt $process.StartTime) { throw 'Stale smoke receipt' }
    $result = Get-Content -LiteralPath $receipt.FullName -Raw | ConvertFrom-Json
    if (-not $result.console -or -not $result.components -or $result.ready -ne 'complete') { throw 'Desktop DOM check failed' }
    $result
} finally {
    $env:PATH = $oldPath
    $env:PYTHONPATH = $oldPythonPath
    $env:PYTHONHOME = $oldPythonHome
    $env:VIRTUAL_ENV = $oldVirtualEnv
}
