"""Server-only factory: one restricted Codex container per role request.

The trusted controller can launch containers; the model container cannot. Bind
only this call's evidence, not other roles' histories or the controller ledger.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from trace2task.codex_app_server import CodexAppServerSession, StdioJsonTransport

RUNTIME_IMAGE = (
    "ghcr.io/astral-sh/uv@sha256:"
    "e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58"
)
OWNER_LABEL = "org.trace2task.rsi.model-call"


@dataclass(frozen=True)
class ModelContainerConfig:
    codex_home: Path
    codex_bin: Path
    launcher: Path
    uid: int
    gid: int
    run_id: str | None = None

    def command(self, call_dir: Path, identity: str) -> list[str]:
        if len(identity) != 32 or any(c not in "0123456789abcdef" for c in identity):
            raise ValueError("Invalid model container identity")
        if self.uid < 1 or self.gid < 1:
            raise ValueError("Model container must run as a non-root user")
        files = [(self.codex_home / name, PurePosixPath("/codex-home") / name)
                 for name in ("auth.json", "config.toml", "models_cache.json")]
        files += [(self.codex_bin, PurePosixPath("/codex-bin")),
                  (self.launcher, PurePosixPath("/launcher.py")), (call_dir, call_dir)]
        command = ["docker", "run", "--rm", "-i", "--pull=never",
                   "--name", f"trace2task-rsi-model-{identity}",
                   "--label", f"{OWNER_LABEL}={identity}",
                   "--cpus", "1", "--memory", "768m", "--pids-limit", "128",
                   "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                   "--user", f"{self.uid}:{self.gid}",
                   "--tmpfs", f"/codex-home:uid={self.uid},gid={self.gid},mode=0700",
                   "--tmpfs", f"/runtime:uid={self.uid},gid={self.gid},mode=0700",
                   "--tmpfs", "/tmp:mode=1777", "-e", "CODEX_HOME=/codex-home"]
        if self.run_id is not None:
            if len(self.run_id) != 32 or any(c not in "0123456789abcdef" for c in self.run_id):
                raise ValueError("Invalid practice run identity")
            command += ["--label", f"org.trace2task.rsi.run={self.run_id}"]
        for source, destination in files:
            if not source.is_absolute() or not destination.is_absolute():
                raise ValueError("Container mounts must use absolute paths")
            if any(c in str(source) + str(destination) for c in (",", "\n", "\r")):
                raise ValueError("Unsafe mount path")
            # --mount fails if a source is missing; -v would create a directory.
            command += ["--mount", f"type=bind,src={source},dst={destination},readonly"]
        command += [RUNTIME_IMAGE, "python", "/launcher.py", "--codex", "/codex-bin/codex",
                    "--codex-home", "/codex-home", "--output", "/runtime"]
        return command


class ModelOnlyTransport(StdioJsonTransport):
    def __init__(self, command: list[str], identity: str):
        self.identity = identity
        self._cleanup_lock = threading.Lock()
        self._container_cleaned = False
        super().__init__("docker", command=command)

    def receive(self, timeout_seconds):
        message = super().receive(timeout_seconds)
        params = message.get("params") or {}
        if "id" in message and "method" in message:
            self.close()
            raise RuntimeError("Model-only process requested an operational tool")
        if (message.get("method") in {"item/started", "item/completed"}
                and (params.get("item") or {}).get("type") not in {
                    "userMessage", "agentMessage", "reasoning",
                }):
            self.close()
            raise RuntimeError("Unexpected operational item in model-only process")
        return message

    def close(self):
        # Watchdog and caller may race to stop; do not leave an orphan container
        # when terminating the docker attach process only closes the transport.
        with self._cleanup_lock:
            super().close()
            if self._container_cleaned:
                return
            name = f"trace2task-rsi-model-{self.identity}"
            result = subprocess.run(["docker", "container", "ls", "-a", "-q",
                                     "--filter", f"name=^/{name}$"],
                                    capture_output=True, text=True, check=True, timeout=15)
            if result.stdout.strip():
                inspected = subprocess.run(["docker", "inspect", name], check=True,
                                           capture_output=True, text=True, timeout=15)
                container = json.loads(inspected.stdout)[0]
                if container["Config"].get("Labels", {}).get(OWNER_LABEL) != self.identity:
                    raise RuntimeError("Model container ownership mismatch; refusing cleanup")
                subprocess.run(["docker", "rm", "-f", container["Id"]], check=True,
                               capture_output=True, timeout=15)
            self._container_cleaned = True


def session_factory(config: ModelContainerConfig):
    if os.name != "posix":
        raise RuntimeError("RSI model factory must run on the Linux controller, not Windows")

    def create(system: str, model: str, effort: str, call_dir: Path):
        identity = uuid.uuid4().hex
        command = config.command(call_dir.resolve(), identity)
        return CodexAppServerSession(
            "docker", model=model, reasoning_effort=effort, cwd=call_dir,
            base_instructions=system, timeout_seconds=120,
            progress_timeout_seconds=120, hard_timeout_seconds=300,
            transport_factory=lambda _: ModelOnlyTransport(command, identity),
        )
    return create
