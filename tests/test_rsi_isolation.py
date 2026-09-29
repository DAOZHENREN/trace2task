import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from trace2task.rsi_isolation import (
    Phase1OfflineIsolation,
    Phase1OfflineIsolationError,
)

HASH = "a" * 64


@dataclass(frozen=True)
class Installation:
    mode: str = "null"
    policy_sha256: str = HASH
    rules_sha256: str = HASH
    outer_container_id: str = "owned-container"
    gateway_ip: str | None = None
    gateway_ports: object | None = None
    gateway_ca_sha256: str | None = None
    gateway_leaf_spki_sha256: str | None = None
    pinned_upstream_ips: tuple[str, ...] = ()
    ipv6_enforcement: str = "ip6tables-plus-ebtables"


@dataclass(frozen=True)
class Probe:
    mode: str = "null"
    exact_streamview_https: str = "BLOCKED"
    wrong_sni_cotenant: str = "BLOCKED"
    direct_ip_https: str = "BLOCKED"
    public_dns: str = "BLOCKED"
    outer_gateway_service: str = "BLOCKED"
    public_non_http_tcp: str = "BLOCKED"
    public_ipv6: str = "BLOCKED"
    transcript_sha256: str = HASH


class Seal:
    def __init__(self, installation=None, probe=None):
        self.installation = installation or Installation()
        self.probe_value = probe or Probe()
        self.calls = []
        self.closed = 0

    def install(self, vm, *, mode):
        self.calls.append(("install", vm, mode))
        return self.installation

    def probe(self, vm, installation):
        assert installation is self.installation
        self.calls.append(("probe", vm, installation))
        return self.probe_value

    def close(self):
        self.closed += 1


class Container:
    def __init__(self, *, host_ip="127.0.0.1"):
        bindings = {
            f"{port}/tcp": [{"HostIp": host_ip, "HostPort": str(port + 10_000)}]
            for port in (5000, 8006, 8080, 9222)
        }
        self.id = "owned-container"
        self.reloads = 0
        self.attrs = {
            "Id": self.id,
            "Config": {"Labels": {"rsiagent.owner": "owner", "rsiagent.launch_token": "token"}},
            "HostConfig": {"PortBindings": bindings},
            "NetworkSettings": {"Ports": bindings},
            "Mounts": [
                {"Type": "bind", "Source": "/images/System.qcow2", "Destination": "/System.qcow2", "RW": False},
                {"Type": "volume", "Source": "anonymous", "Destination": "/storage", "RW": True},
            ],
        }

    def reload(self):
        self.reloads += 1


def vm(closes=None, container=None):
    def close():
        if closes is not None:
            closes.append(True)

    return SimpleNamespace(env=SimpleNamespace(close=close, provider=SimpleNamespace(
        container=container or Container()
    )))


def test_seal_after_reset_writes_immutable_official_null_receipt(tmp_path):
    seal = Seal()
    isolation = Phase1OfflineIsolation(tmp_path, seal_factory=lambda: seal, expected_owner="owner")
    receipt = isolation.seal_after_reset(vm())
    assert [item[0] for item in seal.calls] == ["install", "probe"]
    assert seal.calls[0][2] == "null"
    assert seal.calls[1][2] is seal.installation
    stored = json.loads((tmp_path / "offline-isolation" / "reset-0000.json").read_text())
    assert stored["status"] == "sealed"
    assert stored["mode"] == "null"
    assert stored["installation"]["gateway_ip"] is None
    assert stored["probe"]["outer_gateway_service"] == "BLOCKED"
    assert receipt["receipt_path"].endswith("reset-0000.json")


def test_each_successful_practice_reset_is_sealed_and_receipted(tmp_path):
    seal = Seal()
    isolation = Phase1OfflineIsolation(tmp_path, seal_factory=lambda: seal, expected_owner="owner")
    resets = []

    def upstream_reset(target_vm, target):
        resets.append(target)
        return {"ok": True, "upstream": "scrubbed"}

    wrapped = isolation.wrap_reset_hook(upstream_reset)
    assert wrapped(vm(), "first")["offline_isolation"]["reset_ordinal"] == 0
    assert wrapped(vm(), "second")["offline_isolation"]["reset_ordinal"] == 1
    assert resets == ["first", "second"]
    assert len(list((tmp_path / "offline-isolation").glob("reset-*.json"))) == 2


def test_upstream_reset_failure_is_not_mislabeled_as_sealed(tmp_path):
    isolation = Phase1OfflineIsolation(tmp_path, seal_factory=Seal, expected_owner="owner")
    wrapped = isolation.wrap_reset_hook(lambda *_: {"ok": False, "error": "reset failed"})
    assert wrapped(vm(), "task") == {"ok": False, "error": "reset failed"}
    assert not (tmp_path / "offline-isolation").exists()


def test_bad_probe_fails_closed_closes_guest_and_records_failure(tmp_path):
    closes = []
    seal = Seal(probe=Probe(public_dns="PASS"))
    isolation = Phase1OfflineIsolation(tmp_path, seal_factory=lambda: seal, expected_owner="owner")
    with pytest.raises(Phase1OfflineIsolationError, match="public_dns"):
        isolation.seal_after_reset(vm(closes))
    assert closes == [True]
    receipt = json.loads((tmp_path / "offline-isolation" / "reset-0000.json").read_text())
    assert receipt["status"] == "failed"


def test_receipt_collision_and_out_of_order_reset_refuse_to_continue(tmp_path):
    isolation = Phase1OfflineIsolation(tmp_path, seal_factory=Seal, expected_owner="owner")
    isolation.seal_after_reset(vm())
    with pytest.raises(Phase1OfflineIsolationError, match="Unexpected reset ordinal"):
        isolation.seal_after_reset(vm(), reset_ordinal=2)
    (tmp_path / "offline-isolation" / "reset-0001.json").write_text("{}")
    with pytest.raises(Phase1OfflineIsolationError, match="already exists"):
        isolation.seal_after_reset(vm(), reset_ordinal=1)


def test_close_releases_the_official_seal_once(tmp_path):
    seal = Seal()
    isolation = Phase1OfflineIsolation(tmp_path, seal_factory=lambda: seal, expected_owner="owner")
    isolation.seal_after_reset(vm())
    isolation.close()
    isolation.close()
    assert seal.closed == 1


def test_container_audit_rejects_non_loopback_port_before_official_seal(tmp_path):
    closes = []
    seal = Seal()
    isolation = Phase1OfflineIsolation(tmp_path, seal_factory=lambda: seal, expected_owner="owner")
    with pytest.raises(Phase1OfflineIsolationError, match="not loopback-only"):
        isolation.seal_after_reset(vm(closes, Container(host_ip="0.0.0.0")))
    assert seal.calls == []
    assert closes == [True]
