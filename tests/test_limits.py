"""Phase 5.1 and 5.3: who is calling, how often, how much of a shared pool one caller may hold, and the health and owner status routes."""

import ipaddress
import json

import pytest

from eventmate.service import EventMate
from eventmate.web import limits
from eventmate.web.app import create_app
from tests.conftest import TODAY

PROXY = [ipaddress.ip_network("10.0.0.0/24")]


# ---- client address


@pytest.mark.parametrize(
    "remote,forwarded,expected",
    [
        ("203.0.113.9", "6.6.6.6", "203.0.113.9"),  # no trusted proxy for this peer: the header is a lie the client chose
        ("10.0.0.2", "198.51.100.7", "198.51.100.7"),  # a trusted proxy reports the client
        ("10.0.0.2", "1.1.1.1, 198.51.100.7", "198.51.100.7"),  # the client's own claim at the left is ignored; the proxy's entry at the right counts
        ("10.0.0.2", "198.51.100.7, 10.0.0.9", "198.51.100.7"),  # a second trusted proxy in the chain is skipped
        ("10.0.0.2", "198.51.100.7, garbage", "10.0.0.2"),  # an unreadable hop: do not trust the header at all
        ("10.0.0.2", "", "10.0.0.2"),
        ("10.0.0.2", "[2001:db8::1]", "2001:db8::1"),
        ("not-an-ip", "1.1.1.1", "?"),
        (None, None, "?"),
    ],
)
def test_forwarded_headers_are_read_only_from_a_trusted_proxy(remote, forwarded, expected):
    assert limits.client_address(remote, forwarded, PROXY) == expected


def test_without_any_configured_proxy_every_forwarded_header_is_ignored():
    assert limits.client_address("10.0.0.2", "198.51.100.7", []) == "10.0.0.2"
    assert limits.trusted_proxies({}) == [] and limits.trusted_proxies({"EVENTMATE_TRUSTED_PROXY": "bogus, ,10.0.0.0/24"}) == PROXY  # a bad entry trusts nothing extra


# ---- window limiter


def test_the_window_limiter_counts_per_key_and_slides():
    now = [0.0]
    lim = limits.WindowLimiter(2, 60, clock=lambda: now[0])
    assert [lim.allow("a"), lim.allow("a"), lim.allow("a"), lim.allow("b")] == [True, True, False, True]
    now[0] = 61
    assert lim.allow("a")


def test_the_limiter_forgets_idle_keys_and_cannot_grow_without_bound():
    now = [0.0]
    lim = limits.WindowLimiter(5, 60, clock=lambda: now[0], max_keys=100)
    for i in range(10_000):
        lim.allow(f"addr-{i}")
    assert lim.tracked() <= 101  # the flood shares one overflow bucket instead of using memory
    assert not any(lim.allow("~overflow") for _ in range(10))  # and that bucket limits itself
    now[0] = 200
    lim.allow("fresh")
    assert lim.tracked() == 1  # everything that went quiet was swept


# ---- fair share


def test_one_workspace_cannot_take_the_whole_pool():
    pool = limits.FairShare(total=4, per_workspace=2)
    assert [pool.acquire("a"), pool.acquire("a"), pool.acquire("a")] == [None, None, "share"]
    assert pool.acquire("b") is None and pool.acquire("c") is None and pool.acquire("d") == "busy"
    pool.release("a")
    assert pool.acquire("a") is None and pool.status() == (4, 4)
    pool.release("zzz")  # releasing what was never held changes nothing
    assert pool.status() == (4, 4)


# ---- the app


@pytest.fixture
def em(tmp_path):
    return EventMate(log_dir=tmp_path / "logs", plan_dir=tmp_path / "plans", run_id="lim")


def write(client, remote, headers=None):
    token = client.get("/api/session", environ_base={"REMOTE_ADDR": remote}).get_json()["csrf"]
    return client.post("/api/plans", data="{}", headers={"X-EventMate-CSRF": token, "Content-Type": "application/json", **(headers or {})}, environ_base={"REMOTE_ADDR": remote})


def test_writes_are_limited_per_address_with_a_code_and_a_spoofed_header_buys_nothing(em, monkeypatch):
    monkeypatch.delenv("EVENTMATE_TRUSTED_PROXY", raising=False)
    client = create_app(em, today_fn=lambda: TODAY, rate_limit_per_minute=3).test_client()
    codes = [write(client, "203.0.113.9", {"X-Forwarded-For": f"9.9.9.{i}"}).status_code for i in range(5)]
    assert 429 not in codes[:3] and codes[3:] == [429, 429]
    blocked = write(client, "203.0.113.9").get_json()
    assert blocked == {"error": "too many requests", "code": "rate_limited"}
    assert write(client, "203.0.113.10").status_code != 429  # another address is unaffected


def test_behind_a_trusted_proxy_each_client_gets_its_own_budget(em, monkeypatch):
    monkeypatch.setenv("EVENTMATE_TRUSTED_PROXY", "10.0.0.1")
    client = create_app(em, today_fn=lambda: TODAY, rate_limit_per_minute=2).test_client()
    one = [write(client, "10.0.0.1", {"X-Forwarded-For": "198.51.100.1"}).status_code for _ in range(3)]
    two = write(client, "10.0.0.1", {"X-Forwarded-For": "198.51.100.2"}).status_code
    assert one[2] == 429 and two != 429


def test_healthz_says_ok_and_nothing_else(em):
    r = create_app(em, today_fn=lambda: TODAY).test_client().get("/healthz")
    assert r.status_code == 200 and r.get_json() == {"ok": True}


def test_the_owner_status_route_needs_the_owner_token_and_shows_counts_only(em, monkeypatch):
    client = create_app(em, today_fn=lambda: TODAY).test_client()
    unknown = client.get("/api/does-not-exist")
    monkeypatch.delenv("EVENTMATE_OWNER_TOKEN", raising=False)
    off = client.get("/api/owner/status", headers={"X-EventMate-Owner": "x" * 32})
    assert off.status_code == 404 and off.data == unknown.data  # disabled looks exactly like any unknown route, byte for byte
    monkeypatch.setenv("EVENTMATE_OWNER_TOKEN", "owner-token-0123456789abcdef")
    assert client.get("/api/owner/status").status_code == 404
    assert client.get("/api/owner/status", headers={"X-EventMate-Owner": "wrong-token-0123456789abcdef"}).status_code == 404
    ok = client.get("/api/owner/status", headers={"X-EventMate-Owner": "owner-token-0123456789abcdef"})
    body = ok.get_json()
    assert ok.status_code == 200 and {"uptime_s", "pending_jobs", "searches_in_flight", "searches_capacity", "last_poll", "poll_failures", "model_calls"} <= set(body)
    assert "owner-token" not in json.dumps(body) and body["pending_jobs"] == 0 and body["searches_capacity"] == 4


def test_a_short_owner_token_is_refused_even_if_it_matches(em, monkeypatch):
    monkeypatch.setenv("EVENTMATE_OWNER_TOKEN", "short")
    client = create_app(em, today_fn=lambda: TODAY).test_client()
    assert client.get("/api/owner/status", headers={"X-EventMate-Owner": "short"}).status_code == 404


def test_in_shared_mode_one_workspace_cannot_fill_the_slow_search_slots(tmp_path, monkeypatch):
    import threading
    from datetime import timedelta

    from tests.test_hardening import FlightFake, flat

    monkeypatch.setenv("EVENTMATE_MODE", "shared")
    gate, started = threading.Event(), threading.Semaphore(0)

    class Slow(FlightFake):
        def search_one_way(self, *a, **k):
            started.release()
            gate.wait(10)
            return super().search_one_way(*a, **k)

    em = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="fs", flights=Slow(flat()))
    app = create_app(em, today_fn=lambda: TODAY)
    body = {"origin": "DEL", "destination": "BOM", "date": str(TODAY + timedelta(days=30))}
    codes = []

    def call():
        c = app.test_client()
        tok = c.get("/api/session").get_json()["csrf"]
        codes.append(c.post("/api/travel/flights", data=json.dumps(body), headers={"X-EventMate-CSRF": tok, "Content-Type": "application/json"}))

    held = [threading.Thread(target=call) for _ in range(2)]
    for t in held:
        t.start()
    for _ in range(2):
        assert started.acquire(timeout=10)
    c = app.test_client()
    tok = c.get("/api/session").get_json()["csrf"]
    third = c.post("/api/travel/flights", data=json.dumps(body), headers={"X-EventMate-CSRF": tok, "Content-Type": "application/json"})
    gate.set()
    for t in held:
        t.join(10)
    assert third.status_code == 429 and third.get_json()["code"] == "workspace_share"
