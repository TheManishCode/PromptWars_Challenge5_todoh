"""Page watches through the web app and the background round (architecture L9). Local owner only; a shared host refuses them, and so it refuses missions (same rule)."""

import json
from datetime import timedelta

import pytest

from eventmate.integrations.web_access import Page
from eventmate.service import EventMate
from eventmate.web.app import create_app
from tests.conftest import EXAMPLE, TODAY
from tests.workspace_helpers import Visitor, shared_app

URL = "https://venue.example/packages"


class Pages:
    def __init__(self, text):
        self.text, self.calls = text, 0

    def fetch(self, url):
        self.calls += 1
        return Page(url=url, title="t", text=self.text)


@pytest.fixture
def em(tmp_path):
    e = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="w")
    e.pages = Pages("Hall A\nPackage: Rs 35,000 per day")
    e.contact_fetcher = lambda: e.pages
    return e


@pytest.fixture
def pid(em):
    out = em.plan(EXAMPLE, today=TODAY)
    assert out.plan is not None, "needs the solvers: set EVENTMATE_FD and EVENTMATE_VAL"
    return out.plan.plan_id


@pytest.fixture
def web(em):
    c = create_app(em, today_fn=lambda: TODAY, poll_seconds=0).test_client()
    token = c.get("/api/session").get_json()["csrf"]

    def call(method, url, body=None, csrf=token):
        h = {"X-EventMate-CSRF": csrf, "Content-Type": "application/json"} if method != "get" else {}
        return getattr(c, method)(url, data=json.dumps(body) if body is not None else None, headers=h)

    return call


def test_add_list_resume_and_remove_a_watch(web, pid):
    r = web("post", "/api/watches", {"subject": pid, "url": URL, "condition": "price_below", "amount_inr": 30000})
    w = r.get_json()
    assert r.status_code == 201 and w["host"] == "venue.example" and "last_lines" not in w and w["status"] == "active"
    assert [x["watch_id"] for x in web("get", "/api/watches").get_json()] == [w["watch_id"]]
    assert web("post", f"/api/watches/{w['watch_id']}/resume", {}).status_code == 200
    assert web("delete", f"/api/watches/{w['watch_id']}").status_code == 200 and web("get", "/api/watches").get_json() == []
    assert web("delete", f"/api/watches/{w['watch_id']}").status_code == 404


def test_a_watch_needs_a_subject_this_workspace_knows_and_a_sane_body(web, pid):
    base = {"subject": pid, "url": URL, "condition": "change"}
    assert web("post", "/api/watches", {**base, "subject": "ext_" + "f" * 24}).status_code == 400  # not a business this workspace found
    for bad in ({"url": "file:///etc/passwd"}, {"url": "ftp://x.example/a"}, {"condition": "contains"}, {"condition": "price_below"}, {"interval_minutes": 5},
                {"subject": "../x"}, {"extra": 1}, {"text": "x"}):
        assert web("post", "/api/watches", {**base, **bad}).status_code in (400, 422), bad
    assert web("post", "/api/watches", base, csrf="nope").status_code == 403
    assert web("get", "/api/watches").get_json() == []


def test_the_background_round_checks_due_watches_and_records_what_a_page_says(em, pid):
    w = em.add_watch(pid, URL, "contains", text="december")
    em.background_round()
    assert em.last_watch_report["checked"] == [w.watch_id] and em.evidence.records(pid) == []
    em.pages.text += "\nDecember dates open"
    em.outreach_store.now = lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc) + timedelta(days=2)  # the next check is due
    em.background_round()
    assert em.last_watch_report["found"] and [r.state.value for r in em.evidence.records(pid)] == ["discovered"]


def test_the_owners_kill_switch_stops_watches_too(em, pid):
    em.add_watch(pid, URL, "change")
    em.kill_all_autoreplies()
    assert em.watch_runner().tick()["skipped"] and em.pages.calls == 0


def test_a_workspace_with_no_watches_makes_no_file_and_no_fetch(em, pid):
    em.background_round()
    assert not (em.store.root / "watches.db").exists() and em.pages.calls == 0


def test_a_shared_host_refuses_watches_and_missions_and_a_real_file_error_is_not_mistaken_for_that(tmp_path):
    app, _ = shared_app(tmp_path)
    v = Visitor(app)
    v.start()
    for method, url, body in [("get", "/api/watches", None), ("post", "/api/watches", {"subject": "x", "url": URL, "condition": "change"}), ("post", "/api/watches/abc/resume", {}),
                              ("delete", "/api/watches/abc", None), ("post", "/api/missions", {"brief": "x", "sender_name": "A", "categories": ["caterer"]}), ("get", "/api/missions/abc", None)]:
        r = getattr(v, method)(url, body) if body is not None else getattr(v, method)(url)
        assert r.status_code == 403 and "shared host" in r.get_json()["error"], (url, r.status_code)


def test_an_operating_system_permission_error_is_still_an_internal_error_not_a_polite_403(em, pid, monkeypatch):
    c = create_app(em, today_fn=lambda: TODAY, poll_seconds=0, rate_limit_per_minute=1000).test_client()
    monkeypatch.setattr(em.watches, "all", lambda: (_ for _ in ()).throw(PermissionError(13, "Permission denied", "/secret/path/watches.db")))
    r = c.get("/api/watches")
    assert r.status_code == 500 and "/secret/path" not in r.get_data(as_text=True)
