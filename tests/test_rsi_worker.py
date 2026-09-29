import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from trace2task import rsi_worker
from trace2task.rsi_jobs import PracticeRunStore


@pytest.fixture
def worker(tmp_path, monkeypatch):
    (tmp_path / "osworld").mkdir()
    monkeypatch.chdir(tmp_path)
    rsi_worker._reset_runtime_guard_for_tests()
    store = PracticeRunStore(tmp_path / "jobs")
    run_id = store.create({"instruction": "Practice a document", "model": "test-model",
                           "reasoning_effort": "low", "max_model_calls": 3,
                           "wall_seconds": 30})["id"]

    @dataclass
    class Config:
        model: str = "official-role-model"
        agent_decided_stop: bool = True
        practice_mode: bool = True
        independent_verify: bool = False
        max_resumes: int = 0
        verifier_execution_mode: str = "rollback_mirror"
        verifier_hide_actor_memory: bool = True
        verifier_stage_lifecycle: bool = False

    @dataclass
    class Result:
        status: str = "budget"
        reason: str = "Closed project budget reached"

    context = SimpleNamespace(calls=0, closes=0, result=Result(), on_boot=None,
                              on_run=None, close_error=False, desktop_kwargs=None,
                              reset_args=None, sealed=0, wrapped=False, seal_error=False,
                              evolve_args=None, evolve_kwargs=None)

    class Isolation:
        def __init__(self, *_):
            pass
        def seal_after_reset(self, vm):
            if context.seal_error:
                raise RuntimeError("Offline boundary failed")
            context.sealed += 1
            return {"status": "sealed"}
        def wrap_reset_hook(self, hook):
            context.wrapped = True
            return hook
        def close(self):
            pass

    monkeypatch.setattr(rsi_worker, "Phase1OfflineIsolation", Isolation)

    def evolve(*args, **kwargs):
        assert context.sealed == 1 and context.wrapped
        assert kwargs["hooks"].reset_vm is not None
        context.calls += 1
        context.evolve_args = args
        context.evolve_kwargs = kwargs
        kwargs["event_sink"]("started", payload={})
        if context.on_run:
            context.on_run()
        return context.result

    def load(path):
        config = Config()
        if str(path).endswith("target.yaml"):
            config.independent_verify = True
        return config

    corpus = tmp_path / "upstream" / "results" / "explore" / "corpus_shingles.json"
    corpus.parent.mkdir(parents=True)
    corpus.write_text('{"schema_version": 1}', encoding="utf-8")
    roles = tmp_path / "upstream" / "config" / "roles"
    roles.mkdir(parents=True)
    for filename in set(rsi_worker.PHASE1_ROLE_FILES.values()):
        (roles / filename).write_text(f"# {filename}\n", encoding="utf-8")
    modules = {
        "llm.client": {}, "config.settings": {"load": load},
        "config.runtime_paths": {"normalize_verifier_config_paths": lambda *_: None},
        "explore.commit": {"validate_instruction_corpus": lambda _: None},
        "explore.practice_loop": {"evolve_practice": evolve, "PracticeHooks": lambda: SimpleNamespace(
            reset_vm=lambda *_: {"ok": True})},
        "benchmarks.osworld.provider": {"prepare_checkpointable_docker_provider": lambda _: None},
        "env.vm": {"VM": lambda desktop: desktop},
    }
    for name, attributes in modules.items():
        parts = name.split(".")
        for index in range(1, len(parts) + 1):
            key = ".".join(parts[:index])
            if key not in sys.modules:
                module = ModuleType(key)
                module.__path__ = []
                monkeypatch.setitem(sys.modules, key, module)
            if index > 1:
                monkeypatch.setattr(sys.modules[".".join(parts[:index - 1])],
                                    parts[index - 1], sys.modules[key], raising=False)
        for key, value in attributes.items():
            monkeypatch.setattr(sys.modules[name], key, value, raising=False)
    monkeypatch.setattr(rsi_worker, "check_checkout", lambda *_: None)
    monkeypatch.setattr(rsi_worker, "_git_value", lambda _, expression: f"locked-{expression}")
    monkeypatch.setattr(rsi_worker, "install_upstream_transport", lambda *_: None)
    monkeypatch.setattr(rsi_worker, "install_pinned_osworld_runtime", lambda: None)
    monkeypatch.setattr(rsi_worker, "verify_guest_runtime_image", lambda _: {
        "source": "happysixd/osworld-docker",
        "image_id": "sha256:fe8d9a5e5ad6c593d059887ea2c790481b3f32dd42fa961f2441cdbbe2c70cf4",
        "registry_manifest_digest": "sha256:0e6497a9295647cf05bf2b2af522fdd79bdeba2737595259cab310a3bcf6baa9",
        "container_id": "test-owned-container",
    })
    # Worker mutates process-global import paths by design; tests restore them.
    monkeypatch.setattr(sys, "path", list(sys.path))
    for name in ("RSIAGENT_ROOT", "OSWORLD_ROOT", "RSIAGENT_VM_OWNER",
                 "RSIAGENT_OSWORLD_CACHE_DIR", "RSIAGENT_BOOT_DIAGNOSTICS_DIR"):
        monkeypatch.setenv(name, "test")

    def close():
        context.closes += 1
        if context.close_error:
            raise RuntimeError("Owned VM cleanup failed")

    def boot():
        if context.on_boot:
            context.on_boot()
        return SimpleNamespace(close=close)

    class DesktopEnv:
        def __init__(self, **kwargs):
            context.desktop_kwargs = kwargs

        def reset(self, **kwargs):
            context.reset_args = kwargs

        def close(self):
            close()

    def run(vm_factory=boot):
        return rsi_worker.run_official_practice(
            store=store, run_id=run_id, upstream=tmp_path / "upstream",
            osworld=tmp_path / "osworld", session_factory=lambda *_: None,
            vm_factory=vm_factory)

    return store, run_id, context, run


def test_closed_budget_is_not_a_verified_candidate(worker):
    store, run_id, context, run = worker
    result = run()
    assert result["state"] == "completed"
    assert result["candidate_sha256"] is None
    assert context.closes == 1
    assert any(e["kind"] == "official_event" for e in store.events(run_id))


@pytest.mark.parametrize("when", ["queued", "boot", "run"])
def test_cancellation_waits_for_cleanup(worker, when):
    store, run_id, context, run = worker
    cancel = lambda: store.request_stop(run_id)
    if when == "queued":
        cancel()
    elif when == "boot":
        context.on_boot = cancel
    else:
        context.on_run = cancel
    assert run()["state"] == "cancelled"
    assert context.calls == (1 if when == "run" else 0)
    assert context.closes == (0 if when == "queued" else 1)


def test_cleanup_failure_remains_recoverable_not_cancelled(worker):
    store, run_id, context, run = worker
    context.on_boot = lambda: store.request_stop(run_id)
    context.close_error = True
    with pytest.raises(RuntimeError, match="cleanup"):
        run()
    result = store.get(run_id)
    assert result["state"] == "needs_recovery"
    assert result["result"]["error_type"] == "RuntimeError"
    assert any(e["kind"] == "cleanup_failed" for e in store.events(run_id))


def test_infrastructure_result_is_not_task_failure(worker):
    store, run_id, context, run = worker
    context.result.status = "infra"
    assert run()["state"] == "needs_recovery"
    assert store.get(run_id)["candidate_sha256"] is None


def test_no_model_work_after_failed_offline_isolation(worker):
    store, run_id, context, run = worker
    context.seal_error = True
    with pytest.raises(RuntimeError, match="Offline boundary"):
        run()
    assert context.calls == 0
    assert context.closes == 1
    assert store.get(run_id)["state"] == "needs_recovery"


def test_official_quarantine_cannot_be_resumed(worker):
    _, _, context, run = worker
    context.result.status = "quarantined"
    assert run()["state"] == "failed"


def test_candidate_requires_official_closed_pass_evidence(tmp_path):
    memory = tmp_path / "memory"
    memory.mkdir()
    (memory / "lesson.md").write_text("Check the saved result", encoding="utf-8")
    episode = tmp_path / "episodes" / "0001"
    episode.mkdir(parents=True)
    receipt = episode / "outcome.json"
    receipt.write_text(json.dumps({"terminal_outcome": "FAIL", "verifier_reports": ["FAIL"],
                                   "memory_after": {}}))
    assert rsi_worker.collect_candidate(tmp_path) is None
    expected = {"lesson.md": {"bytes": len("Check the saved result"),
                               "sha256": hashlib.sha256(
                                   b"Check the saved result").hexdigest()}}
    receipt.write_text(json.dumps({"terminal_outcome": "PASS", "verifier_reports": ["PASS: saved"],
                                   "memory_after": expected}))
    candidate = rsi_worker.collect_candidate(tmp_path)
    assert candidate["memory"]["lesson.md"]["text"] == "Check the saved result"
    assert candidate["verification"]["evidence"][0]["sha256"]
    assert "human review" in candidate["verification"]["scope"]


def test_candidate_refuses_pass_receipt_when_final_memory_has_later_fail(tmp_path):
    memory = tmp_path / "memory"
    memory.mkdir()
    (memory / "lesson.md").write_text("new fail-derived lesson", encoding="utf-8")
    episodes = tmp_path / "episodes"
    first = episodes / "0001"
    second = episodes / "0002"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "outcome.json").write_text(json.dumps({
        "terminal_outcome": "PASS", "verifier_reports": ["PASS"],
        "memory_after": {"lesson.md": {"bytes": 1, "sha256": "x"}},
    }))
    (second / "outcome.json").write_text(json.dumps({
        "terminal_outcome": "FAIL", "verifier_reports": ["FAIL"],
        "memory_after": {"lesson.md": {"bytes": 24, "sha256": "y"}},
    }))
    assert rsi_worker.collect_candidate(tmp_path) is None


def test_manifest_labels_distribution_practice_and_has_locked_hashes(worker):
    store, run_id, context, run = worker
    result = run()
    assert result["state"] == "completed"
    manifest = json.loads((store.root / run_id / "lineage" / "manifest.json").read_text())
    assert manifest["practice"]["mode"] == "distribution_guided_capability_exploration"
    assert manifest["practice"]["task_execution"] is False
    assert manifest["practice"]["target_query_conditioned"] is False
    assert manifest["practice"]["subscription_protocol"] == "codex_subscription_v2"
    profile = manifest["practice"]["context_transport_profile"]
    assert profile["trace_context_max_chars"] == 12_000
    assert profile["trace_head"] == profile["trace_tail"] == 0
    overlay = store.root / run_id / "lineage" / profile["agentic_verifier_overlay"]["relative_path"]
    assert overlay.is_file()
    assert profile["agentic_verifier_overlay"]["sha256"] == rsi_worker._sha256_file(overlay)
    for config in (*context.evolve_args[3:7], context.evolve_kwargs["agentic_verifier_cfg"]):
        assert config.trace_context_max_chars == 12_000
        assert config.trace_head == config.trace_tail == 0
    assert context.evolve_kwargs["agentic_verifier_cfg"].agentic_verifier_config == str(overlay.resolve())
    assert "memory_actor" in manifest["role_configs"]
    assert manifest["role_configs"]["memory_actor"]["sha256"]
    assert manifest["osworld_runtime"]["image_id"] == (
        "sha256:fe8d9a5e5ad6c593d059887ea2c790481b3f32dd42fa961f2441cdbbe2c70cf4"
    )
    runtime_events = [event for event in store.events(run_id) if event["kind"] == "osworld_runtime_image"]
    assert runtime_events[-1]["payload"]["container_id"] == "test-owned-container"


def test_bounded_verifier_overlay_changes_only_context_limit(tmp_path):
    upstream = tmp_path / "upstream"
    source = upstream / "config/roles/practice_verifier.yaml"
    source.parent.mkdir(parents=True)
    original = b"model: official\ntrace_context_max_chars: 0\nkeep_chars: 500000\n"
    source.write_bytes(original)
    lineage = tmp_path / "lineage"
    profile = rsi_worker._context_transport_profile(lineage=lineage, upstream=upstream, create=True)
    overlay = lineage / rsi_worker.AGENTIC_VERIFIER_OVERLAY_RELATIVE
    assert source.read_bytes() == original
    assert overlay.read_bytes() == b"model: official\ntrace_context_max_chars: 12000\nkeep_chars: 500000\n"
    assert profile["agentic_verifier_overlay"]["source_sha256"] == rsi_worker._sha256_file(source)


def test_recovery_refuses_missing_or_changed_run_local_context_profile(worker):
    store, run_id, _, run = worker
    assert run()["state"] == "completed"
    lineage = store.root / run_id / "lineage"
    overlay = lineage / rsi_worker.AGENTIC_VERIFIER_OVERLAY_RELATIVE
    overlay.write_text("changed", encoding="utf-8")
    result = rsi_worker.inspect_recovery(
        store=store, run_id=run_id, upstream=store.root.parent / "upstream",
        osworld=store.root.parent / "osworld",
    )
    assert result.eligible is False
    assert result.reason == "context_transport_profile_invalid"


def test_default_vm_factory_resets_official_screen_profile(worker, monkeypatch):
    _, run_id, _, run = worker
    desktop_module = ModuleType("desktop_env.desktop_env")
    captures = {}

    class CapturingDesktop:
        def __init__(self, **kwargs):
            captures["kwargs"] = kwargs

        def reset(self, **kwargs):
            captures["reset"] = kwargs

        def close(self):
            captures["closed"] = True

    desktop_module.DesktopEnv = CapturingDesktop
    package = ModuleType("desktop_env")
    package.__path__ = []
    package.desktop_env = desktop_module
    monkeypatch.setitem(sys.modules, "desktop_env", package)
    monkeypatch.setitem(sys.modules, "desktop_env.desktop_env", desktop_module)
    assert run(vm_factory=None)["state"] == "completed"
    assert captures["kwargs"]["screen_size"] == (1920, 1080)
    cache_dir = Path(captures["kwargs"]["cache_dir"])
    assert (cache_dir.parent.name, cache_dir.name) == (run_id, "vm-cache")
    assert captures["reset"] == {"task_config": None}
    assert captures["closed"] is True


def test_second_upstream_runtime_binding_in_same_process_is_rejected(worker):
    _, _, _, run = worker
    run()
    with pytest.raises(RuntimeError, match="fresh worker process"):
        rsi_worker._claim_fresh_runtime()


def _locked_task_checkout(tmp_path, monkeypatch, *, status_paths):
    checkout = tmp_path / "OSWorld-V2"
    task_root = checkout / rsi_worker.OSWORLD_TASK_SOURCE_ROOT
    task_root.mkdir(parents=True)
    for index, name in enumerate(rsi_worker.OSWORLD_TASK_SOURCE_NAMES, start=1):
        (task_root / name).write_bytes(f"locked task {index}\n".encode())
    identity = rsi_worker._osworld_task_source_identity(task_root)
    monkeypatch.setattr(rsi_worker, "OSWORLD_TASK_CONTENT_TREE_SHA256", identity["content_tree_sha256"])
    monkeypatch.setattr(rsi_worker, "OSWORLD_TASK_TOTAL_BYTES", identity["total_bytes"])
    monkeypatch.setattr(rsi_worker, "_git_value", lambda *_: rsi_worker.OSWORLD_REVISION)
    output = "".join(f"!! {path}\0" for path in status_paths).encode()
    monkeypatch.setattr(rsi_worker.subprocess, "run", lambda *_, **__: SimpleNamespace(stdout=output))
    return checkout, task_root


def test_checkout_allows_only_full_hash_verified_osworld_task_release(tmp_path, monkeypatch):
    checkout, _ = _locked_task_checkout(
        tmp_path, monkeypatch, status_paths=["evaluation_examples/task_class/task_001.py"]
    )
    rsi_worker.check_checkout(checkout, rsi_worker.OSWORLD_REVISION)


def test_checkout_rejects_tampered_osworld_task_even_when_ignored(tmp_path, monkeypatch):
    checkout, task_root = _locked_task_checkout(
        tmp_path, monkeypatch, status_paths=["evaluation_examples/task_class/task_001.py"]
    )
    original = rsi_worker._osworld_task_source_identity(task_root)
    monkeypatch.setattr(rsi_worker, "OSWORLD_TASK_CONTENT_TREE_SHA256", original["content_tree_sha256"])
    monkeypatch.setattr(rsi_worker, "OSWORLD_TASK_TOTAL_BYTES", original["total_bytes"])
    (task_root / "task_001.py").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="identity mismatch"):
        rsi_worker.check_checkout(checkout, rsi_worker.OSWORLD_REVISION)


def test_checkout_rejects_extra_or_external_ignored_python_sources(tmp_path, monkeypatch):
    checkout, task_root = _locked_task_checkout(
        tmp_path, monkeypatch, status_paths=["evaluation_examples/task_class/task_109.py"]
    )
    (task_root / "task_109.py").write_text("extra", encoding="utf-8")
    with pytest.raises(ValueError, match="Ignored executable/config source"):
        rsi_worker.check_checkout(checkout, rsi_worker.OSWORLD_REVISION)

    checkout, _ = _locked_task_checkout(tmp_path / "second", monkeypatch, status_paths=["outside/evil.py"])
    with pytest.raises(ValueError, match="Ignored executable/config source"):
        rsi_worker.check_checkout(checkout, rsi_worker.OSWORLD_REVISION)


def test_checkout_rejects_symlinked_locked_task_source(tmp_path, monkeypatch):
    checkout, task_root = _locked_task_checkout(
        tmp_path, monkeypatch, status_paths=["evaluation_examples/task_class/task_001.py"]
    )
    target = tmp_path / "outside.py"
    target.write_text("outside", encoding="utf-8")
    task = task_root / "task_001.py"
    task.unlink()
    try:
        task.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable on this test host")
    with pytest.raises(ValueError, match="symlink"):
        rsi_worker.check_checkout(checkout, rsi_worker.OSWORLD_REVISION)
