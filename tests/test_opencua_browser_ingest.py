import importlib.util
import json
import sys
import types
import urllib.error
import urllib.request
from pathlib import Path

import pytest


@pytest.fixture
def ingress(monkeypatch):
    spec = importlib.util.spec_from_file_location("browser_ingest", Path(__file__).parents[1] / "scripts/opencua/browser_ingest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    constants = types.ModuleType("core.constants")
    constants.SUCCEED = "ok"
    monkeypatch.setitem(sys.modules, "core.constants", constants)

    class Writer:
        def __init__(self):
            self.calls = []
        def append_browser_html(self, payload):
            self.calls.append(("html", payload))
            return "ok", "saved"
        def append_browser_element(self, payload):
            self.calls.append(("element", payload))
            return "ok", "saved"
    bridge = module.BrowserIngress(Writer(), port=0)
    bridge.start()
    yield bridge
    bridge.close()


def post(bridge, path="append_html", origin="chrome-extension://" + "a" * 32):
    data = {"data": {"html": "<p>Example</p>", "dom": "[]", "url": "https://example.com"}, "pageAxTree": {}}
    request = urllib.request.Request(f"http://127.0.0.1:{bridge.server.server_port}/api/browser/{path}",
        data=json.dumps(data).encode(), headers={"Origin": origin, "Content-Type": "application/json"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request) as response:
        return response.status


def test_official_writer_payloads_pass_through_without_repackaging(ingress):
    ingress.active = True
    assert post(ingress) == 200
    assert post(ingress, "append_element") == 200
    assert ingress.service.calls[0][1]["pageAxTree"] == {}
    assert "data" not in ingress.service.calls[1][1]
    assert ingress.receipt()["status"] == "received"


def test_no_recording_and_web_origin_never_write(ingress):
    with pytest.raises(urllib.error.HTTPError) as error:
        post(ingress)
    assert error.value.code == 409
    ingress.active = True
    with pytest.raises(urllib.error.HTTPError) as error:
        post(ingress, origin="https://example.com")
    assert error.value.code == 403
    assert ingress.service.calls == []
    assert ingress.receipt()["status"] == "no_html_received"
