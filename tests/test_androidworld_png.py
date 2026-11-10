import hashlib
import io
from email.message import Message
from types import SimpleNamespace
import numpy as np
from PIL import Image
import pytest
from trace2task.android_world_benchmark import WorldClient


def test_lossless_client_and_instance_check():
    pixels = np.random.default_rng(42).integers(0, 256, (47, 31, 3), dtype=np.uint8)
    out = io.BytesIO()
    Image.fromarray(pixels).save(out, format="PNG", compress_level=1)
    headers = Message()
    headers["Content-Type"] = "image/png"
    headers["X-Pixels-SHA256"] = hashlib.sha256(pixels.tobytes()).hexdigest()
    headers["X-Screenshot-Instance"] = "one"

    class Response(io.BytesIO):
        pass

    def response(*args, **kwargs):
        r = Response(out.getvalue())
        r.headers = headers
        return r

    c = WorldClient(5000, "test")
    c.opener = SimpleNamespace(open=response)
    assert np.array_equal(c.screenshot(expected_instance="one"), pixels)
    with pytest.raises(ValueError, match="instance"):
        c.screenshot(expected_instance="two")
    headers.replace_header("X-Pixels-SHA256", "wrong")
    with pytest.raises(ValueError, match="hash"):
        c.screenshot(expected_instance="one")
