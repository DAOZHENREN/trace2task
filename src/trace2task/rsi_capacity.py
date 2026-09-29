"""Trusted Docker-root capacity admission probe for RSI guest practice."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

MINIMUM_DOCKER_AVAILABLE_BYTES = 120 * 1024**3

# This program only asks the kernel for filesystem capacity.  It never lists or
# reads Docker-root contents, and is passed as argv rather than through a shell.
_STATVFS_PROGRAM = (
    "import json,os,stat; assert stat.S_ISDIR(os.stat('/capacity').st_mode); s=os.statvfs('/capacity'); "
    "print(json.dumps({'available_bytes':s.f_bavail*s.f_frsize}))"
)


def _docker_root_dir(info: Any) -> Path:
    if not isinstance(info, dict):
        raise TypeError("Docker info response is invalid")
    raw = info.get("DockerRootDir")
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise ValueError("DockerRootDir is invalid")
    root = Path(raw)
    # DockerRootDir is host metadata. The trusted controller runs in a
    # container and intentionally does not mount that host directory itself.
    # The fixed probe below is therefore the authority for directory/socket
    # type validation after Docker mounts this exact host path read-only.
    if not root.is_absolute() or root == Path(root.anchor) or raw.endswith(".sock"):
        raise ValueError("DockerRootDir must be a non-root absolute directory")
    return root


def probe_docker_root_capacity(config: dict) -> dict[str, int | bool]:
    """Run a fixed, isolated statvfs utility against Docker's real storage root.

    The control process owns the Docker client. The utility has no network, no
    Docker socket, no writable mount, and no capabilities; a model never sees
    the root path or this result. Fail closed if metadata, image identity, or
    utility output cannot be verified.
    """
    image_id = config.get("controller_image")
    if not isinstance(image_id, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
        return {"ready": False, "available_bytes": 0, "required_bytes": MINIMUM_DOCKER_AVAILABLE_BYTES}
    try:
        import docker

        client = docker.from_env()
        try:
            root = _docker_root_dir(client.info())
            image = client.images.get(image_id)
            if getattr(image, "id", None) != image_id:
                raise ValueError("Pinned controller image is unavailable")
            output = client.containers.run(
                image_id,
                ["python", "-I", "-c", _STATVFS_PROGRAM],
                detach=False,
                remove=True,
                network_disabled=True,
                read_only=True,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
                pids_limit=16,
                mem_limit="64m",
                tmpfs={"/tmp": "rw,noexec,nosuid,size=8m"},
                volumes={str(root): {"bind": "/capacity", "mode": "ro"}},
            )
            payload = json.loads(bytes(output).decode("utf-8"))
            available = payload.get("available_bytes") if isinstance(payload, dict) else None
            if type(available) is not int or available < 0:
                raise ValueError("Capacity utility returned invalid bytes")
            return {
                "ready": available >= MINIMUM_DOCKER_AVAILABLE_BYTES,
                "available_bytes": available,
                "required_bytes": MINIMUM_DOCKER_AVAILABLE_BYTES,
            }
        finally:
            client.close()
    except Exception:  # noqa: BLE001 -- capacity admission must fail closed
        return {"ready": False, "available_bytes": 0, "required_bytes": MINIMUM_DOCKER_AVAILABLE_BYTES}
