"""Workbench boundary for explicitly selected Android devices (no auto-connect)."""
import re
import subprocess
from pathlib import Path

from trace2task.android_execution import AndroidDriver


def validate_config(value):
    required = {'adb', 'serial', 'package', 'server_port'}
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - {'launch_app'}:
        raise ValueError('Android requires adb, serial, package and server_port')
    config = dict(value)
    adb = Path(config['adb']) if isinstance(config['adb'], str) else Path()
    if not adb.is_absolute() or adb.name.lower() not in {'adb', 'adb.exe'} or not adb.is_file():
        raise ValueError('ADB must be an existing absolute adb executable path')
    AndroidDriver('.', None, None, **config)  # Validate without starting any process.
    return config


def probe(value):
    from trace2task.windows_runner import Win32EmergencyStop
    config = validate_config(value)
    config['launch_app'] = False  # A read-only probe must never launch an application.
    stop = Win32EmergencyStop()
    driver = AndroidDriver('.', lambda *a, **kw: None, stop, **config)
    try:
        stop.start()
        driver.start()
        return {'target': driver.target, 'state': driver.state(), 'input_actions': 0, 'model_calls': 0}
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(f'ADB 检查失败或超时，请检查云手机连接：{error}') from error
    finally:
        driver.close()
        stop.close()


def prepare(value, *, connect=False):
    """Explicit connection or read-only preflight; never launches apps or invokes a model."""
    from trace2task.windows_runner import Win32EmergencyStop
    config = validate_config(value)
    stop = Win32EmergencyStop()
    driver = AndroidDriver('.', lambda *a, **kw: None, stop, **config)
    try:
        stop.start()
        if connect:
            endpoint = config['serial']
            if not re.fullmatch(r'[A-Za-z0-9_.-]+:[0-9]{1,5}', endpoint) or not 1 <= int(endpoint.rsplit(':', 1)[1]) <= 65535:
                raise ValueError('连接云手机需要明确的 主机:端口；USB 设备请直接检查')
            driver._run('connect', endpoint, device=False)
        driver.bind()
        component = driver.launcher()
        state = None if connect or config.get('launch_app') else driver.state()
        return {'target': driver.target, 'state': state, 'launcher': component,
                'message': '手机已连接、授权应用已安装；' + ('确认任务后自动打开应用' if config.get('launch_app')
                    else '请打开授权应用或勾选自动打开，再进行启动前检查' if connect else '前台应用检查通过'),
                'input_actions': 0, 'model_calls': 0}
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(f'ADB 连接/启动前检查失败或超时：{error}') from error
    finally:
        driver.close()
        stop.close()


def run_mobileforge(*, upstream, **kwargs):
    from trace2task.chat_agent_runner import run_chat_agent
    from trace2task.mobileforge_adapter import (
        MobileForgeAdapter,
        MobileForgeSession,
        MobileForgeSource,
    )
    from trace2task.mobileforge_codex import MobileForgeCodexSession
    from trace2task.mobileforge_pipeline import require_local_endpoint
    source = MobileForgeSource(upstream)
    config = kwargs.get('api_config')
    if config is not None:
        require_local_endpoint(config.base_url)
        session = MobileForgeSession(config, kwargs['model'])
    else:
        session = MobileForgeCodexSession(kwargs['output_root'], kwargs['model'])
    try:
        return run_chat_agent(**kwargs, session=session, model_adapter=MobileForgeAdapter(session, source))
    finally:
        session.close()
