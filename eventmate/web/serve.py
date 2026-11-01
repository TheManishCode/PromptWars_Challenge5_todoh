"""Serving the app (docs/HOSTING.md). Flask's built-in server is for one person on this machine; anything reachable from another machine needs waitress.

The built-in server has no idle timeout (1,500 half-open connections were held open indefinitely in the pentest, F-002), no connection cap and no body
limit before the app sees it. waitress (ZPL-2.1, pure Python, optional: ``pip install eventmate[serve]``) adds all three. This module only chooses and
configures; nothing here is on by default for a loopback bind.
"""

from __future__ import annotations

from typing import Any, Optional

# 6 MB: the largest upload route is audio (5 MB) plus a multipart envelope; every other route is held to 64 KB by the app itself
MAX_REQUEST_BYTES = 6 * 1024 * 1024
DEFAULTS = dict(threads=8, channel_timeout=30, connection_limit=200, cleanup_interval=5, max_request_body_size=MAX_REQUEST_BYTES, ident="to;doh")


class ServerUnavailable(RuntimeError):
    """waitress is not installed (or a server was requested that does not exist)."""


def waitress_installed() -> bool:
    try:
        import waitress  # noqa: F401
    except ImportError:
        return False
    return True


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
        make_waitress(app, host, port, **overrides).run()
    else:
        app.run(host=host, port=port, threaded=True, debug=False, use_reloader=False)
    return kind
