$ErrorActionPreference = "Stop"
$scriptFolder = "\\host.lan\Data"
$pythonScriptFile = Join-Path $scriptFolder "server\main.py"
$pythonServerPort = 5000
$serverLog = Join-Path $scriptFolder "windows_arena_server_log.txt"
$serverStdoutLog = Join-Path $scriptFolder "windows_arena_server_stdout.txt"
$serverStderrLog = Join-Path $scriptFolder "windows_arena_server_stderr.txt"

try {
    $pythonExecutable = Get-ChildItem `
        -Path "$env:LOCALAPPDATA\Programs\Python" `
        -Filter python.exe `
        -Recurse `
        -ErrorAction Stop | Select-Object -First 1 -ExpandProperty FullName
    $caddyExecutable = "C:\Users\$env:USERNAME\caddy_windows_amd64.exe"
    $cachedCaddyExecutable = Join-Path $scriptFolder "downloads\caddy_windows_amd64.exe"
    if (-not (Test-Path $pythonExecutable)) {
        throw "Python executable was not found."
    }
    $pythonRoot = Split-Path -Parent $pythonExecutable
    $pywin32DllDirectory = Join-Path $pythonRoot "Lib\site-packages\pywin32_system32"
    if (Test-Path $pywin32DllDirectory) {
        $env:PATH = "$pywin32DllDirectory;$env:PATH"
    }
    if (Test-Path $cachedCaddyExecutable) {
        $replaceCaddy = -not (Test-Path $caddyExecutable)
        if (-not $replaceCaddy) {
            $installedHash = (Get-FileHash -Path $caddyExecutable -Algorithm SHA256).Hash
            $cachedHash = (Get-FileHash -Path $cachedCaddyExecutable -Algorithm SHA256).Hash
            $replaceCaddy = $installedHash -ne $cachedHash
        }
        if ($replaceCaddy) {
            Copy-Item -Path $cachedCaddyExecutable -Destination $caddyExecutable -Force
        }
    }
    $mfcRuntime = Join-Path $env:WINDIR "System32\mfc140u.dll"
    if (-not (Test-Path $mfcRuntime)) {
        $vcRuntimeInstaller = Join-Path $scriptFolder "downloads\vc_redist.x64.exe"
        if (-not (Test-Path $vcRuntimeInstaller)) {
            throw "Microsoft VC++ runtime installer was not found: $vcRuntimeInstaller"
        }
        $vcRuntimeProcess = Start-Process `
            -FilePath $vcRuntimeInstaller `
            -ArgumentList "/install", "/quiet", "/norestart" `
            -PassThru `
            -Wait
        if ($vcRuntimeProcess.ExitCode -notin 0, 1638, 3010) {
            throw "Microsoft VC++ runtime installer exited with code $($vcRuntimeProcess.ExitCode)"
        }
    }
    if (-not (Test-Path $caddyExecutable)) {
        throw "Caddy executable was not found: $caddyExecutable"
    }

    "Starting Caddy and Windows Arena server at $(Get-Date -Format o)" |
        Out-File -FilePath $serverLog -Encoding utf8
    "Python executable: $pythonExecutable" |
        Out-File -FilePath $serverLog -Encoding utf8 -Append
    "pywin32 DLL directory: $pywin32DllDirectory" |
        Out-File -FilePath $serverLog -Encoding utf8 -Append
    "MFC runtime: $mfcRuntime" |
        Out-File -FilePath $serverLog -Encoding utf8 -Append
    $caddyProcess = Start-Process `
        -NoNewWindow `
        -FilePath $caddyExecutable `
        -ArgumentList "reverse-proxy", "--from", ":9222", "--to", ":1337" `
        -PassThru
    "Caddy process started: $($caddyProcess.Id)" |
        Out-File -FilePath $serverLog -Encoding utf8 -Append
    "Launching Windows Arena server on port $pythonServerPort" |
        Out-File -FilePath $serverLog -Encoding utf8 -Append
    $pythonProcess = Start-Process `
        -NoNewWindow `
        -FilePath $pythonExecutable `
        -ArgumentList $pythonScriptFile, "--port", $pythonServerPort `
        -RedirectStandardOutput $serverStdoutLog `
        -RedirectStandardError $serverStderrLog `
        -PassThru `
        -Wait
    "Windows Arena server exited with code $($pythonProcess.ExitCode)" |
        Out-File -FilePath $serverLog -Encoding utf8 -Append
} catch {
    $_ | Out-File -FilePath $serverLog -Encoding utf8 -Append
    throw
}
