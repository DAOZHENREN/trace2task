"""Exercise the real wire envelopes across controller/client/web boundaries."""
import json
from types import SimpleNamespace

from trace2task import rsi_control, rsi_reconcile, rsi_remote
from trace2task.rsi_jobs import PracticeRunStore
from trace2task.web_console import WebConsoleController


def test_real_dispatch_envelopes_reach_web_without_double_wrapping(tmp_path, monkeypatch):
    config = {"root": str(tmp_path / "remote"), "models": ["gpt-6-astra"]}
    monkeypatch.setattr(rsi_control, "health", lambda _: {"ready": True})
    monkeypatch.setattr(rsi_control, "launch", lambda *_: "test-container-not-a-real-launch")

    def wire(_command, **kwargs):
        request = json.loads(kwargs["input"])
        result = rsi_control.dispatch(config, request)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "result": result}).encode(),
                               stderr=b"")

    monkeypatch.setattr(rsi_remote.subprocess, "run", wire)
    profile = rsi_remote.RSIRemoteProfile(ssh_host="fixture", remote_config_path="/fixture.json",
                                         control_launcher_argv=("fixture-control",))
    web = WebConsoleController(tmp_path / "local", rsi_client=rsi_remote.RSIRemoteClient(profile))
    created = web.start_rsi_practice({"instruction": "contract fixture", "model": "gpt-6-astra",
                                    "reasoning_effort": "low", "max_model_calls": 2,
                                    "wall_seconds": 60, "project_budget": 1})["run"]
    identity = created["id"]
    assert len(identity) == 32 and created["state"] == "queued"
    assert web.get_rsi_run(identity)["run"]["spec"]["instruction"] == "contract fixture"
    assert web.list_rsi_runs()["runs"][0]["id"] == identity
    assert web.rsi_events(identity)["events"]
    store = PracticeRunStore(tmp_path / "remote/jobs")
    # This is protocol testing, not proof of VM verification or cleanup.
    candidate = {"verification": {"verdict": "PASS", "artifact": "fixture/outcome.json"},
                 "memory": {"fixture.txt": {"text": "untrusted fixture"}}}
    digest = store.register_candidate(identity, candidate)
    assert web.get_rsi_candidate(identity, digest) == {"candidate": candidate}
    reviewed = web.review_rsi_candidate({"run_id": identity, "digest": digest,
                                        "decision": "accepted", "note": "protocol test only"})
    assert reviewed["run"]["review"]["decision"] == "accepted"
    monkeypatch.setattr(rsi_reconcile, "reconcile_stopped_orphan",
                        lambda ledger, run_id: {"run": ledger.get(run_id), "status": "pending"})
    stopped = web.stop_rsi_practice(identity)["run"]
    assert stopped["stop_requested"] is True
    assert stopped["state"] == "queued"  # Stop request is not cleanup completion.


def test_supervisor_nested_error_survives_safe_web_projection():
    from trace2task.web_console import _rsi_run_summary
    result = _rsi_run_summary({
        "id": "b" * 32, "state": "failed",
        "result": {"reason": "worker_exit", "cleanup_confirmed": True,
                   "worker_result": {"error_type": "LLMTransportError",
                                     "error": "codex_authentication_required",
                                     "private_path": "must not expose"}},
    })["result"]
    assert result["error"] == "codex_authentication_required"
    assert result["reason"] == "worker_exit"
    assert "private_path" not in result
