"""Serialized loopback native105 worker; caller uses Trace2Task's hybrid chain."""

import argparse
import hmac
import json
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from trace2task.android_world_benchmark import native_plan
from trace2task.mai_swift_hybrid import MaiSwiftHybridPredictor, load_runtime

p = argparse.ArgumentParser()
p.add_argument("--release", required=True)
p.add_argument("--base", required=True)
p.add_argument("--bundle", required=True)
p.add_argument("--token-file", required=True)
p.add_argument("--output", required=True)
a = p.parse_args()
root = Path(a.output).resolve()
root.mkdir(parents=True, exist_ok=True)
token = Path(a.token_file).read_text().strip()
predictor = MaiSwiftHybridPredictor(load_runtime(a.release, a.base, a.bundle), mapper=native_plan)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, value):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        return hmac.compare_digest(self.headers.get("X-Trace2Task-Token", ""), token)

    def do_GET(self):
        if not self.authorized():
            return self.reply(401, {"error": "unauthorized"})
        self.reply(200, {"status": "ready", "model": "mai-swift-2b"})

    def do_POST(self):
        if not self.authorized():
            return self.reply(401, {"error": "unauthorized"})
        if self.path != "/predict":
            return self.reply(404, {"error": "unknown route"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 64_000_000:
                raise ValueError("Invalid request size")
            value = json.loads(self.rfile.read(length))
            request = value["request"]
            Path(request["audit_dir"]).resolve().relative_to(root)
            result = predictor(value["instruction"], **request)
            self.reply(200, result)
        except Exception as e:  # noqa: BLE001 - record worker errors without retrying actions
            traceback.print_exc()
            self.reply(500, {"error": str(e), "exception_type": type(e).__name__})


HTTPServer(("127.0.0.1", 8877), Handler).serve_forever()
