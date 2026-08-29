from __future__ import annotations

import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LocalTranscription:
    transcript: str
    segments: list[dict[str, object]]
    language: str
    language_probability: float
    model: str
    device: str
    compute_type: str


ModelFactory = Callable[..., Any]


class TurboTranscriber:
    """Lazily load and reuse faster-whisper Turbo with a safe CPU fallback."""

    def __init__(
        self,
        *,
        model_name: str = "turbo",
        model_factory: ModelFactory | None = None,
    ) -> None:
        self.model_name = model_name
        self._model_factory = model_factory
        self._models: dict[tuple[str, str, str], Any] = {}
        self._preferred_runtime: tuple[str, str] | None = None
        self._lock = threading.RLock()

    def _factory(self) -> ModelFactory:
        if self._model_factory is not None:
            return self._model_factory
        try:
            from faster_whisper import WhisperModel
        except ImportError as error:
            raise RuntimeError(
                "本地 Whisper 尚未安装；请先运行 'uv sync --extra dev'"
            ) from error
        return WhisperModel

    def _model(
        self,
        *,
        device: str,
        compute_type: str,
        cache_dir: Path,
    ) -> Any:
        key = (device, compute_type, str(cache_dir))
        model = self._models.get(key)
        if model is not None:
            return model
        cache_dir.mkdir(parents=True, exist_ok=True)
        kwargs: dict[str, object] = {
            "device": device,
            "compute_type": compute_type,
            "download_root": str(cache_dir),
        }
        if device == "cpu":
            kwargs["cpu_threads"] = max(1, min(os.cpu_count() or 4, 8))
        model = self._factory()(self.model_name, **kwargs)
        self._models[key] = model
        return model

    def transcribe(
        self,
        audio_path: Path,
        *,
        cache_dir: Path,
        initial_prompt: str = "",
    ) -> LocalTranscription:
        source = audio_path.expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"讲解音频不存在：{source}")
        runtimes = [("cuda", "float16"), ("cpu", "int8")]
        if self._preferred_runtime is not None:
            runtimes.remove(self._preferred_runtime)
            runtimes.insert(0, self._preferred_runtime)
        failures: list[str] = []
        with self._lock:
            for device, compute_type in runtimes:
                key = (device, compute_type, str(cache_dir.expanduser().resolve()))
                try:
                    model = self._model(
                        device=device,
                        compute_type=compute_type,
                        cache_dir=cache_dir,
                    )
                    raw_segments, info = model.transcribe(
                        str(source),
                        language="zh",
                        beam_size=5,
                        vad_filter=True,
                        condition_on_previous_text=True,
                        initial_prompt=initial_prompt or None,
                    )
                    segments = [
                        {
                            "start_ms": round(float(segment.start) * 1000, 3),
                            "end_ms": round(float(segment.end) * 1000, 3),
                            "text": " ".join(str(segment.text).split()),
                        }
                        for segment in raw_segments
                        if str(segment.text).strip()
                    ]
                    transcript = " ".join(
                        str(segment["text"]) for segment in segments
                    ).strip()
                    self._preferred_runtime = (device, compute_type)
                    return LocalTranscription(
                        transcript=transcript,
                        segments=segments,
                        language=str(getattr(info, "language", "zh") or "zh"),
                        language_probability=float(
                            getattr(info, "language_probability", 0.0) or 0.0
                        ),
                        model=self.model_name,
                        device=device,
                        compute_type=compute_type,
                    )
                except (OSError, RuntimeError, ValueError) as error:
                    self._models.pop(key, None)
                    failures.append(f"{device}: {type(error).__name__}: {error}")
        detail = "; ".join(failures)
        raise RuntimeError(f"本地 Whisper Turbo 转写失败：{detail}")
