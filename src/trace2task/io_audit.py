"""Append-only, run-local model and executor boundary evidence."""

import hashlib
import json
import re
import threading
from datetime import UTC, datetime
from pathlib import Path


class IOAudit:
    def __init__(self, root: Path):
        self.root = Path(root) / "io-audit"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._sequence = 0

    def record(self, kind, *, secrets=(), **data):
        # Codex local images may live in temporary directories. Archive the
        # bytes before dispatch; preserve original paths in the protocol data.
        assets = {}

        def archive(value):
            if isinstance(value, dict):
                if value.get("type") == "localImage" and value.get("path"):
                    path = Path(value["path"])
                    blob = path.read_bytes()
                    digest = hashlib.sha256(blob).hexdigest()
                    target = self.root / (digest + path.suffix)
                    target.write_bytes(blob)
                    assets[str(path)] = {"file": target.name, "sha256": digest}
                for item in value.values():
                    archive(item)
            elif isinstance(value, list):
                for item in value:
                    archive(item)

        with self._lock:
            if kind == "codex_request":
                archive(data)
            self._sequence += 1
            entry = {"seq": self._sequence, "timestamp": datetime.now(UTC).isoformat(),
                     "type": kind, **data}
            if assets:
                entry["image_assets"] = assets
            text = json.dumps(entry, ensure_ascii=False)
            for secret in secrets:
                if secret:
                    text = text.replace(json.dumps(secret, ensure_ascii=False)[1:-1], "[REDACTED]")
            text = re.sub(r"(?i)Bearer\s+[^\s\"\\]+", "Bearer [REDACTED]", text)
            with (self.root / "events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(text + "\n")
