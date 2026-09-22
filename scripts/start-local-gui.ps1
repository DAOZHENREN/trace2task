param(
    [string]$Python = 'D:\Models\Trace2Task-D-5970\.venv\Scripts\python.exe',
    [string]$ModelRoot = 'D:\Models\Trace2Task-GUI',
    [string]$DataRoot = 'D:\MyProject\trace2task'
)
$ErrorActionPreference = 'Stop'
$codeRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path -LiteralPath $Python)) { throw 'CUDA Python environment not found' }
$logDirectory = Join-Path $DataRoot 'runs\local-gui'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
# Python creates the token only if absent. The value is never printed.
$tokenFile = Join-Path $logDirectory 'service.token'
& $Python -c "import pathlib,secrets,sys; p=pathlib.Path(sys.argv[1]); p.exists() or p.write_text(secrets.token_hex(32),encoding='ascii')" $tokenFile
& $Python (Join-Path $codeRoot 'scripts\local_gui\server.py') --root $ModelRoot --output $logDirectory --token-file $tokenFile
