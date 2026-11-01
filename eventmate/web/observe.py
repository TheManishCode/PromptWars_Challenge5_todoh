"""What the operator can see (Phase 7.1): a request id on every answer, one structured access-log line per request, and counters.

An access-log line holds the request id, method, the route *template* (``/api/plans/<plan_id>/state``, never the path with its ids), the status, the time
taken and the opaque workspace id. It never holds a query string, a header, a cookie, a body, an address or a name, so the log is safe to keep and to share.
The counters say how many requests there were by status class and how long they took; they hold no content at all.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
from collections import Counter

REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
BUCKETS_MS = (50, 200, 1000, 5000)
access_log = logging.getLogger("eventmate.access")


def new_request_id(supplied: str | None = None) -> str:
    """A caller's own id is kept (so a proxy's id threads through) only if it is a plain token; anything else, which could be used to forge a log line, is replaced."""
    return supplied if supplied and REQUEST_ID.fullmatch(supplied) else secrets.token_hex(8)


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._by_class: Counter = Counter()
        self._latency: Counter = Counter()
        self.in_flight = 0
        self.total = 0
        self._routes: Counter = Counter()  # (route template, status class): a bounded set, the templates are fixed by the code
        self._hist: Counter = Counter()  # cumulative-bucket counts for the Prometheus histogram
        self._sum_s = 0.0

    def begin(self) -> None:
        with self._lock:
            self.in_flight += 1

    def end(self, status: int, ms: float, route: str | None = None) -> None:
        with self._lock:
            self._sum_s += ms / 1000
            for b in PROM_BUCKETS_S:
                if ms / 1000 <= b:
                    self._hist[b] += 1
            if len(self._routes) < 600 or (route or "unmatched", f"{status // 100}xx") in self._routes:
                self._routes[(route or "unmatched", f"{status // 100}xx")] += 1
            self.in_flight = max(0, self.in_flight - 1)
            self.total += 1
            self._by_class[f"{status // 100}xx"] += 1
            self._latency[next((f"<{b}ms" for b in BUCKETS_MS if ms < b), f">={BUCKETS_MS[-1]}ms")] += 1

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {"total": self.total, "in_flight": self.in_flight, "by_status_class": dict(sorted(self._by_class.items())),
                    "latency": {k: self._latency.get(k, 0) for k in [f"<{b}ms" for b in BUCKETS_MS] + [f">={BUCKETS_MS[-1]}ms"]}}


PROM_BUCKETS_S = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)


def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def prometheus(metrics: Metrics, gauges: dict[str, float], model_calls: dict[str, int]) -> str:
    """The counters in the Prometheus text format (version 0.0.4): totals by route template and status class, a latency histogram, and the gauges the server hands in.
    Labels are route templates and status classes only: no ids, no addresses, no content."""
    with metrics._lock:
        routes, hist, total, ssum, inflight = dict(metrics._routes), dict(metrics._hist), metrics.total, metrics._sum_s, metrics.in_flight
    out = ["# HELP eventmate_http_requests_total Requests by route template and status class.", "# TYPE eventmate_http_requests_total counter"]
    for (route, klass), n in sorted(routes.items()):
        out.append(f'eventmate_http_requests_total{{route="{_label(route)}",status="{klass}"}} {n}')
    out += ["# HELP eventmate_http_request_duration_seconds Request latency.", "# TYPE eventmate_http_request_duration_seconds histogram"]
    for b in PROM_BUCKETS_S:
        out.append(f'eventmate_http_request_duration_seconds_bucket{{le="{b}"}} {hist.get(b, 0)}')
    out += [f'eventmate_http_request_duration_seconds_bucket{{le="+Inf"}} {total}', f"eventmate_http_request_duration_seconds_sum {ssum:.6f}", f"eventmate_http_request_duration_seconds_count {total}",
            "# TYPE eventmate_http_requests_in_flight gauge", f"eventmate_http_requests_in_flight {inflight}"]
    for name, value in sorted(gauges.items()):
        out += [f"# TYPE eventmate_{name} gauge", f"eventmate_{name} {value}"]
    for name, value in sorted(model_calls.items()):
        out += [f"# TYPE eventmate_model_{name} counter", f"eventmate_model_{name} {value}"]
    return "\n".join(out) + "\n"


def log_request(rid: str, method: str, route: str | None, status: int, ms: float, workspace: str | None) -> None:
    access_log.info(json.dumps({"rid": rid, "method": method, "route": route or "unmatched", "status": status, "ms": round(ms, 1), "ws": workspace}, separators=(",", ":")))


def configure_access_log(destination: str) -> None:
    """``stderr`` writes one JSON object per line to standard error (for the operator's log collector); ``off`` writes nothing. Idempotent."""
    for handler in list(access_log.handlers):
        access_log.removeHandler(handler)
    access_log.propagate = False
    if destination == "stderr":
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        access_log.addHandler(handler)
        access_log.setLevel(logging.INFO)
    else:
        access_log.addHandler(logging.NullHandler())
