from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from trace2task.speech_transcription import TurboTranscriber


@dataclass
class FakeSegment:
    start: float
    end: float
    text: str


@dataclass
class FakeInfo:
    language: str = "zh"
    language_probability: float = 0.97


def test_turbo_transcriber_uses_cuda_and_returns_timed_segments(tmp_path: Path) -> None:
    audio_path = tmp_path / "narration.webm"
    audio_path.write_bytes(b"audio")
    factory_calls: list[tuple[str, dict[str, object]]] = []

    class FakeModel:
        def transcribe(self, path: str, **kwargs: object):
            assert path == str(audio_path)
            assert kwargs["language"] == "zh"
            assert kwargs["vad_filter"] is True
            return iter(
                [
                    FakeSegment(0.1, 1.2, " 先点攻击 "),
                    FakeSegment(1.3, 2.5, "再选择三张卡"),
                ]
            ), FakeInfo()

    def factory(model_name: str, **kwargs: object) -> FakeModel:
        factory_calls.append((model_name, kwargs))
        return FakeModel()

    result = TurboTranscriber(model_factory=factory).transcribe(
        audio_path,
        cache_dir=tmp_path / "models",
        initial_prompt="游戏操作示范",
    )

    assert factory_calls[0][0] == "turbo"
    assert factory_calls[0][1]["device"] == "cuda"
    assert factory_calls[0][1]["compute_type"] == "float16"
    assert result.transcript == "先点攻击 再选择三张卡"
    assert result.segments == [
        {"start_ms": 100.0, "end_ms": 1200.0, "text": "先点攻击"},
        {"start_ms": 1300.0, "end_ms": 2500.0, "text": "再选择三张卡"},
    ]
    assert result.device == "cuda"


def test_turbo_transcriber_falls_back_to_cpu_when_cuda_fails(tmp_path: Path) -> None:
    audio_path = tmp_path / "narration.webm"
    audio_path.write_bytes(b"audio")
    devices: list[str] = []

    class FakeModel:
        def __init__(self, device: str) -> None:
            self.device = device

        def transcribe(self, path: str, **kwargs: object):
            if self.device == "cuda":
                raise RuntimeError("missing cuDNN")
            return iter([FakeSegment(0, 1, "CPU 转写成功")]), FakeInfo()

    def factory(model_name: str, **kwargs: object) -> FakeModel:
        device = str(kwargs["device"])
        devices.append(device)
        return FakeModel(device)

    result = TurboTranscriber(model_factory=factory).transcribe(
        audio_path,
        cache_dir=tmp_path / "models",
    )

    assert devices == ["cuda", "cpu"]
    assert result.transcript == "CPU 转写成功"
    assert result.device == "cpu"
    assert result.compute_type == "int8"
