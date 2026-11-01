"""Phase 7.1 and production behaviour: request ids, a structured access log with nothing sensitive in it, counters, readiness, an orderly stop, and the shape of a failure."""

import errno
import json
import logging
import socket
import threading

import pytest

from eventmate.service import EventMate
from eventmate.web import observe
from eventmate.web.app import create_app
from eventmate.web.server import make_bounded_server
from tests.conftest import EXAMPLE, TODAY
from tests.workspace_helpers import Visitor, shared_app


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("EVENTMATE_ENABLE_LLM", "0")


@pytest.fixture
def local(tmp_path):
    em = EventMate(log_dir=tmp_path / "logs", plan_dir=tmp_path / "plans", run_id="obs")
    return create_app(em, today_fn=lambda: TODAY), em


def csrf(c):
    return {"X-EventMate-CSRF": c.get("/api/session").get_json()["csrf"], "Content-Type": "application/json"}


# ---- request ids


def test_every_answer_carries_a_request_id_and_a_plain_caller_id_is_kept(local):
    c = local[0].test_client()
    first, second = c.get("/healthz"), c.get("/healthz")
    assert observe.REQUEST_ID.fullmatch(first.headers["X-Request-Id"]) and first.headers["X-Request-Id"] != second.headers["X-Request-Id"]
    assert c.get("/healthz", headers={"X-Request-Id": "proxy-abc12345"}).headers["X-Request-Id"] == "proxy-abc12345"


@pytest.mark.parametrize("hostile", ['x"}{"forged": 1', "a" * 200, "short", "has space in it", "../../etc", "é" * 20])
def test_a_hostile_request_id_is_replaced_so_it_cannot_forge_a_log_line(local, hostile):
    got = local[0].test_client().get("/healthz", headers={"X-Request-Id": hostile}).headers["X-Request-Id"]
    assert got != hostile and observe.REQUEST_ID.fullmatch(got)


def test_an_error_names_the_request_and_nothing_else(local, monkeypatch):
    app, em = local
    c = app.test_client()
    h = csrf(c)
    monkeypatch.setattr(em.store, "save", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret detail at /home/owner/plans")))
    r = c.post("/api/plans", data=json.dumps({"request": EXAMPLE}), headers=h)
    body = r.get_json()
    assert r.status_code == 500 and set(body) == {"error", "code", "request_id"} and body["code"] == "internal" and body["request_id"] == r.headers["X-Request-Id"]
    assert "secret" not in r.get_data(as_text=True) and "/home" not in r.get_data(as_text=True)


def test_a_full_disk_is_a_clear_507_and_not_a_generic_500(local, monkeypatch):
    app, em = local
    c = app.test_client()
    h = csrf(c)
    monkeypatch.setattr(em.store, "save", lambda *a, **k: (_ for _ in ()).throw(OSError(errno.ENOSPC, "No space left on device: /secret/path")))
    r = c.post("/api/plans", data=json.dumps({"request": EXAMPLE}), headers=h)
    assert r.status_code == 507 and r.get_json()["code"] == "storage_full" and "/secret" not in r.get_data(as_text=True)


# ---- the access log


def test_the_access_log_has_the_route_template_and_nothing_that_identifies_anyone(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("EVENTMATE_ACCESS_LOG", "off")
    app, registry = shared_app(tmp_path)
    observe.access_log.propagate = True
    observe.access_log.setLevel(logging.INFO)
    v = Visitor(app, "203.0.113.77")
    with caplog.at_level(logging.INFO, logger="eventmate.access"):
        v.start()
        plan = v.make_plan()
        v.get(f"/api/plans/{plan}/state?token=SECRETQUERY", headers={"X-Secret-Header": "HEADERVALUE"})
        v.get("/api/plans/zzzzzzzzzzzz/ledger")
        v.get("/definitely/not/a/route")
    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "eventmate.access"]
    text = " ".join(r.getMessage() for r in caplog.records)
    routes = {(x["method"], x["route"]) for x in lines}
    assert ("GET", "/api/plans/<plan_id>/state") in routes and ("POST", "/api/plans") in routes and ("GET", "unmatched") in routes
    assert all(set(x) == {"rid", "method", "route", "status", "ms", "ws"} for x in lines)
    for secret in (plan, "SECRETQUERY", "HEADERVALUE", "203.0.113.77", v.credential(), v.csrf, "definitely", "zzzzzzzzzzzz"):
        assert secret not in text, secret
    assert any(x["ws"] == v.workspace_id for x in lines)  # the opaque workspace id, which says nothing about the person


def test_probes_are_not_logged(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("EVENTMATE_ACCESS_LOG", "off")
    app, _ = shared_app(tmp_path)
    observe.access_log.propagate = True
    observe.access_log.setLevel(logging.INFO)
    with caplog.at_level(logging.INFO, logger="eventmate.access"):
        c = app.test_client()
        for _ in range(3):
            c.get("/healthz", base_url="https://todoh.example", headers={"Host": "todoh.example"})
            c.get("/readyz", base_url="https://todoh.example", headers={"Host": "todoh.example"})
    assert [r for r in caplog.records if r.name == "eventmate.access"] == []


# ---- counters


def test_the_owner_status_counts_requests_by_class_and_latency_and_holds_no_content(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENTMATE_OWNER_TOKEN", "owner-token-0123456789abcdef")
    app, _ = shared_app(tmp_path)
    v = Visitor(app)
    v.start()
    v.get("/api/plans"), v.get("/api/nope")
    r = app.test_client().get("/api/owner/status", base_url="https://todoh.example", headers={"Host": "todoh.example", "X-EventMate-Owner": "owner-token-0123456789abcdef"})
    req = r.get_json()["requests"]
    assert req["total"] >= 3 and req["by_status_class"]["2xx"] >= 2 and req["by_status_class"]["4xx"] >= 1 and set(req["latency"]) == {"<50ms", "<200ms", "<1000ms", "<5000ms", ">=5000ms"}
    assert sum(req["latency"].values()) == req["total"] - req["in_flight"] or sum(req["latency"].values()) <= req["total"]


# ---- readiness


def test_readyz_is_ready_when_storage_works_and_503_with_a_reason_when_it_does_not(tmp_path, monkeypatch):
    app, registry = shared_app(tmp_path)
    c = app.test_client()
    ok = c.get("/readyz", base_url="https://todoh.example", headers={"Host": "todoh.example"})
    assert ok.status_code == 200 and ok.get_json() == {"ready": True}
    monkeypatch.setattr(registry.directory, "count", lambda: (_ for _ in ()).throw(RuntimeError("database is locked: /secret/workspaces.db")))
    bad = c.get("/readyz", base_url="https://todoh.example", headers={"Host": "todoh.example"})
    assert bad.status_code == 503 and bad.get_json() == {"ready": False, "problems": ["the workspace database cannot be opened"]}
    assert "/secret" not in bad.get_data(as_text=True)


def test_readyz_says_so_when_the_disk_is_nearly_full_or_read_only(tmp_path, monkeypatch):
    import shutil

    app, _ = shared_app(tmp_path)
    c = app.test_client()
    monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(100, 99, 1024))
    r = c.get("/readyz", base_url="https://todoh.example", headers={"Host": "todoh.example"})
    assert r.status_code == 503 and "less than 50 MB" in r.get_json()["problems"][0]


# ---- an orderly stop


class Slow:
    """A WSGI app whose request waits for a gate, to stand in for a long request."""

    def __init__(self):
        self.gate, self.started = threading.Event(), threading.Event()

    def __call__(self, environ, start_response):
        self.started.set()
        self.gate.wait(10)
        start_response("200 OK", [("Content-Type", "text/plain"), ("Content-Length", "4")])
        return [b"done"]


def request_to(port, results):
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    s.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
    data = b""
    while chunk := s.recv(1000):
        data += chunk
    results.append(data)
    s.close()


def test_a_stop_finishes_a_running_request_and_refuses_new_ones(tmp_path):
    app = Slow()
    srv = make_bounded_server(app, "127.0.0.1", 0, max_connections=8, idle_timeout_s=10)
    port = srv.server_port
    serving = threading.Thread(target=srv.serve_forever, daemon=True)
    serving.start()
    results = []
    t = threading.Thread(target=request_to, args=(port, results))
    t.start()
    assert app.started.wait(5) and srv.active == 1
    srv.stop()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=2)  # the door is shut
    app.gate.set()
    assert srv.drain(5) is True
    t.join(5)
    assert results and b"200 OK" in results[0] and b"done" in results[0]  # the request that was running was answered


def test_a_drain_that_runs_out_of_time_says_so(tmp_path):
    app = Slow()
    srv = make_bounded_server(app, "127.0.0.1", 0, max_connections=8, idle_timeout_s=10)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=request_to, args=(srv.server_port, []), daemon=True).start()
    assert app.started.wait(5)
    srv.stop()
    assert srv.drain(0.3) is False  # still running after the time allowed: reported, not hidden
    app.gate.set()
    assert srv.drain(5) is True
