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
