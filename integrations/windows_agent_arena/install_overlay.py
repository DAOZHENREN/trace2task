from __future__ import annotations

import argparse
import shutil
from pathlib import Path

AGENT_BRANCH = '''    elif cfg_args["agent_name"] == "trace2task":
        from mm_agents.trace2task.agent import Trace2TaskAgent
        agent = Trace2TaskAgent()
'''
ANCHOR = '''    elif cfg_args["agent_name"] == "claude":
'''
DOCKER_ENV_BLOCK = '''    # Trace2Task plans on the Windows host using the saved Codex subscription.
    if [ "$agent" = "trace2task" ]; then
        : "${TRACE2TASK_WAA_BRIDGE_URL:?TRACE2TASK_WAA_BRIDGE_URL is required}"
        : "${TRACE2TASK_WAA_TOKEN:?TRACE2TASK_WAA_TOKEN is required}"
        : "${TRACE2TASK_WAA_JSON_NAME:=evaluation_examples_windows/test_trace2task.json}"
        : "${TRACE2TASK_WAA_RESULT_DIR:=./results/trace2task}"
        docker_command+=" --add-host=host.docker.internal:host-gateway"
        docker_command+=" -e TRACE2TASK_WAA_BRIDGE_URL=$TRACE2TASK_WAA_BRIDGE_URL"
        docker_command+=" -e TRACE2TASK_WAA_TOKEN=$TRACE2TASK_WAA_TOKEN"
        docker_command+=" -e TRACE2TASK_WAA_TIMEOUT=${TRACE2TASK_WAA_TIMEOUT:-330}"
    fi

'''
DOCKER_ANCHOR = '''    # Add the image name with tag
'''
DOCKER_BLOCK_START = '''    # Trace2Task plans on the Windows host using the saved Codex subscription.
'''
ENTRYPOINT_TRACE2TASK_BLOCK = '''    if [ "$agent" = "trace2task" ]; then
        entrypoint_args+=" --json-name $TRACE2TASK_WAA_JSON_NAME"
        entrypoint_args+=" --result-dir $TRACE2TASK_WAA_RESULT_DIR"
    fi
'''
ENTRYPOINT_ANCHOR = '''    docker_command+=$entrypoint_args
'''
RESET_IMPORT_BLOCK = '''from trace2task_reset import (
    apply_trace2task_reset,
    verify_trace2task_reset,
)
'''
RESET_IMPORT_ANCHOR = '''from trajectory_recorder import TrajectoryRecorder
'''
RESET_RUN_BLOCK = '''    agent.reset()
    apply_trace2task_reset(env, example)
    obs = env.reset(task_config=example)
    verify_trace2task_reset(env, example)
'''
RESET_RUN_ANCHOR = '''    agent.reset()
    obs = env.reset(task_config=example)
'''
VM_REQUIREMENT = "pyperclip==1.9.0"
SERVER_ROUTE_BLOCK = '''from trace2task_input import register_trace2task_input_routes

app = Flask(__name__)
register_trace2task_input_routes(app)
'''
SERVER_ROUTE_ANCHOR = '''app = Flask(__name__)
'''
BUILD_ARGS_MARKER = "${TRACE2TASK_DOCKER_BUILD_ARGS:-}"
FUSE_ANCHOR = '''# Install fuse
RUN apt-get update && apt-get install -y fuse
'''
FUSE_MIRROR_BLOCK = '''# Install fuse
ARG TRACE2TASK_DEBIAN_MIRROR=""
ARG TRACE2TASK_DEBIAN_SECURITY_MIRROR=""
RUN if [ -n "$TRACE2TASK_DEBIAN_MIRROR" ]; then \\
        sed -i "s|http://deb.debian.org/debian|$TRACE2TASK_DEBIAN_MIRROR|g" \\
          /etc/apt/sources.list /etc/apt/sources.list.d/*.sources 2>/dev/null || true; \\
    fi && \\
    if [ -n "$TRACE2TASK_DEBIAN_SECURITY_MIRROR" ]; then \\
        sed -i "s|http://deb.debian.org/debian-security|$TRACE2TASK_DEBIAN_SECURITY_MIRROR|g" \\
          /etc/apt/sources.list /etc/apt/sources.list.d/*.sources 2>/dev/null || true; \\
    fi && \\
    apt-get update && apt-get install -y fuse
'''
STALE_LIBREOFFICE_VERSION = "24.8.2"
LIBREOFFICE_VERSION = "25.8.7"
LIBREOFFICE_DOWNLOAD_ANCHOR = '''    $libreOfficeInstallerFilePath = "$env:TEMP\\libreOffice_installer.exe"

    $downloadResult = Invoke-DownloadFileFromAvailableMirrors -mirrorUrls $libreOfficeToolDetails.mirrors -outfile $libreOfficeInstallerFilePath
'''
LIBREOFFICE_CACHE_BLOCK = '''    $libreOfficeInstallerFilePath = "$env:TEMP\\libreOffice_installer.exe"
    $cachedLibreOfficeInstaller = Join-Path $scriptFolder "downloads\\LibreOffice_25.8.7_Win_x86-64.msi"
    if (Test-Path $cachedLibreOfficeInstaller) {
        Copy-Item -Path $cachedLibreOfficeInstaller -Destination $libreOfficeInstallerFilePath -Force
        Write-Host "Using cached LibreOffice installer: $cachedLibreOfficeInstaller"
        $downloadResult = $true
    } else {
        $downloadResult = Invoke-DownloadFileFromAvailableMirrors -mirrorUrls $libreOfficeToolDetails.mirrors -outfile $libreOfficeInstallerFilePath
    }
'''
GIMP_DOWNLOAD_ANCHOR = '''    $gimpInstallerFilePath = "$env:TEMP\\gimp_installer.exe"
    $downloadResult = Invoke-DownloadFileFromAvailableMirrors -mirrorUrls $gimpToolDetails.mirrors -outfile $gimpInstallerFilePath
'''
GIMP_CACHE_BLOCK = '''    $gimpInstallerFilePath = "$env:TEMP\\gimp_installer.exe"
    $cachedGimpInstaller = Join-Path $scriptFolder "downloads\\gimp-2.10.38-setup.exe"
    if (Test-Path $cachedGimpInstaller) {
        Copy-Item -Path $cachedGimpInstaller -Destination $gimpInstallerFilePath -Force
        Write-Host "Using cached GIMP installer: $cachedGimpInstaller"
        $downloadResult = $true
    } else {
        $downloadResult = Invoke-DownloadFileFromAvailableMirrors -mirrorUrls $gimpToolDetails.mirrors -outfile $gimpInstallerFilePath
    }
'''
THUNDERBIRD_DOWNLOAD_ANCHOR = '''    $thunderbirdInstallerFilePath = "$env:TEMP\\ThunderbirdSetup.exe"
    $downloadResult = Invoke-DownloadFileFromAvailableMirrors -mirrorUrls $thunderbirdToolDetails.mirrors -outfile $thunderbirdInstallerFilePath
'''
THUNDERBIRD_CACHE_BLOCK = '''    $thunderbirdInstallerFilePath = "$env:TEMP\\ThunderbirdSetup.exe"
    $cachedThunderbirdInstaller = Join-Path $scriptFolder "downloads\\Thunderbird Setup 115.12.1.exe"
    if (Test-Path $cachedThunderbirdInstaller) {
        Copy-Item -Path $cachedThunderbirdInstaller -Destination $thunderbirdInstallerFilePath -Force
        Write-Host "Using cached Thunderbird installer: $cachedThunderbirdInstaller"
        $downloadResult = $true
    } else {
        $downloadResult = Invoke-DownloadFileFromAvailableMirrors -mirrorUrls $thunderbirdToolDetails.mirrors -outfile $thunderbirdInstallerFilePath
    }
'''
CADDY_DOWNLOAD_ANCHOR = '''    $downloadResult = Invoke-DownloadFileFromAvailableMirrors -mirrorUrls $caddyProxyToolDetails.mirrors -outfile $caddyProxyExecutablePath
'''
CADDY_CACHE_BLOCK = '''    $cachedCaddyExecutable = Join-Path $scriptFolder "downloads\\caddy_windows_amd64.exe"
    if (Test-Path $cachedCaddyExecutable) {
        Copy-Item -Path $cachedCaddyExecutable -Destination $caddyProxyExecutablePath -Force
        Write-Host "Using cached Caddy executable: $cachedCaddyExecutable"
        $downloadResult = $true
    } else {
        $downloadResult = Invoke-DownloadFileFromAvailableMirrors -mirrorUrls $caddyProxyToolDetails.mirrors -outfile $caddyProxyExecutablePath
    }
'''
PYTHON_INSTALL_ANCHOR = '''# Force to install Python 3.10 as the pre-installed version on Windows may not work sometimes
Write-Host "Downloading Python $pythonVersion..."
$pythonInstallerFilePath = "$env:TEMP\\python_installer.exe"
$downloadResult = Invoke-DownloadFileFromAvailableMirrors -mirrorUrls $pythonDetails.mirrors -outfile $pythonInstallerFilePath
if (-not $downloadResult) {
    Write-Host "Failed to download Python. Please try again later or install manually."
} else {
    Write-Host "Installing Python for current user..."
    Start-Process -FilePath $pythonInstallerFilePath -Args "/quiet InstallAllUsers=0 PrependPath=0" -NoNewWindow -Wait
    $pythonExecutablePath = "$userPythonPath\\Python310\\python.exe"
    $setAliasExpression = "Set-Alias -Name $pythonAlias -Value `"$pythonExecutablePath`""
    Add-Content -Path $PROFILE -Value $setAliasExpression
    Invoke-Expression $setAliasExpression
}
'''
PYTHON_INSTALL_IDEMPOTENT_BLOCK = '''if ($pythonExecutablePath) {
    Write-Host "Python is already installed: $pythonExecutablePath"
    $setAliasExpression = "Set-Alias -Name $pythonAlias -Value `"$pythonExecutablePath`""
    Invoke-Expression $setAliasExpression
} else {
    Write-Host "Downloading Python 3.10..."
    $pythonInstallerFilePath = "$env:TEMP\\python_installer.exe"
    $downloadResult = Invoke-DownloadFileFromAvailableMirrors -mirrorUrls $pythonDetails.mirrors -outfile $pythonInstallerFilePath
    if (-not $downloadResult) {
        Write-Host "Failed to download Python. Please try again later or install manually."
    } else {
        Write-Host "Installing Python for current user..."
        Start-Process -FilePath $pythonInstallerFilePath -Args "/quiet InstallAllUsers=0 PrependPath=0" -NoNewWindow -Wait
        $pythonExecutablePath = "$userPythonPath\\Python310\\python.exe"
        $setAliasExpression = "Set-Alias -Name $pythonAlias -Value `"$pythonExecutablePath`""
        Add-Content -Path $PROFILE -Value $setAliasExpression
        Invoke-Expression $setAliasExpression
    }
}
'''


def install(waa_root: Path) -> None:
    root = waa_root.expanduser().resolve()
    client = root / "src" / "win-arena-container" / "client"
    run_path = client / "run.py"
    if not run_path.is_file():
        raise FileNotFoundError(f"Windows Agent Arena run.py was not found under {root}")
    source_agent = Path(__file__).parent / "mm_agents" / "trace2task"
    target_agent = client / "mm_agents" / "trace2task"
    shutil.copytree(source_agent, target_agent, dirs_exist_ok=True)
    shutil.copy2(
        Path(__file__).parent / "test_trace2task.json",
        client / "evaluation_examples_windows" / "test_trace2task.json",
    )
    requirements_path = root / "src" / "win-arena-container" / "vm" / "setup" / "server" / "requirements.txt"
    if not requirements_path.is_file():
        raise FileNotFoundError(f"WAA VM requirements were not found under {root}")
    requirements = requirements_path.read_text(encoding="utf-8").splitlines()
    if VM_REQUIREMENT not in requirements:
        requirements.append(VM_REQUIREMENT)
        requirements_path.write_text(
            "\n".join(requirements) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    server_root = requirements_path.parent
    shutil.copy2(
        Path(__file__).parent / "vm_server" / "trace2task_input.py",
        server_root / "trace2task_input.py",
    )
    server_main = server_root / "main.py"
    server_text = server_main.read_text(encoding="utf-8")
    if SERVER_ROUTE_BLOCK not in server_text:
        if SERVER_ROUTE_ANCHOR not in server_text:
            raise RuntimeError("WAA VM server no longer contains the Flask app anchor")
        server_text = server_text.replace(
            SERVER_ROUTE_ANCHOR,
            SERVER_ROUTE_BLOCK,
            1,
        )
        server_main.write_text(server_text, encoding="utf-8", newline="\n")
    shutil.copy2(
        Path(__file__).parent / "client" / "trace2task_human_trace.py",
        client / "trace2task_human_trace.py",
    )
    shutil.copy2(
        Path(__file__).parent / "client" / "trace2task_reset.py",
        client / "trace2task_reset.py",
    )
    run_single_path = client / "lib_run_single.py"
    if not run_single_path.is_file():
        raise FileNotFoundError(f"WAA single-example runner was not found under {root}")
    run_single_text = run_single_path.read_text(encoding="utf-8")
    if RESET_IMPORT_BLOCK not in run_single_text:
        if RESET_IMPORT_ANCHOR not in run_single_text:
            raise RuntimeError("WAA single-example runner no longer contains the import anchor")
        run_single_text = run_single_text.replace(
            RESET_IMPORT_ANCHOR,
            RESET_IMPORT_ANCHOR + RESET_IMPORT_BLOCK,
            1,
        )
    if RESET_RUN_BLOCK not in run_single_text:
        if RESET_RUN_ANCHOR not in run_single_text:
            raise RuntimeError("WAA single-example runner no longer contains the reset anchor")
        run_single_text = run_single_text.replace(
            RESET_RUN_ANCHOR,
            RESET_RUN_BLOCK,
            1,
        )
    run_single_path.write_text(run_single_text, encoding="utf-8", newline="\n")
    shutil.copy2(
        Path(__file__).parent / "vm_setup" / "recover_trace2task.bat",
        requirements_path.parent.parent / "recover_trace2task.bat",
    )
    shutil.copy2(
        Path(__file__).parent / "vm_setup" / "on-logon.ps1",
        requirements_path.parent.parent / "on-logon.ps1",
    )
    text = run_path.read_text(encoding="utf-8")
    if AGENT_BRANCH not in text:
        if ANCHOR not in text:
            raise RuntimeError("WAA run.py no longer contains the expected agent selection anchor")
        run_path.write_text(text.replace(ANCHOR, AGENT_BRANCH + ANCHOR), encoding="utf-8")
    run_script = root / "scripts" / "run.sh"
    script = run_script.read_text(encoding="utf-8")
    if DOCKER_ANCHOR not in script:
        raise RuntimeError("WAA run.sh no longer contains the expected Docker anchor")
    block_start = script.find(DOCKER_BLOCK_START)
    block_end = script.find(DOCKER_ANCHOR)
    if block_start >= 0:
        script = script[:block_start] + DOCKER_ENV_BLOCK + script[block_end:]
    else:
        script = script.replace(DOCKER_ANCHOR, DOCKER_ENV_BLOCK + DOCKER_ANCHOR)
    if ENTRYPOINT_TRACE2TASK_BLOCK not in script:
        if ENTRYPOINT_ANCHOR not in script:
            raise RuntimeError("WAA run.sh no longer contains the entrypoint anchor")
        script = script.replace(
            ENTRYPOINT_ANCHOR,
            ENTRYPOINT_TRACE2TASK_BLOCK + ENTRYPOINT_ANCHOR,
        )
    run_script.write_text(script, encoding="utf-8", newline="\n")
    build_script = root / "scripts" / "build-container-image.sh"
    if build_script.is_file():
        build_text = build_script.read_text(encoding="utf-8")
        if BUILD_ARGS_MARKER not in build_text:
            build_text = build_text.replace(
                "docker build --build-arg",
                f"docker build {BUILD_ARGS_MARKER} --build-arg",
            )
            build_script.write_text(build_text, encoding="utf-8", newline="\n")
    dockerfile = root / "src" / "win-arena-container" / "Dockerfile-WinArena"
    if dockerfile.is_file():
        dockerfile_text = dockerfile.read_text(encoding="utf-8")
        if FUSE_MIRROR_BLOCK not in dockerfile_text:
            if FUSE_ANCHOR not in dockerfile_text:
                raise RuntimeError("WAA Dockerfile no longer contains the fuse install anchor")
            dockerfile.write_text(
                dockerfile_text.replace(FUSE_ANCHOR, FUSE_MIRROR_BLOCK, 1),
                encoding="utf-8",
                newline="\n",
            )
    tools_config = (
        root
        / "src"
        / "win-arena-container"
        / "vm"
        / "setup"
        / "tools_config.json"
    )
    if tools_config.is_file():
        tools_text = tools_config.read_text(encoding="utf-8")
        updated_tools_text = tools_text.replace(
            STALE_LIBREOFFICE_VERSION,
            LIBREOFFICE_VERSION,
        )
        if updated_tools_text != tools_text:
            tools_config.write_text(
                updated_tools_text,
                encoding="utf-8",
                newline="\n",
            )
    setup_script = tools_config.parent / "setup.ps1"
    if setup_script.is_file():
        setup_text = setup_script.read_text(encoding="utf-8")
        if LIBREOFFICE_CACHE_BLOCK not in setup_text:
            if LIBREOFFICE_DOWNLOAD_ANCHOR not in setup_text:
                raise RuntimeError(
                    "WAA setup.ps1 no longer contains the LibreOffice download anchor"
                )
            setup_script.write_text(
                setup_text.replace(
                    LIBREOFFICE_DOWNLOAD_ANCHOR,
                    LIBREOFFICE_CACHE_BLOCK,
                    1,
                ),
                encoding="utf-8",
                newline="\n",
            )
            setup_text = setup_script.read_text(encoding="utf-8")
        if GIMP_CACHE_BLOCK not in setup_text:
            if GIMP_DOWNLOAD_ANCHOR not in setup_text:
                raise RuntimeError(
                    "WAA setup.ps1 no longer contains the GIMP download anchor"
                )
            setup_script.write_text(
                setup_text.replace(
                    GIMP_DOWNLOAD_ANCHOR,
                    GIMP_CACHE_BLOCK,
                    1,
                ),
                encoding="utf-8",
                newline="\n",
            )
            setup_text = setup_script.read_text(encoding="utf-8")
        if THUNDERBIRD_CACHE_BLOCK not in setup_text:
            if THUNDERBIRD_DOWNLOAD_ANCHOR not in setup_text:
                raise RuntimeError(
                    "WAA setup.ps1 no longer contains the Thunderbird download anchor"
                )
            setup_script.write_text(
                setup_text.replace(
                    THUNDERBIRD_DOWNLOAD_ANCHOR,
                    THUNDERBIRD_CACHE_BLOCK,
                    1,
                ),
                encoding="utf-8",
                newline="\n",
            )
            setup_text = setup_script.read_text(encoding="utf-8")
        if CADDY_CACHE_BLOCK not in setup_text:
            if CADDY_DOWNLOAD_ANCHOR not in setup_text:
                raise RuntimeError(
                    "WAA setup.ps1 no longer contains the Caddy download anchor"
                )
            setup_script.write_text(
                setup_text.replace(
                    CADDY_DOWNLOAD_ANCHOR,
                    CADDY_CACHE_BLOCK,
                    1,
                ),
                encoding="utf-8",
                newline="\n",
            )
            setup_text = setup_script.read_text(encoding="utf-8")
        if PYTHON_INSTALL_IDEMPOTENT_BLOCK not in setup_text:
            if PYTHON_INSTALL_ANCHOR not in setup_text:
                raise RuntimeError(
                    "WAA setup.ps1 no longer contains the Python install anchor"
                )
            setup_script.write_text(
                setup_text.replace(
                    PYTHON_INSTALL_ANCHOR,
                    PYTHON_INSTALL_IDEMPOTENT_BLOCK,
                    1,
                ),
                encoding="utf-8",
                newline="\n",
            )
    print(f"Installed Trace2Task WAA overlay into {root}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("waa_root", type=Path)
    args = parser.parse_args()
    install(args.waa_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
