"""The workspace recovery phrase (architecture L2): get back into your workspace from another browser without an account, and without the server ever keeping the phrase."""

import sqlite3


from eventmate import workspaces as W
from eventmate.service import EventMate
from eventmate.web.app import create_app
from tests.conftest import TODAY
from tests.workspace_helpers import Visitor, shared_app


def started(app, address):
    v = Visitor(app, address)
    assert v.start().status_code == 200
    return v


def phrase_of(v):
    r = v.post("/api/workspace/recovery")
    assert r.status_code == 200 and r.get_json()["shown_once"], r.get_json()
    return r.get_json()["phrase"]


def test_the_phrase_is_eighty_bits_in_a_forgiving_alphabet():
    p = W.new_recovery_phrase()
    assert len(p) == 19 and p.count("-") == 3 and all(c in W.RECOVERY_ALPHABET + "-" for c in p) and p != W.new_recovery_phrase()
    n = W.normalise_phrase(p)
    assert len(n) == 16
    assert W.normalise_phrase(p.lower()) == n and W.normalise_phrase(p.replace("-", " ")) == n and W.normalise_phrase(" " + p + "\n") == n
    assert W.normalise_phrase("OIL0-1111-0000-ABCD") == W.normalise_phrase("0110-1111-0000-abcd")  # the look-alikes read as their digits
    for bad in (None, 5, "", "ABCD", "U" * 16, "A" * 17, "a" * 41, "ABCD-EFGH-JKMN-PQR!", b"x"):
        assert W.normalise_phrase(bad) is None


def test_a_phrase_makes_a_new_browser_the_owner_and_locks_the_old_one_out(tmp_path):
    app, registry = shared_app(tmp_path)
    a = started(app, "198.51.100.1")
    plan = a.make_plan()
    phrase = phrase_of(a)
    b = started(app, "198.51.100.2")
    assert b.get(f"/api/plans/{plan}").status_code == 404 and registry.directory.count() == 2
    r = b.post("/api/workspace/recover", {"phrase": phrase.lower().replace("-", " ")})
    assert r.status_code == 200 and r.get_json()["recovered"] and r.get_json()["phrase"] != phrase
    assert registry.directory.count() == 1  # the empty workspace b was handed is gone
    b.start()
    assert b.get(f"/api/plans/{plan}").status_code == 200  # b now holds the workspace, plans and all
    assert a.get(f"/api/plans/{plan}").status_code == 404  # the credential was rotated: the old browser is out


def test_a_phrase_opens_a_workspace_once_and_the_new_one_works_again(tmp_path):
    app, _ = shared_app(tmp_path)
    a = started(app, "198.51.100.1")
    a.make_plan()
    old = phrase_of(a)
    b = started(app, "198.51.100.2")
    fresh = b.post("/api/workspace/recover", {"phrase": old}).get_json()["phrase"]
    c = started(app, "198.51.100.3")
    assert c.post("/api/workspace/recover", {"phrase": old}).status_code == 404  # used
    assert c.post("/api/workspace/recover", {"phrase": fresh}).status_code == 200


def test_making_a_new_phrase_retires_the_old_one(tmp_path):
    app, _ = shared_app(tmp_path)
    a = started(app, "198.51.100.1")
    first = phrase_of(a)
    second = phrase_of(a)
    b = started(app, "198.51.100.2")
    assert first != second and b.post("/api/workspace/recover", {"phrase": first}).status_code == 404
    assert b.post("/api/workspace/recover", {"phrase": second}).status_code == 200


def test_a_browser_that_already_holds_plans_is_never_switched_away_from_them(tmp_path):
    app, _ = shared_app(tmp_path)
    a = started(app, "198.51.100.1")
    a.make_plan()
    phrase = phrase_of(a)
    b = started(app, "198.51.100.2")
    mine = b.make_plan()
    r = b.post("/api/workspace/recover", {"phrase": phrase})
    assert r.status_code == 409 and r.get_json()["code"] == "workspace_has_data"
    assert b.get(f"/api/plans/{mine}").status_code == 200
    assert started(app, "198.51.100.3").post("/api/workspace/recover", {"phrase": phrase}).status_code == 200  # and the phrase was not used up by the refused attempt


def test_a_wrong_or_malformed_phrase_gets_one_answer_and_the_tries_are_limited(tmp_path):
    app, _ = shared_app(tmp_path)
    b = started(app, "198.51.100.9")
    answers = {b.post("/api/workspace/recover", {"phrase": p}).get_json()["code"] for p in ("0000-0000-0000-0000", "nonsense", "A" * 16, "")}
    assert answers == {"recovery_not_recognised"}
    for _ in range(6):
        b.post("/api/workspace/recover", {"phrase": "0000-0000-0000-0000"})
    assert b.post("/api/workspace/recover", {"phrase": "0000-0000-0000-0000"}).get_json()["code"] == "recovery_rate_limited"


def test_a_revoked_workspace_cannot_be_recovered_and_the_server_never_keeps_the_phrase(tmp_path):
    app, registry = shared_app(tmp_path)
    a = started(app, "198.51.100.1")
    phrase = phrase_of(a)
    raw = phrase.replace("-", "")
    blob = b"".join(p.read_bytes() for p in (tmp_path / "data").rglob("workspaces.db*"))
    assert phrase.encode() not in blob and raw.encode() not in blob
    registry.revoke(a.workspace_id)
    assert started(app, "198.51.100.2").post("/api/workspace/recover", {"phrase": phrase}).status_code == 404


def test_the_phrase_is_shown_once_and_never_cached(tmp_path):
    app, _ = shared_app(tmp_path)
    a = started(app, "198.51.100.1")
    assert a.get("/api/workspace/recovery").get_json() == {"has_phrase": False}
    r = a.post("/api/workspace/recovery")
    assert "no-store" in r.headers["Cache-Control"]
    assert a.get("/api/workspace/recovery").get_json() == {"has_phrase": True} and "phrase" not in a.get("/api/workspace/recovery").get_json()


def test_bad_bodies_and_a_missing_token_are_refused(tmp_path):
    app, _ = shared_app(tmp_path)
    a = started(app, "198.51.100.1")
    assert a.post("/api/workspace/recover", {"phrase": "x", "extra": 1}).status_code == 422
    assert a.post("/api/workspace/recover", {}).status_code == 422
    assert a.post("/api/workspace/recovery", {"x": 1}).status_code == 422
    assert a.post("/api/workspace/recovery", headers={"X-EventMate-CSRF": "nope"}).status_code == 403


def test_a_local_install_has_no_cookie_to_lose_so_none_of_this_exists(tmp_path):
    c = create_app(EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="r"), today_fn=lambda: TODAY, poll_seconds=0).test_client()
    token = c.get("/api/session").get_json()["csrf"]
    h = {"X-EventMate-CSRF": token, "Content-Type": "application/json"}
    assert c.get("/api/workspace/recovery").status_code == 404
    assert c.post("/api/workspace/recovery", data="{}", headers=h).status_code == 404
    assert c.post("/api/workspace/recover", data='{"phrase": "x"}', headers=h).status_code == 404


def test_a_directory_made_before_recovery_phrases_gains_the_column_and_keeps_its_workspaces(tmp_path):
    root = tmp_path / "d"
    root.mkdir()
    db = sqlite3.connect(root / "workspaces.db")
    db.execute("CREATE TABLE workspaces (id TEXT PRIMARY KEY, cred_hash TEXT UNIQUE, created REAL NOT NULL, last_seen REAL NOT NULL)")
    db.execute("INSERT INTO workspaces VALUES ('ws-0123456789abcdef', 'h', 1, 1)")
    db.commit()
    db.close()
    d = W.WorkspaceDirectory(root)
    assert d.exists("ws-0123456789abcdef") and not d.has_recovery("ws-0123456789abcdef")
