"""Loopback transport for the official htmlsniffer -> FileService protocol."""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class BrowserIngress:
    def __init__(self, service, port=5328):
        self.service = service
        self.active = False
        self.counts = {"html": 0, "element": 0, "errors": 0, "rejected_origin": 0}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass  # Never log page content/URLs.

            def allowed_origin(self):
                origin = self.headers.get("Origin", "")
                return bool(re.fullmatch(r"chrome-extension://[a-p]{32}", origin))

            def respond(self, status, message):
                self.send_response(status)
                if self.allowed_origin():
                    self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
                    self.send_header("Vary", "Origin")
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"message": message}).encode())

            def do_OPTIONS(self):
                if not self.allowed_origin():
                    owner.counts["rejected_origin"] += 1
                    return self.respond(403, "Extension origin required")
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
                self.send_header("Access-Control-Allow-Methods", "POST")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()

            def do_POST(self):
                self.connection.settimeout(5)
                if not self.allowed_origin():
                    return self.respond(403, "Extension origin required")
                if not owner.active:
                    return self.respond(409, "No active recording")
                routes = {"/api/browser/append_html": "html", "/api/browser/append_element": "element"}
                kind = routes.get(self.path)
                if not kind:
                    return self.respond(404, "Unknown endpoint")
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 8 * 1024 * 1024:
                        return self.respond(413, "Payload size invalid")
                    if self.headers.get_content_type() != "application/json":
                        return self.respond(415, "JSON required")
                    payload = json.loads(self.rfile.read(length))
                    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
                        raise TypeError("Invalid browser payload")
                    if kind == "html":
                        status, message = owner.service.append_browser_html(payload)
                    else:
                        status, message = owner.service.append_browser_element(payload["data"])
                    from core.constants import SUCCEED
                    if status != SUCCEED:
                        owner.counts["errors"] += 1
                        return self.respond(422, "Official writer rejected payload")
                    owner.counts[kind] += 1
                    self.respond(200, message)
                except (ValueError, OSError, TypeError):
                    owner.counts["errors"] += 1
                    self.respond(400, "Invalid browser payload")

        self.server = HTTPServer(("127.0.0.1", port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.active = False
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(6)

    def receipt(self):
        return {"status": "received" if self.counts["html"] else "no_html_received",
                "counts": dict(self.counts), "endpoint": "http://localhost:5328",
                "writer": "official FileService", "extension": "official htmlsniffer 1.1"}
