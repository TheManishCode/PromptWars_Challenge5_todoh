"""The built-in server with two limits the development server lacks (red-team finding: 300 half-open connections stayed open for ever).

An idle connection is closed after ``IDLE_TIMEOUT_S`` without data, and no more than ``MAX_CONNECTIONS`` are served at once; a connection over the cap is
closed at once. This does not stop a client that trickles one byte just inside the timeout; a reverse proxy with its own header and body timeouts is
still needed in front of anything exposed (docs/HOSTING.md).
"""

from __future__ import annotations

import os
import signal
import threading
import time

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
        self._active = 0
        self._active_lock = threading.Lock()

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.refused += 1
            self.shutdown_request(request)  # over the cap: closed without a reply, which costs the server no thread
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        with self._active_lock:
            self._active += 1
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._active_lock:
                self._active -= 1
            self._slots.release()

    @property
    def active(self) -> int:
        with self._active_lock:
            return self._active

    def stop(self) -> None:
        """Stop accepting: the serving loop ends and the listening socket closes, so a new connection is refused at once. Requests already running are not touched."""
        self.shutdown()
        self.server_close()

    def drain(self, timeout_s: float) -> bool:
        """Wait for running requests to finish. True if none are left, False if the time ran out (they are then abandoned: their threads are daemons)."""
        deadline = time.monotonic() + timeout_s
        while self.active and time.monotonic() < deadline:
            time.sleep(0.05)
        return self.active == 0

    def serve_until_signalled(self, drain_s: float = 20.0, install: bool = True) -> bool:
        """Serve until SIGTERM or SIGINT, then stop accepting, let running requests finish for up to ``drain_s`` seconds and return whether they all did.
        What a request has begun writing to disk is finished or not begun at all (plan versions are written create-only), so an orderly stop loses nothing."""
        if install:
            def on_signal(signum, _frame):
                threading.Thread(target=self.shutdown, daemon=True).start()  # shutdown() waits for the loop, which this very thread is running

            signal.signal(signal.SIGTERM, on_signal)
            signal.signal(signal.SIGINT, on_signal)
        try:
            self.serve_forever()
        finally:
            self.server_close()
        return self.drain(drain_s)


def make_bounded_server(app, host: str, port: int, max_connections=None, idle_timeout_s=None) -> BoundedServer:
    return BoundedServer(host, port, app, max_connections or _int_env("EVENTMATE_MAX_CONNECTIONS", MAX_CONNECTIONS, 1, 10000),
                         idle_timeout_s or _int_env("EVENTMATE_IDLE_TIMEOUT_S", IDLE_TIMEOUT_S, 1, 600))
