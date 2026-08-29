from __future__ import annotations

import json
from pathlib import Path

import pytest

from trace2task.narration import archive_narration, load_narration


def test_archive_narration_keeps_editable_transcript_segments_and_audio(
    tmp_path: Path,
) -> None:
    (tmp_path / "trace.jsonl").write_text('{"seq":0}\n', encoding="utf-8")

    result = archive_narration(
        tmp_path,
        transcript="  先点攻击，  再选择三张卡。 ",
        segments=[
            {"start_ms": 120, "end_ms": 900, "text": "先点攻击"},
            {"start_ms": 910, "end_ms": 1600, "text": "再选择三张卡"},
        ],
        audio=b"webm-audio",
        mime_type="audio/webm",
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert result.transcript == "先点攻击， 再选择三张卡。"
    assert result.segment_count == 2
    assert result.audio_path is not None
    assert result.audio_path.read_bytes() == b"webm-audio"
    assert manifest["audio"]["path"] == "narration.webm"
    assert load_narration(result.manifest_path) == {
        "transcript": "先点攻击， 再选择三张卡。",
        "segments": [
            {"start_ms": 120.0, "end_ms": 900.0, "text": "先点攻击"},
            {"start_ms": 910.0, "end_ms": 1600.0, "text": "再选择三张卡"},
        ],
        "transcription_engine": "browser_web_speech",
    }


def test_archive_narration_rejects_unsupported_audio_without_touching_trace(
    tmp_path: Path,
) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_text('{"seq":0}\n', encoding="utf-8")
    original = trace.read_bytes()

    with pytest.raises(ValueError, match="format is not supported"):
        archive_narration(
            tmp_path,
            transcript="讲解",
            audio=b"audio",
            mime_type="audio/mpeg",
        )

    assert trace.read_bytes() == original
    assert not (tmp_path / "narration.json").exists()


def test_archive_narration_can_reference_audio_saved_before_review(tmp_path: Path) -> None:
    (tmp_path / "trace.jsonl").write_text('{"seq":0}\n', encoding="utf-8")
    audio_path = tmp_path / "narration.webm"
    audio_path.write_bytes(b"already-saved")

    result = archive_narration(
        tmp_path,
        transcript="人工修订后的文字",
        existing_audio_path=audio_path,
        mime_type="audio/webm",
        transcription_engine="faster_whisper:turbo",
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert result.audio_path == audio_path
    assert manifest["audio"] == {
        "path": "narration.webm",
        "mime_type": "audio/webm",
        "bytes": len(b"already-saved"),
    }
    assert manifest["transcription_engine"] == "faster_whisper:turbo"
