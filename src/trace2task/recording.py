from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import pygame


@dataclass(frozen=True)
class RecordedTrace:
    trace_path: Path
    actions: list[str]


class TraceWriter:
    def __init__(self, run_dir: Path, *, task_id: str, seed: int, source: str) -> None:
        self.run_dir = run_dir
        self.frames_dir = run_dir / "frames"
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        self.trace_path = run_dir / "trace.jsonl"
        self.metadata_path = run_dir / "metadata.json"
        self._started = perf_counter()
        self._sequence = 0
        self._actions: list[str] = []
        self.metadata: dict[str, Any] = {
            "schema_version": "0.1",
            "task_id": task_id,
            "seed": seed,
            "source": source,
            "started_at": datetime.now(UTC).isoformat(),
        }
        self.trace_path.write_text("", encoding="utf-8")

    def record(
        self,
        event_type: str,
        surface: pygame.Surface,
        *,
        action: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        frame_name = f"{self._sequence:04d}.png"
        pygame.image.save(surface, self.frames_dir / frame_name)
        event: dict[str, Any] = {
            "seq": self._sequence,
            "elapsed_ms": round((perf_counter() - self._started) * 1000, 3),
            "type": event_type,
            "frame": f"frames/{frame_name}",
        }
        if action is not None:
            event["action"] = action
            self._actions.append(action)
        if details:
            event["details"] = details
        with self.trace_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._sequence += 1

    def finish(self, *, success: bool, extra: dict[str, Any] | None = None) -> RecordedTrace:
        metadata = {
            **self.metadata,
            "finished_at": datetime.now(UTC).isoformat(),
            "success": success,
            "event_count": self._sequence,
            "action_count": len(self._actions),
        }
        if extra:
            metadata.update(extra)
        self.metadata_path.write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return RecordedTrace(trace_path=self.trace_path, actions=list(self._actions))


def load_actions(trace_path: Path) -> list[str]:
    actions: list[str] = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        action = event.get("action")
        if isinstance(action, str):
            actions.append(action)
    return actions


def make_run_dir(root: Path, label: str) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    return root / f"{timestamp}-{label}"
