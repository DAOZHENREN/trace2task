import json
from io import BytesIO

import pytest

from trace2task import trained_model_client as client


def test_prediction_bridge_keeps_image_local_and_does_not_execute(monkeypatch):
    requests = []

    class Opener:
        page_requests = 0

        def open(self, request, timeout):
            if isinstance(request, str):
                if request.endswith("/health"):
                    return BytesIO(b'{"protocol":2,"status":"ready"}')
                return BytesIO(b"'X-Preview-Token':'" + b"a" * 32 + b"'")
            requests.append(request)
            return BytesIO(json.dumps({"status": "predicted", "executed": False}).encode())

    monkeypatch.setattr(client, "build_opener", lambda *args: Opener())
    monkeypatch.setattr(client.socket, "create_connection", lambda *args, **kwargs: BytesIO())
    result = client.predict_local("hello", image="aGVsbG8=")
    assert result["executed"] is False
    assert requests[0].full_url == "http://127.0.0.1:8767/predict"
    assert json.loads(requests[0].data) == {"task": "hello", "image": "aGVsbG8=", "capture": False}


def test_invalid_task_and_concurrent_prediction_rejected():
    with pytest.raises(ValueError):
        client.predict_local("")
    with client._lock, pytest.raises(RuntimeError, match="正在预测"):
        client.predict_local("hello")
