"""Minimal localhost HTTP hook for agents: POST JSON → EventBus.publish."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from neurosync_pro.bus import EventBus


class _Handler(BaseHTTPRequestHandler):
    bus: "EventBus | None" = None

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in ("/v1/event", "/v1/event/"):
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return
        topic = body.get("topic", "agent.raw")
        payload = body.get("payload", body)
        if _Handler.bus is not None:
            _Handler.bus.publish(str(topic), payload)
        self.send_response(204)
        self.end_headers()


def start_agent_api(
    bus: "EventBus",
    host: str = "127.0.0.1",
    port: int = 8765,
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    _Handler.bus = bus
    server = ThreadingHTTPServer((host, port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def stop_agent_api(server: ThreadingHTTPServer) -> None:
    server.shutdown()
    server.server_close()
