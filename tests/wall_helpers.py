"""Shared pieces for the wall tests: a controllable clock, an app on temporary files, and a ``Visitor`` that behaves like one browser (its own cookie jar)."""

import json
import random
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from eventmate.service import EventMate
from eventmate.wall.limits import WallLimits
from eventmate.wall.store import WallConfig, WallStore
from eventmate.web.app import create_app

T0 = datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc)
WALL_CSRF = "X-EventMate-Wall-CSRF"


class Clock:
    def __init__(self, start=T0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


def make_wall(tmp_path, clock=None, rng_seed=1, **config):
    clock = clock or Clock()
    cfg = replace(WallConfig(), **config) if config else WallConfig()
    return WallStore(tmp_path / "wall" / "wall.sqlite3", cfg, clock=clock, rng=random.Random(rng_seed)), clock


def make_app(tmp_path, clock=None, api_token=None, limits=None, rng_seed=1, **config):
    store, clock = make_wall(tmp_path, clock, rng_seed, **config)
    em = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="wall")
    app = create_app(em, api_token=api_token, wall_store=store, wall_limits=limits or WallLimits(writes_per_ip_minute=10**6, bootstrap_per_ip_hour=10**6, bootstrap_global_hour=10**6, submit_per_ip_hour=10**6))
    return app, store, clock


class Visitor:
    """One browser: Flask's test client keeps the cookie jar."""

    def __init__(self, app, headers=None):
        self.client = app.test_client()
        self.extra = headers or {}
        self.csrf = None

    def get(self, path, **kw):
        return self.client.get(path, headers={**self.extra, **kw.pop("headers", {})}, **kw)

    def bootstrap(self):
        r = self.get("/api/wall/session")
        assert r.status_code == 200, r.get_json()
        self.csrf = r.get_json()["csrf"]
        return r

    def post(self, path, body=None, idem=None, csrf=True, headers=None, raw=None):
        h = {"Content-Type": "application/json", **self.extra}
        if csrf and self.csrf:
            h[WALL_CSRF] = self.csrf
        if idem:
            h["Idempotency-Key"] = idem
        h.update(headers or {})
        return self.client.post(path, data=raw if raw is not None else json.dumps({} if body is None else body), headers=h)

    def mark(self, name="Asha", idem=None):
        return self.post("/api/wall/marks", {"name": name}, idem=idem or uuid.uuid4().hex)

    def round(self, mode="timed"):
        return self.post("/api/wall/rounds", {"mode": mode})


def visible_visitor(app, store, name):
    """A visitor whose mark is approved and visible."""
    v = Visitor(app)
    v.bootstrap()
    r = v.mark(name)
    assert r.status_code == 201, r.get_json()
    store.approve(r.get_json()["mark_id"])
    return v, r.get_json()["mark_id"]
