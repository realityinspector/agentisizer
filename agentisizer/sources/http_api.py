"""
Localhost HTTP source.

For anything that would rather make a request than write a file — a Claude
Code hook, a CI job, a webhook relay, another process on the box.

    curl -s localhost:8912/event -d '{"text": "all tests passed"}'
    curl -s localhost:8912/state

Stdlib http.server on purpose. This handles a handful of requests a second
from processes on the same machine; a framework would be a dependency and a
build step in exchange for nothing.

Binds to 127.0.0.1 only. This accepts unauthenticated writes by design, on
the assumption that anything already running as you on your laptop could
make noise anyway — but that assumption breaks the moment it is exposed, so
it never binds to a routable address.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..events import Event


DEFAULT_PORT = 8912


class _Handler(BaseHTTPRequestHandler):
    emit = None          # set on the class by HttpSource
    snapshot = None

    def log_message(self, *args):
        pass             # the whole point is to be quiet

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") in ("/state", "/health", ""):
            snap = _Handler.snapshot() if _Handler.snapshot else {}
            self._json(200, {"ok": True, **snap})
        else:
            self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if self.path.rstrip("/") != "/event":
            self._json(404, {"ok": False, "error": "post to /event"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode(errors="replace") if length else ""

        # Accept JSON, but take a bare string too — `curl -d 'it worked'`
        # should do the obvious thing.
        try:
            data = json.loads(raw) if raw.strip().startswith("{") else {"text": raw}
        except json.JSONDecodeError:
            data = {"text": raw}

        if not (data.get("text") or "").strip():
            self._json(400, {"ok": False, "error": "need a 'text' field"})
            return

        data.setdefault("source", "http")
        try:
            event = Event.from_dict(data)
        except (TypeError, ValueError) as e:
            self._json(400, {"ok": False, "error": str(e)})
            return

        _Handler.emit(event)
        self._json(202, {"ok": True, "id": event.id})


class HttpSource:
    def __init__(self, emit, port: int = DEFAULT_PORT, snapshot=None):
        self.port = port
        _Handler.emit = emit
        _Handler.snapshot = snapshot
        self._server: ThreadingHTTPServer | None = None

    def start(self) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)
        threading.Thread(
            target=self._server.serve_forever, name="http", daemon=True
        ).start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()

    def describe(self) -> str:
        return f"http on 127.0.0.1:{self.port}"
