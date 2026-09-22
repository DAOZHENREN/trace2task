param([string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference = 'Stop'
$codeRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $codeRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run uv sync --extra desktop first.' }
$shell = New-Object -ComObject WScript.Shell
$shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Trace2Task Desktop.lnk'
if (Test-Path -LiteralPath $shortcutPath) { throw "Shortcut already exists: $shortcutPath" }
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $python
$shortcut.Arguments = '-m trace2task.desktop_app --project-root "' + (Resolve-Path -LiteralPath $ProjectRoot).Path + '"'
$shortcut.WorkingDirectory = $codeRoot
$shortcut.Description = 'Trace2Task local desktop console'
$shortcut.Save()
Write-Output $shortcutPath
