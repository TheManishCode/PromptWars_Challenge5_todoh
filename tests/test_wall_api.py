"""8.2, 8.5, 8.6 and 8.11 (items 4 to 6, 9, 11, 12): the wall's HTTP surface, its own authentication, caching, and its isolation from everything private."""

import json
import re
import uuid
from pathlib import Path

import pytest

from eventmate.wall.limits import WallLimits
from eventmate.wall.routes import PRIVATE_READS, PUBLIC_READS, WALL_RULES, WRITES
from tests.wall_helpers import WALL_CSRF, Visitor, make_app, visible_visitor


def wall_rules(app):
    return {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/api/wall/")}


def test_the_registered_wall_routes_equal_the_contract_exactly(tmp_path):
    app, _, _ = make_app(tmp_path)
    assert wall_rules(app) == set(WALL_RULES) and len(WALL_RULES) == 14
    assert len(PUBLIC_READS) == 3 and len(PRIVATE_READS) == 2 and len(WRITES) == 9
    methods = {r.rule: r.methods - {"HEAD", "OPTIONS"} for r in app.url_map.iter_rules() if r.rule.startswith("/api/wall/")}
    assert all(m == {"GET"} for rule, m in methods.items() if rule in PUBLIC_READS + PRIVATE_READS)
    assert all(m == {"POST"} for rule, m in methods.items() if rule in WRITES)


@pytest.mark.parametrize("method,path", [("GET", "/api/wall/admin"), ("GET", "/api/wall/"), ("POST", "/api/wall/admin/approve"), ("GET", "/api/wall/sections/abc"), ("GET", "/api/wall/sections/-1"),
                                         ("POST", "/api/wall/marks/1/approve"), ("DELETE", "/api/wall/marks"), ("GET", "/api/wall/me/extra"), ("PUT", "/api/wall/me")])
def test_an_unlisted_wall_path_is_404_and_gets_no_exemption(tmp_path, method, path):
    app, _, _ = make_app(tmp_path)
    c = app.test_client()
    r = c.open(path, method=method, data="{}", headers={"Content-Type": "application/json"})
    assert r.status_code in (404, 405) and r.get_json().get("error")
    guarded, _, _ = make_app(tmp_path / "t", api_token="s3cret-token")
    r = guarded.test_client().open(path, method=method, data="{}", headers={"Content-Type": "application/json"})
    assert r.status_code == 401  # not exempt from the bearer token


def test_private_routes_still_need_the_token_and_the_allow_listed_wall_routes_do_not(tmp_path):
    app, _, _ = make_app(tmp_path, api_token="s3cret-token")
    c = app.test_client()
    assert c.get("/api/plans").status_code == 401 and c.get("/api/session").status_code == 401
    assert c.get("/api/wall/meta").status_code == 200
    v = Visitor(app)
    v.bootstrap()
    assert v.mark("Asha").status_code == 201
    assert c.get("/api/plans", headers={"Authorization": "Bearer s3cret-token"}).status_code == 200


def test_the_config_switch_keeps_the_bearer_on_wall_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENTMATE_WALL_BEARER", "1")
    app, _, _ = make_app(tmp_path, api_token="s3cret-token")
    c = app.test_client()
    for path in ("/api/wall/meta", "/api/wall/session", "/api/wall/hall"):
        r = c.get(path)
        assert r.status_code == 401 and r.get_json()["code"] == "bearer_required", path
    assert c.get("/api/wall/meta", headers={"Authorization": "Bearer s3cret-token"}).status_code == 200


def test_wall_writes_need_the_wall_csrf_and_the_cookie_and_nothing_else_works(tmp_path):
    app, _, _ = make_app(tmp_path)
    v = Visitor(app)
    v.bootstrap()
    body = {"name": "Asha"}
    for kwargs in ({"csrf": False}, {"headers": {WALL_CSRF: "nope"}}, {"headers": {WALL_CSRF: ""}}):
        r = v.post("/api/wall/marks", body, idem="idem-aaaaaaaaa", **kwargs)
        assert r.status_code == 401 and r.get_json()["code"] == "unauthorized", kwargs
    nocookie = Visitor(app)
    nocookie.csrf = v.csrf  # the right value, but no cookie: it is bound to the cookie's credential
    assert nocookie.post("/api/wall/marks", body, idem="idem-aaaaaaaaa").status_code == 401
    assert v.post("/api/wall/marks", body, idem="idem-aaaaaaaaa").status_code == 201


def test_the_two_csrf_values_are_not_interchangeable(tmp_path):
    app, _, _ = make_app(tmp_path)
    v = Visitor(app)
    v.bootstrap()
    private = v.client.get("/api/session").get_json()["csrf"]
    assert private != v.csrf
    # the private value does not authorize a wall write ...
    r = v.client.post("/api/wall/marks", data=json.dumps({"name": "Asha"}), headers={"Content-Type": "application/json", "Idempotency-Key": "idem-aaaaaaaaa", WALL_CSRF: private})
    assert r.status_code == 401
    # ... and the wall value does not authorize a private write
    r = v.client.post("/api/inbox/poll", data="{}", headers={"Content-Type": "application/json", "X-EventMate-CSRF": v.csrf})
    assert r.status_code == 403
    r = v.client.post("/api/inbox/poll", data="{}", headers={"Content-Type": "application/json", WALL_CSRF: v.csrf})
    assert r.status_code == 403


def test_no_wall_response_contains_the_private_csrf_or_any_secret(tmp_path):
    app, store, _ = make_app(tmp_path)
    private = app.test_client().get("/api/session").get_json()["csrf"]
    v, mid = visible_visitor(app, store, "Asha")
    code = v.post("/api/wall/me/recovery-code", {"replace_existing": False}).get_json()["recovery_code"]
    cred = v.client.get_cookie("todoh_wall").value
    texts = []
    for path in ("/api/wall/meta", "/api/wall/sections/0", "/api/wall/hall", "/api/wall/hall?order=fastest", "/api/wall/session", "/api/wall/me"):
        texts.append(v.get(path).get_data(as_text=True))
    import hashlib
    for t in texts:
        assert private not in t
    public = "".join(texts[:4])
    for secret in (v.csrf, code, cred, hashlib.sha256(cred.encode()).hexdigest()):
        assert secret not in public
    assert cred not in "".join(texts)  # the credential itself is only ever in the cookie


def test_public_reads_are_byte_identical_with_and_without_a_cookie_and_cacheable(tmp_path):
    app, store, _ = make_app(tmp_path)
    v, _mid = visible_visitor(app, store, "Asha")
    anon = app.test_client()
    for path in ("/api/wall/meta", "/api/wall/sections/0", "/api/wall/hall", "/api/wall/hall?order=fastest"):
        a, b = anon.get(path), v.get(path)
        assert a.status_code == 200 and a.get_data() == b.get_data(), path
        assert "public" in a.headers["Cache-Control"] and a.headers["ETag"] == b.headers["ETag"]
        assert "Set-Cookie" not in a.headers and "Cookie" not in a.headers.get("Vary", "")
        assert anon.get(path, headers={"If-None-Match": a.headers["ETag"]}).status_code == 304
    assert anon.get("/api/wall/hall").headers["Cache-Control"] == "public, max-age=30"
    assert anon.get("/api/wall/meta").headers["Cache-Control"] == "public, max-age=60"


def test_private_responses_are_no_store_and_vary_on_cookie(tmp_path):
    app, store, _ = make_app(tmp_path)
    v, _ = visible_visitor(app, store, "Asha")
    r = v.get("/api/wall/session")
    assert r.headers["Cache-Control"] == "no-store" and r.headers["Vary"] == "Cookie"
    for path, body in (("/api/wall/me", None), ):
        r = v.get(path)
        assert r.headers["Cache-Control"] == "no-store" and r.headers["Vary"] == "Cookie"
    for path, body in (("/api/wall/me/recovery-code", {"replace_existing": False}), ("/api/wall/rounds", {"mode": "timed"}), ("/api/wall/me/hall", {"opt_in": False}), ("/api/wall/marks", {"name": "Again"})):
        r = v.post(path, body, idem=uuid.uuid4().hex)
        assert r.headers["Cache-Control"] == "no-store" and r.headers["Vary"] == "Cookie", path
    err = v.post("/api/wall/me/remove", {"recovery_code": "nope"})
    assert err.headers["Cache-Control"] == "no-store"


def test_public_reads_never_include_pending_hidden_or_removed_names_and_are_bounded(tmp_path):
    app, store, _ = make_app(tmp_path, section_capacity=300)
    ids = {}
    for name in ("Shown", "Hidden", "Removed", "Pending"):
        v = Visitor(app)
        v.bootstrap()
        ids[name] = v.mark(name).get_json()["mark_id"]
    store.approve(ids["Shown"]), store.approve(ids["Hidden"]), store.approve(ids["Removed"])
    store.hide(ids["Hidden"]), store.remove(ids["Removed"])
    anon = app.test_client()
    text = anon.get("/api/wall/sections/0").get_data(as_text=True) + anon.get("/api/wall/hall").get_data(as_text=True) + anon.get("/api/wall/meta").get_data(as_text=True)
    assert "Shown" in text
    for gone in ("Hidden", "Removed", "Pending"):
        assert gone not in text
    for i in range(30):
        w = Visitor(app)
        w.bootstrap()
        store.approve(w.mark(f"Crowd{i:02d}x").get_json()["mark_id"])
    page = anon.get("/api/wall/sections/0?limit=10").get_json()
    assert len(page["slots"]) == 10 and page["next_after_slot"] == page["slots"][-1]["slot"]
    rest = anon.get(f"/api/wall/sections/0?after_slot={page['next_after_slot']}&limit=300").get_json()
    assert rest["next_after_slot"] is None and len(page["slots"]) + len(rest["slots"]) == 31
    for bad in ("limit=0", "limit=301", "limit=abc", "after_slot=-2", "after_slot=x", "limit=1.5"):
        assert anon.get(f"/api/wall/sections/0?{bad}").status_code == 400, bad
    assert anon.get("/api/wall/sections/99").get_json() == {"error": "not found", "code": "not_found"}
    assert anon.get("/api/wall/hall?limit=1000").status_code == 400 and anon.get("/api/wall/hall?cursor=" + "x" * 300).status_code == 400


# ------------------------------------------------------------------ bootstrap and cookies (8.5)

def test_bootstrap_sets_a_safe_cookie_and_never_replaces_a_valid_credential(tmp_path):
    app, _, _ = make_app(tmp_path)
    v = Visitor(app)
    r = v.bootstrap()
    cookie = r.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Path=/" in cookie and "Max-Age=31536000" in cookie and "Secure" not in cookie and "Domain" not in cookie
    first = v.client.get_cookie("todoh_wall").value
    assert len(first) >= 43  # 256 bits
    again = v.get("/api/wall/session")
    assert "Set-Cookie" not in again.headers and again.get_json()["csrf"] == v.csrf
    assert v.client.get_cookie("todoh_wall").value == first


def test_the_cookie_is_secure_over_https(tmp_path):
    app, _, _ = make_app(tmp_path)
    c = app.test_client()
    r = c.get("/api/wall/session", base_url="https://localhost")
    assert "Secure" in r.headers["Set-Cookie"]


def test_a_forged_or_unknown_cookie_gets_a_fresh_anonymous_visitor_and_changes_nothing_elsewhere(tmp_path):
    app, store, _ = make_app(tmp_path)
    c = app.test_client()
    for forged in ("x", "A" * 43, "A" * 44, "../../etc/passwd", ""):
        c.set_cookie("todoh_wall", forged)
        r = c.get("/api/wall/session")
        assert r.status_code == 200 and r.get_json()["has_mark"] is False
        assert c.get("/api/wall/me").status_code == 200  # the new credential works
    c2 = app.test_client()
    c2.set_cookie("todoh_wall", "A" * 43)
    assert c2.get("/api/wall/me").status_code == 401  # any other route creates nothing
    assert store.stats()["unclaimed_sessions"] == 5


def test_two_tabs_without_a_cookie_get_two_credentials_and_the_stale_csrf_is_rejected(tmp_path):
    app, _, _ = make_app(tmp_path)
    c = app.test_client()
    tab1 = c.get("/api/wall/session").get_json()["csrf"]
    c.delete_cookie("todoh_wall")  # the second tab loaded before the first Set-Cookie was stored
    tab2 = c.get("/api/wall/session").get_json()["csrf"]
    assert tab1 != tab2
    r = c.post("/api/wall/marks", data=json.dumps({"name": "Asha"}), headers={"Content-Type": "application/json", "Idempotency-Key": "idem-aaaaaaaaa", WALL_CSRF: tab1})
    assert r.status_code == 401  # the browser keeps the last cookie: the first tab's token no longer matches
    assert c.post("/api/wall/marks", data=json.dumps({"name": "Asha"}), headers={"Content-Type": "application/json", "Idempotency-Key": "idem-aaaaaaaaa", WALL_CSRF: tab2}).status_code == 201


def test_bootstrap_is_rate_limited_per_address_and_globally(tmp_path):
    clock = [0.0]
    limits = WallLimits(clock=lambda: clock[0], bootstrap_per_ip_hour=3, bootstrap_global_hour=5, writes_per_ip_minute=1000)
    app, store, _ = make_app(tmp_path, limits=limits)
    c = app.test_client()
    codes = []
    for i in range(4):
        c.delete_cookie("todoh_wall")
        codes.append(c.get("/api/wall/session").status_code)
    assert codes == [200, 200, 200, 429]
    assert store.stats()["unclaimed_sessions"] == 3  # the refused one created nothing
    other = app.test_client()
    other_codes = []
    for i in range(3):  # another address has its own budget of 3, but the global hour allows 5 in all and 3 are spent
        other.delete_cookie("todoh_wall")
        other_codes.append(other.get("/api/wall/session", environ_overrides={"REMOTE_ADDR": "10.1.1.1"}).status_code)
    assert other_codes == [200, 200, 429]
    clock[0] += 3601
    c.delete_cookie("todoh_wall")
    assert c.get("/api/wall/session").status_code == 200  # the hour passed


# ------------------------------------------------------------------ writes (8.6 items 6 to 10)

def test_submit_contract(tmp_path):
    app, store, _ = make_app(tmp_path)
    v = Visitor(app)
    v.bootstrap()
    assert v.post("/api/wall/marks", {"name": "Asha"}).status_code == 400  # Idempotency-Key is required
    assert v.post("/api/wall/marks", {"name": "Asha"}, idem="short").get_json()["code"] == "bad_request"
    bad = v.post("/api/wall/marks", {"name": "<b>x</b>"}, idem="idem-aaaaaaaaa")
    assert bad.status_code == 400 and bad.get_json()["code"] == "invalid_name" and "<b>" not in bad.get_data(as_text=True)
    for body in ({}, {"name": "Asha", "extra": 1}, {"name": 5}, [], "x"):
        assert v.post("/api/wall/marks", body, idem="idem-aaaaaaaaa").status_code == 400
    ok = v.post("/api/wall/marks", {"name": "  Asha  "}, idem="idem-aaaaaaaaa")
    assert ok.status_code == 201 and ok.get_json() == {"mark_id": 1, "status": "pending"}
    assert v.post("/api/wall/marks", {"name": "Asha"}, idem="idem-aaaaaaaaa").get_json() == {"mark_id": 1, "status": "pending"}  # a lost response is safe to retry
    dup = v.post("/api/wall/marks", {"name": "Other"}, idem="idem-bbbbbbbbb")
    assert dup.status_code == 409 and dup.get_json() == {"error": "this browser already has a name on the wall", "code": "already_marked", "mark_id": 1, "status": "pending"}
    me = v.get("/api/wall/me").get_json()
    assert me == {"has_mark": True, "mark": {"mark_id": 1, "name": "Asha", "status": "pending", "section_id": None, "slot": None}, "hall": False, "hall_eligible": False,
                  "best_elapsed_ms": None, "has_recovery_code": False}
    assert v.get("/api/wall/session").get_json() == {"csrf": v.csrf, "has_mark": True, "mark_status": "pending"}


def test_submit_closed_and_rate_limited(tmp_path):
    limits = WallLimits(submit_per_ip_hour=1, writes_per_ip_minute=1000, bootstrap_per_ip_hour=1000, bootstrap_global_hour=1000)
    app, store, _ = make_app(tmp_path, limits=limits)
    a, b = Visitor(app), Visitor(app)
    a.bootstrap(), b.bootstrap()
    assert a.mark("First").status_code == 201
    r = b.mark("Second")
    assert r.status_code == 429 and r.get_json()["code"] == "rate_limited"
    closed, store2, _ = make_app(tmp_path / "c")
    store2.set_accepting(False)
    c = Visitor(closed)
    c.bootstrap()
    r = c.mark("Third")
    assert r.status_code == 503 and r.get_json()["code"] == "wall_closed"


def test_the_content_type_origin_and_body_rules_apply_to_wall_writes(tmp_path):
    app, _, _ = make_app(tmp_path)
    v = Visitor(app)
    v.bootstrap()
    h = {WALL_CSRF: v.csrf, "Idempotency-Key": "idem-aaaaaaaaa"}
    assert v.client.post("/api/wall/marks", data='{"name":"Asha"}', headers={**h, "Content-Type": "text/plain"}).status_code == 415
    assert v.client.post("/api/wall/marks", data='{"name":"Asha"}', headers={**h}).status_code == 415
    r = v.client.post("/api/wall/marks", data='{"name":"Asha"}', headers={**h, "Content-Type": "application/json", "Origin": "https://evil.example"})
    assert r.status_code == 403
    ok_origin = v.client.post("/api/wall/marks", data='{"name":"Asha"}', headers={**h, "Content-Type": "application/json", "Origin": "http://localhost"})
    assert ok_origin.status_code == 201
    assert v.client.post("/api/wall/marks", data=b"x" * 70_000, headers={**h, "Content-Type": "application/json"}).status_code in (400, 413)
    assert v.client.post("/api/wall/rounds", data="not json", headers={WALL_CSRF: v.csrf, "Content-Type": "application/json"}).status_code == 400
    foreign = app.test_client().get("/api/wall/meta", headers={"Host": "evil.example"})
    assert foreign.status_code == 400  # the app's host check still applies to the wall


def test_recovery_flow_over_http(tmp_path):
    app, store, _ = make_app(tmp_path)
    v, mid = visible_visitor(app, store, "Asha")
    assert v.post("/api/wall/me/recovery-code", {}).status_code == 200
    again = v.post("/api/wall/me/recovery-code", {"replace_existing": False})
    assert again.status_code == 409 and again.get_json()["code"] == "code_exists"
    code = v.post("/api/wall/me/recovery-code", {"replace_existing": True}).get_json()["recovery_code"]
    assert re.fullmatch(r"(?:[A-Z2-7]{4}-){6}[A-Z2-7]{2}", code)
    anon = Visitor(app)
    anon.bootstrap()
    spare = anon.client.get_cookie("todoh_wall").value
    wrong = anon.post("/api/wall/recover", {"recovery_code": "AAAA-AAAA-AAAA-AAAA-AAAA-AA"})
    garbage = anon.post("/api/wall/recover", {"recovery_code": "nonsense"})
    unknown_len = anon.post("/api/wall/recover", {"recovery_code": "x" * 500})
    assert {wrong.status_code, garbage.status_code, unknown_len.status_code} == {404} and wrong.get_json() == garbage.get_json() == unknown_len.get_json() == {"error": "not found", "code": "not_found"}
    ok = anon.post("/api/wall/recover", {"recovery_code": code.lower().replace("-", " ")})
    assert ok.status_code == 200 and ok.get_json() == {"mark": {"mark_id": mid, "name": "Asha", "status": "visible", "section_id": 0, "slot": 0}, "has_recovery_code": False}
    assert anon.client.get_cookie("todoh_wall").value != spare
    anon.bootstrap()
    assert anon.get("/api/wall/me").get_json()["mark"]["mark_id"] == mid
    assert v.get("/api/wall/me").status_code == 401  # the old browser's cookie stopped working
    replay = Visitor(app)
    replay.bootstrap()
    assert replay.post("/api/wall/recover", {"recovery_code": code}).status_code == 404


def test_recovery_attempts_are_bounded_per_address_and_globally(tmp_path):
    clock = [0.0]
    limits = WallLimits(clock=lambda: clock[0], recover_fail_per_ip_hour=3, recover_fail_global_hour=5, writes_per_ip_minute=1000, bootstrap_per_ip_hour=1000, bootstrap_global_hour=1000)
    app, store, _ = make_app(tmp_path, limits=limits)
    v = Visitor(app)
    v.bootstrap()
    codes = [v.post("/api/wall/recover", {"recovery_code": "AAAA-AAAA-AAAA-AAAA-AAAA-AA"}).status_code for _ in range(5)]
    assert codes == [404, 404, 404, 429, 429]
    w = Visitor(app)
    w.bootstrap()
    other = [w.client.post("/api/wall/recover", data=json.dumps({"recovery_code": "AAAA-AAAA-AAAA-AAAA-AAAA-AA"}), headers={"Content-Type": "application/json", WALL_CSRF: w.csrf},
                           environ_overrides={"REMOTE_ADDR": "10.9.9.9"}).status_code for _ in range(4)]
    assert other == [404, 404, 429, 429]  # another address has its own budget, but the global one (5) runs out
    clock[0] += 3601
    assert v.post("/api/wall/recover", {"recovery_code": "AAAA-AAAA-AAAA-AAAA-AAAA-AA"}).status_code == 404


def test_remove_over_http_with_cookie_or_code_and_the_public_id_alone_grants_nothing(tmp_path):
    app, store, _ = make_app(tmp_path)
    owner, mid = visible_visitor(app, store, "Owner")
    other, mid2 = visible_visitor(app, store, "Other")
    code = other.post("/api/wall/me/recovery-code", {}).get_json()["recovery_code"]
    stranger = Visitor(app)
    stranger.bootstrap()
    assert stranger.post("/api/wall/me/remove", {}).status_code == 404  # no mark of its own
    assert stranger.post("/api/wall/me/remove", {"recovery_code": str(mid)}).status_code == 404  # a mark id is not a code
    assert stranger.post("/api/wall/me/remove", {"recovery_code": code}).get_json() == {"removed": True}
    assert owner.post("/api/wall/me/remove", {}).get_json() == {"removed": True}
    assert owner.post("/api/wall/me/remove", {}).get_json() == {"removed": True}
    names = app.test_client().get("/api/wall/sections/0").get_json()["slots"]
    assert names == []
    me = owner.get("/api/wall/me").get_json()
    assert me["has_mark"] is True and me["mark"] == {"mark_id": mid, "status": "removed", "name": None, "section_id": None, "slot": None}


def test_hall_opt_in_and_opt_out_over_http(tmp_path):
    app, store, clock = make_app(tmp_path)
    v, mid = visible_visitor(app, store, "Asha")
    assert v.post("/api/wall/me/hall", {"opt_in": True}).status_code == 409
    assert v.post("/api/wall/me/hall", {"opt_in": False}).get_json() == {"hall": False}
    assert v.post("/api/wall/me/hall", {"opt_in": "yes"}).status_code == 400 and v.post("/api/wall/me/hall", {}).status_code == 400


# ------------------------------------------------------------------ rounds over HTTP (8.6 items 11 to 14)

def play(v, store, clock, mode="timed", wait_ms=900, hit=True, body_override=None):
    r = v.round(mode).get_json()
    assert r["available"] is True, r
    rid = r["round_id"]
    rd = v.post(f"/api/wall/rounds/{rid}/ready", {})
    assert rd.status_code == 200, rd.get_json()
    clock.advance(milliseconds=wait_ms)
    mark_id = v.get("/api/wall/me").get_json()["mark"]["mark_id"]
    if body_override is not None:
        body = body_override
    elif mode == "timed":
        body = {"slot": r["to_slot"] if hit else r["to_slot"] + 1}
    else:
        body = {"mark_id": mark_id if hit else mark_id + 99}
    return r, v.post(f"/api/wall/rounds/{rid}/tap", body)


def test_round_contract_shapes(tmp_path):
    app, store, clock = make_app(tmp_path, section_capacity=12)
    v, mid = visible_visitor(app, store, "Asha")
    r = v.round("timed").get_json()
    assert set(r) == {"available", "round_id", "section_id", "from_slot", "to_slot", "snapshot", "mode", "window_ms", "issued_at", "expires_at", "server_now"}
    assert set(r["snapshot"]) == {"layout_version", "capacity", "next_slot", "slots"} and r["snapshot"]["capacity"] == 12 and r["window_ms"] == 5000 and r["mode"] == "timed"
    rd = v.post(f"/api/wall/rounds/{r['round_id']}/ready", {}).get_json()
    assert set(rd) == {"ready_at", "deadline_at", "server_now"} and rd["deadline_at"]
    clock.advance(seconds=1)
    tap = v.post(f"/api/wall/rounds/{r['round_id']}/tap", {"slot": r["to_slot"]})
    assert tap.status_code == 200 and set(tap.get_json()) == {"result", "hall_offer", "elapsed_ms", "ranked", "server_now"} and tap.get_json()["result"] == "found" and tap.get_json()["elapsed_ms"] == 1000
    assert v.post(f"/api/wall/rounds/{r['round_id']}/tap", {"slot": r["to_slot"]}).get_json()["code"] == "round_used"
    assert app.test_client().get("/api/wall/meta").get_json()["hall_unlocked"] is True
    assert v.round("sideways").status_code == 400
    assert v.post("/api/wall/rounds", {"mode": "timed", "x": 1}).status_code == 400


def test_round_errors_over_http(tmp_path):
    app, store, clock = make_app(tmp_path, section_capacity=12)
    v, _ = visible_visitor(app, store, "Asha")
    stranger = Visitor(app)
    stranger.bootstrap()
    r = v.round("timed").get_json()
    rid = r["round_id"]
    assert v.round("timed").get_json() == {"available": False, "reason": "round_in_progress"}
    assert v.post(f"/api/wall/rounds/{rid}/tap", {"slot": r["to_slot"]}).get_json()["code"] == "not_ready"
    foreign = [stranger.post(f"/api/wall/rounds/{rid}/{p}", b).get_json() for p, b in (("ready", {}), ("tap", {"slot": 1}), ("cancel", {}))]
    unknown = [stranger.post(f"/api/wall/rounds/nope/{p}", b).get_json() for p, b in (("ready", {}), ("tap", {"slot": 1}), ("cancel", {}))]
    assert foreign == unknown == [{"error": "not found", "code": "not_found"}] * 3
    assert stranger.post("/api/wall/rounds/bad%20id/ready", {}).status_code == 404
    for body in ({}, {"slot": 1, "mark_id": 2}, {"slot": "1"}, {"slot": True}, {"mark_id": 1}, {"slot": 1, "x": 1}):
        res = v.post(f"/api/wall/rounds/{rid}/tap", body)
        assert res.status_code in (400, 409), body
    assert v.post(f"/api/wall/rounds/{rid}/cancel", {}).get_json() == {"cancelled": True}
    assert v.post(f"/api/wall/rounds/{rid}/cancel", {}).get_json() == {"cancelled": False}
    nobody = Visitor(app)
    nobody.bootstrap()
    assert nobody.round("timed").get_json() == {"available": False, "reason": "not_visible"}
    assert Visitor(app).post("/api/wall/rounds", {"mode": "timed"}).status_code == 401  # no cookie, no csrf


def test_stale_round_over_http_is_a_409_and_asks_for_a_new_one(tmp_path):
    app, store, clock = make_app(tmp_path, section_capacity=12, rounds_per_day=1)
    v, mid = visible_visitor(app, store, "Asha")
    w, wid = visible_visitor(app, store, "Bee")
    r = v.round("timed").get_json()
    store.hide(wid)
    res = v.post(f"/api/wall/rounds/{r['round_id']}/ready", {})
    assert res.status_code == 409 and res.get_json()["code"] == "round_stale"
    assert v.round("timed").get_json()["available"] is True


def test_untimed_round_over_http_uses_mark_id_and_is_never_ranked(tmp_path):
    app, store, clock = make_app(tmp_path, section_capacity=12)
    v, mid = visible_visitor(app, store, "Asha")
    r, tap = play(v, store, clock, mode="untimed", wait_ms=30_000)
    assert r["window_ms"] is None and tap.get_json()["result"] == "found" and tap.get_json()["elapsed_ms"] is None and tap.get_json()["ranked"] is False
    assert v.get("/api/wall/me").get_json()["hall_eligible"] is True
    assert app.test_client().get("/api/wall/hall?order=fastest").get_json()["entries"] == []


def test_the_fastest_hall_over_http_and_opt_in(tmp_path):
    app, store, clock = make_app(tmp_path, section_capacity=30)
    for i, ms in enumerate((900, 400, 700)):
        v, _ = visible_visitor(app, store, f"Player{i}x")
        play(v, store, clock, wait_ms=ms)
        assert v.post("/api/wall/me/hall", {"opt_in": True}).get_json() == {"hall": True}
    anon = app.test_client()
    top = anon.get("/api/wall/hall?order=fastest").get_json()
    assert [(e["name"], e["elapsed_ms"], e["rank"]) for e in top["entries"]] == [("Player1x", 400, 1), ("Player2x", 700, 2), ("Player0x", 900, 3)]
    assert set(top) == {"entries", "next_cursor", "period"} and set(top["period"]) == {"id", "kind", "starts_at", "ends_at"}
    assert set(top["entries"][0]) == {"mark_id", "name", "elapsed_ms", "rank"}
    newest = anon.get("/api/wall/hall").get_json()
    assert [e["name"] for e in newest["entries"]] == ["Player2x", "Player1x", "Player0x"] and "period" not in newest
    assert anon.get("/api/wall/hall?order=fastest&limit=2").get_json()["entries"][-1]["rank"] == 2
    text = anon.get("/api/wall/hall?order=fastest").get_data(as_text=True)
    assert "round_id" not in text and "cred" not in text


def test_meta_contract(tmp_path):
    app, store, _ = make_app(tmp_path)
    m = app.test_client().get("/api/wall/meta").get_json()
    assert set(m) == {"schema_version", "accepting", "section_count", "visible_count", "hall_unlocked", "hall_period", "limits", "round"}
    assert m["limits"] == {"name_min": 2, "name_max": 24} and m["round"] == {"window_ms_default": 5000, "untimed_available": True} and m["hall_unlocked"] is False


# ------------------------------------------------------------------ isolation (8.11 item 9)

FORBIDDEN = ("plan_store", "guestlist", "outreach", "conversations", "service", "schemas", "followthrough", "voice", "assistant", "messaging", "agents")


def test_the_wall_package_imports_no_plan_guest_draft_or_workspace_store():
    import ast
    import eventmate.wall as pkg
    for path in Path(pkg.__file__).parent.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            for mod in mods:
                if mod.startswith("eventmate."):
                    parts = mod.split(".")
                    assert parts[1] == "wall", f"{path.name} imports {mod}"
    admin = Path(pkg.__file__).parent.parent / "wall_admin.py"
    imported = [n.module for n in ast.walk(ast.parse(admin.read_text())) if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("eventmate.")]
    assert set(imported) <= {"eventmate.plan_store", "eventmate.wall.store"}  # plan_store only for the default directory constant, never to read a plan
    assert "DEFAULT_PLAN_DIR" in admin.read_text()


def test_deleting_a_plan_never_touches_the_wall_and_the_wall_file_is_outside_the_plan_directory(tmp_path):
    from tests.conftest import EXAMPLE, TODAY
    app, store, _ = make_app(tmp_path)
    v, mid = visible_visitor(app, store, "Asha")
    em_root = tmp_path / "p"
    assert store.path.parent != em_root and em_root not in store.path.parents
    before = (store.list_marks("visible"), store.stats())
    from eventmate.service import EventMate
    em = EventMate(log_dir=tmp_path / "l", plan_dir=em_root, run_id="x")
    pid = em.plan(EXAMPLE, today=TODAY).plan.plan_id
    em.delete_plan(pid)
    assert (store.list_marks("visible"), store.stats()) == before and store.path.exists()


def test_no_wall_response_contains_plan_data(tmp_path):
    from tests.conftest import EXAMPLE, TODAY
    from eventmate.service import EventMate
    app, store, _ = make_app(tmp_path)
    em = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="x")
    pid = em.plan(EXAMPLE, today=TODAY).plan.plan_id
    v, _ = visible_visitor(app, store, "Asha")
    seen = ""
    for path in ("/api/wall/meta", "/api/wall/sections/0", "/api/wall/hall", "/api/wall/session", "/api/wall/me"):
        seen += v.get(path).get_data(as_text=True)
    assert pid not in seen and "Pune" not in seen and "engagement" not in seen


def test_a_wall_name_never_appears_in_logs_or_files_outside_the_wall(tmp_path, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    marker = "Zyxwvu Marker"
    app, store, _ = make_app(tmp_path)
    v = Visitor(app)
    v.bootstrap()
    mid = v.mark(marker).get_json()["mark_id"]
    store.approve(mid)
    v.post("/api/wall/me/remove", {})
    v.mark("Second Try")
    v.post("/api/wall/recover", {"recovery_code": "nonsense"})
    assert marker not in caplog.text
    for path in (tmp_path / "l", tmp_path / "p"):
        for f in path.rglob("*") if path.exists() else []:
            if f.is_file():
                assert marker.encode() not in f.read_bytes(), f
