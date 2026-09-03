from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import trace2task.waa_experiment as experiment_module
from trace2task.waa_experiment import (
    _client_run_command,
    _load_reset_spec,
    _require_completed_waa_run,
    _task_ids,
    _validate_automatic_compiler_snapshot,
    _validate_conditions,
)


def test_reset_spec_covers_every_selected_waa_task(tmp_path: Path) -> None:
    spec = tmp_path / "reset.json"
    spec.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "tasks": {
                    "task-1": {
                        "must_not_exist": [r"C:\Users\Docker\Documents\draft.txt"]
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    payload = _load_reset_spec(spec, expected_task_ids={"task-1"})

    assert payload["tasks"]["task-1"]["must_not_exist"]


def test_reset_spec_rejects_missing_task(tmp_path: Path) -> None:
    spec = tmp_path / "reset.json"
    spec.write_text('{"schema_version":"0.1","tasks":{}}', encoding="utf-8")

    with pytest.raises(ValueError, match="missing task ids"):
        _load_reset_spec(spec, expected_task_ids={"task-1"})


def test_task_ids_reject_path_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="inside the client directory"):
        _task_ids(tmp_path, "../tasks.json")


def test_conditions_require_reviewed_semantic_experience() -> None:
    draft = SimpleNamespace(
        task=SimpleNamespace(requires_confirmation=True),
        semantic_experience=None,
        human_guidance=None,
    )
    with pytest.raises(RuntimeError, match="confirmed task pack"):
        _validate_conditions(draft, ("baseline",))

    confirmed = SimpleNamespace(
        task=SimpleNamespace(requires_confirmation=False),
        semantic_experience=None,
        human_guidance=None,
    )
    with pytest.raises(RuntimeError, match="semantic experience"):
        _validate_conditions(confirmed, ("baseline", "compiled"))

    automatic = SimpleNamespace(
        task=SimpleNamespace(requires_confirmation=True),
        semantic_experience=SimpleNamespace(narration_available=False),
        human_guidance=None,
    )
    assert _validate_conditions(
        automatic,
        ("compiled",),
        allow_automatic_compiler_draft=True,
    ) == ("compiled",)
    with pytest.raises(RuntimeError, match="isolated compiled condition"):
        _validate_conditions(
            automatic,
            ("baseline", "compiled"),
            allow_automatic_compiler_draft=True,
        )


def test_automatic_compiler_draft_requires_matching_frozen_manifest(
    tmp_path: Path,
) -> None:
    task = tmp_path / "snapshot" / "taskpack" / "task.yaml"
    task.parent.mkdir(parents=True)
    task.write_text("id: auto\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="frozen Compiler snapshot"):
        _validate_automatic_compiler_snapshot(task.resolve())

    manifest = {
        "kind": "automatic_compiler_output",
        "task_path": str(task.resolve()),
        "tree_sha256": "abc",
    }
    (task.parent.parent / "snapshot.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    assert _validate_automatic_compiler_snapshot(task.resolve()) == manifest


def test_client_command_passes_exact_reset_and_result_scope() -> None:
    command = _client_run_command(
        distro="Trace2Task-WAA",
        container="winarena",
        bridge_url="http://172.18.0.1:8876",
        token="local-token",
        reset_spec_path="/client/trace2task_reset_spec.json",
        result_dir="/client/results/experiment/baseline-r01",
        json_name="evaluation_examples_windows/test_trace2task.json",
    )
    joined = " ".join(command)

    assert "TRACE2TASK_WAA_RESET_SPEC=/client/trace2task_reset_spec.json" in joined
    assert "TRACE2TASK_WAA_BRIDGE_URL=http://172.18.0.1:8876" in joined
    assert "/client/results/experiment/baseline-r01" in command
    assert command[command.index("--result_dir") + 1] == (
        "/client/results/experiment/baseline-r01"
    )
    assert "evaluation_examples_windows/test_trace2task.json" in command
    assert command[command.index("--observation_type") + 1] == "screenshot"
    assert "a11y_tree" not in command


def test_completed_waa_run_requires_an_evaluator_result(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    failed_task = run_root / "pyautogui" / "screenshot" / "unused" / "0" / "notepad" / "task-1"
    failed_task.mkdir(parents=True)
    (failed_task / "traj.jsonl").write_text(
        json.dumps({"Error": "Exception in notepad/task-1", "Exception": "VM probe timed out"}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="VM probe timed out"):
        _require_completed_waa_run(run_root, expected_task_ids={"task-1"})

    (failed_task / "result.txt").write_text("0.0\n", encoding="utf-8")
    _require_completed_waa_run(run_root, expected_task_ids={"task-1"})


def test_container_gateway_parser_returns_strict_ipv4() -> None:
    raw_routes = json.dumps(
        [{"dst": "default", "gateway": "172.18.0.1", "dev": "eth0"}]
    )

    assert experiment_module._parse_container_gateway(raw_routes) == "172.18.0.1"


@pytest.mark.parametrize(
    "raw_routes",
    (
        "default via 172.18.0.1 dev eth0\n",
        json.dumps([{"dst": "default", "gateway": "default via 172.18.0.1"}]),
        json.dumps([{"dst": "default", "gateway": "999.18.0.1"}]),
    ),
)
def test_container_gateway_parser_rejects_route_text_and_malformed_values(
    raw_routes: str,
) -> None:
    with pytest.raises(RuntimeError):
        experiment_module._parse_container_gateway(raw_routes)


def test_health_checks_do_not_pass_an_unescaped_glob_to_wsl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    results = iter(
        (
            SimpleNamespace(returncode=0, stdout="true\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="Windows\n", stderr=""),
            SimpleNamespace(
                returncode=0,
                stdout='[{"dst":"default","gateway":"172.18.0.1"}]',
                stderr="",
            ),
            SimpleNamespace(
                returncode=0,
                stdout='{"experience_mode":"baseline"}',
                stderr="",
            ),
        )
    )

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        return next(results)

    monkeypatch.setattr(experiment_module, "_run", fake_run)

    experiment_module._require_running_vm("Trace2Task-WAA", "winarena")
    experiment_module._check_bridge_health(
        "Trace2Task-WAA",
        "winarena",
        bridge_url="http://172.18.0.1:8876",
        condition="baseline",
    )

    curl_commands = [command for command in commands if "curl" in command]
    assert len(curl_commands) == 2
    assert all("*" not in command for command in curl_commands)
    noproxy_hosts = {
        command[command.index("--noproxy") + 1] for command in curl_commands
    }
    assert noproxy_hosts == {"20.20.20.21", "172.18.0.1"}


def test_experiment_runs_each_condition_and_repetition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waa_root = tmp_path / "waa"
    client = waa_root / "src" / "win-arena-container" / "client"
    task_list = client / "evaluation_examples_windows" / "test_trace2task.json"
    task_list.parent.mkdir(parents=True)
    task_list.write_text('{"notepad":["task-1"]}', encoding="utf-8")
    task = tmp_path / "compiled" / "task.yaml"
    task.parent.mkdir()
    task.write_text("placeholder", encoding="utf-8")
    narrated_task = tmp_path / "narrated" / "task.yaml"
    narrated_task.parent.mkdir()
    narrated_task.write_text("placeholder", encoding="utf-8")
    for task_root in (task.parent, narrated_task.parent):
        trace = task_root / "reference" / "trace.jsonl"
        trace.parent.mkdir()
        trace.write_bytes(b'{"seq":0,"type":"windows_input"}\n')
    reset_spec = tmp_path / "reset.json"
    reset_spec.write_text(
        '{"schema_version":"0.1","tasks":{"task-1":'
        '{"must_not_exist":["C:\\\\Users\\\\Docker\\\\Documents\\\\draft.txt"]}}}',
        encoding="utf-8",
    )
    contract = SimpleNamespace(
        task=SimpleNamespace(requires_confirmation=False, source_path=task),
        semantic_experience=SimpleNamespace(
            narration_available=False,
            narration_kind="none",
            source_path=task.parent / "experience.yaml",
        ),
        human_guidance=None,
    )
    narrated_contract = SimpleNamespace(
        task=SimpleNamespace(requires_confirmation=False, source_path=narrated_task),
        semantic_experience=SimpleNamespace(
            narration_available=True,
            narration_kind="human",
            source_path=narrated_task.parent / "experience.yaml",
        ),
        human_guidance=SimpleNamespace(revision=1),
    )

    def fake_load_windows_task(path: Path) -> SimpleNamespace:
        if path.resolve() == narrated_task.resolve():
            return narrated_contract
        return contract

    monkeypatch.setattr(experiment_module, "load_windows_task", fake_load_windows_task)
    monkeypatch.setattr(
        experiment_module,
        "_require_running_vm",
        lambda _distro, _container: "172.18.0.1",
    )
    monkeypatch.setattr(experiment_module, "_ensure_relay", lambda *args, **kwargs: None)
    monkeypatch.setattr(experiment_module, "_check_bridge_health", lambda *args, **kwargs: None)
    monkeypatch.setattr(experiment_module, "_stop_relay", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        experiment_module,
        "_require_completed_waa_run",
        lambda *args, **kwargs: None,
    )

    commands: list[list[str]] = []
    monkeypatch.setattr(
        experiment_module,
        "_run",
        lambda command, **_kwargs: commands.append(command),
    )

    class FakeServer:
        def __init__(self) -> None:
            self.bridge = SimpleNamespace(close=lambda: None)

        def serve_forever(self) -> None:
            return None

        def shutdown(self) -> None:
            return None

        def server_close(self) -> None:
            return None

    server_calls: list[tuple[Path, str]] = []

    def fake_create_server(task_path: Path, **kwargs: object) -> FakeServer:
        server_calls.append((task_path.resolve(), str(kwargs["experience_mode"])))
        return FakeServer()

    monkeypatch.setattr(experiment_module, "create_waa_bridge_server", fake_create_server)

    def fake_report(_results: Path, output: Path) -> dict[str, object]:
        output.mkdir(parents=True)
        path = output / "waa-ablation-report.json"
        path.write_text("{}", encoding="utf-8")
        return {"report_path": str(path), "episodes": 6}

    monkeypatch.setattr(experiment_module, "write_waa_report", fake_report)

    result = experiment_module.run_waa_experiment(
        waa_root,
        task,
        reset_spec=reset_spec,
        conditions=("baseline", "compiled", "narrated_compiled", "feedback"),
        narrated_task_path=narrated_task,
        repetitions=2,
        output_root=tmp_path / "reports",
    )

    assert result["status"] == "completed"
    assert len(commands) == 8
    assert sum("trace2task-baseline" in " ".join(command) for command in commands) == 2
    assert sum("trace2task-compiled" in " ".join(command) for command in commands) == 2
    assert sum(
        "trace2task-narrated_compiled" in " ".join(command) for command in commands
    ) == 2
    assert sum("trace2task-feedback" in " ".join(command) for command in commands) == 2
    assert server_calls == [
        (task.resolve(), "baseline"),
        (task.resolve(), "baseline"),
        (task.resolve(), "compiled"),
        (task.resolve(), "compiled"),
        (narrated_task.resolve(), "narrated_compiled"),
        (narrated_task.resolve(), "narrated_compiled"),
        (narrated_task.resolve(), "feedback"),
        (narrated_task.resolve(), "feedback"),
    ]
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["status"] == "completed"
    assert len(manifest["completed_episodes"]) == 8


@pytest.mark.parametrize(
    (
        "compiled_narration_kind",
        "narrated_narration_kind",
        "narrated_trace",
        "error_match",
    ),
    (
        ("human", "human", b"same-trace\n", "narration"),
        ("none", "none", b"same-trace\n", "narration"),
        ("none", "task_instruction", b"same-trace\n", "[Hh]uman"),
        ("none", "human", b"different-trace\n", "[Tt]race"),
    ),
    ids=(
        "compiled-is-clean",
        "narrated-has-narration",
        "task-instruction-is-not-human",
        "trace-bytes-match",
    ),
)
def test_narrated_compiled_requires_clean_matched_taskpacks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    compiled_narration_kind: str,
    narrated_narration_kind: str,
    narrated_trace: bytes,
    error_match: str,
) -> None:
    waa_root = tmp_path / "waa"
    task_list = (
        waa_root
        / "src"
        / "win-arena-container"
        / "client"
        / "evaluation_examples_windows"
        / "test_trace2task.json"
    )
    task_list.parent.mkdir(parents=True)
    task_list.write_text('{"notepad":["task-1"]}', encoding="utf-8")
    reset_spec = tmp_path / "reset.json"
    reset_spec.write_text(
        '{"schema_version":"0.1","tasks":{"task-1":'
        '{"must_not_exist":["C:\\\\draft.txt"]}}}',
        encoding="utf-8",
    )
    compiled_task = tmp_path / "compiled" / "task.yaml"
    narrated_task = tmp_path / "narrated" / "task.yaml"
    for task_path, trace_bytes in (
        (compiled_task, b"same-trace\n"),
        (narrated_task, narrated_trace),
    ):
        task_path.parent.mkdir()
        task_path.write_text("placeholder", encoding="utf-8")
        trace_path = task_path.parent / "reference" / "trace.jsonl"
        trace_path.parent.mkdir()
        trace_path.write_bytes(trace_bytes)

    contracts = {
        compiled_task.resolve(): SimpleNamespace(
            task=SimpleNamespace(
                requires_confirmation=False,
                source_path=compiled_task,
            ),
            semantic_experience=SimpleNamespace(
                narration_available=compiled_narration_kind != "none",
                narration_kind=compiled_narration_kind,
                source_path=compiled_task.parent / "experience.yaml",
            ),
            human_guidance=None,
        ),
        narrated_task.resolve(): SimpleNamespace(
            task=SimpleNamespace(
                requires_confirmation=False,
                source_path=narrated_task,
            ),
            semantic_experience=SimpleNamespace(
                narration_available=narrated_narration_kind != "none",
                narration_kind=narrated_narration_kind,
                source_path=narrated_task.parent / "experience.yaml",
            ),
            human_guidance=None,
        ),
    }
    monkeypatch.setattr(
        experiment_module,
        "load_windows_task",
        lambda path: contracts[path.resolve()],
    )
    monkeypatch.setattr(
        experiment_module,
        "_require_running_vm",
        lambda *_args: pytest.fail("task-pack validation must precede VM access"),
    )

    with pytest.raises(RuntimeError, match=error_match):
        experiment_module.run_waa_experiment(
            waa_root,
            compiled_task,
            reset_spec=reset_spec,
            conditions=("compiled", "narrated_compiled"),
            narrated_task_path=narrated_task,
            repetitions=1,
            output_root=tmp_path / "reports",
        )
