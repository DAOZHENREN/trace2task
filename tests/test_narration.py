from __future__ import annotations

import json
from pathlib import Path

import pytest

from trace2task.narration import archive_narration, load_narration
from trace2task.windows_experience import _aligned_narration_context


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
        "audio_start_trace_elapsed_ms": None,
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


def test_archive_narration_preserves_audio_start_on_the_trace_timeline(
    tmp_path: Path,
) -> None:
    (tmp_path / "trace.jsonl").write_text('{"seq":0}\n', encoding="utf-8")

    result = archive_narration(
        tmp_path,
        transcript="先说明下一步，再开始操作。",
        segments=[{"start_ms": 1000, "end_ms": 1300, "text": "点击下一步"}],
        audio_start_trace_elapsed_ms=3000,
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["audio_start_trace_elapsed_ms"] == 3000.0
    assert load_narration(result.manifest_path)["audio_start_trace_elapsed_ms"] == 3000.0


def test_narration_alignment_applies_offset_and_bounded_forward_lookahead() -> None:
    timeline = [
        {"start_elapsed_ms": 1000, "end_elapsed_ms": 1200},
        {"start_elapsed_ms": 6000, "end_elapsed_ms": 6200},
    ]
    narration = {
        "transcript": "点击下一步",
        "segments": [{"start_ms": 1000, "end_ms": 1300, "text": "点击下一步"}],
        "audio_start_trace_elapsed_ms": 3000,
    }

    aligned = _aligned_narration_context(narration, timeline)

    assert aligned[0]["audio_start_ms"] == 1000.0
    assert aligned[0]["audio_end_ms"] == 1300.0
    assert aligned[0]["trace_start_ms"] == 4000.0
    assert aligned[0]["trace_end_ms"] == 4300.0
    assert aligned[0]["aligned_action_range"] == [1, 1]
    assert aligned[0]["alignment"] == "spoken_before_action"

    too_early = _aligned_narration_context(
        {
            "transcript": "很早以前说过的提示",
            "segments": [
                {"start_ms": 0, "end_ms": 500, "text": "很早以前说过的提示"}
            ],
        },
        [{"start_elapsed_ms": 5001, "end_elapsed_ms": 5200}],
    )
    assert too_early[0]["alignment"] == "nearest_action"
