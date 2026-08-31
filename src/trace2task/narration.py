from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_NARRATION_TEXT_CHARS = 20_000
MAX_NARRATION_SEGMENTS = 512
MAX_NARRATION_AUDIO_BYTES = 20 * 1024 * 1024
NARRATION_AUDIO_EXTENSIONS = {
    "audio/mp4": ".m4a",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/webm": ".webm",
}


@dataclass(frozen=True)
class NarrationArchive:
    manifest_path: Path
    transcript: str
    segment_count: int
    audio_path: Path | None


def _normalize_transcript(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("Narration transcript must be a string")
    normalized = " ".join(value.split())
    if len(normalized) > MAX_NARRATION_TEXT_CHARS:
        raise ValueError(
            f"Narration transcript must not exceed {MAX_NARRATION_TEXT_CHARS} characters"
        )
    return normalized


def _normalize_segments(value: object) -> list[dict[str, object]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError("Narration segments must be a list")
    if len(value) > MAX_NARRATION_SEGMENTS:
        raise ValueError(f"Narration must not exceed {MAX_NARRATION_SEGMENTS} segments")
    segments: list[dict[str, object]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise TypeError(f"Narration segment {index} must be an object")
        start_ms = raw.get("start_ms")
        end_ms = raw.get("end_ms")
        if (
            not isinstance(start_ms, (int, float))
            or isinstance(start_ms, bool)
            or not isinstance(end_ms, (int, float))
            or isinstance(end_ms, bool)
            or start_ms < 0
            or end_ms < start_ms
        ):
            raise ValueError(f"Narration segment {index} has invalid timestamps")
        text = _normalize_transcript(raw.get("text"))
        if not text:
            continue
        segments.append(
            {
                "start_ms": round(float(start_ms), 3),
                "end_ms": round(float(end_ms), 3),
                "text": text,
            }
        )
    return segments


def _normalize_audio_start_trace_elapsed_ms(value: object) -> float | None:
    if value is None:
        return None
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
    ):
        raise ValueError("audio_start_trace_elapsed_ms must be a finite number")
    return round(float(value), 3)


def save_narration_audio(
    recording_dir: Path,
    *,
    audio: bytes,
    mime_type: str | None,
) -> Path:
    root = recording_dir.resolve()
    if not (root / "trace.jsonl").is_file():
        raise FileNotFoundError("Narration target has no trace.jsonl")
    if not audio:
        raise ValueError("Narration audio is empty")
    if len(audio) > MAX_NARRATION_AUDIO_BYTES:
        raise ValueError(f"Narration audio must not exceed {MAX_NARRATION_AUDIO_BYTES} bytes")
    if mime_type not in NARRATION_AUDIO_EXTENSIONS:
        raise ValueError("Narration audio format is not supported")
    audio_path = root / f"narration{NARRATION_AUDIO_EXTENSIONS[mime_type]}"
    temporary_audio = audio_path.with_suffix(audio_path.suffix + ".tmp")
    temporary_audio.write_bytes(audio)
    temporary_audio.replace(audio_path)
    return audio_path


def archive_narration(
    recording_dir: Path,
    *,
    transcript: object,
    segments: object = None,
    audio: bytes | None = None,
    existing_audio_path: Path | None = None,
    mime_type: str | None = None,
    transcription_engine: str = "browser_web_speech",
    audio_start_trace_elapsed_ms: object = None,
) -> NarrationArchive:
    """Archive an optional microphone track next to an immutable human Trace."""

    root = recording_dir.resolve()
    if not (root / "trace.jsonl").is_file():
        raise FileNotFoundError("Narration target has no trace.jsonl")
    normalized_transcript = _normalize_transcript(transcript)
    normalized_segments = _normalize_segments(segments)
    normalized_audio_offset = _normalize_audio_start_trace_elapsed_ms(
        audio_start_trace_elapsed_ms
    )
    audio_path: Path | None = None
    audio_payload: dict[str, object] | None = None
    if audio:
        audio_path = save_narration_audio(root, audio=audio, mime_type=mime_type)
    elif existing_audio_path is not None:
        audio_path = existing_audio_path.expanduser().resolve()
        if audio_path.parent != root or not audio_path.is_file():
            raise ValueError("Existing narration audio must be inside the recording directory")
        if mime_type not in NARRATION_AUDIO_EXTENSIONS:
            raise ValueError("Narration audio format is not supported")
        expected_name = f"narration{NARRATION_AUDIO_EXTENSIONS[mime_type]}"
        if audio_path.name != expected_name:
            raise ValueError("Existing narration audio does not match its MIME type")
        if audio_path.stat().st_size > MAX_NARRATION_AUDIO_BYTES:
            raise ValueError(
                f"Narration audio must not exceed {MAX_NARRATION_AUDIO_BYTES} bytes"
            )
    if audio_path is not None:
        audio_payload = {
            "path": audio_path.name,
            "mime_type": mime_type,
            "bytes": audio_path.stat().st_size,
        }
    manifest = {
        "schema_version": "0.1",
        "created_at": datetime.now(UTC).isoformat(),
        "transcription_engine": transcription_engine,
        "transcript": normalized_transcript,
        "segments": normalized_segments,
        "audio_start_trace_elapsed_ms": normalized_audio_offset,
        "audio": audio_payload,
    }
    manifest_path = root / "narration.json"
    temporary_manifest = manifest_path.with_suffix(".json.tmp")
    temporary_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_manifest.replace(manifest_path)
    return NarrationArchive(
        manifest_path=manifest_path,
        transcript=normalized_transcript,
        segment_count=len(normalized_segments),
        audio_path=audio_path,
    )


def load_narration(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("Narration manifest must be an object")
    transcript = _normalize_transcript(payload.get("transcript"))
    segments = _normalize_segments(payload.get("segments"))
    return {
        "transcript": transcript,
        "segments": segments,
        "transcription_engine": str(payload.get("transcription_engine") or "unknown"),
        "audio_start_trace_elapsed_ms": _normalize_audio_start_trace_elapsed_ms(
            payload.get("audio_start_trace_elapsed_ms")
        ),
    }
