"""The two-workspace helper (v6 A2): a shared-mode app, and visitors that each hold their own cookie jar."""

import json
from pathlib import Path

from eventmate.web.app import create_app
from eventmate.workspaces import WorkspaceRegistry
from tests.conftest import EXAMPLE, TODAY

HOST = "todoh.example"
BASE = f"https://{HOST}"
SECRET = "test-secret-" + "x" * 32


def shared_app(tmp_path: Path, **registry_kw):
    registry = WorkspaceRegistry(tmp_path / "data", SECRET, **registry_kw)
    app = create_app(registry=registry, allowed_hosts={HOST}, today_fn=lambda: TODAY, rate_limit_per_minute=10_000)
    return app, registry


class Visitor:
    """One browser: a test client with its own cookies. ``start`` is the page's first call, which makes the workspace and hands back the CSRF value."""

    def __init__(self, app, address="198.51.100.1"):
        self.app, self.client, self.address = app, app.test_client(), address
        self.csrf = None
        self.workspace_id = None
        self.response = None

    def _call(self, method, url, body=None, headers=None, raw=None):
        h = {"Host": HOST, **(headers or {})}
        if method != "GET" and self.csrf is not None:
            h.setdefault("X-EventMate-CSRF", self.csrf)
        kw = {"headers": h, "base_url": BASE, "environ_base": {"REMOTE_ADDR": self.address}}
        if raw is not None:
            kw["data"] = raw
        elif body is not None:
            kw["data"], h["Content-Type"] = json.dumps(body), "application/json"
        elif method in ("POST", "PUT", "PATCH"):
            kw["data"], h["Content-Type"] = "{}", "application/json"
        self.response = getattr(self.client, method.lower())(url, **kw)
        return self.response

    def start(self):
        r = self._call("GET", "/api/session")
        if r.status_code == 200:
            self.csrf, self.workspace_id = r.get_json()["csrf"], r.get_json()["workspace"]["id"]
        return r

    def credential(self):
        cookie = self.client.get_cookie("todoh_ws", domain=HOST)
        return cookie.value if cookie else None

    def get(self, url, **kw):
        return self._call("GET", url, **kw)

    def post(self, url, body=None, **kw):
        return self._call("POST", url, body, **kw)

    def delete(self, url, body=None, **kw):
        return self._call("DELETE", url, body, **kw)

    def make_plan(self, text=EXAMPLE):
        r = self.post("/api/plans", {"request": text})
        assert r.status_code == 201, r.get_json()
        return r.get_json()["plan"]["plan_id"]
