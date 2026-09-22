"""Unit tests for resident GUI service observability and cancellation primitives.

These intentionally use no CUDA model: GPU loading is covered by the manual
local acceptance run, while request cancellation must remain deterministic.
"""
import base64
import contextlib
import importlib.util
import json
import sys
import threading
import types
import weakref
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def service_module():
    path = Path(__file__).parents[1] / "scripts" / "local_gui" / "server.py"
    spec = importlib.util.spec_from_file_location("trace2task_local_gui_service", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cancel_before_claim_is_preserved(service_module, tmp_path):
    engine = service_module.Engine(tmp_path, tmp_path / "output")
    request_id = "request-before-predict"
    receipt = engine.cancel(request_id)
    assert receipt == {"status": "cancel_requested", "request_id": request_id, "active": False}
    assert engine.claim_cancellation(request_id).is_set()


def test_active_cancel_sets_same_event(service_module, tmp_path):
    engine = service_module.Engine(tmp_path, tmp_path / "output")
    request_id = "request-active-predict"
    event = engine.claim_cancellation(request_id)
    assert not event.is_set()
    assert engine.cancel(request_id)["active"] is True
    assert event.is_set()
    engine.finish_cancellation(request_id)
    assert engine._cancellations[request_id]["active"] is False


def test_active_request_is_not_pruned_while_generation_is_slow(service_module, tmp_path, monkeypatch):
    engine = service_module.Engine(tmp_path, tmp_path / "output")
    request_id = "request-slow-generation"
    event = engine.claim_cancellation(request_id)
    monkeypatch.setattr(service_module.time, "monotonic", lambda: 10_000_000)
    engine._prune_cancellations()
    assert engine._cancellations[request_id]["event"] is event
    assert engine.cancel(request_id)["active"] is True
    assert event.is_set()


def test_request_id_is_strict(service_module, tmp_path):
    engine = service_module.Engine(tmp_path, tmp_path / "output")
    assert engine._request_id("safe_request-123") == "safe_request-123"
    for value in ("short", "contains space", "../unsafe", 7):
        with pytest.raises(ValueError):
            engine._request_id(value)


def test_generation_stopper_reads_event(service_module):
    event = threading.Event()
    stopper = service_module._CancelGeneration(event)
    assert stopper(None, None) is False
    event.set()
    assert stopper(None, None) is True


def test_memory_snapshot_has_peak_fields(service_module):
    class FakeCuda:
        @staticmethod
        def is_available(): return True
        @staticmethod
        def mem_get_info(): return (9, 12)
        @staticmethod
        def memory_allocated(): return 2
        @staticmethod
        def memory_reserved(): return 3
        @staticmethod
        def max_memory_allocated(): return 4
        @staticmethod
        def max_memory_reserved(): return 5
    class FakeTorch:
        cuda = FakeCuda()
    assert service_module.Engine._memory(FakeTorch(), peak=True) == {
        "available": True, "allocated_bytes": 2, "reserved_bytes": 3,
        "free_bytes": 9, "total_bytes": 12,
        "peak_allocated_bytes": 4, "peak_reserved_bytes": 5,
    }


class _FakeTensor:
    def __init__(self, values):
        self.values = values
        self.shape = (1, len(values))

    def tolist(self):
        return list(self.values)

    def __len__(self):
        return len(self.values)

    def __getitem__(self, value):
        if isinstance(value, tuple):
            return _FakeTensor(self.values[value[1]])
        return self.values[value]


class _FakeInputs(dict):
    def __init__(self):
        self.input_ids = _FakeTensor([1, 2, 3])
        self.attention_mask = _FakeTensor([1, 1, 1])
        self.image_grid_thw = _FakeTensor([1, 1, 1])
        super().__init__(input_ids=self.input_ids, attention_mask=self.attention_mask,
                         image_grid_thw=self.image_grid_thw)

    def to(self, device):
        assert device == "cuda"
        return self


class _FakeImage:
    width, height = 4, 3
    size = (4, 3)

    def convert(self, mode):
        assert mode == "RGB"
        return self

    def save(self, path):
        Path(path).write_bytes(b"fake-image")

    def copy(self):
        return _FakeImage()


class _FakeCuda:
    @staticmethod
    def is_available(): return False
    @staticmethod
    def synchronize(): pass
    @staticmethod
    def reset_peak_memory_stats(): pass


class _FakeTorch:
    cuda = _FakeCuda()

    @staticmethod
    def inference_mode():
        return contextlib.nullcontext()


class _FakeProcessor:
    def apply_chat_template(self, messages, **kwargs):
        return "formatted prompt"

    def __call__(self, **kwargs):
        return _FakeInputs()

    def decode(self, ids, **kwargs):
        return '{"actions":[{"skill":"wait","args":{"duration_ms":100}}]}'


class _FakeDraw:
    def ellipse(self, *args, **kwargs): pass
    def text(self, *args, **kwargs): pass


def _install_predict_fakes(monkeypatch, cuda=None):
    cuda = cuda or _FakeCuda()
    torch = types.ModuleType("torch")
    torch.cuda = cuda
    torch.inference_mode = _FakeTorch.inference_mode
    transformers = types.ModuleType("transformers")
    transformers.StoppingCriteriaList = list
    pil = types.ModuleType("PIL")
    pil.Image = types.SimpleNamespace(open=lambda stream: _FakeImage())
    pil.ImageDraw = types.SimpleNamespace(Draw=lambda image: _FakeDraw())
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    monkeypatch.setitem(sys.modules, "PIL", pil)


def _request(request_id):
    return {
        "request_id": request_id,
        "model": "qwen3-vl-2b",
        "task": "wait briefly",
        "history": [],
        "image": base64.b64encode(b"not-a-real-image").decode(),
    }


def _engine_with_model(service_module, tmp_path, generate):
    model = types.SimpleNamespace(
        generation_config=types.SimpleNamespace(eos_token_id=99, cache_implementation=None),
        generate=generate,
    )
    engine = service_module.Engine(tmp_path, tmp_path / "out")
    engine.model = model
    engine.processor = _FakeProcessor()
    engine.key = "qwen3-vl-2b"
    engine.load = lambda key: None
    return engine


def test_predict_persists_complete_input_output_metrics_and_releases_generated(
        service_module, tmp_path, monkeypatch):
    _install_predict_fakes(monkeypatch)
    observed = {}

    def generate(**kwargs):
        value = _FakeTensor([1, 2, 3, 42, 99])
        observed["generated"] = weakref.ref(value)
        return value

    result = _engine_with_model(service_module, tmp_path, generate).predict(_request("predict-success-001"))
    assert result["status"] == "predicted"
    assert result["metrics"]["phase_ms"]["total_ms"] >= 0
    assert result["metrics"]["tokens"] == {"input_tokens": 3, "output_tokens": 2}
    assert result["elapsed_seconds"] == result["metrics"]["phase_ms"]["total_ms"] / 1000
    folder = Path(result["output_directory"])
    assert json.loads((folder / "input.json").read_text())["request_id"] == "predict-success-001"
    assert "wait briefly" in json.loads((folder / "input.json").read_text())["messages"][1]["content"][1]["text"]
    assert (folder / "formatted-prompt.txt").read_text() == "formatted prompt"
    assert json.loads((folder / "raw-output.json").read_text())["input_tokens"] == 3
    assert json.loads((folder / "outcome.json").read_text())["status"] == "predicted"
    assert observed["generated"]() is None


def test_predict_cancelled_during_generate_keeps_partial_output_and_metrics(
        service_module, tmp_path, monkeypatch):
    _install_predict_fakes(monkeypatch)
    engine = None

    def generate(**kwargs):
        engine.cancel("predict-cancel-001")
        assert kwargs["stopping_criteria"][0](None, None)
        return _FakeTensor([1, 2, 3, 99])

    engine = _engine_with_model(service_module, tmp_path, generate)
    result = engine.predict(_request("predict-cancel-001"))
    assert result["status"] == "cancelled"
    assert result["reason"] == "cancelled_during_generation"
    folder = Path(result["output_directory"])
    assert json.loads((folder / "raw-output.json").read_text())["cancel_requested"] is True
    assert json.loads((folder / "outcome.json").read_text())["metrics"]["phase_ms"]["generate_ms"] >= 0


def test_predict_error_keeps_auditable_input_and_metrics(service_module, tmp_path, monkeypatch):
    _install_predict_fakes(monkeypatch)

    def generate(**kwargs):
        raise RuntimeError("simulated generation failure")

    result = _engine_with_model(service_module, tmp_path, generate).predict(_request("predict-error-001"))
    assert result["status"] == "error"
    assert "simulated generation failure" in result["error"]
    assert result["metrics"]["phase_ms"]["generate_ms"] >= 0
    folder = Path(result["output_directory"])
    assert (folder / "input.json").is_file()
    assert (folder / "error.log").is_file()
    assert json.loads((folder / "outcome.json").read_text())["status"] == "error"


def test_cleanup_metric_failure_does_not_erase_prediction(service_module, tmp_path, monkeypatch):
    class CleanupFailsCuda:
        @staticmethod
        def is_available(): return True
        @staticmethod
        def synchronize(): pass
        @staticmethod
        def reset_peak_memory_stats(): pass
        @staticmethod
        def empty_cache(): raise RuntimeError("cleanup unavailable")
        @staticmethod
        def mem_get_info(): return (9, 12)
        @staticmethod
        def memory_allocated(): return 2
        @staticmethod
        def memory_reserved(): return 3
        @staticmethod
        def max_memory_allocated(): return 4
        @staticmethod
        def max_memory_reserved(): return 5
    _install_predict_fakes(monkeypatch, CleanupFailsCuda())

    result = _engine_with_model(
        service_module, tmp_path, lambda **kwargs: _FakeTensor([1, 2, 3, 99])
    ).predict(_request("predict-cleanup-001"))
    assert result["status"] == "predicted"
    assert "cleanup unavailable" in result["metrics"]["memory"]["cleanup_error"]
    assert result["peak_reserved_bytes"] == 5
