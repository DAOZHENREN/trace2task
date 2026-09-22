"""Offline contract tests; no weight loading and no desktop input."""
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def preview():
    path = Path(__file__).parents[1] / "scripts/trained_model/preview.py"
    spec = importlib.util.spec_from_file_location("trained_preview", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checkpoint_hash_pinned_and_no_executor(preview):
    assert preview.CHECKPOINT_SHA == "4c07d912b881ccc69a28477aedf6c2b349f91965dc5dca89ebba8995987d02d3"
    text = Path(preview.__file__).read_text(encoding="utf-8")
    assert "WindowsMotorExecutor" not in text
    assert "weights_only=False" in text
    assert "set(saved) != set(trainable)" in text


def test_verify_rejects_wrong_checkpoint_before_import(preview, tmp_path):
    config = {"model_revision": preview.REVISION, "optimizer": {"optimizer_steps": 5970},
              "weight_sha256": "0" * 64, "source_manifest_sha256": "0" * 64,
              "processor_files_sha256": {}}
    (tmp_path / "frozen-launch.json").write_text(json.dumps(config))
    (tmp_path / "step-00005970.pt").write_bytes(b"untrusted")
    with pytest.raises(ValueError, match="Hash mismatch"):
        preview.verify(tmp_path)


def test_annotate_click_and_drag_use_frozen_coordinate_names(preview):
    Image = pytest.importorskip("PIL.Image", reason="Optional D-preview environment dependency")
    image = Image.new("RGB", (200, 100), "white")
    marked = preview.annotate(image, [
        {"skill": "click", "args": {"x": 1, "y": 1}},
        {"skill": "drag", "args": {"start_x": 0.1, "start_y": 0.5,
                                     "end_x": 0.8, "end_y": 0.5}},
    ])
    assert marked.getpixel((80, 50)) != image.getpixel((80, 50))
    assert marked.size == image.size


def test_idle_browser_connection_does_not_block_page_and_duplicate_bind(preview):
    pytest.importorskip("PIL.Image")
    import socket
    import threading
    import urllib.request

    # Select a free port, then exercise the real HTTP handler without loading weights.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = preview.create_server(port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with (
            socket.create_connection(("127.0.0.1", port)),
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as response,
        ):
            assert response.status == 200
            assert b"__TOKEN__" not in response.read()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
            assert json.load(response)["status"] == "loading"
        with pytest.raises(OSError):
            preview.create_server(port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
