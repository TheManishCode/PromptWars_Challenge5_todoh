"""Serving the app (docs/HOSTING.md). Flask's built-in server is for one person on this machine; anything reachable from another machine needs waitress.

The plain built-in server has no idle timeout (1,500 half-open connections were held open indefinitely in the pentest, F-002) and no connection cap; the
bounded variant in ``web/server.py`` adds both for loopback use. It still cannot stop a client that trickles bytes just inside the timeout, and it has no body
limit before the app sees it. waitress (ZPL-2.1, pure Python, optional: ``pip install eventmate[serve]``) adds all three. This module only chooses and
configures; nothing here is on by default for a loopback bind.
"""

from __future__ import annotations

import sys
import time
from typing import Any

# 6 MB: the largest upload route is audio (5 MB) plus a multipart envelope; every other route is held to 64 KB by the app itself
MAX_REQUEST_BYTES = 6 * 1024 * 1024
DEFAULTS = dict(threads=8, channel_timeout=30, connection_limit=200, cleanup_interval=5, max_request_body_size=MAX_REQUEST_BYTES, ident="to;doh",
                clear_untrusted_proxy_headers=False)  # waitress otherwise DROPS X-Forwarded-For/-Proto when it has no trusted_proxy of its own: behind a proxy every visitor would then look like the proxy (one shared limit for all) and HTTPS would never be seen. The app believes those headers only from the peers in EVENTMATE_TRUSTED_PROXY (web/limits.py), so passing them through is safe.


class ServerUnavailable(RuntimeError):
    """waitress is not installed (or a server was requested that does not exist)."""


def waitress_installed() -> bool:
    import importlib.util

    return importlib.util.find_spec("waitress") is not None


def choose(requested: str, remote: bool) -> str:
    """'waitress' or 'dev'. ``auto`` uses waitress when it is installed; a non-loopback bind never gets the built-in server."""
    if requested not in ("auto", "dev", "waitress"):
        raise ServerUnavailable(f"unknown server {requested!r}: use auto, dev or waitress")
    have = waitress_installed()
    if requested == "waitress" and not have:
        raise ServerUnavailable("waitress is not installed: pip install 'eventmate[serve]'")
    if remote and (requested == "dev" or not have):
        raise ServerUnavailable("a bind that other machines can reach needs waitress (pip install 'eventmate[serve]'): the built-in server has no idle timeout or connection limit")
    return "waitress" if requested == "waitress" or (requested == "auto" and have) else "dev"


def make_waitress(app, host: str, port: int, **overrides: Any):
    from waitress.server import create_server

    return create_server(app, host=host, port=port, **{**DEFAULTS, **overrides})


def run(app, host: str, port: int, requested: str = "auto", remote: bool = False, **overrides: Any) -> str:
    """Serve until interrupted. Returns the name of the server used."""
    kind = choose(requested, remote)
    if kind == "waitress":
        drain_s = float(overrides.pop("drain_s", 20.0))
        server = make_waitress(app, host, port, **overrides)
        import signal
        import threading

        from waitress import wasyncore

        def pull(fn):
            """Ask the loop's own thread to run ``fn``. If the wake-up pipe is already closed (EBADF) the loop has finished closing everything, which is exactly what the stop wanted:
            nothing is left to ask. Seen about one stop in four under load as a traceback from the helper thread."""
            try:
                server.trigger.pull_trigger(fn)
            except OSError:
                pass

        def busy(ch):  # a request being handled, queued, or a response not yet fully sent
            return bool(getattr(ch, "running_tasks", False) or getattr(ch, "requests", None) or getattr(ch, "total_outbufs_len", 0))

        def drain():
            # Only the listening socket is closed: the trigger the worker threads use to hand back finished responses must stay open, or the answer to a request that is
            # still running is lost. Once no connection is busy (idle keep-alive ones do not count) or ``drain_s`` is over, everything is closed and run() returns.
            deadline = time.monotonic() + drain_s
            while time.monotonic() < deadline:
                chans = [d for d in list(server._map.values()) if isinstance(d, server.channel_class)]
                if not any(busy(c) for c in chans):
                    break
                time.sleep(0.05)
            pull(lambda: wasyncore.close_all(server._map))  # in the loop's own thread, like the listener: closing under a running select() is EBADF

        def stop(_signum, _frame):
            # The listener is closed from inside the loop (the trigger runs the function in the loop's own thread): closing it here would pull a descriptor out from under a
            # select() that Python restarts after the signal, which fails the whole loop. Not server.close(): that also closes the trigger.
            pull(lambda: wasyncore.dispatcher.close(server))
            threading.Thread(target=drain, daemon=True).start()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        server.run()  # returns when the listener is closed and no connection is left
        dispatcher = getattr(server, "task_dispatcher", None)
        if dispatcher is not None:
            dispatcher.shutdown(cancel_pending=False, timeout=drain_s)
        clean = not (dispatcher.threads if dispatcher is not None else [])
        print("stopped" if clean else "stopped; some requests were still running and were abandoned", file=sys.stderr)
    else:
        from eventmate.web.server import make_bounded_server  # the built-in server with an idle timeout and a connection cap (thread B's R-6)

        clean = make_bounded_server(app, host, port).serve_until_signalled()  # SIGTERM or SIGINT: stop accepting, let running requests finish, then exit
        print("stopped" if clean else "stopped; some requests were still running and were abandoned", file=sys.stderr)
    return kind
