param([string]$Iscc = 'D:\Tools\InnoSetup\ISCC.exe')
$ErrorActionPreference = 'Stop'
$codeRoot = Split-Path -Parent $PSScriptRoot
Push-Location $codeRoot
try {
    Push-Location (Join-Path $codeRoot 'frontend')
    try {
        & npm.cmd ci --no-fund --no-audit
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependencies failed' }
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Workbench build failed' }
    } finally { Pop-Location }
    # Only build machines need uv. End users receive the native installer binary.
    $vendorDir = Join-Path $codeRoot 'build/desktop-vendor'
    New-Item -ItemType Directory -Force -Path $vendorDir | Out-Null
    $uvSource = (Get-Command uv.exe -ErrorAction Stop).Source
    $uvTarget = Join-Path $vendorDir 'uv.exe'
    if (-not (Test-Path -LiteralPath $uvTarget) -or
        (Get-FileHash -LiteralPath $uvSource).Hash -ne (Get-FileHash -LiteralPath $uvTarget).Hash) {
        Copy-Item -LiteralPath $uvSource -Destination $uvTarget
    }
    $uvVersion = & $uvSource --version
    if ($uvVersion -notmatch '^uv 0\.11\.28 ') { throw 'Build requires uv 0.11.28; review runtime compatibility before upgrading' }
    Invoke-WebRequest 'https://raw.githubusercontent.com/astral-sh/uv/0.11.28/LICENSE-MIT' -OutFile (Join-Path $vendorDir 'uv-LICENSE-MIT')
    $webview = Join-Path $vendorDir 'MicrosoftEdgeWebview2Setup.exe'
    Invoke-WebRequest 'https://go.microsoft.com/fwlink/p/?LinkId=2124703' -OutFile $webview
    $signature = Get-AuthenticodeSignature -LiteralPath $webview
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') { throw 'WebView2 bootstrapper signature verification failed' }
    # Run uv sync --extra desktop --extra dev and install pyinstaller==6.16.0 first.
    & .venv\Scripts\python.exe -m PyInstaller --noconfirm packaging\windows\Trace2Task.spec
    if ($LASTEXITCODE -ne 0) { throw 'Application build failed' }
    & $Iscc packaging\windows\installer.iss
    if ($LASTEXITCODE -ne 0) { throw 'Installer build failed' }
    Get-FileHash dist\installer\Trace2Task-Setup-*-win64.exe -Algorithm SHA256
} finally { Pop-Location }
