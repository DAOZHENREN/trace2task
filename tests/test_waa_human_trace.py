from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


def _load_recorder(monkeypatch):
    desktop_env = ModuleType("desktop_env")
    envs = ModuleType("desktop_env.envs")
    desktop_env_module = ModuleType("desktop_env.envs.desktop_env")
    desktop_env_module.DesktopEnv = object
    reset_module = ModuleType("trace2task_reset")
    reset_module.apply_trace2task_reset = lambda env, example: None
    reset_module.verify_trace2task_reset = lambda env, example: None
    monkeypatch.setitem(sys.modules, "requests", ModuleType("requests"))
    monkeypatch.setitem(sys.modules, "desktop_env", desktop_env)
    monkeypatch.setitem(sys.modules, "desktop_env.envs", envs)
    monkeypatch.setitem(sys.modules, "desktop_env.envs.desktop_env", desktop_env_module)
    monkeypatch.setitem(sys.modules, "trace2task_reset", reset_module)
    return runpy.run_path(
        str(
            Path(__file__).parents[1]
            / "integrations"
            / "windows_agent_arena"
            / "client"
            / "trace2task_human_trace.py"
        )
    )


def test_waa_human_trace_normalizes_buffered_mouse_events(monkeypatch) -> None:
    module = _load_recorder(monkeypatch)
    event = {
        "seq": 7,
        "device": "mouse",
        "event": "down",
        "button": "left",
        "screen_position": [960, 600],
        "sampled_elapsed_ms": 250.0,
    }

    normalized = module["_normalize_raw_input"](event)

    assert "seq" not in normalized
    assert normalized["normalized_position"] == [0.5, 0.5]
    assert normalized["inside_target"] is True


def test_waa_human_trace_uses_python_39_compatible_utc(monkeypatch) -> None:
    module = _load_recorder(monkeypatch)

    assert module["UTC_TIMEZONE"] is module["timezone"].utc


def test_waa_human_trace_allows_a_full_human_demo_by_default(monkeypatch) -> None:
    module = _load_recorder(monkeypatch)

    assert module["DEFAULT_MAX_SECONDS"] == 30 * 60


def test_waa_human_trace_evaluator_reports_success(monkeypatch) -> None:
    module = _load_recorder(monkeypatch)
    env = type("Env", (), {"evaluate": lambda self: 1.0})()

    assert module["_evaluate_task"](env) == (1.0, None)


def test_waa_human_trace_evaluator_failure_can_be_corrected(monkeypatch) -> None:
    module = _load_recorder(monkeypatch)

    class BrokenEvaluator:
        def evaluate(self):
            raise RuntimeError("result file is missing")

    score, error = module["_evaluate_task"](BrokenEvaluator())

    assert score == 0.0
    assert error == "RuntimeError: result file is missing"


def test_waa_human_trace_waits_for_go_and_omits_synthetic_narration(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    module = _load_recorder(monkeypatch)
    example_path = tmp_path / "example.json"
    example_path.write_text(
        json.dumps(
            {
                "id": "notepad-example",
                "instruction": "Open Notepad and type hello.",
            }
        ),
        encoding="utf-8",
    )
    requests_module = module["requests"]
    request_log: list[str] = []
    reset_order: list[str] = []
    events_sent = False

    class Response:
        def __init__(self, payload=None) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self.payload

    def post(url: str, *, timeout: float):
        request_log.append(url)
        return Response()

    def get(url: str, *, params: dict[str, int], timeout: float):
        nonlocal events_sent
        request_log.append(url)
        if events_sent:
            return Response({"events": []})
        events_sent = True
        return Response(
            {
                "events": [
                    {"seq": 0, "device": "keyboard", "event": "down", "key": "f8"}
                ]
            }
        )

    requests_module.post = post
    requests_module.get = get

    class FakeDesktopEnv:
        def __init__(self, **kwargs) -> None:
            pass

        def reset(self, *, task_config):
            reset_order.append("env.reset")
            return {"screenshot": b"initial-png"}

        def evaluate(self) -> float:
            return 1.0

        def _get_screenshot(self) -> bytes:
            return b"success-png"

        def close(self) -> None:
            pass

    module["record"].__globals__["DesktopEnv"] = FakeDesktopEnv
    module["record"].__globals__["apply_trace2task_reset"] = (
        lambda env, example: (
            reset_order.append("apply"),
            {
                "status": "success",
                "action": "apply",
                "removed": [r"C:\Users\Docker\Documents\draft.txt"],
                "verified": [r"C:\Users\Docker\Documents\draft.txt"],
            },
        )[1]
    )
    module["record"].__globals__["verify_trace2task_reset"] = (
        lambda env, example: (
            reset_order.append("verify"),
            {
                "status": "success",
                "action": "verify",
                "removed": [],
                "verified": [r"C:\Users\Docker\Documents\draft.txt"],
            },
        )[1]
    )

    class GateInput:
        def readline(self) -> str:
            output = capsys.readouterr().out.strip().splitlines()
            assert output[-1].startswith(module["CONTROL_EVENT_PREFIX"])
            ready = json.loads(output[-1].removeprefix(module["CONTROL_EVENT_PREFIX"]))
            assert ready["type"] == "ready"
            assert ready["waa_task_id"] == "notepad-example"
            assert ready["reset_receipt"]["status"] == "verified"
            assert reset_order == ["apply", "env.reset", "verify"]
            assert request_log == []
            traces = list((tmp_path / "recordings").rglob("trace.jsonl"))
            assert not traces or all(not path.read_text(encoding="utf-8") for path in traces)
            return "GO\n"

    monkeypatch.setattr(sys, "stdin", GateInput())
    monkeypatch.setattr(
        module["record"].__globals__["select"],
        "select",
        lambda *args: ([], [], []),
    )
    result = module["record"](
        SimpleNamespace(
            example=str(example_path),
            task_id="waa-narrated",
            output=str(tmp_path / "recordings"),
            poll_hz=60,
            max_seconds=5,
            wait_for_go=True,
            no_task_narration=True,
        )
    )

    run_dir = Path(result["trace_path"]).parent
    assert result["success"] is True
    assert request_log[0].endswith("/trace2task/input/start")
    assert request_log[-1].endswith("/trace2task/input/stop")
    assert (run_dir / "trace.jsonl").is_file()
    receipt = json.loads((run_dir / "reset-receipt.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "verified"
    assert receipt["apply"]["removed"] == [
        r"C:\Users\Docker\Documents\draft.txt"
    ]
    assert not (run_dir / "narration.json").exists()
