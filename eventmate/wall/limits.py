"""In-memory sliding-window limits for the wall. Nothing here is written to disk: an address stays personal data even when hashed (docs/WALL_PRIVACY.md),
so the limiter forgets it on restart and never stores it with a mark."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from typing import Callable, Deque

MAX_KEYS = 20_000  # bounded: a flood of distinct addresses cannot grow this without limit; the oldest keys are dropped first


class Window:
    """At most ``limit`` events per ``seconds`` for each key."""

    def __init__(self, limit: int, seconds: float, clock: Callable[[], float] = time.monotonic):
        self.limit, self.seconds, self.clock = limit, seconds, clock
        self._events: "OrderedDict[str, Deque[float]]" = OrderedDict()
        self._lock = threading.Lock()

    def _trim(self, q: Deque[float], now: float) -> None:
        while q and now - q[0] > self.seconds:
            q.popleft()

    def allow(self, key: str) -> bool:
        """Count one event if there is room. False means over the limit (and the event is not counted)."""
        with self._lock:
            now = self.clock()
            q = self._events.get(key)
            if q is None:
                q = self._events[key] = deque()
                while len(self._events) > MAX_KEYS:
                    self._events.popitem(last=False)
            else:
                self._events.move_to_end(key)
            self._trim(q, now)
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def peek(self, key: str) -> bool:
        """Is there room, without counting."""
        with self._lock:
            q = self._events.get(key)
            if q is None:
                return True
            self._trim(q, self.clock())
            return len(q) < self.limit

    def add(self, key: str) -> None:
        """Count an event unconditionally (used for failures, which are counted after the fact)."""
        with self._lock:
            now = self.clock()
            q = self._events.setdefault(key, deque())
            self._trim(q, now)
            q.append(now)
            while len(self._events) > MAX_KEYS:
                self._events.popitem(last=False)


class WallLimits:
    """The wall's limits, one object so tests can swap the clock and the numbers (defaults from docs/WALL.md)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, bootstrap_per_ip_hour: int = 30, bootstrap_global_hour: int = 2000, submit_per_ip_hour: int = 5,
                 recover_fail_per_ip_hour: int = 10, recover_fail_global_hour: int = 100, writes_per_ip_minute: int = 60):
        hour = 3600.0
        self.bootstrap_ip = Window(bootstrap_per_ip_hour, hour, clock)
        self.bootstrap_all = Window(bootstrap_global_hour, hour, clock)
        self.submit_ip = Window(submit_per_ip_hour, hour, clock)
        self.recover_fail_ip = Window(recover_fail_per_ip_hour, hour, clock)
        self.recover_fail_all = Window(recover_fail_global_hour, hour, clock)
        self.writes_ip = Window(writes_per_ip_minute, 60.0, clock)
