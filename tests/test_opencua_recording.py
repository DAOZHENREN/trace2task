import json
import threading

import pytest

from trace2task.opencua_recording import record_opencua
from trace2task.web_console import WebConsoleController


@pytest.mark.parametrize("kind", ["wasapi_input_capture", "wasapi_output_capture", "unknown", None])
def test_obs_rejects_unexpected_sources_before_recording(kind):
    from scripts.opencua.worker import validate_capture_sources

    with pytest.raises(RuntimeError, match="refusing capture"):
        validate_capture_sources([{"inputKind": "monitor_capture"}, {"inputKind": kind}])


def test_obs_accepts_display_only_collection():
    from scripts.opencua.worker import validate_capture_sources

    validate_capture_sources([{"inputKind": "monitor_capture"}])


def test_native_archive_list_is_not_compilable(tmp_path):
    run = tmp_path / "runs" / "sample-opencua-human"
    run.mkdir(parents=True)
    (run / "events.jsonl").write_text("", encoding="utf-8")
    (run / "metadata.json").write_text(json.dumps({
        "source": "opencua_native", "task_id": "demo", "success": True,
        "execution_scope": "desktop", "input_event_count": 12,
    }), encoding="utf-8")
    records = WebConsoleController(tmp_path).list_recordings()
    assert len(records) == 1
    assert records[0]["recording_backend"] == "opencua"
    assert records[0]["compilation_supported"] is False
    assert records[0]["trace_path"].endswith("events.jsonl")


@pytest.mark.parametrize("scope,narrated", [("window", False), ("desktop", True)])
def test_native_rejects_unsupported_modes(tmp_path, scope, narrated):
    with pytest.raises(ValueError):
        WebConsoleController(tmp_path).start_recording(
            task_id="demo", handle=0, execution_scope=scope, narrated=narrated,
            recording_backend="opencua",
        )


def test_missing_components_fail_before_start(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACE2TASK_OPENCUA_PYTHON", str(tmp_path / "absent.exe"))
    with pytest.raises(RuntimeError, match="组件不完整"):
        record_opencua(task_id="demo", output_root=tmp_path,
                       stop_event=threading.Event(), status_callback=lambda _: None)


def test_official_metadata_is_separate_from_task_sidecar(tmp_path):
    run = tmp_path / "runs" / "099a8b83-87f2-44c8-bcb8-000000000000"
    run.mkdir(parents=True)
    original = '{"system":"Windows","screen_width":2560,"obs_record_state_timings":{}}'
    (run / "metadata.json").write_text(original, encoding="utf-8")
    (run / "events.jsonl").write_text("", encoding="utf-8")
    (run / "trace2task.json").write_text(json.dumps({
        "source": "opencua_native", "task_id": "official demo", "success": True,
        "execution_scope": "desktop", "input_event_count": 8,
        "derivation": {"status": "completed", "action_count": 3, "review_path": str(run / "derived/index.html")},
    }), encoding="utf-8")
    records = WebConsoleController(tmp_path).list_recordings()
    assert records[0]["task_id"] == "official demo"
    assert records[0]["input_events"] == 8
    assert records[0]["derivation"]["action_count"] == 3
    assert (run / "metadata.json").read_text(encoding="utf-8") == original
