from __future__ import annotations

import runpy
from pathlib import Path

install = runpy.run_path(
    str(
        Path(__file__).parents[1]
        / "integrations"
        / "windows_agent_arena"
        / "install_overlay.py"
    )
)["install"]


def _fake_waa_checkout(root: Path) -> Path:
    client = root / "src" / "win-arena-container" / "client"
    client.mkdir(parents=True)
    (client / "evaluation_examples_windows").mkdir()
    (client / "run.py").write_text(
        '    elif cfg_args["agent_name"] == "claude":\n',
        encoding="utf-8",
    )
    (client / "lib_run_single.py").write_text(
        "from trajectory_recorder import TrajectoryRecorder\n\n"
        "def run_single_example(agent, env, example):\n"
        "    agent.reset()\n"
        "    obs = env.reset(task_config=example)\n"
        "    return obs\n",
        encoding="utf-8",
    )
    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "run.sh").write_text(
        "    # Add the image name with tag\n"
        '    entrypoint_args=" -c test"\n'
        "    docker_command+=$entrypoint_args\n",
        encoding="utf-8",
    )
    (scripts / "build-container-image.sh").write_text(
        "docker build --build-arg DEPLOY_MODE=dev .\n",
        encoding="utf-8",
    )
    dockerfile = root / "src" / "win-arena-container" / "Dockerfile-WinArena"
    dockerfile.write_text(
        "# Install fuse\nRUN apt-get update && apt-get install -y fuse\n",
        encoding="utf-8",
    )
    requirements = root / "src" / "win-arena-container" / "vm" / "setup" / "server"
    requirements.mkdir(parents=True)
    (requirements / "requirements.txt").write_text("requests\n", encoding="utf-8")
    (requirements / "main.py").write_text("app = Flask(__name__)\n", encoding="utf-8")
    (requirements.parent / "tools_config.json").write_text(
        '{"LibreOffice":{"mirrors":["https://example.test/stable/24.8.2/'
        'LibreOffice_24.8.2_Win_x86-64.msi"]}}\n',
        encoding="utf-8",
    )
    (requirements.parent / "setup.ps1").write_text(
        '# Force to install Python 3.10 as the pre-installed version on Windows may not work sometimes\n'
        'Write-Host "Downloading Python $pythonVersion..."\n'
        '$pythonInstallerFilePath = "$env:TEMP\\python_installer.exe"\n'
        '$downloadResult = Invoke-DownloadFileFromAvailableMirrors '
        '-mirrorUrls $pythonDetails.mirrors -outfile $pythonInstallerFilePath\n'
        'if (-not $downloadResult) {\n'
        '    Write-Host "Failed to download Python. Please try again later or install manually."\n'
        '} else {\n'
        '    Write-Host "Installing Python for current user..."\n'
        '    Start-Process -FilePath $pythonInstallerFilePath -Args '
        '"/quiet InstallAllUsers=0 PrependPath=0" -NoNewWindow -Wait\n'
        '    $pythonExecutablePath = "$userPythonPath\\Python310\\python.exe"\n'
        '    $setAliasExpression = "Set-Alias -Name $pythonAlias -Value '
        '`"$pythonExecutablePath`""\n'
        '    Add-Content -Path $PROFILE -Value $setAliasExpression\n'
        '    Invoke-Expression $setAliasExpression\n'
        '}\n'
        '    $libreOfficeInstallerFilePath = "$env:TEMP\\libreOffice_installer.exe"\n'
        "\n"
        "    $downloadResult = Invoke-DownloadFileFromAvailableMirrors "
        "-mirrorUrls $libreOfficeToolDetails.mirrors "
        "-outfile $libreOfficeInstallerFilePath\n"
        '    $gimpInstallerFilePath = "$env:TEMP\\gimp_installer.exe"\n'
        "    $downloadResult = Invoke-DownloadFileFromAvailableMirrors "
        "-mirrorUrls $gimpToolDetails.mirrors -outfile $gimpInstallerFilePath\n"
        '    $thunderbirdInstallerFilePath = "$env:TEMP\\ThunderbirdSetup.exe"\n'
        "    $downloadResult = Invoke-DownloadFileFromAvailableMirrors "
        "-mirrorUrls $thunderbirdToolDetails.mirrors "
        "-outfile $thunderbirdInstallerFilePath\n"
        "    $downloadResult = Invoke-DownloadFileFromAvailableMirrors "
        "-mirrorUrls $caddyProxyToolDetails.mirrors "
        "-outfile $caddyProxyExecutablePath\n",
        encoding="utf-8",
    )
    return root


def test_waa_overlay_is_idempotent_and_preserves_lf(tmp_path: Path) -> None:
    checkout = _fake_waa_checkout(tmp_path / "waa")

    install(checkout)
    install(checkout)

    run_script = checkout / "scripts" / "run.sh"
    script = run_script.read_bytes()
    assert b"\r\n" not in script
    text = script.decode("utf-8")
    assert text.count("Trace2Task plans on the Windows host") == 1
    assert text.count("--json-name $TRACE2TASK_WAA_JSON_NAME") == 1
    build_script = checkout / "scripts" / "build-container-image.sh"
    build_text = build_script.read_text(encoding="utf-8")
    assert build_text.count("${TRACE2TASK_DOCKER_BUILD_ARGS:-}") == 1
    dockerfile_text = (
        checkout / "src" / "win-arena-container" / "Dockerfile-WinArena"
    ).read_text(encoding="utf-8")
    assert dockerfile_text.count('ARG TRACE2TASK_DEBIAN_MIRROR=""') == 1

    client = checkout / "src" / "win-arena-container" / "client"
    assert (client / "mm_agents" / "trace2task" / "agent.py").is_file()
    assert (client / "evaluation_examples_windows" / "test_trace2task.json").is_file()
    assert (
        client
        / "evaluation_examples_windows"
        / "test_trace2task_count_token_d0.json"
    ).is_file()
    assert (
        client
        / "evaluation_examples_windows"
        / "test_trace2task_find_file_d0.json"
    ).is_file()
    assert (
        client
        / "evaluation_examples_windows"
        / "examples"
        / "notepad"
        / "351f1d5e-f3f7-4efe-8fda-e8a8e9eacf4c-WOS.json"
    ).is_file()
    assert (
        client
        / "evaluation_examples_windows"
        / "examples"
        / "file_explorer"
        / "c05b680d-bda5-48db-984b-c1024496d088-WOS.json"
    ).is_file()
    requirements_path = (
        checkout
        / "src"
        / "win-arena-container"
        / "vm"
        / "setup"
        / "server"
        / "requirements.txt"
    )
    requirements = requirements_path.read_text(encoding="utf-8")
    assert requirements.count("pyperclip==1.9.0") == 1
    server_root = requirements_path.parent
    assert (server_root / "trace2task_input.py").is_file()
    server_main = (server_root / "main.py").read_text(encoding="utf-8")
    assert server_main.count("register_trace2task_input_routes(app)") == 1
    assert (client / "trace2task_human_trace.py").is_file()
    assert (client / "trace2task_reset.py").is_file()
    single_runner = (client / "lib_run_single.py").read_text(encoding="utf-8")
    assert single_runner.count("from trace2task_reset import") == 1
    assert single_runner.count("apply_trace2task_reset(env, example)") == 1
    assert single_runner.count("verify_trace2task_reset(env, example)") == 1
    assert (requirements_path.parent.parent / "recover_trace2task.bat").is_file()
    on_logon = requirements_path.parent.parent / "on-logon.ps1"
    assert on_logon.is_file()
    on_logon_text = on_logon.read_text(encoding="utf-8")
    assert "caddy_windows_amd64.exe" in on_logon_text
    assert "Get-FileHash" in on_logon_text
    assert "pywin32_system32" in on_logon_text
    assert "vc_redist.x64.exe" in on_logon_text
    assert "windows_arena_server_log.txt" in on_logon_text
    assert "RedirectStandardError" in on_logon_text
    tools_config = requirements_path.parent.parent / "tools_config.json"
    tools_text = tools_config.read_text(encoding="utf-8")
    assert "24.8.2" not in tools_text
    assert tools_text.count("25.8.7") == 2
    setup_text = (requirements_path.parent.parent / "setup.ps1").read_text(
        encoding="utf-8"
    )
    assert setup_text.count("Using cached LibreOffice installer") == 1
    assert setup_text.count("Using cached GIMP installer") == 1
    assert setup_text.count("Using cached Thunderbird installer") == 1
    assert setup_text.count("Using cached Caddy executable") == 1
    assert setup_text.count("Python is already installed") == 1
