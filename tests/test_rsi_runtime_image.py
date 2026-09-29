import sys
from types import ModuleType, SimpleNamespace

import pytest

from trace2task.rsi_runtime_image import (
    OSWORLD_RUNTIME_IMAGE_ID,
    OSWORLD_RUNTIME_MANIFEST_DIGEST,
    OSWORLD_RUNTIME_MEMORY_BYTES,
    OSWORLD_RUNTIME_NANO_CPUS,
    OSWORLD_RUNTIME_PIDS_LIMIT,
    OSWORLD_RUNTIME_SOURCE,
    PinnedOSWorldRuntimeError,
    _OwnedContainer,
    _PinnedContainers,
    install_pinned_osworld_runtime,
    runtime_manifest,
    verify_guest_runtime_image,
)


class Container:
    def __init__(self, image_id=OSWORLD_RUNTIME_IMAGE_ID):
        self.id = "owned"
        self.attrs = {
            "Id": self.id,
            "Image": image_id,
            "HostConfig": {
                "NanoCpus": OSWORLD_RUNTIME_NANO_CPUS,
                "Memory": OSWORLD_RUNTIME_MEMORY_BYTES,
                "MemorySwap": OSWORLD_RUNTIME_MEMORY_BYTES,
                "PidsLimit": OSWORLD_RUNTIME_PIDS_LIMIT,
            },
        }
        self.image = SimpleNamespace(id=image_id)
        self.reloads = 0
        self.removed = []

    def reload(self):
        self.reloads += 1

    def remove(self, **kwargs):
        self.removed.append(kwargs)


class Collection:
    def __init__(self, container):
        self.container = container
        self.calls = []

    def run(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.container


def test_runtime_provenance_is_fixed_to_recorded_registry_artifact():
    assert runtime_manifest() == {
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


def test_provider_tag_is_replaced_by_pinned_image_and_verified():
    container = Container()
    collection = Collection(container)
    result = _PinnedContainers(collection).run(OSWORLD_RUNTIME_SOURCE, detach=True)
    assert result._container is container
    assert collection.calls == [((OSWORLD_RUNTIME_IMAGE_ID,), {
        "detach": True, "nano_cpus": OSWORLD_RUNTIME_NANO_CPUS,
        "mem_limit": "6g", "memswap_limit": "6g", "pids_limit": OSWORLD_RUNTIME_PIDS_LIMIT,
    })]
    assert container.reloads == 1


def test_mismatched_returned_container_is_removed_and_refused():
    container = Container("sha256:" + "a" * 64)
    collection = Collection(container)
    with pytest.raises(PinnedOSWorldRuntimeError, match="does not match"):
        _PinnedContainers(collection).run(f"{OSWORLD_RUNTIME_SOURCE}:latest")
    assert collection.calls[0][0][0] == OSWORLD_RUNTIME_IMAGE_ID
    assert container.removed == [{"force": True, "v": True}]


def test_mismatched_resource_limit_is_removed_and_refused():
    container = Container()
    container.attrs["HostConfig"]["PidsLimit"] = 64
    collection = Collection(container)
    with pytest.raises(PinnedOSWorldRuntimeError, match="resource limits"):
        _PinnedContainers(collection).run(OSWORLD_RUNTIME_SOURCE)
    assert container.removed == [{"force": True, "v": True}]


def test_returned_owned_guest_removal_keeps_anonymous_volume_cleanup_enabled():
    container = Container()
    result = _PinnedContainers(Collection(container)).run(OSWORLD_RUNTIME_SOURCE)
    result.remove(force=True)
    assert container.removed == [{"force": True, "v": True}]


def test_owned_guest_remove_treats_only_docker_not_found_as_idempotent(monkeypatch):
    class NotFound(RuntimeError):
        pass

    docker = ModuleType("docker")
    errors = ModuleType("docker.errors")
    errors.NotFound = NotFound
    docker.errors = errors
    monkeypatch.setitem(sys.modules, "docker", docker)
    monkeypatch.setitem(sys.modules, "docker.errors", errors)

    class MissingContainer:
        def remove(self, **kwargs):
            assert kwargs == {"force": True, "v": True}
            raise NotFound("owned guest has already gone")

    assert _OwnedContainer(MissingContainer()).remove(force=True) is None

    class OtherDockerFailure:
        def remove(self, **kwargs):
            raise RuntimeError("daemon unavailable")

    with pytest.raises(RuntimeError, match="daemon unavailable"):
        _OwnedContainer(OtherDockerFailure()).remove(force=True)


def test_unexpected_source_tag_is_refused_without_docker_call():
    collection = Collection(Container())
    with pytest.raises(PinnedOSWorldRuntimeError, match="Unexpected"):
        _PinnedContainers(collection).run("attacker/osworld:latest")
    assert collection.calls == []


def test_live_guest_requires_owned_container_and_exact_image():
    guest = SimpleNamespace(env=SimpleNamespace(provider=SimpleNamespace(container=Container())))
    receipt = verify_guest_runtime_image(guest)
    assert receipt["container_id"] == "owned"
    assert receipt["image_id"] == OSWORLD_RUNTIME_IMAGE_ID
    with pytest.raises(PinnedOSWorldRuntimeError, match="unavailable"):
        verify_guest_runtime_image(SimpleNamespace(env=SimpleNamespace(provider=SimpleNamespace())))


def test_provider_constructor_composes_outside_official_wrapper(monkeypatch):
    container = Container()
    collection = Collection(container)

    class DockerProvider:
        def __init__(self, region):
            self.region = region
            self.client = SimpleNamespace(containers=collection)

    provider_module = ModuleType("desktop_env.providers.docker.provider")
    provider_module.DockerProvider = DockerProvider
    docker_package = ModuleType("desktop_env.providers.docker")
    docker_package.__path__ = []
    docker_package.provider = provider_module
    providers_package = ModuleType("desktop_env.providers")
    providers_package.__path__ = []
    providers_package.docker = docker_package
    desktop_package = ModuleType("desktop_env")
    desktop_package.__path__ = []
    desktop_package.providers = providers_package
    monkeypatch.setitem(sys.modules, "desktop_env", desktop_package)
    monkeypatch.setitem(sys.modules, "desktop_env.providers", providers_package)
    monkeypatch.setitem(sys.modules, "desktop_env.providers.docker", docker_package)
    monkeypatch.setitem(sys.modules, "desktop_env.providers.docker.provider", provider_module)

    install_pinned_osworld_runtime()
    first_init = DockerProvider.__init__
    install_pinned_osworld_runtime()
    assert DockerProvider.__init__ is first_init
    DockerProvider("region").client.containers.run(OSWORLD_RUNTIME_SOURCE)
    assert collection.calls == [((OSWORLD_RUNTIME_IMAGE_ID,), {
        "nano_cpus": OSWORLD_RUNTIME_NANO_CPUS,
        "mem_limit": "6g", "memswap_limit": "6g", "pids_limit": OSWORLD_RUNTIME_PIDS_LIMIT,
    })]
