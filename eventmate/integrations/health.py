"""What the outside services are doing right now, in words a person can act on (WP5).

Places, weather, the OpenStreetMap lookup, web search and flights are free services with limits of their own. When one is used up, slow or down, the page should say so instead of showing an
empty list as if nothing exists. Each call is recorded here as a success or as one of a few kinds of failure (never the service's own words, which can carry a key or a query), and ``snapshot``
turns that into a state: ``ok``, ``quota_used`` (the service said its allowance is spent), ``degraded`` (it failed since it last worked), ``not_configured`` (no key: not an outage) or ``unknown``
(nothing has been asked yet). Read-only: nothing here changes what a service is asked.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

FAILURE_KINDS = ("not_configured", "quota", "unreachable", "error")
QUOTA_WORDS = ("quota", "rate limit", "rate-limit", "too many", "429", "used up", "limit reached", "exceeded")
UNREACHABLE_WORDS = ("timed out", "timeout", "could not reach", "unreachable", "connection", "temporarily", "502", "503", "504", "unavailable")
QUOTA_FOR_S = 3600.0  # a "quota used" report is believed this long, then the service is simply asked again

LABELS = {"places": "Google Places (finding businesses online)", "overpass": "OpenStreetMap lookup (finding venues)", "weather": "OpenWeather (the forecast)", "search": "Web search (finding a business's own page)",
          "flights": "Flight prices", "model": "The AI model"}
# What the owner needs to know about each service's free allowance; each line is from the provider's own page, dated in docs/INTEGRATIONS.md.
FREE_TIER = {
    "places": "Google gives a monthly free allowance per billing SKU (5,000 for Text Search Pro and Place Details Pro, unlimited for IDs-only calls); only the place id may be stored.",
    "overpass": "The public OpenStreetMap server asks for fewer than 10,000 queries and 1 GB a day, and about 100 a day for regular use.",
    "weather": "OpenWeather's free plan: 60 calls a minute and 1,000,000 a month, with the 5-day forecast.",
    "search": "Brave: a monthly credit of $5 (about 1,000 searches), prepaid credits after that; the fallback search has no key and no promise.",
    "flights": "Unofficial: it reads public Google Flights data and may break or be refused at any time.",
    "model": "Free model routes have per-minute and per-day limits that depend on credits bought; when they are used up the app answers without a model.",
}


def classify(exc: BaseException) -> str:
    """The kind of failure, from the exception's type and wording. The wording is looked at and thrown away."""
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if "notconfigured" in name or "not configured" in text or "not set" in text:
        return "not_configured"
    if any(w in text for w in QUOTA_WORDS):
        return "quota"
    if isinstance(exc, (OSError, TimeoutError)) or any(w in text for w in UNREACHABLE_WORDS):
        return "unreachable"
    return "error"


@dataclass
class _Service:
    ok_at: float | None = None
    failed_at: float | None = None
    kind: str = ""
    failures_since_ok: int = 0
    calls: int = 0
    failures: int = 0


@dataclass
class ServiceHealth:
    clock: Callable[[], float] = time.time
    _services: dict[str, _Service] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def ok(self, name: str) -> None:
        with self._lock:
            s = self._services.setdefault(name, _Service())
            s.ok_at, s.failures_since_ok, s.calls = self.clock(), 0, s.calls + 1

    def failed(self, name: str, kind: str) -> None:
        with self._lock:
            s = self._services.setdefault(name, _Service())
            s.failed_at, s.kind, s.failures_since_ok, s.calls, s.failures = self.clock(), kind if kind in FAILURE_KINDS else "error", s.failures_since_ok + 1, s.calls + 1, s.failures + 1

    def snapshot(self, configured: dict[str, bool] | None = None) -> list[dict]:
        configured = configured or {}
        out = []
        now = self.clock()
        with self._lock:
            for name, label in LABELS.items():
                s = self._services.get(name, _Service())
                if configured.get(name) is False and not s.ok_at:
                    state, note = "not_configured", "no key is set, so this is switched off, not broken"
                elif s.failed_at and (not s.ok_at or s.failed_at >= s.ok_at):
                    if s.kind == "quota" and now - s.failed_at < QUOTA_FOR_S:
                        state, note = "quota_used", "its free allowance is used up for now; what it would have found is missing, not absent"
                    elif s.kind == "not_configured":
                        state, note = "not_configured", "no key is set, so this is switched off, not broken"
                    else:
                        state, note = "degraded", {"unreachable": "it could not be reached on the last try; results from it may be missing", "quota": "it said it was over its limit a while ago; it is being asked again"}.get(s.kind, "it failed on the last try; results from it may be missing")
                elif s.ok_at:
                    state, note = "ok", "answering"
                else:
                    state, note = "unknown", "not used yet"
                out.append({"name": name, "label": label, "state": state, "note": note, "free_tier": FREE_TIER[name], "calls": s.calls, "failures": s.failures,
                            "last_ok_seconds_ago": None if s.ok_at is None else int(now - s.ok_at), "last_failure_seconds_ago": None if s.failed_at is None else int(now - s.failed_at)})
        return out


HEALTH = ServiceHealth()  # one for the process: the allowances belong to the keys, which every workspace shares


class Watched:
    """Wraps a service object: the listed methods record a success or a kind of failure and otherwise behave exactly as before (the exception is re-raised untouched)."""

    def __init__(self, inner, name: str, methods: tuple[str, ...], health: ServiceHealth = HEALTH):
        self._inner, self._name, self._methods, self._health = inner, name, frozenset(methods), health

    @property
    def __class__(self):  # an isinstance check against the wrapped service's class still holds
        return self._inner.__class__

    def __getattr__(self, attr):
        target = getattr(self._inner, attr)
        if attr not in self._methods or not callable(target):
            return target

        def watched(*args, **kwargs):
            try:
                result = target(*args, **kwargs)
            except BaseException as exc:
                if isinstance(exc, Exception):
                    self._health.failed(self._name, classify(exc))
                raise
            self._health.ok(self._name)
            return result

        return watched

    def __repr__(self) -> str:
        return repr(self._inner)
