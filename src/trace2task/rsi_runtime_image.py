"""Pin the OSWorld QEMU container to a verified immutable local image.

The frozen OSWorld provider asks Docker for ``happysixd/osworld-docker`` by
tag.  That is upstream behavior, but tags can change between two practice
runs.  This harness-side adapter intercepts that one provider call, replaces
the requested tag with the locally verified image ID, and verifies the image
on the returned *owned* container.  It never pulls, retags, or lists Docker
resources.
"""
from __future__ import annotations

from typing import Any

OSWORLD_RUNTIME_SOURCE = "happysixd/osworld-docker"
OSWORLD_RUNTIME_IMAGE_ID = (
    "sha256:fe8d9a5e5ad6c593d059887ea2c790481b3f32dd42fa961f2441cdbbe2c70cf4"
)
OSWORLD_RUNTIME_MANIFEST_DIGEST = (
    "sha256:0e6497a9295647cf05bf2b2af522fdd79bdeba2737595259cab310a3bcf6baa9"
)
OSWORLD_RUNTIME_NANO_CPUS = 4_000_000_000
OSWORLD_RUNTIME_MEMORY_BYTES = 6 * 1024**3
OSWORLD_RUNTIME_PIDS_LIMIT = 512
_PATCH_MARKER = "_trace2task_pinned_osworld_runtime_v1"


class PinnedOSWorldRuntimeError(RuntimeError):
    """The provider did not launch the locally verified OSWorld image."""


def runtime_manifest() -> dict[str, Any]:
    """Return the immutable image provenance recorded in every lineage."""
    return {
        "source": OSWORLD_RUNTIME_SOURCE,
        "image_id": OSWORLD_RUNTIME_IMAGE_ID,
        "registry_manifest_digest": OSWORLD_RUNTIME_MANIFEST_DIGEST,
        "resource_limits": {
            "nano_cpus": OSWORLD_RUNTIME_NANO_CPUS,
            "memory_bytes": OSWORLD_RUNTIME_MEMORY_BYTES,
            "memory_swap_bytes": OSWORLD_RUNTIME_MEMORY_BYTES,
            "pids_limit": OSWORLD_RUNTIME_PIDS_LIMIT,
        },
    }


def _requested_image(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[Any, tuple[Any, ...], dict[str, Any]]:
    if args:
        return args[0], args[1:], dict(kwargs)
    if "image" in kwargs:
        copied = dict(kwargs)
        return copied.pop("image"), (), copied
    raise PinnedOSWorldRuntimeError("OSWorld Docker provider omitted its image argument")


def _validate_resource_limits(host_config: Any) -> None:
    if not isinstance(host_config, dict):
        raise PinnedOSWorldRuntimeError("OSWorld Docker HostConfig is unreadable")
    actual = {
        "nano_cpus": host_config.get("NanoCpus"),
        "memory_bytes": host_config.get("Memory"),
        "memory_swap_bytes": host_config.get("MemorySwap"),
        "pids_limit": host_config.get("PidsLimit"),
    }
    expected = runtime_manifest()["resource_limits"]
    if actual != expected:
        raise PinnedOSWorldRuntimeError(
            "OSWorld Docker container resource limits do not match the pinned boundary"
        )


def verify_container_image(container: Any) -> dict[str, Any]:
    """Fail closed unless the returned owned container has the pinned ID."""
    reload = getattr(container, "reload", None)
    if not callable(reload):
        raise PinnedOSWorldRuntimeError("OSWorld Docker container cannot be inspected")
    try:
        reload()
        attrs = getattr(container, "attrs", None)
        image = getattr(container, "image", None)
        actual_from_image = getattr(image, "id", None)
    except Exception as exc:
        raise PinnedOSWorldRuntimeError(
            f"Could not inspect OSWorld Docker image: {type(exc).__name__}: {exc}"
        ) from exc
    actual_from_attrs = attrs.get("Image") if isinstance(attrs, dict) else None
    if actual_from_attrs != OSWORLD_RUNTIME_IMAGE_ID or actual_from_image != OSWORLD_RUNTIME_IMAGE_ID:
        raise PinnedOSWorldRuntimeError(
            "OSWorld Docker container image does not match the pinned immutable image ID"
        )
    _validate_resource_limits(attrs.get("HostConfig") if isinstance(attrs, dict) else None)
    container_id = str(getattr(container, "id", "") or attrs.get("Id", ""))
    if not container_id:
        raise PinnedOSWorldRuntimeError("OSWorld Docker container has no identity")
    return {**runtime_manifest(), "container_id": container_id}


class _OwnedContainer:
    """Delegate an exact returned guest while retaining its anonymous volume."""

    def __init__(self, container: Any) -> None:
        self._container = container

    def __getattr__(self, name: str) -> Any:
        return getattr(self._container, name)

    def remove(self, *args: Any, **kwargs: Any) -> Any:
        # The frozen provider's normal stop path omits ``v``.  This proxy only
        # ever wraps its own just-created guest, so retaining its anonymous
        # /storage volume serves no recovery purpose and leaks Docker-root disk.
        copied = dict(kwargs)
        copied.setdefault("v", True)
        try:
            from docker.errors import NotFound
        except ImportError:
            # Unit tests and source inspection do not require the Docker SDK;
            # production has it through the official OSWorld runtime.
            return self._container.remove(*args, **copied)
        try:
            return self._container.remove(*args, **copied)
        except NotFound:
            # This exact handle comes only from this wrapper's owned `run`
            # call. A concurrent owned cleanup may already have removed it;
            # Docker's explicit 404 is therefore idempotent completion, while
            # every other API failure remains visible to the caller.
            return None


class _PinnedContainers:
    """One-call adapter around the official provider's existing collection."""

    def __init__(self, collection: Any) -> None:
        self._collection = collection

    def __getattr__(self, name: str) -> Any:
        return getattr(self._collection, name)

    def run(self, *args: Any, **kwargs: Any) -> Any:
        requested, rest, copied = _requested_image(args, kwargs)
        if str(requested) not in {OSWORLD_RUNTIME_SOURCE, f"{OSWORLD_RUNTIME_SOURCE}:latest"}:
            raise PinnedOSWorldRuntimeError(
                f"Unexpected OSWorld Docker image request: {requested!r}"
            )
        requested_limits = {
            "nano_cpus": OSWORLD_RUNTIME_NANO_CPUS,
            "mem_limit": "6g",
            "memswap_limit": "6g",
            "pids_limit": OSWORLD_RUNTIME_PIDS_LIMIT,
        }
        for key, value in requested_limits.items():
            if key in copied and copied[key] != value:
                raise PinnedOSWorldRuntimeError(
                    f"OSWorld Docker provider attempted to override pinned {key}"
                )
            copied[key] = value
        container = _OwnedContainer(self._collection.run(OSWORLD_RUNTIME_IMAGE_ID, *rest, **copied))
        try:
            verify_container_image(container)
        except Exception as verification_error:
            # This handle came from this exact ``run`` call; do not search for
            # or touch any other container when a launch violates the pin.
            try:
                container.remove(force=True, v=True)
            except Exception as cleanup_error:
                raise PinnedOSWorldRuntimeError(
                    "Pinned OSWorld Docker launch verification failed and its owned "
                    "container could not be removed"
                ) from cleanup_error
            raise PinnedOSWorldRuntimeError(
                f"Pinned OSWorld Docker launch verification failed: {verification_error}"
            ) from verification_error
        return container


class _PinnedClient:
    def __init__(self, client: Any) -> None:
        self._client = client

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    @property
    def containers(self) -> _PinnedContainers:
        return _PinnedContainers(self._client.containers)


def install_pinned_osworld_runtime() -> None:
    """Patch only future frozen-provider instances to use the image ID.

    Call this after the upstream checkpointable-provider wrapper.  The latter
    applies labels and loopback port policy; this wrapper composes outside it
    and leaves all of those controls in place.
    """
    from desktop_env.providers.docker import provider as provider_module

    provider = provider_module.DockerProvider
    current = provider.__init__
    if getattr(current, _PATCH_MARKER, False):
        return

    def pinned_init(self: Any, region: str) -> None:
        current(self, region)
        self.client = _PinnedClient(self.client)

    setattr(pinned_init, _PATCH_MARKER, True)
    pinned_init._trace2task_original = current
    provider.__init__ = pinned_init


def verify_guest_runtime_image(vm: Any) -> dict[str, str]:
    """Verify the live guest again after reset, before roles/model work."""
    environment = getattr(vm, "env", None)
    provider = getattr(environment, "provider", None)
    container = getattr(provider, "container", None)
    if container is None:
        raise PinnedOSWorldRuntimeError("OSWorld guest container is unavailable after reset")
    return verify_container_image(container)


__all__ = [
    "OSWORLD_RUNTIME_IMAGE_ID",
    "OSWORLD_RUNTIME_MANIFEST_DIGEST",
    "OSWORLD_RUNTIME_MEMORY_BYTES",
    "OSWORLD_RUNTIME_NANO_CPUS",
    "OSWORLD_RUNTIME_PIDS_LIMIT",
    "OSWORLD_RUNTIME_SOURCE",
    "PinnedOSWorldRuntimeError",
    "install_pinned_osworld_runtime",
    "runtime_manifest",
    "verify_container_image",
    "verify_guest_runtime_image",
]
