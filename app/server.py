"""Dependency-free local web dashboard for the Sentinel hackathon demo."""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .session import DemoSession

HOST = "127.0.0.1"
PORT = 8000
STATIC_DIR = Path(__file__).parent / "static"
SESSION = DemoSession()


class SentinelHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/state":
            self._json(HTTPStatus.OK, SESSION.snapshot())
            return
        if path in {"/", "/index.html"}:
            self._file("index.html", "text/html; charset=utf-8")
            return
        if path == "/app.js":
            self._file("app.js", "application/javascript; charset=utf-8")
            return
        if path == "/styles.css":
            self._file("styles.css", "text/css; charset=utf-8")
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "Route not found."})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            payload = self._body()
            if path == "/api/reset":
                state = SESSION.reset()
            elif path == "/api/investigate":
                state = SESSION.investigate()
            elif path == "/api/recover":
                state = SESSION.recover()
            elif path == "/api/request-scaling":
                state = SESSION.request_scaling(int(payload.get("workers", 1)))
            elif path == "/api/approval":
                state = SESSION.decide_approval(bool(payload.get("approved")))
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Route not found."})
                return
            self._json(HTTPStatus.OK, state)
        except (KeyError, TypeError, ValueError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def log_message(self, _format: str, *_args: object) -> None:
        """Keep the demo terminal intentionally quiet."""

    def _body(self) -> dict[str, object]:
        size = int(self.headers.get("Content-Length", "0"))
        if size == 0:
            return {}
        return json.loads(self.rfile.read(size).decode("utf-8"))

    def _file(self, name: str, content_type: str) -> None:
        body = (STATIC_DIR / name).read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: HTTPStatus, body: dict[str, object]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def run(host: str = HOST, port: int = PORT) -> None:
    server = ThreadingHTTPServer((host, port), SentinelHandler)
    print(f"Sentinel dashboard: http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run()
