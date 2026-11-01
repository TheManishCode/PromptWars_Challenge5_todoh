"""Abuse limits for the web layer (Phase 5.1): who is calling, how often, and how much of a shared resource one caller may hold.

Three small pieces, none of which needs a login:
* ``client_address``: the caller's address. A forwarded header is read only when the connection comes from a configured trusted proxy
  (``EVENTMATE_TRUSTED_PROXY``, addresses or networks), and then the address added by the nearest proxy we do not trust is used, so a client cannot
  choose its own address by writing the header. Without that setting every forwarded header is ignored.
* ``WindowLimiter``: calls per minute per key, with a bound on how many keys are remembered (the earlier version never forgot an address).
* ``FairShare``: a shared pool where one workspace may hold only part of the slots, so one visitor cannot fill it.
"""

from __future__ import annotations

import ipaddress
import os
import threading
import time
from collections import deque
from typing import Callable, Iterable

MAX_KEYS = 20_000
_OVERFLOW = "~overflow"  # every key beyond MAX_KEYS shares this bucket, so memory stays bounded and the flood limits itself


def _networks(spec: str) -> list[ipaddress._BaseNetwork]:
    out = []
    for item in (s.strip() for s in spec.split(",")):
        if not item:
            continue
        try:
            out.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            continue  # a malformed entry trusts nothing rather than everything
    return out


def trusted_proxies(environ=None) -> list[ipaddress._BaseNetwork]:
    return _networks((os.environ if environ is None else environ).get("EVENTMATE_TRUSTED_PROXY", ""))


def _ip(text: str) -> ipaddress._BaseAddress | None:
    try:
        return ipaddress.ip_address(text.strip().strip("[]"))
    except ValueError:
        return None


def client_address(remote_addr: str | None, forwarded_for: str | None, proxies: Iterable[ipaddress._BaseNetwork]) -> str:
    """The caller's address. ``forwarded_for`` is believed only when ``remote_addr`` is a trusted proxy; the entries are read from the right, and the first
    one that is not itself a trusted proxy is the client. Anything unreadable falls back to the connection's own address."""
    proxies = list(proxies)
    peer = _ip(remote_addr or "")
    if peer is None:
        return "?"
    if not proxies or not any(peer in n for n in proxies) or not forwarded_for:
        return str(peer)
    for hop in reversed([h for h in forwarded_for.split(",") if h.strip()]):
        ip = _ip(hop)
        if ip is None:
            return str(peer)  # a hop we cannot read means the header cannot be trusted
        if not any(ip in n for n in proxies):
            return str(ip)
    return str(peer)


def limiter_key(address: str) -> str:
    """The key a per-caller limit is counted under. An IPv6 customer owns a whole /64 (2**64 addresses) and can change address on every request, so counting per
    address gives them no limit at all (F-015); an IPv6 address is therefore counted as its /64. An IPv4 address, or an IPv4-mapped one, is counted as itself."""
    ip = _ip(address)
    if ip is None:
        return address
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


class WindowLimiter:
    """At most ``limit`` calls per ``window_s`` for each key. Keys with no recent calls are dropped, and no more than ``MAX_KEYS`` are tracked."""

    def __init__(self, limit: int, window_s: float = 60.0, clock: Callable[[], float] = time.monotonic, max_keys: int = MAX_KEYS):
        self.limit, self.window, self._clock, self._max = limit, window_s, clock, max_keys
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._swept = clock()

    def allow(self, key: str) -> bool:
        with self._lock:
            now = self._clock()
            if now - self._swept > self.window:
                self._sweep(now)
            if key not in self._hits and len(self._hits) >= self._max:
                key = _OVERFLOW
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def _sweep(self, now: float) -> None:
        for k in [k for k, q in self._hits.items() if not q or now - q[-1] > self.window]:
            del self._hits[k]
        self._swept = now

    def tracked(self) -> int:
        with self._lock:
            return len(self._hits)


class FairShare:
    """``total`` slots shared by everyone, of which one workspace may hold at most ``per_workspace``. Non-blocking: ``acquire`` says no instead of waiting."""

    def __init__(self, total: int, per_workspace: int):
        self.total, self.per = total, max(1, min(per_workspace, total))
        self._held: dict[str, int] = {}
        self._used = 0
        self._lock = threading.Lock()

    def acquire(self, workspace: str) -> str | None:
        """A reason for refusing ('busy' when the pool is full, 'share' when this workspace holds its share), or None when a slot was taken."""
        with self._lock:
            if self._used >= self.total:
                return "busy"
            if self._held.get(workspace, 0) >= self.per:
                return "share"
            self._used += 1
            self._held[workspace] = self._held.get(workspace, 0) + 1
            return None

    def release(self, workspace: str) -> None:
        with self._lock:
            if self._held.get(workspace, 0) > 0:
                self._held[workspace] -= 1
                self._used -= 1
                if not self._held[workspace]:
                    del self._held[workspace]

    def status(self) -> tuple[int, int]:
        with self._lock:
            return self._used, self.total
