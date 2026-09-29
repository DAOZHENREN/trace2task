"""Fail-closed offline egress sealing for Trace2Task's RSI Phase-1 guests.

The upstream Phase-1 launcher intentionally boots a null OSWorld guest but does
not install the stricter Phase-2 network boundary.  This module does *not*
reimplement that policy.  It wraps the upstream ``TargetIsolationSeal`` in its
official ``null`` mode after every null reset, records the returned installation
and live-probe receipts, and makes a failed boundary unusable.

It is deliberately a harness-side facility: it must be invoked before any
Curriculum, Actor, Verifier, or model work in a fresh guest.  It is not a task
grader and it must not be used to relax target egress.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class Phase1OfflineIsolationError(RuntimeError):
    """The official offline boundary was not installed and verified."""


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _json_value(value: Any) -> Any:
    """Convert only ordinary receipt values; reject opaque proof objects."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _json_value(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise Phase1OfflineIsolationError(
        f"Isolation receipt contains unsupported value {type(value).__name__}"
    )


def _close_guest(vm: Any) -> str | None:
    """Best-effort owned-guest close, returning any cleanup error for evidence."""
    environment = getattr(vm, "env", None)
    close = getattr(environment, "close", None)
    if not callable(close):
        return "VM environment has no close method"
    try:
        close()
    except Exception as exc:  # noqa: BLE001 - preserve the boundary failure
        return f"{type(exc).__name__}: {exc}"
    return None


class Phase1OfflineIsolation:
    """Install the official null egress policy after each Phase-1 reset.

    ``run_root`` is the durable Trace2Task run directory, not the upstream
    lineage directory.  Receipts are immutable, one per reset boundary.  A
    caller should use :meth:`wrap_reset_hook` for every reset performed inside
    ``evolve_practice`` and call :meth:`seal_after_reset` for the initial boot
    reset before entering that upstream loop.
    """

    def __init__(
        self,
        run_root: str | Path,
        *,
        seal_factory: Callable[[], Any] | None = None,
        expected_owner: str | None = None,
    ) -> None:
        self.run_root = Path(run_root)
        self.receipts_root = self.run_root / "offline-isolation"
        self._seal_factory = seal_factory
        self.expected_owner = expected_owner or os.environ.get("RSIAGENT_VM_OWNER", "")
        self._seal: Any | None = None
        self._next_reset = 0
        self._closed = False

    def _get_seal(self) -> Any:
        if self._closed:
            raise Phase1OfflineIsolationError("Offline isolation is already closed")
        if self._seal is None:
            if self._seal_factory is not None:
                self._seal = self._seal_factory()
            else:
                # Upstream is deliberately imported only after the trusted
                # worker has pinned it and placed it on sys.path.
                from explore.target_isolation import TargetIsolationSeal

                self._seal = TargetIsolationSeal()
        return self._seal

    @staticmethod
    def _binding_projection(bindings: Any, *, source: str) -> dict[str, list[dict[str, str]]]:
        if not isinstance(bindings, dict):
            raise Phase1OfflineIsolationError(f"Owned container {source} ports are unreadable")
        projected: dict[str, list[dict[str, str]]] = {}
        for port, entries in bindings.items():
            if entries is None:
                continue
            if not isinstance(entries, list) or not entries:
                raise Phase1OfflineIsolationError(
                    f"Owned container has malformed {source} port binding for {port}"
                )
            values: list[dict[str, str]] = []
            for entry in entries:
                if not isinstance(entry, dict):
                    raise Phase1OfflineIsolationError(
                        f"Owned container has malformed {source} port entry for {port}"
                    )
                host_ip = str(entry.get("HostIp", ""))
                host_port = str(entry.get("HostPort", ""))
                if host_ip != "127.0.0.1" or not host_port.isdigit():
                    raise Phase1OfflineIsolationError(
                        f"Owned container {source} port {port} is not loopback-only"
                    )
                values.append({"host_ip": host_ip, "host_port": host_port})
            projected[str(port)] = values
        return projected

    def inspect_owned_container(self, vm: Any) -> dict[str, Any]:
        """Read and validate only this run's QEMU-owning Docker container.

        The upstream Docker provider deliberately owns the container.  This
        method never lists containers or networks and does not invoke Docker
        mutation APIs.  It verifies the specific launch constraints on that
        already-owned handle before the official egress seal is installed.
        """
        if not self.expected_owner:
            raise Phase1OfflineIsolationError("RSI owner label expectation is missing")
        environment = getattr(vm, "env", None)
        provider = getattr(environment, "provider", None)
        container = getattr(provider, "container", None)
        reload = getattr(container, "reload", None)
        if container is None or not callable(reload):
            raise Phase1OfflineIsolationError("Owned Docker guest container is unavailable")
        try:
            reload()
            attrs = getattr(container, "attrs", None)
        except Exception as exc:
            raise Phase1OfflineIsolationError(
                f"Could not inspect owned Docker guest: {type(exc).__name__}: {exc}"
            ) from exc
        if not isinstance(attrs, dict):
            raise Phase1OfflineIsolationError("Owned Docker guest attributes are unreadable")
        config = attrs.get("Config")
        host_config = attrs.get("HostConfig")
        network = attrs.get("NetworkSettings")
        if not isinstance(config, dict) or not isinstance(host_config, dict) or not isinstance(network, dict):
            raise Phase1OfflineIsolationError("Owned Docker guest inspection is incomplete")
        labels = config.get("Labels") or attrs.get("Config", {}).get("Labels")
        if not isinstance(labels, dict):
            raise Phase1OfflineIsolationError("Owned Docker guest labels are missing")
        if labels.get("rsiagent.owner") != self.expected_owner:
            raise Phase1OfflineIsolationError("Owned Docker guest owner label does not match run")
        token = labels.get("rsiagent.launch_token")
        if not isinstance(token, str) or not token:
            raise Phase1OfflineIsolationError("Owned Docker guest launch token is missing")

        mounts = attrs.get("Mounts")
        if not isinstance(mounts, list):
            raise Phase1OfflineIsolationError("Owned Docker guest mounts are unreadable")
        bind_mounts: list[dict[str, Any]] = []
        for mount in mounts:
            if not isinstance(mount, dict):
                raise Phase1OfflineIsolationError("Owned Docker guest mount entry is malformed")
            source = str(mount.get("Source", ""))
            destination = str(mount.get("Destination", ""))
            mount_type = str(mount.get("Type", ""))
            if source.endswith("docker.sock") or destination.endswith("docker.sock"):
                raise Phase1OfflineIsolationError("Owned Docker guest exposes Docker socket")
            if mount_type == "bind":
                bind_mounts.append({
                    "source": source,
                    "destination": destination,
                    "read_only": mount.get("RW") is False,
                })
        if len(bind_mounts) != 1:
            raise Phase1OfflineIsolationError(
                "Owned Docker guest must have exactly one host bind mount"
            )
        qcow = bind_mounts[0]
        if (
            not str(qcow["source"]).startswith("/")
            or qcow["destination"] != "/System.qcow2"
            or qcow["read_only"] is not True
        ):
            raise Phase1OfflineIsolationError(
                "Owned Docker guest bind mount is not the read-only qcow image"
            )

        host_ports = self._binding_projection(
            host_config.get("PortBindings"), source="HostConfig"
        )
        runtime_ports = self._binding_projection(network.get("Ports"), source="NetworkSettings")
        required_ports = {"5000/tcp", "8006/tcp", "8080/tcp", "9222/tcp"}
        if not required_ports.issubset(host_ports) or not required_ports.issubset(runtime_ports):
            raise Phase1OfflineIsolationError("Owned Docker guest lacks required loopback port bindings")
        container_id = str(getattr(container, "id", "") or attrs.get("Id", ""))
        if not container_id:
            raise Phase1OfflineIsolationError("Owned Docker guest has no container identity")
        return {
            "container_id": container_id,
            "owner": self.expected_owner,
            "launch_token": token,
            "qcow_bind": qcow,
            "host_port_bindings": host_ports,
            "runtime_port_bindings": runtime_ports,
        }

    @staticmethod
    def _validate_installation(installation: dict[str, Any]) -> None:
        if installation.get("mode") != "null":
            raise Phase1OfflineIsolationError("Official isolation did not install null mode")
        for key in ("policy_sha256", "rules_sha256"):
            if not _SHA256.fullmatch(str(installation.get(key, ""))):
                raise Phase1OfflineIsolationError(
                    f"Official null isolation receipt lacks valid {key}"
                )
        if installation.get("gateway_ip") is not None:
            raise Phase1OfflineIsolationError("Null mode unexpectedly installed a gateway")
        if installation.get("pinned_upstream_ips") not in ([], ()):
            raise Phase1OfflineIsolationError("Null mode unexpectedly permits upstream IPs")
        if not str(installation.get("outer_container_id", "")):
            raise Phase1OfflineIsolationError("Official null isolation receipt lacks container identity")
        if not str(installation.get("ipv6_enforcement", "")):
            raise Phase1OfflineIsolationError("Official null isolation receipt lacks IPv6 proof")

    @staticmethod
    def _validate_probe(probe: dict[str, Any]) -> None:
        if probe.get("mode") != "null":
            raise Phase1OfflineIsolationError("Official isolation probe was not null mode")
        expected = {
            "exact_streamview_https": "BLOCKED",
            "wrong_sni_cotenant": "BLOCKED",
            "direct_ip_https": "BLOCKED",
            "public_dns": "BLOCKED",
            "outer_gateway_service": "BLOCKED",
            "public_non_http_tcp": "BLOCKED",
            "public_ipv6": "BLOCKED",
        }
        for key, value in expected.items():
            if probe.get(key) != value:
                raise Phase1OfflineIsolationError(
                    f"Official null isolation probe did not block {key}"
                )
        if not _SHA256.fullmatch(str(probe.get("transcript_sha256", ""))):
            raise Phase1OfflineIsolationError(
                "Official null isolation probe lacks a valid transcript hash"
            )

    def _write_receipt(self, ordinal: int, payload: dict[str, Any]) -> Path:
        self._assert_receipt_available(ordinal)
        path = self.receipts_root / f"reset-{ordinal:04d}.json"
        try:
            with path.open("x", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise Phase1OfflineIsolationError(
                f"Could not persist offline isolation receipt: {exc}"
            ) from exc
        return path

    def _assert_receipt_available(self, ordinal: int) -> None:
        if ordinal < 0:
            raise Phase1OfflineIsolationError("Reset ordinal must be nonnegative")
        if self.receipts_root.is_symlink():
            raise Phase1OfflineIsolationError("Offline isolation receipt root is a symlink")
        self.receipts_root.mkdir(parents=True, exist_ok=True)
        path = self.receipts_root / f"reset-{ordinal:04d}.json"
        if path.exists() or path.is_symlink():
            raise Phase1OfflineIsolationError(
                f"Offline isolation receipt already exists for reset {ordinal}"
            )

    def seal_after_reset(self, vm: Any, *, reset_ordinal: int | None = None) -> dict[str, Any]:
        """Seal and probe one already-reset guest; fail closed on any defect."""
        ordinal = self._next_reset if reset_ordinal is None else reset_ordinal
        if ordinal != self._next_reset:
            raise Phase1OfflineIsolationError(
                f"Unexpected reset ordinal {ordinal}; expected {self._next_reset}"
            )
        timestamp = datetime.now(UTC).isoformat()
        inspection: dict[str, Any] | None = None
        try:
            # Refuse an accidental resume/collision before touching the guest.
            self._assert_receipt_available(ordinal)
            inspection = self.inspect_owned_container(vm)
            seal = self._get_seal()
            installation_raw = seal.install(vm, mode="null")
            installation = _json_value(installation_raw)
            self._validate_installation(installation)
            # The upstream probe dereferences ``installation.mode``.  Preserve
            # its concrete SealInstallation object; JSON is only for records.
            probe = _json_value(seal.probe(vm, installation_raw))
            self._validate_probe(probe)
            receipt = {
                "schema_version": 1,
                "kind": "trace2task_rsi_phase1_offline_isolation",
                "status": "sealed",
                "mode": "null",
                "reset_ordinal": ordinal,
                "sealed_at": timestamp,
                "container_inspection": inspection,
                "installation": installation,
                "probe": probe,
            }
            path = self._write_receipt(ordinal, receipt)
        except Exception as exc:  # noqa: BLE001 - record then close the owned guest
            error = exc if isinstance(exc, Phase1OfflineIsolationError) else (
                Phase1OfflineIsolationError(
                    f"Official null isolation failed: {type(exc).__name__}: {exc}"
                )
            )
            failure = {
                "schema_version": 1,
                "kind": "trace2task_rsi_phase1_offline_isolation",
                "status": "failed",
                "mode": "null",
                "reset_ordinal": ordinal,
                "sealed_at": timestamp,
                "error_type": type(error).__name__,
                "error": str(error),
            }
            if inspection is not None:
                failure["container_inspection"] = inspection
            try:
                self._write_receipt(ordinal, failure)
            except Phase1OfflineIsolationError as receipt_error:
                error = Phase1OfflineIsolationError(
                    f"{error}; additionally could not persist failure receipt: {receipt_error}"
                )
            cleanup_error = _close_guest(vm)
            if cleanup_error:
                error = Phase1OfflineIsolationError(
                    f"{error}; additionally owned guest cleanup failed: {cleanup_error}"
                )
            raise error
        self._next_reset += 1
        receipt["receipt_path"] = str(path)
        return receipt

    def wrap_reset_hook(self, reset_vm: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
        """Return an ``evolve_practice`` reset hook that seals every reset.

        The upstream hook performs the actual reset/rollback and null-state scrub
        first.  QEMU verifier rollback is different: it restores only the guest
        checkpoint inside the same outer container, so the outer firewall seal
        persists and must not be replaced with a guest-only rule.
        """
        if not callable(reset_vm):
            raise TypeError("reset_vm must be callable")

        def sealed_reset(vm: Any, target_direction: str) -> dict[str, Any]:
            result = reset_vm(vm, target_direction)
            if not isinstance(result, dict) or not result.get("ok"):
                return result
            try:
                receipt = self.seal_after_reset(vm)
            except Phase1OfflineIsolationError as exc:
                return {"ok": False, "error": str(exc), "offline_isolation": "failed"}
            return {**result, "offline_isolation": receipt}

        return sealed_reset

    def close(self) -> None:
        """Release only official seal-owned host-side resources (none in null mode)."""
        if self._closed:
            return
        self._closed = True
        close = getattr(self._seal, "close", None)
        if callable(close):
            try:
                close()
            except Exception as exc:
                raise Phase1OfflineIsolationError(
                    f"Official isolation close failed: {type(exc).__name__}: {exc}"
                ) from exc


__all__ = ["Phase1OfflineIsolation", "Phase1OfflineIsolationError"]
