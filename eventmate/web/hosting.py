"""Start-up checks for shared (hosted) mode (Phase 5.2). Local mode is the default and needs none of this.

Shared mode means people other than the owner can reach the server. It refuses to start unless every condition below holds; each failure is named so
the owner can fix it. It never starts "mostly safe". ``WORKSPACES_IMPLEMENTED`` says per-visitor workspaces exist: without them shared mode would show
one visitor's plans to the next, so a build without them refuses unconditionally.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Mapping, Optional, Sequence

from eventmate.web import limits

WORKSPACES_IMPLEMENTED = True  # per-visitor workspaces exist (eventmate/workspaces.py) and the two-workspace suite covers every plan-scoped route
MODES = ("local", "shared")
LOOPBACK_NAMES = {"127.0.0.1", "localhost", "[::1]", "::1"}
MIN_SECRET = 32


def mode(environ: Mapping[str, str]) -> str:
    """'local' unless EVENTMATE_MODE says 'shared'. An unknown value is an error, not a silent fallback to either."""
    raw = environ.get("EVENTMATE_MODE", "local").strip().lower() or "local"
    if raw not in MODES:
        raise ValueError(f"EVENTMATE_MODE must be one of {', '.join(MODES)}, not {raw[:20]!r}")
    return raw


def shared_mode_problems(environ: Mapping[str, str], allowed_hosts: Sequence[str], env_file: Optional[Path] = None, workspaces_implemented: Optional[bool] = None) -> List[str]:
    """What stops shared mode from starting. An empty list means it may start."""
    problems: List[str] = []
    if not (WORKSPACES_IMPLEMENTED if workspaces_implemented is None else workspaces_implemented):
        problems.append("shared mode needs a separate workspace for each visitor, and this build does not have them yet: visitors would see each other's plans")
    if not any(h.strip().lower() not in LOOPBACK_NAMES for h in allowed_hosts):
        problems.append("name the host you serve under with --allowed-host (loopback names alone are not a hosted setup)")
    data_dir = environ.get("EVENTMATE_DATA_DIR", "")
    if not data_dir or not Path(data_dir).is_absolute() or not Path(data_dir).is_dir():
        problems.append("set EVENTMATE_DATA_DIR to an existing absolute directory on a persistent disk: every visitor's workspace is stored under it")
    if len(environ.get("EVENTMATE_SECRET", "")) < MIN_SECRET:
        problems.append(f"set EVENTMATE_SECRET to at least {MIN_SECRET} random characters (it derives each workspace's CSRF value)")
    if not limits.trusted_proxies(environ):
        problems.append("set EVENTMATE_TRUSTED_PROXY to the address of the reverse proxy that terminates TLS (client addresses and HTTPS are read only from it)")
    if not environ.get("EVENTMATE_PUBLIC_URL", "").startswith("https://"):
        problems.append("set EVENTMATE_PUBLIC_URL to the https:// address people will use; plain http is not served in shared mode")
    if len(environ.get("EVENTMATE_OWNER_TOKEN", "")) < 16:
        problems.append("set EVENTMATE_OWNER_TOKEN (at least 16 characters) so the status route is yours alone")
    if env_file is not None and env_file.exists():
        problems.append(f"a .env file exists at {env_file}: in shared mode secrets come from the real environment only; remove the file")
    return problems


def is_https(scheme: str, remote_addr: Optional[str], forwarded_proto: Optional[str], proxies) -> bool:
    """True when the request arrived over TLS: directly, or through a trusted proxy that says so. A forwarded header from anyone else is ignored."""
    if scheme == "https":
        return True
    peer = limits._ip(remote_addr or "")
    return bool(peer is not None and any(peer in n for n in proxies) and (forwarded_proto or "").strip().lower() == "https")
