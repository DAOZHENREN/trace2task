"""Trusted controller for the official grader-free practice lifecycle.

This runs in a dedicated worker process on the server, not in a GUI/model process.
The model-session factory and VM deployment must pass their separate readiness
checks before this entry is admitted. No benchmark results are fabricated here.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from trace2task.rsi_codex import UPSTREAM_REVISION, CodexRSITransport, install_upstream_transport
from trace2task.rsi_isolation import Phase1OfflineIsolation
from trace2task.rsi_jobs import PracticeRunStore
from trace2task.rsi_recovery import validate_completed_boundary
from trace2task.rsi_runtime_image import (
    install_pinned_osworld_runtime,
    runtime_manifest,
    verify_guest_runtime_image,
)

OSWORLD_REVISION = "d578d2d4e0dc82b43e270fdaa7fa89d9708cd154"
OSWORLD_TASK_SOURCE_ROOT = Path("evaluation_examples/task_class")
OSWORLD_TASK_SOURCE_NAMES = tuple(f"task_{index:03d}.py" for index in range(1, 109))
# These are the fixed contents admitted by upstream/config/osworld/baseline.lock.json.
# Keep this small identity checker local: importing the task package before it is
# verified could execute an ignored task module.
OSWORLD_TASK_CONTENT_TREE_SHA256 = "1b923d2b8ec94c15cf4056ff9242985dbe922614fd1f7a84fd20407b460ad18d"
OSWORLD_TASK_TOTAL_BYTES = 2_668_122
PHASE1_ROLE_FILES = {
    "actor": "actor.yaml",
    "verifier": "practice_verifier.yaml",
    "verifier_control": "target.yaml",
    "curriculum": "curriculum.yaml",
    # This is the official Phase-1 default, not an alias: its separately
    # loaded object remains independently recorded and passed downstream.
    "memory_actor": "actor.yaml",
}
CONTEXT_TRANSPORT_PROTOCOL = "codex_subscription_v2"
CONTEXT_TRANSPORT_PROFILE = "bounded_program_output_v1"
TRACE_CONTEXT_MAX_CHARS = 12_000
AGENTIC_VERIFIER_OVERLAY_RELATIVE = Path(
    "runtime-config/practice_verifier.trace_context_12000.yaml")
_TRACE_CONTEXT_SETTING = re.compile(rb"(?m)^trace_context_max_chars:[^\r\n]*")

# Upstream imports ``chat`` into several module globals.  Replacing
# ``llm.client.chat`` after a run has started cannot safely replace those bound
# functions.  A production supervisor therefore starts a fresh Python process
# for every run; this guard makes an accidental in-process second run fail
# before it can mix a previous run's transport, budget, or audit directory.
_RUNTIME_GUARD = threading.Lock()
_UPSTREAM_RUNTIME_BOUND = False


def _claim_fresh_runtime() -> None:
    global _UPSTREAM_RUNTIME_BOUND
    with _RUNTIME_GUARD:
        if _UPSTREAM_RUNTIME_BOUND:
            raise RuntimeError(
                "RSIAgent upstream runtime is already bound in this process; "
                "start a fresh worker process for each practice run"
            )
        _UPSTREAM_RUNTIME_BOUND = True


def _reset_runtime_guard_for_tests() -> None:
    """Test-only seam; production must use a new process rather than this."""
    global _UPSTREAM_RUNTIME_BOUND
    with _RUNTIME_GUARD:
        _UPSTREAM_RUNTIME_BOUND = False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_value(path: Path, expression: str) -> str:
    # The dedicated controller container deliberately runs under a different
    # UID from the server checkout owner.  Trust only this explicit, read-only
    # checkout for this one Git invocation; never persist a global safe-directory
    # exemption in the image or on the server.
    checkout = path.resolve()
    return subprocess.run(
        ["git", "-c", f"safe.directory={checkout}", "-C", str(checkout), "rev-parse", expression], check=True,
        capture_output=True, text=True, timeout=20,
    ).stdout.strip()


def _osworld_task_source_identity(task_root: Path) -> dict[str, int | str]:
    """Hash the exact release task-source set without importing any task code."""
    if task_root.is_symlink() or not task_root.is_dir():
        raise ValueError("OSWorld task source root is not a real directory")
    rows: list[str] = []
    total_bytes = 0
    for name in OSWORLD_TASK_SOURCE_NAMES:
        task = task_root / name
        if task.is_symlink() or not task.is_file():
            raise ValueError(f"OSWorld locked task source is not a real file: {name}")
        size = task.stat().st_size
        total_bytes += size
        rows.append(f"{name}\t{size}\t{_sha256_file(task)}")
    return {
        "content_tree_sha256": hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest(),
        "total_bytes": total_bytes,
        "task_count": len(rows),
    }


def _verify_osworld_locked_task_sources(path: Path) -> None:
    identity = _osworld_task_source_identity(path / OSWORLD_TASK_SOURCE_ROOT)
    if identity != {
        "content_tree_sha256": OSWORLD_TASK_CONTENT_TREE_SHA256,
        "total_bytes": OSWORLD_TASK_TOTAL_BYTES,
        "task_count": len(OSWORLD_TASK_SOURCE_NAMES),
    }:
        raise ValueError("OSWorld locked task source identity mismatch")


def check_checkout(path: Path, revision: str) -> None:
    actual = _git_value(path, "HEAD")
    if actual != revision:
        raise ValueError(f"Upstream revision mismatch: {path}")
    # A clean diff alone is insufficient: imports can resolve an untracked
    # Python module or a substituted role configuration.  Normal untracked
    # files are rejected outright.  Git-ignored generated resources remain
    # permitted, except executable/configuration source under an importable
    # source tree.
    checkout = path.resolve()
    status = subprocess.run(
        ["git", "-c", f"safe.directory={checkout}", "-C", str(checkout), "status", "--porcelain=v1", "-z",
         "--untracked-files=all", "--ignored"],
        check=True, capture_output=True, timeout=20,
    ).stdout.decode("utf-8", errors="strict")
    verified_locked_osworld_tasks = False
    allowed_task_paths = {
        (OSWORLD_TASK_SOURCE_ROOT / name).as_posix() for name in OSWORLD_TASK_SOURCE_NAMES
    }
    for entry in (item for item in status.split("\0") if item):
        code, relative = entry[:2], entry[3:]
        if code != "!!":
            raise ValueError(f"Upstream checkout is not immutable: {relative}")
        normalized = relative.replace("\\", "/").rstrip("/")
        locked_task_source = (
            revision == OSWORLD_REVISION
            and (normalized == OSWORLD_TASK_SOURCE_ROOT.as_posix() or normalized in allowed_task_paths)
        )
        if locked_task_source:
            if not verified_locked_osworld_tasks:
                _verify_osworld_locked_task_sources(path)
                verified_locked_osworld_tasks = True
            # Git may report the ignored task directory as one entry, or report
            # individual task files.  In both cases the complete fixed set above
            # is verified before this representation is admitted.
            continue
        suffix = Path(normalized).suffix.lower()
        importable_config = normalized.startswith("config/") and suffix in {
            ".json", ".toml", ".yaml", ".yml",
        }
        if suffix in {".py", ".pyi"} or importable_config:
            raise ValueError(f"Ignored executable/config source is not allowed: {relative}")


def _memory_manifest(memory: dict[str, bytes]) -> dict[str, dict[str, int | str]]:
    return {
        name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in sorted(memory.items())
    }


def _read_memory_for_candidate(memory_root: Path, lineage: Path) -> dict[str, bytes]:
    if memory_root.is_symlink() or not memory_root.is_dir():
        raise ValueError("Candidate durable memory root is not a real directory")
    memory: dict[str, bytes] = {}
    for path in sorted(memory_root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Candidate memory contains a symlink")
        if path.is_file():
            if not path.resolve().is_relative_to(lineage):
                raise ValueError("Unsafe candidate memory path")
            memory[str(path.relative_to(memory_root))] = path.read_bytes()
    return memory


def _safe_lineage_child(lineage: Path, relative: Path) -> Path:
    """Reject an overlay path that could escape the immutable run lineage."""
    root = lineage.resolve(strict=True)
    if root.is_symlink() or relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Unsafe run-local configuration path")
    child = root.joinpath(relative)
    try:
        child.resolve(strict=False).relative_to(root)
    except ValueError as error:
        raise ValueError("Run-local configuration escapes its lineage") from error
    current = root
    for part in relative.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValueError("Run-local configuration path contains a symlink")
    return child


def _bounded_verifier_overlay(source: bytes) -> bytes:
    """Change only the official verifier's live Program-observation bound."""
    matches = _TRACE_CONTEXT_SETTING.findall(source)
    if len(matches) > 1:
        raise ValueError("Official verifier config has duplicate trace context settings")
    if matches:
        return _TRACE_CONTEXT_SETTING.sub(
            f"trace_context_max_chars: {TRACE_CONTEXT_MAX_CHARS}".encode("ascii"), source, count=1)
    suffix = b"" if source.endswith((b"\n", b"\r")) else b"\n"
    return source + suffix + (
        b"# Trace2Task bounded live Program-observation repair profile.\n"
        + f"trace_context_max_chars: {TRACE_CONTEXT_MAX_CHARS}\n".encode("ascii")
    )


def _context_transport_profile(*, lineage: Path, upstream: Path, create: bool) -> dict:
    """Create or read-only validate the one permitted run-local verifier overlay."""
    source = upstream / "config/roles" / PHASE1_ROLE_FILES["verifier"]
    if source.is_symlink() or not source.is_file():
        raise ValueError("Frozen official verifier configuration is missing")
    source_bytes = source.read_bytes()
    overlay_bytes = _bounded_verifier_overlay(source_bytes)
    if create:
        lineage.mkdir(parents=True, exist_ok=False)
    overlay = _safe_lineage_child(lineage, AGENTIC_VERIFIER_OVERLAY_RELATIVE)
    if create:
        overlay.parent.mkdir(parents=True, exist_ok=True)
        with overlay.open("xb") as handle:
            handle.write(overlay_bytes)
            handle.flush()
            os.fsync(handle.fileno())
    elif overlay.is_symlink() or not overlay.is_file() or overlay.read_bytes() != overlay_bytes:
        # Do not synthesize a profile during resume: an old or altered lineage
        # is not the same protocol and must start a new run instead.
        raise ValueError("Run-local bounded verifier profile is missing or altered")
    return {
        "name": CONTEXT_TRANSPORT_PROFILE,
        "trace_head": 0,
        "trace_tail": 0,
        "trace_context_max_chars": TRACE_CONTEXT_MAX_CHARS,
        "execution_roles": sorted(PHASE1_ROLE_FILES),
        "agentic_verifier_overlay": {
            "relative_path": AGENTIC_VERIFIER_OVERLAY_RELATIVE.as_posix(),
            "sha256": _sha256_file(overlay),
            "source_relative_path": (Path("config/roles") / PHASE1_ROLE_FILES["verifier"]).as_posix(),
            "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        },
    }


def _apply_context_transport_profile(configs: dict, *, load, normalize, upstream: Path,
                                     lineage: Path, profile: dict) -> dict:
    """Apply the narrow live-context bound without changing official source files."""
    if (profile.get("name") != CONTEXT_TRANSPORT_PROFILE
            or profile.get("trace_head") != 0 or profile.get("trace_tail") != 0
            or profile.get("trace_context_max_chars") != TRACE_CONTEXT_MAX_CHARS
            or profile.get("execution_roles") != sorted(PHASE1_ROLE_FILES)):
        raise ValueError("Unexpected context transport profile")
    overlay = _safe_lineage_child(lineage, AGENTIC_VERIFIER_OVERLAY_RELATIVE)
    overlay_data = profile.get("agentic_verifier_overlay")
    if not isinstance(overlay_data, dict) or overlay_data.get("sha256") != _sha256_file(overlay):
        raise ValueError("Verifier overlay hash does not match context profile")
    # The agentic verifier independently reloads the path named by its control
    # config.  Point it at the run-local copy, not merely at the in-memory
    # ``configs['verifier']`` object that this path would otherwise bypass.
    configs["verifier"] = load(str(overlay))
    normalize(configs["verifier"], upstream)
    for cfg in configs.values():
        cfg.trace_head = 0
        cfg.trace_tail = 0
        cfg.trace_context_max_chars = TRACE_CONTEXT_MAX_CHARS
    configs["verifier_control"].agentic_verifier_config = str(overlay.resolve())
    return configs


def _build_manifest(*, upstream: Path, osworld: Path, corpus: Path,
                    configs: dict, spec: dict, context_profile: dict) -> dict:
    """Write an immutable run receipt before a guest is created.

    This is intentionally a Trace2Task receipt rather than an assertion that the
    subscription-adapted protocol is an official benchmark run.
    """
    return {
        "schema_version": 3,
        "kind": "trace2task_subscription_adapted_rsi_phase1",
        "official": {
            "rsiagent_revision": UPSTREAM_REVISION,
            "rsiagent_tree": _git_value(upstream, "HEAD^{tree}"),
            "osworld_revision": OSWORLD_REVISION,
            "osworld_tree": _git_value(osworld, "HEAD^{tree}"),
        },
        "corpus": {"path": str(corpus), "sha256": _sha256_file(corpus)},
        "role_configs": {
            role: {
                "path": str(upstream / "config/roles" / PHASE1_ROLE_FILES[role]),
                "sha256": _sha256_file(
                    upstream / "config/roles" / PHASE1_ROLE_FILES[role]),
                "effective": dataclasses.asdict(cfg),
            }
            for role, cfg in configs.items()
        },
        "practice": {
            "subscription_protocol": CONTEXT_TRANSPORT_PROTOCOL,
            "mode": "distribution_guided_capability_exploration",
            "task_execution": False,
            "target_query_conditioned": False,
            "phase1_exploration": True,
            "phase1_target_conditioned": False,
            "checkpoint_projects": sorted({0, spec.get("project_budget", 1)}),
            "isolation_policy": "official_target_isolation_null_v1",
            "instruction_sha256": hashlib.sha256(
                spec["instruction"].encode("utf-8")).hexdigest(),
            "project_budget": spec.get("project_budget", 1),
            "max_model_calls": spec["max_model_calls"],
            "wall_seconds": spec["wall_seconds"],
            "context_transport_profile": context_profile,
        },
        # This is a launch requirement, not a mutable Docker tag lookup.
        # The returned guest is independently verified before any role runs.
        "osworld_runtime": runtime_manifest(),
    }


def _write_manifest(*, lineage: Path, upstream: Path, osworld: Path, corpus: Path,
                    configs: dict, spec: dict, context_profile: dict) -> dict:
    manifest = _build_manifest(upstream=upstream, osworld=osworld, corpus=corpus,
                               configs=configs, spec=spec, context_profile=context_profile)
    if lineage.is_symlink() or not lineage.is_dir():
        raise ValueError("Run lineage must be created before writing its manifest")
    path = lineage / "manifest.json"
    with path.open("x", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return manifest


def _load_phase1_configs(*, upstream: Path, load, normalize, lineage: Path,
                         context_profile: dict) -> dict:
    """Mirror the official Phase-1 role loading and admission invariants.

    The role model names remain in the pinned files for provenance; the
    subscription transport records and applies the explicit Trace2Task model.
    Keeping the official configuration shapes and admission checks here avoids
    silently treating the Actor as a substitute for the memory-writing seat.
    """
    configs = {
        role: load(str(upstream / "config/roles" / filename))
        for role, filename in PHASE1_ROLE_FILES.items()
    }
    for cfg in configs.values():
        normalize(cfg, upstream)
    for role in ("actor", "verifier", "curriculum", "memory_actor"):
        cfg = configs[role]
        if (not cfg.agent_decided_stop or not cfg.practice_mode
                or cfg.independent_verify or cfg.max_resumes != 0):
            raise ValueError(
                f"Phase-1 {role} must use Agent-owned practice mode")
    if configs["memory_actor"].model != configs["actor"].model:
        raise ValueError("Memory writing must use the same Actor model identity")
    control = configs["verifier_control"]
    if (not control.independent_verify
            or control.verifier_stage_lifecycle
            or control.verifier_execution_mode != "rollback_mirror"
            or not control.verifier_hide_actor_memory):
        raise ValueError("Independent verifier isolation profile is required")
    return _apply_context_transport_profile(
        configs, load=load, normalize=normalize, upstream=upstream,
        lineage=lineage, profile=context_profile)


def collect_candidate(lineage: Path) -> dict | None:
    """Use closed official episode receipts, never Actor's own success claim.

    This binds the imported candidate to the official artifacts; it does not
    regrade them or reinterpret a FAIL as PASS. Failures may still have useful
    archived memory, but this first review path admits only PASS-backed memory.
    """
    lineage = lineage.resolve()
    outcomes = []
    for path in sorted((lineage / "episodes").glob("*/outcome.json")):
        if path.is_symlink() or not path.resolve().is_relative_to(lineage):
            raise ValueError("Unsafe episode evidence path")
        data = path.read_bytes()
        outcome = json.loads(data)
        outcomes.append((path, data, outcome))
    if not outcomes:
        return None
    # The final canonical memory can only be reviewed as a PASS candidate when
    # it is exactly the immutable memory_after receipt of the *latest* closed
    # official episode.  Earlier PASS receipts do not prove later FAIL-derived
    # edits, so they must not be used to bless the final tree.
    path, data, outcome = outcomes[-1]
    if (outcome.get("terminal_outcome") != "PASS"
            or not isinstance(outcome.get("verifier_reports"), list)
            or not outcome["verifier_reports"]
            or not isinstance(outcome.get("memory_after"), dict)):
        return None
    memory_root = lineage / "memory"
    raw_memory = _read_memory_for_candidate(memory_root, lineage)
    if _memory_manifest(raw_memory) != outcome["memory_after"]:
        return None
    memory = {
        name: {"text": value.decode("utf-8"),
               "sha256": hashlib.sha256(value).hexdigest()}
        for name, value in raw_memory.items()
    }
    if not memory:
        return None
    return {"source": "official_rsi_practice", "memory": memory,
            "verification": {
                "verdict": "PASS", "artifact": str(path.relative_to(lineage)),
                "evidence": [{"artifact": str(path.relative_to(lineage)),
                              "sha256": hashlib.sha256(data).hexdigest(),
                              "reports": outcome["verifier_reports"],
                              "memory_after": outcome["memory_after"]}],
                "scope": ("Latest closed PASS episode and current canonical memory "
                          "manifest match exactly; memory rules still require human review"),
            }}


def inspect_recovery(*, store: PracticeRunStore, run_id: str, upstream: Path, osworld: Path,
                     corpus_path: Path | None = None):
    """Read-only validation in a fresh control process; never boots a VM."""
    upstream, osworld = upstream.resolve(), osworld.resolve()
    check_checkout(upstream, UPSTREAM_REVISION)
    check_checkout(osworld, OSWORLD_REVISION)
    for path in (str(osworld), str(upstream)):
        sys.path.insert(0, path)
    from config.runtime_paths import normalize_verifier_config_paths
    from config.settings import load
    spec = store.get(run_id)["spec"]
    lineage = store.root / run_id / "lineage"
    try:
        context_profile = _context_transport_profile(
            lineage=lineage, upstream=upstream, create=False)
    except (OSError, ValueError):
        # A legacy or altered lineage cannot be upgraded in place merely to
        # make a recovery possible.  The caller gets a normal, fail-closed
        # recovery result without booting a guest.
        from trace2task.rsi_recovery import RecoveryEligibility
        return RecoveryEligibility(False, "context_transport_profile_invalid")
    configs = _load_phase1_configs(upstream=upstream, load=load,
                                   normalize=normalize_verifier_config_paths,
                                   lineage=lineage, context_profile=context_profile)
    expected = _build_manifest(upstream=upstream, osworld=osworld,
                               corpus=corpus_path or upstream / "results/explore/corpus_shingles.json",
                               configs=configs, spec=spec, context_profile=context_profile)
    return validate_completed_boundary(
        lineage=lineage, expected_manifest=expected,
        target_direction=spec["instruction"], project_budget=spec.get("project_budget", 1),
        checkpoint_projects=tuple(expected["practice"]["checkpoint_projects"]))


def run_official_practice(*, store: PracticeRunStore, run_id: str, upstream: Path,
                         osworld: Path, session_factory, vm_factory=None,
                         supervised: bool = False, attempt_no: int | None = None,
                         corpus_path: Path | None = None) -> dict:
    """Execute official roles; all guest ownership and rollback remain upstream.

    vm_factory is a test seam; production uses DesktopEnv plus the official
    checkpointable Docker provider. It must return a disposable owned desktop.
    """
    run = store.get(run_id)
    spec = run["spec"]
    def finish(expected, state, detail=None):
        if supervised:
            return store.transition(run_id, expected, "finalizing", detail={
                "intended_state": state, "worker_result": detail or {}})
        return store.transition(run_id, expected, state, detail=detail)
    if run["state"] == "queued" and run["stop_requested"]:
        return finish("queued", "cancelled")
    owns_attempt = attempt_no is None
    if owns_attempt:
        attempt_no = store.claim_fresh_attempt(run_id)["attempt_no"]
    attempt = next(a for a in store.attempts(run_id) if a["attempt_no"] == attempt_no)
    if attempt["state"] != "active":
        raise RuntimeError("Practice attempt is not active")
    resumed = attempt["mode"] == "resume"
    store.transition(run_id, "needs_recovery" if resumed else "queued", "preflight")
    attempt_started = time.monotonic()
    desktop = None
    isolation = None
    stop = threading.Event()
    done = threading.Event()
    watcher = None
    lineage = store.root / run_id / "lineage"
    try:
        upstream, osworld = upstream.resolve(), osworld.resolve()
        check_checkout(upstream, UPSTREAM_REVISION)
        check_checkout(osworld, OSWORLD_REVISION)
        os.environ["RSIAGENT_ROOT"] = str(upstream)
        os.environ["OSWORLD_ROOT"] = str(osworld)
        os.environ["RSIAGENT_VM_OWNER"] = f"trace2task_{run_id}"
        os.environ["RSIAGENT_OSWORLD_CACHE_DIR"] = str(store.root / run_id / "vm-cache")
        os.environ["RSIAGENT_BOOT_DIAGNOSTICS_DIR"] = str(store.root / run_id / "boot-diagnostics")
        for path in (str(osworld), str(upstream)):
            sys.path.insert(0, path)
        # Must precede any core/explore import that binds llm.client.chat.
        _claim_fresh_runtime()
        from llm import client
        budget = store.budget_status(run_id)
        adapter = CodexRSITransport(
            session_factory=session_factory, model=spec["model"],
            reasoning_effort=spec["reasoning_effort"],
            audit_dir=store.root / run_id / "model-calls", stop_event=stop,
            max_calls=budget["model_calls_remaining"], wall_seconds=budget["active_remaining_ms"] / 1000,
            reserve_call=lambda: store.reserve_model_call(run_id, attempt_no),
        )
        install_upstream_transport(client, adapter)
        from benchmarks.osworld.provider import prepare_checkpointable_docker_provider
        from config.runtime_paths import normalize_verifier_config_paths
        from config.settings import load
        from env.vm import VM
        from explore.commit import validate_instruction_corpus
        from explore.practice_loop import PracticeHooks, evolve_practice

        corpus = corpus_path or upstream / "results/explore/corpus_shingles.json"
        validate_instruction_corpus(str(corpus))
        if resumed:
            context_profile = _context_transport_profile(
                lineage=lineage, upstream=upstream, create=False)
        else:
            context_profile = _context_transport_profile(
                lineage=lineage, upstream=upstream, create=True)
        configs = _load_phase1_configs(
            upstream=upstream, load=load, normalize=normalize_verifier_config_paths,
            lineage=lineage, context_profile=context_profile)
        control = configs["verifier_control"]
        if resumed:
            expected = _build_manifest(upstream=upstream, osworld=osworld, corpus=corpus,
                                       configs=configs, spec=spec, context_profile=context_profile)
            recovery = validate_completed_boundary(
                lineage=lineage, expected_manifest=expected, target_direction=spec["instruction"],
                project_budget=spec.get("project_budget", 1),
                checkpoint_projects=tuple(expected["practice"]["checkpoint_projects"]))
            if not recovery.eligible or recovery.lineage_manifest_sha256 != attempt["lineage_manifest_sha256"]:
                raise RuntimeError("Recovery boundary changed; no VM or model started")
        else:
            _write_manifest(lineage=lineage, upstream=upstream, osworld=osworld,
                            corpus=corpus, configs=configs, spec=spec,
                            context_profile=context_profile)
        store.append_event(run_id, "effective_protocol", {
            "upstream": UPSTREAM_REVISION, "model": spec["model"],
            "role_configs": {role: dataclasses.asdict(cfg) for role, cfg in configs.items()},
            "mode": "distribution_guided_capability_exploration",
            "task_execution": False,
            "target_query_conditioned": False,
        })
        if store.get(run_id)["stop_requested"]:
            stop.set()
            return finish("preflight", "cancelled")
        prepare_checkpointable_docker_provider("rollback_mirror")
        # The frozen provider launches a mutable source tag.  Compose a
        # harness-side wrapper after its official checkpoint/loopback wrapper
        # so every future provider instance receives only the immutable ID.
        install_pinned_osworld_runtime()
        if vm_factory is None:
            os.chdir(osworld)
            from desktop_env.desktop_env import DesktopEnv
            def vm_factory():
                candidate = DesktopEnv(
                    provider_name="docker", action_space="pyautogui", os_type="Ubuntu",
                    screen_size=(1920, 1080), headless=True,
                    require_a11y_tree=False, volume_size=60,
                    # ``cache_dir`` is used by OSWorld task setup/evaluation
                    # artifacts, not by the read-only qcow bind.  Never leave
                    # it at upstream's cwd-relative ``cache`` default, which
                    # would leak controller artifacts across practice runs.
                    cache_dir=str(store.root / run_id / "vm-cache"),
                )
                try:
                    candidate.reset(task_config=None)
                    return candidate
                except Exception:
                    candidate.close()
                    raise
        desktop = vm_factory()
        if store.get(run_id)["stop_requested"]:
            desktop.close()
            desktop = None
            return finish("preflight", "cancelled")
        vm = VM(desktop)
        runtime_image = verify_guest_runtime_image(vm)
        store.append_event(run_id, "osworld_runtime_image", runtime_image)
        isolation = Phase1OfflineIsolation(store.attempt_log_dir(run_id, attempt_no))
        receipt = isolation.seal_after_reset(vm)
        store.append_event(run_id, "offline_isolation", receipt)
        hooks = PracticeHooks()
        hooks.reset_vm = isolation.wrap_reset_hook(hooks.reset_vm)
        store.transition(run_id, "preflight", "running")

        def watch_stop():
            while not done.wait(0.2):
                if store.get(run_id)["stop_requested"]:
                    stop.set()
                    return

        watcher = threading.Thread(target=watch_stop, daemon=True)
        watcher.start()
        result = evolve_practice(
            vm, str(lineage), spec["instruction"], configs["actor"],
            configs["verifier"], configs["curriculum"], configs["memory_actor"],
            corpus_path=str(corpus), project_budget=spec.get("project_budget", 1),
            checkpoint_projects=(0, spec.get("project_budget", 1)),
            phase1_exploration=True, agentic_verifier_cfg=control,
            resume_completed_boundary=resumed,
            hooks=hooks,
            event_sink=lambda event_type, **kwargs: store.append_event(
                run_id, "official_event", {"type": event_type, **kwargs}),
        )
        # Cancellation is not acknowledged until owned VM cleanup succeeds.
        desktop.close()
        desktop = None
        payload = dataclasses.asdict(result)
        if stop.is_set() or store.get(run_id)["stop_requested"]:
            return finish("running", "cancelled", payload)
        if result.status == "quarantined":
            payload["recovery_blocked"] = "Official quarantine cannot be resumed"
            return finish("running", "failed", payload)
        if result.status == "infra":
            return finish("running", "needs_recovery", payload)
        candidate = collect_candidate(lineage)
        if candidate is not None:
            payload["candidate_sha256"] = store.register_candidate(run_id, candidate)
        return finish("running", "completed", payload)
    except Exception as error:
        current = store.get(run_id)["state"]
        target = "needs_recovery" if desktop is not None or current == "running" else "failed"
        if current in {"preflight", "running"}:
            finish(current, target, detail={
                "error_type": type(error).__name__, "error": str(error),
            })
        raise
    finally:
        done.set()
        if watcher is not None:
            watcher.join(timeout=2)
        cleanup_ok = True
        if isolation is not None:
            try:
                isolation.close()
            except Exception as error:  # noqa: BLE001 -- supervisor also cleans owned containers
                cleanup_ok = False
                store.append_event(run_id, "cleanup_failed", {"error_type": type(error).__name__})
        if desktop is not None:
            try:
                desktop.close()
            except Exception as error:  # noqa: BLE001 -- preserve primary worker failure
                cleanup_ok = False
                # Preserve the original error, but never silently report a
                # stopped VM when cleanup has not been confirmed.
                store.append_event(run_id, "cleanup_failed", {
                    "error_type": type(error).__name__, "error": str(error),
                })
        if owns_attempt:
            store.finalize_attempt(run_id, attempt_no,
                active_elapsed_ms=round((time.monotonic() - attempt_started) * 1000),
                cleanup_confirmed=cleanup_ok)
