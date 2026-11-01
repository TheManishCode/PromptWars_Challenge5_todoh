"""Phase 2: the workspace directory (credentials, rotate, revoke, limits, sweep) and the cookie and session behaviour of the app."""

import contextlib

import sqlite3

import pytest

from eventmate.workspaces import CREDENTIAL, ID, WorkspaceError, WorkspaceRegistry, csrf_for
from tests.workspace_helpers import BASE, HOST, SECRET, Visitor, shared_app


@pytest.fixture
def registry(tmp_path):
    return WorkspaceRegistry(tmp_path / "data", SECRET, create_per_address_per_hour=1000, create_per_hour=10_000)


# ---- credentials


def test_a_new_workspace_gets_a_strong_credential_and_only_its_hash_is_stored(registry, tmp_path):
    ws, credential = registry.create("198.51.100.1")
    assert ID.fullmatch(ws.id) and CREDENTIAL.fullmatch(credential) and len(credential) >= 32  # 192 bits, above the 128 asked for
    assert registry.open(credential).id == ws.id
    for path in (tmp_path / "data").rglob("*"):
        if path.is_file():
            assert credential.encode() not in path.read_bytes(), path  # the credential is nowhere on disk
    with contextlib.closing(sqlite3.connect(tmp_path / "data" / "workspaces.db")) as db:
        stored = db.execute("SELECT cred_hash FROM workspaces").fetchone()[0]
    assert len(stored) == 64 and stored != credential


@pytest.mark.parametrize("bad", [None, "", "short", "x" * 31, "x" * 33, "a" * 32, "../../etc/passwd" + "x" * 16, "ü" * 32, "a b" * 11, "\x00" * 32, "A" * 1000])
def test_unknown_or_malformed_credentials_open_nothing(registry, bad):
    registry.create("a")
    assert registry.open(bad) is None


def test_rotate_invalidates_the_old_credential_and_revoke_closes_the_workspace_without_deleting_data(registry):
    ws, old = registry.create("a")
    ws.em.plan  # the workspace exists and can be built
    new = registry.rotate(ws.id)
    assert registry.open(old) is None and registry.open(new).id == ws.id and new != old
    assert registry.revoke(ws.id) is True
    assert registry.open(new) is None and registry.rotate(ws.id) is None and registry.directory.exists(ws.id)  # the row and the data remain until a delete


def test_each_workspace_has_its_own_csrf_value_derived_from_the_server_secret(registry):
    a, _ = registry.create("a")
    b, _ = registry.create("b")
    assert a.csrf != b.csrf and a.csrf == csrf_for(SECRET, a.id) and csrf_for("other-secret-" + "y" * 32, a.id) != a.csrf and len(a.csrf) == 43


def test_a_secret_that_is_too_short_is_refused(tmp_path):
    with pytest.raises(ValueError):
        WorkspaceRegistry(tmp_path, "short")


# ---- bounded creation


def test_creation_is_limited_per_address_and_globally_and_capped(tmp_path):
    r = WorkspaceRegistry(tmp_path / "d", SECRET, create_per_address_per_hour=3, create_per_hour=5, max_workspaces=1000)
    for _ in range(3):
        r.create("1.1.1.1")
    with pytest.raises(WorkspaceError) as exc:
        r.create("1.1.1.1")
    assert exc.value.status == 429 and exc.value.code == "workspace_rate_limited"
    r.create("2.2.2.2"), r.create("3.3.3.3")
    with pytest.raises(WorkspaceError) as exc:
        r.create("4.4.4.4")  # five an hour in all
    assert exc.value.code == "workspace_rate_limited"
    capped = WorkspaceRegistry(tmp_path / "c", SECRET, max_workspaces=2)
    capped.create("a"), capped.create("b")
    with pytest.raises(WorkspaceError) as exc:
        capped.create("c")
    assert exc.value.status == 503 and exc.value.code == "workspace_limit"


def test_storage_growth_per_new_workspace_is_small_and_measured(tmp_path):
    """Measured: an empty workspace is one database row plus three empty directories (its home, plans and logs). No file exists until it saves something."""
    r = WorkspaceRegistry(tmp_path / "d", SECRET, create_per_address_per_hour=10_000, create_per_hour=10_000)
    r.create("a")
    db = tmp_path / "d" / "workspaces.db"
    before_db = db.stat().st_size
    for i in range(200):
        r.create(f"addr-{i}")
    root = tmp_path / "d" / "workspaces"
    dirs = [p for p in root.rglob("*") if p.is_dir()]
    files = [p for p in (tmp_path / "d").rglob("*") if p.is_file()]
    allocated = sum(p.stat().st_blocks * 512 for p in dirs)
    assert (db.stat().st_size - before_db) / 200 < 512  # the row
    assert len(dirs) == 201 * 3 and [f.name for f in files] == ["workspaces.db"]
    assert allocated / 201 <= 16 * 1024, allocated / 201  # the directories: a few KB on a typical file system, so the cap of 1,000 workspaces is about 12 MB


def test_only_empty_idle_workspaces_are_swept_and_one_with_data_never_is(tmp_path):
    now = [1_000_000.0]
    r = WorkspaceRegistry(tmp_path / "d", SECRET, clock=lambda: now[0], create_per_address_per_hour=1000, create_per_hour=1000)
    empty, _ = r.create("a")
    kept, _ = r.create("b")
    (r.home(kept.id) / "plans").mkdir(parents=True, exist_ok=True)
    (r.home(kept.id) / "plans" / "plan-x-v0001.json").write_text("{}")
    recent, _ = r.create("c")
    now[0] += 40 * 86400
    r.directory.lookup(r.rotate(recent.id))  # touched just now: not idle
    removed = r.sweep_idle_empty(30 * 86400)
    assert removed == [empty.id] and r.directory.exists(kept.id) and r.directory.exists(recent.id) and not r.directory.exists(empty.id)


def test_a_workspace_dropped_from_memory_is_rebuilt_from_its_files(tmp_path):
    r = WorkspaceRegistry(tmp_path / "d", SECRET, max_live=1, create_per_address_per_hour=1000, create_per_hour=1000)
    a, cred_a = r.create("a")
    from tests.conftest import EXAMPLE, TODAY

    plan_id = a.em.plan(EXAMPLE, today=TODAY).plan.plan_id
    r.create("b")  # evicts a
    again = r.open(cred_a)
    assert again is not a and again.em.store.latest(plan_id).plan_id == plan_id


# ---- the app: cookie and session


def test_the_first_session_call_makes_a_workspace_and_sets_a_strict_http_only_cookie(tmp_path):
    app, registry = shared_app(tmp_path)
    v = Visitor(app)
    r = v.start()
    cookie = r.headers["Set-Cookie"]
    body = r.get_json()
    assert r.status_code == 200 and body["workspace"]["mode"] == "shared" and ID.fullmatch(body["workspace"]["id"]) and len(body["csrf"]) == 43
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Secure" in cookie and "Path=/" in cookie and "Max-Age=31536000" in cookie and "Domain" not in cookie
    assert registry.directory.count() == 1
    again = v.get("/api/session")  # the cookie comes back: the same workspace, and the year is pushed forward
    assert again.get_json()["workspace"]["id"] == body["workspace"]["id"] and registry.directory.count() == 1 and "Max-Age=31536000" in again.headers["Set-Cookie"]


def test_two_visitors_get_two_workspaces_with_different_csrf_values(tmp_path):
    app, _ = shared_app(tmp_path)
    a, b = Visitor(app, "198.51.100.1"), Visitor(app, "198.51.100.2")
    a.start(), b.start()
    assert a.response.get_json()["workspace"]["id"] != b.response.get_json()["workspace"]["id"] and a.csrf != b.csrf


def test_a_visitors_csrf_value_does_not_work_for_another_workspace(tmp_path):
    app, _ = shared_app(tmp_path)
    a, b = Visitor(app, "198.51.100.1"), Visitor(app, "198.51.100.2")
    a.start(), b.start()
    r = b.post("/api/plans", {"request": "x"}, headers={"X-EventMate-CSRF": a.csrf})
    assert r.status_code == 403


@pytest.mark.parametrize("cookie", [None, "garbage", "a" * 32, "x" * 500])
def test_any_other_route_without_a_valid_credential_is_the_generic_404_and_creates_nothing(tmp_path, cookie):
    app, registry = shared_app(tmp_path)
    client = app.test_client()
    if cookie is not None:
        client.set_cookie("todoh_ws", cookie, domain=HOST)
    unknown = client.get("/api/definitely-not-a-route", base_url=BASE, headers={"Host": HOST})
    for method, url in (("get", "/api/plans"), ("post", "/api/plans"), ("get", "/api/plans/abc/state"), ("delete", "/api/plans/abc"), ("post", "/api/assistant/ask")):
        r = getattr(client, method)(url, base_url=BASE, headers={"Host": HOST, "X-EventMate-CSRF": "x", "Content-Type": "application/json"})
        assert r.status_code == 404 and r.data == unknown.data, (method, url, r.data)
    assert registry.directory.count() == 0


def test_a_revoked_or_rotated_credential_gets_the_same_generic_answer(tmp_path):
    app, registry = shared_app(tmp_path)
    v = Visitor(app)
    v.start()
    workspace_id = v.response.get_json()["workspace"]["id"]
    assert v.get("/api/plans").status_code == 200
    registry.rotate(workspace_id)
    assert v.get("/api/plans").status_code == 404 and v.get("/api/plans").get_json() == {"error": "not found", "code": "not_found"}
    assert registry.directory.count() == 1  # nothing new was made for it


def test_a_burst_of_new_workspaces_from_one_address_is_limited_with_a_code(tmp_path):
    app, registry = shared_app(tmp_path, create_per_address_per_hour=3)
    codes = [Visitor(app, "203.0.113.50").start().status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429] and registry.directory.count() == 3
    r = Visitor(app, "203.0.113.50").start()
    assert r.get_json()["code"] == "workspace_rate_limited" and Visitor(app, "203.0.113.51").start().status_code == 200


def test_the_cap_on_workspaces_is_a_clear_503(tmp_path):
    app, _ = shared_app(tmp_path, max_workspaces=1)
    assert Visitor(app, "198.51.100.1").start().status_code == 200
    r = Visitor(app, "198.51.100.2").start()
    assert r.status_code == 503 and r.get_json()["code"] == "workspace_limit"


def test_the_page_its_files_and_healthz_need_no_workspace(tmp_path):
    app, registry = shared_app(tmp_path)
    c = app.test_client()
    for url in ("/", "/healthz", "/static/arcade.js"):
        assert c.get(url, base_url=BASE, headers={"Host": HOST}).status_code == 200, url
    assert registry.directory.count() == 0  # looking at the page makes nothing


def test_a_foreign_host_cannot_make_a_workspace(tmp_path):
    app, registry = shared_app(tmp_path)
    r = app.test_client().get("/api/session", base_url="https://evil.example", headers={"Host": "evil.example"})
    assert r.status_code == 400 and registry.directory.count() == 0


def test_the_cookie_is_scoped_to_the_workspace_never_to_a_domain_and_the_json_never_contains_the_credential(tmp_path):
    app, _ = shared_app(tmp_path)
    v = Visitor(app)
    r = v.start()
    credential = r.headers["Set-Cookie"].split("todoh_ws=", 1)[1].split(";", 1)[0]
    assert credential not in r.get_data(as_text=True)
    assert all(credential not in str(h) for k, h in r.headers if k.lower() != "set-cookie")


def test_the_app_sweeps_empty_idle_workspaces_by_itself_and_never_one_with_data(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENTMATE_WS_SWEEP_SECONDS", "0")  # no background thread: the round is driven by hand
    monkeypatch.setenv("EVENTMATE_WS_EMPTY_IDLE_HOURS", "1")
    from eventmate.web.app import create_app
    from tests.conftest import TODAY

    now = [1_000_000.0]
    registry = WorkspaceRegistry(tmp_path / "d", SECRET, clock=lambda: now[0], create_per_address_per_hour=1000, create_per_hour=1000)
    app = create_app(registry=registry, allowed_hosts={HOST}, today_fn=lambda: TODAY)
    throwaway, _ = registry.create("a")
    kept, _ = registry.create("b")
    (registry.home(kept.id) / "plans" / "plan-x-v0001.json").write_text("{}")
    now[0] += 2 * 3600
    assert app.config["WORKSPACE_SWEEP"]() == 1
    assert not registry.directory.exists(throwaway.id) and registry.directory.exists(kept.id)
