"""The built-in server with two limits the development server lacks (red-team finding: 300 half-open connections stayed open for ever).

An idle connection is closed after ``IDLE_TIMEOUT_S`` without data, and no more than ``MAX_CONNECTIONS`` are served at once; a connection over the cap is
closed at once. This does not stop a client that trickles one byte just inside the timeout; a reverse proxy with its own header and body timeouts is
still needed in front of anything exposed (docs/HOSTING.md).
"""

from __future__ import annotations

import os
import threading

from werkzeug.serving import ThreadedWSGIServer, WSGIRequestHandler

IDLE_TIMEOUT_S = 30
MAX_CONNECTIONS = 128


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(os.environ.get(name, default))))
    except ValueError:
        return default


class _Handler(WSGIRequestHandler):
    timeout = IDLE_TIMEOUT_S


class BoundedServer(ThreadedWSGIServer):
    def __init__(self, host, port, app, max_connections: int, idle_timeout_s: float):
        handler = type("Handler", (_Handler,), {"timeout": idle_timeout_s})
        super().__init__(host, port, app, handler)
        self._slots = threading.BoundedSemaphore(max_connections)
        self.refused = 0

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.refused += 1
            self.shutdown_request(request)  # over the cap: closed without a reply, which costs the server no thread
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def make_bounded_server(app, host: str, port: int, max_connections=None, idle_timeout_s=None) -> BoundedServer:
    return BoundedServer(host, port, app, max_connections or _int_env("EVENTMATE_MAX_CONNECTIONS", MAX_CONNECTIONS, 1, 10000),
                         idle_timeout_s or _int_env("EVENTMATE_IDLE_TIMEOUT_S", IDLE_TIMEOUT_S, 1, 600))
