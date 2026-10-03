import json

import pytest

from trace2task.local_model_service import control_service, is_model_process, process_service


@pytest.mark.parametrize('name,command,expected', [
    ('python.exe', 'python D:/Apps/Trace2Task/_internal/scripts/local_gui/server.py', True),
    ('python.exe', 'python D:/MyProject/trace2task/scripts/trained_model/preview.py --serve', True),
    ('python.exe', 'python D:/other/scripts/local_gui/server.py', False),
    ('python.exe', 'python D:/MyProject/trace2task/scripts/train.py', False),
    ('llama-server.exe', '-m D:/Models/Qwen3-VL-8B-Instruct/Qwen3VL-8B-Instruct-Q4_K_M.gguf', True),
    ('llama-server.exe', '-m D:/other/model.gguf --port 8081', False),
    ('Trace2Task.exe', 'D:/Apps/Trace2Task/Trace2Task.exe', False),
    ('python.exe', None, False),
])
def test_only_known_model_processes(name, command, expected):
    assert is_model_process({'Name': name, 'CommandLine': command}) is expected


def test_invalid_action_cannot_kill_processes(tmp_path):
    with pytest.raises(ValueError):
        control_service('delete', 'trained_d', tmp_path)


def test_stop_empty(monkeypatch, tmp_path):
    monkeypatch.setattr('trace2task.local_model_service.managed_processes', list)
    assert '0' in control_service('stop', 'trained_d', tmp_path)['message']


def test_managed_processes_accepts_single_json_object(monkeypatch):
    from types import SimpleNamespace

    from trace2task import local_model_service as service

    process = {'ProcessId': 100, 'Name': 'python.exe',
               'CommandLine': 'python D:/MyProject/trace2task/scripts/trained_model/preview.py --serve'}
    monkeypatch.setattr(service.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(
        stdout=json.dumps(process)
    ))
    assert service.managed_processes() == [process]


def test_process_service_keeps_model_services_separate():
    gui = {'Name': 'python.exe', 'CommandLine': 'python D:/Apps/Trace2Task/_internal/scripts/local_gui/server.py'}
    trained = {'Name': 'python.exe', 'CommandLine': 'python D:/MyProject/trace2task/scripts/trained_model/preview.py --serve'}
    qwen = {'Name': 'llama-server.exe', 'CommandLine': '-m D:/Models/Qwen3-VL-8B-Instruct/Qwen3VL-8B-Instruct-Q4_K_M.gguf'}
    assert process_service(gui) == 'gui'
    assert process_service(trained) == 'trained_d'
    assert process_service(qwen) == 'qwen3-vl-8b-instruct'


def test_stop_stops_only_requested_service(monkeypatch, tmp_path):
    from trace2task import local_model_service as service

    gui = {'ProcessId': 100, 'Name': 'python.exe', 'CommandLine': 'python D:/Apps/Trace2Task/_internal/scripts/local_gui/server.py'}
    trained = {'ProcessId': 200, 'Name': 'python.exe', 'CommandLine': 'python D:/MyProject/trace2task/scripts/trained_model/preview.py --serve'}
    killed = []
    observations = iter(([gui, trained], [gui]))
    monkeypatch.setattr(service, 'managed_processes', lambda: next(observations, [gui]))
    monkeypatch.setattr(service.subprocess, 'run', lambda args, **kwargs: killed.append(args))
    control_service('stop', 'trained_d', tmp_path)
    assert killed == [['taskkill.exe', '/PID', '200', '/F']]


def test_missing_runtime_does_not_stop_existing_service(monkeypatch, tmp_path):
    from trace2task import local_model_service as service

    monkeypatch.setenv('TRACE2TASK_D_BUNDLE', str(tmp_path / 'missing-bundle'))
    monkeypatch.setattr(service, '_stop_processes', lambda processes: pytest.fail('must not stop first'))
    monkeypatch.setattr(service, 'managed_processes', lambda: pytest.fail('must not inspect first'))
    with pytest.raises(RuntimeError, match='运行环境不存在'):
        control_service('start', 'trained_d', tmp_path)


def test_8b_gui_worker_is_separate_from_legacy_8081_service(monkeypatch, tmp_path):
    from trace2task import local_model_service as service

    weights = 'D:/Models/Qwen3-VL-8B-Instruct/Qwen3VL-8B-Instruct-Q4_K_M.gguf'
    legacy = {'ProcessId': 8, 'Name': 'llama-server.exe', 'CommandLine': f'-m {weights} --alias qwen3-vl-8b-instruct --port 8081'}
    worker = {'ProcessId': 9, 'Name': 'llama-server.exe', 'CommandLine': f'-m {weights} --alias trace2task-qwen3-vl-8b-instruct --port 8769'}
    assert process_service(worker) == 'gui'
    assert process_service(legacy) == 'qwen3-vl-8b-instruct'
    summary = {'Name': 'llama-server.exe', 'CommandLine': '-m '
        'E:/managed/gguf/qwen3-vl-8b-instruct/Qwen3VL-8B-Instruct-Q4_K_M.gguf '
        '--alias trace2task-gui-summary-qwen8b --port 8769'}
    assert process_service(summary) == 'gui'
    observations = iter(([legacy, worker], [legacy]))
    killed = []
    monkeypatch.setattr(service, 'managed_processes', lambda: next(observations, [legacy]))
    monkeypatch.setattr(service.subprocess, 'run', lambda args, **kwargs: killed.append(args))
    control_service('stop', 'qwen3-vl-8b-instruct', tmp_path)
    assert killed == [['taskkill.exe', '/PID', '9', '/T', '/F']]


def test_8b_rejects_transformers_before_inspecting_or_stopping_processes(monkeypatch, tmp_path):
    from trace2task import local_model_service as service

    monkeypatch.setattr(service, 'managed_processes', lambda: pytest.fail('must validate first'))
    with pytest.raises(ValueError, match='不支持 transformers'):
        control_service('start', 'qwen3-vl-8b-instruct', tmp_path, backend='transformers')


def test_8b_start_uses_shared_load_endpoint_and_needs_no_hf_copy(monkeypatch, tmp_path):
    import io
    import socket
    from types import SimpleNamespace

    from trace2task import components, local_gui_llama
    from trace2task import local_model_service as service
    from trace2task.model_registry import profile_for

    profile = profile_for('qwen3-vl-8b-instruct')
    app = tmp_path / 'app'
    script = app / 'scripts/local_gui/server.py'
    script.parent.mkdir(parents=True)
    script.touch()
    python, executable = tmp_path / 'python.exe', tmp_path / 'llama-server.exe'
    python.touch()
    executable.touch()
    weights = tmp_path / 'weights'
    weights.mkdir()
    for name, _ in profile.prebuilt_gguf.files:
        (weights / name).touch()
    monkeypatch.setenv(profile.prebuilt_gguf.directory_env, str(weights))
    monkeypatch.setenv('TRACE2TASK_LLAMA_SERVER', str(executable))
    monkeypatch.setattr(service.sys, '_MEIPASS', str(app), raising=False)
    monkeypatch.setattr(components, 'gui_paths', lambda _: (python, tmp_path / 'no-hf-copy'))
    order, commands, requests = [], [], []
    monkeypatch.setattr(local_gui_llama, 'verify_weights', lambda paths, key: order.append(('verified', key)))
    monkeypatch.setattr(service, 'managed_processes', lambda: order.append(('processes', None)) or [])
    monkeypatch.setattr(service.subprocess, 'Popen', lambda args, **kwargs: commands.append(args) or SimpleNamespace(poll=lambda: None))

    class Probe:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def connect_ex(self, address):
            assert address == ('127.0.0.1', 8768)
            return 1

    def open_request(request, **kwargs):
        requests.append(request)
        payload = {'service': 'trace2task-local-gui'} if isinstance(request, str) else {'status': 'loaded'}
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(socket, 'socket', Probe)
    monkeypatch.setattr(service, 'build_opener', lambda *args: SimpleNamespace(open=open_request))
    result = control_service('start', profile.id, tmp_path / 'data', backend='llama-server')
    assert order[0] == ('verified', profile.id)
    assert commands[0][1] == str(script)
    assert '8081' not in ' '.join(commands[0])
    assert requests[-1].full_url == 'http://127.0.0.1:8768/load'
    assert json.loads(requests[-1].data) == {'model': profile.id}
    assert '8768' in result['message']
