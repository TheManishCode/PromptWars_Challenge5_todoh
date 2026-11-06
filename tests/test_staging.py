"""The brief between the reader and the planner: staged on the server, corrected there, committed once, built on a queue, read back as a graph.

Each test is a way the old arrangement (the page holds the draft and the confirm runs inline) could go wrong: a draft changed between review and commit, a double click, an edit that breaks the
schema, a brief that timed out, a build that fails, two commits at once."""

import json
import threading
from datetime import UTC, datetime, timedelta

import pytest

from eventmate.planning.staging import PENDING_TTL, StageConflict, StageGone, StagingStore
from eventmate.service import EventMate
from eventmate.web.app import create_app
from tests.conftest import TODAY

FULL = "wedding for 100 guests in Pune on 12 December 2026, budget 2 lakh"
PRICE_TOO_LOW = "wedding for 100 guests in Pune on 12 December 2026, budget 5000"


@pytest.fixture
def em(tmp_path):
    return EventMate(log_dir=tmp_path / "logs", plan_dir=tmp_path / "plans", run_id="staging")


@pytest.fixture
def api(em):
    client = create_app(em, today_fn=lambda: TODAY, commit_inline=True, rate_limit_per_minute=10_000).test_client()
    token = client.get("/api/session").get_json()["csrf"]

    class Api:
        def call(self, method, url, body=None):
            r = client.open(url, method=method, data=json.dumps(body) if body is not None else None, headers={"X-EventMate-CSRF": token, "Content-Type": "application/json"})
            return r.status_code, r.get_json(silent=True)

        def say(self, text, staged=None, **extra):
            return self.call("POST", "/api/intake", {"messages": [text], **({"staged_id": staged} if staged else {}), **extra})

        def commit(self, t, **over):
            return self.call("POST", f"/api/intake/{t['staged_id']}/commit", {"version": t["version"], "draft_hash": t["draft_hash"], **over})

    return Api()


def test_a_reading_is_staged_pending_and_every_correction_overwrites_it(api, em):
    s, t = api.say("wedding in Pune")
    assert s == 200 and t["state"] == "pending_validation" and t["version"] == 1 and len(t["draft_hash"]) == 64
    s, t2 = api.say("100 guests", t["staged_id"])
    assert t2["staged_id"] == t["staged_id"] and t2["version"] == 2 and t2["draft_hash"] != t["draft_hash"]
    stored = em.staging.require(t["staged_id"])
    assert stored.draft.guest_count == 100 and stored.messages == ("wedding in Pune", "100 guests")


def test_the_staged_draft_is_the_one_corrected_not_the_one_the_caller_sends_back(api):
    _, t = api.say(FULL)
    forged = {**t["draft"], "guest_count": 1, "budget_total": 1}
    _, t2 = api.say("make it 150 guests", t["staged_id"], draft=forged)
    assert t2["draft"]["guest_count"] == 150 and t2["draft"]["budget_total"] == 200000


def test_values_typed_on_the_card_pass_the_schema_and_answer_what_was_asked(api):
    _, t = api.say("wedding in Pune")
    assert "guest_count" in t["missing"]
    s, e = api.call("PATCH", f"/api/intake/{t['staged_id']}/fields", {"fields": {"guest_count": 80, "budget_total": 150000, "event_date": "2026-12-12"}, "version": t["version"]})
    assert s == 200 and e["draft"]["guest_count"] == 80 and e["version"] == 2 and e["ready"] and any("80" in c for c in e["changes"])
    _, c = api.call("PATCH", f"/api/intake/{t['staged_id']}/fields", {"fields": {"city": "bangalore"}, "version": 2})
    assert c["draft"]["city"] == "Bengaluru"


@pytest.mark.parametrize("fields,word", [
    ({"guest_count": 0}, "guest_count"), ({"guest_count": 10**9}, "guest_count"), ({"budget_total": -5}, "budget_total"), ({"event_date": "not a date"}, "event_date"),
    ({"event_date": "2020-01-01"}, "past"), ({"city": "<script>"}, "city"), ({"event_type": "rave"}, "event_type"), ({"unresolved": "none"}, "can be changed"), ({"settled": 5}, "can be changed"), ({"city": "x" * 90}, "city"),
])
def test_an_edit_that_would_break_the_schema_is_refused_and_changes_nothing(api, em, fields, word):
    _, t = api.say(FULL)
    s, e = api.call("PATCH", f"/api/intake/{t['staged_id']}/fields", {"fields": fields, "version": t["version"]})
    assert s in (400, 422) and word in json.dumps(e), e
    assert em.staging.require(t["staged_id"]).version == t["version"]


def test_a_correction_made_against_an_old_version_is_refused(api):
    _, t = api.say(FULL)
    api.call("PATCH", f"/api/intake/{t['staged_id']}/fields", {"fields": {"guest_count": 90}, "version": 1})
    s, e = api.call("PATCH", f"/api/intake/{t['staged_id']}/fields", {"fields": {"guest_count": 95}, "version": 1})
    assert s == 409 and e["code"] == "brief_changed"


def test_commit_builds_the_plan_once_and_a_second_done_changes_nothing(api, em):
    _, t = api.say(FULL)
    s, c = api.commit(t)
    assert s in (200, 202)
    s, st = api.call("GET", f"/api/intake/{t['staged_id']}/status")
    assert st["state"] == "done" and st["result"]["plan_id"], st
    s2, again = api.commit(t)
    assert s2 == 200 and again["state"] == "done"
    assert len(em.store.list_plans()) == 1, "pressing done twice built one plan"


def test_a_draft_that_changed_since_it_was_reviewed_is_not_built(api, em):
    _, t = api.say(FULL)
    api.call("PATCH", f"/api/intake/{t['staged_id']}/fields", {"fields": {"guest_count": 90}, "version": 1})
    s, e = api.commit(t)  # the commit still carries version 1 and the old hash
    assert s == 409 and e["code"] == "brief_changed"
    s, e = api.commit({**t, "version": 2}, draft_hash="0" * 64)
    assert s == 409
    assert em.store.list_plans() == [] and em.staging.require(t["staged_id"]).state == "pending_validation"


@pytest.mark.parametrize("text,word", [("wedding in Pune", "still needed"), ("wedding for 100 guests in Mysuru on 12 December 2026, budget 2 lakh", "no vendor records")])
def test_a_brief_that_is_not_ready_or_cannot_be_built_is_refused_before_anything_starts(api, em, text, word):
    _, t = api.say(text)
    s, e = api.commit(t)
    assert s == 400 and word in e["error"]
    assert em.staging.require(t["staged_id"]).state == "pending_validation" and em.store.list_plans() == []


def test_a_brief_being_built_cannot_be_corrected(api):
    _, t = api.say(FULL)
    api.commit(t)
    s, e = api.call("PATCH", f"/api/intake/{t['staged_id']}/fields", {"fields": {"guest_count": 90}, "version": t["version"]})
    assert s == 409 and e["code"] == "brief_closed"
    assert api.say("make it 90 guests", t["staged_id"])[0] == 409


def test_a_failed_build_says_why_and_can_be_corrected_and_sent_again(api, em):
    _, t = api.say(PRICE_TOO_LOW)
    api.commit(t)
    _, st = api.call("GET", f"/api/intake/{t['staged_id']}/status")
    assert st["state"] == "failed" and st["error"] and em.store.list_plans() == []
    s, r = api.call("POST", f"/api/intake/{t['staged_id']}/reopen", {})
    assert s == 200 and r["state"] == "pending_validation" and r["version"] > t["version"]
    _, e = api.call("PATCH", f"/api/intake/{t['staged_id']}/fields", {"fields": {"budget_total": 200000}, "version": r["version"]})
    api.commit(e)
    assert api.call("GET", f"/api/intake/{t['staged_id']}/status")[1]["state"] == "done"


def test_a_finished_plan_reads_back_as_a_graph(api):
    _, t = api.say(FULL)
    api.commit(t)
    plan_id = api.call("GET", f"/api/intake/{t['staged_id']}/status")[1]["result"]["plan_id"]
    s, g = api.call("GET", f"/api/plans/{plan_id}/graph")
    ids = {n["id"] for n in g["nodes"]}
    assert s == 200 and "event" in ids and all(e["source"] in ids and e["target"] in ids for e in g["edges"])


def test_an_unknown_or_malformed_id_is_a_clean_refusal(api):
    for bad in ("0" * 24, "nope", "../../etc/passwd"):
        s, e = api.call("GET", f"/api/intake/{bad}/status")
        assert s in (404, 410), (bad, s)
    assert api.call("POST", "/api/intake", {"messages": ["x"], "staged_id": "../x"})[0] == 422


# ------------------------------------------------------------------ the store on its own


def test_a_brief_nobody_approved_expires_and_the_page_falls_back_to_its_own_draft(tmp_path):
    clock = [datetime(2026, 10, 30, 9, tzinfo=UTC)]
    store = StagingStore(tmp_path, now=lambda: clock[0])
    em = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="exp")
    t = em.intake_turn([FULL], today=TODAY)
    em.staging = store
    row = store.create(t.draft, [FULL], "rule:parser")
    clock[0] += PENDING_TTL + timedelta(seconds=1)
    assert store.require(row.staged_id).state == "expired"
    with pytest.raises(StageGone):
        store.overwrite(row.staged_id, t.draft, [], "rule:parser")
    with pytest.raises(StageGone):
        store.begin_commit(row.staged_id, row.version, row.draft_hash)
    again = em.intake_turn(["150 guests"], t.draft, TODAY, staged_id=row.staged_id)  # the caller's draft carries on in a new staged brief
    assert again.staged_id != row.staged_id and again.draft.guest_count == 150


def test_finished_and_expired_briefs_are_swept_when_a_new_one_is_made(tmp_path):
    clock = [datetime(2026, 10, 30, 9, tzinfo=UTC)]
    store = StagingStore(tmp_path, now=lambda: clock[0])
    em = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="sweep")
    t = em.intake_turn([FULL], today=TODAY)
    old = store.create(t.draft)
    clock[0] += timedelta(days=2)
    fresh = store.create(t.draft)
    files = {p.name for p in (tmp_path / "staging").glob("staged-*.jsonl")}
    assert files == {f"staged-{fresh.staged_id}.jsonl"}, "the one past its time went; the new one stayed"
    assert store.get(old.staged_id) is None


def test_two_commits_at_once_start_exactly_one_build(tmp_path):
    em = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="race")
    row = em.staging.create(em.intake_turn([FULL], today=TODAY).draft, [FULL])
    started, errors = [], []

    def go():
        try:
            started.append(em.staging.begin_commit(row.staged_id, row.version, row.draft_hash)[1])
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert started.count(True) == 1 and not errors


def test_a_stale_version_or_hash_is_a_conflict_at_the_store(tmp_path):
    em = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="conflict")
    row = em.staging.create(em.intake_turn([FULL], today=TODAY).draft, [FULL])
    with pytest.raises(StageConflict):
        em.staging.begin_commit(row.staged_id, row.version + 1, row.draft_hash)
    with pytest.raises(StageConflict):
        em.staging.begin_commit(row.staged_id, row.version, "f" * 64)


# ------------------------------------------------------------------ the queue


@pytest.fixture
def threaded(em):
    client = create_app(em, today_fn=lambda: TODAY, rate_limit_per_minute=10_000).test_client()
    token = client.get("/api/session").get_json()["csrf"]

    def call(method, url, body=None):
        r = client.open(url, method=method, data=json.dumps(body) if body is not None else None, headers={"X-EventMate-CSRF": token, "Content-Type": "application/json"})
        return r.status_code, r.get_json(silent=True)

    return call


def test_commit_returns_at_once_and_the_page_can_follow_a_slow_build(em, threaded, monkeypatch):
    release, started = threading.Event(), threading.Event()
    real = em.build_staged

    def slow(*a, **kw):
        started.set()
        assert release.wait(20)
        return real(*a, **kw)

    monkeypatch.setattr(em, "build_staged", slow)
    _, t = threaded("POST", "/api/intake", {"messages": [FULL]})
    s, c = threaded("POST", f"/api/intake/{t['staged_id']}/commit", {"version": t["version"], "draft_hash": t["draft_hash"]})
    assert s == 202 and c["state"] == "processing"
    assert started.wait(10)
    s, st = threaded("GET", f"/api/intake/{t['staged_id']}/status")  # answers while the build holds the workspace lock
    assert s == 200 and st["state"] == "processing"
    s, again = threaded("POST", f"/api/intake/{t['staged_id']}/commit", {"version": t["version"], "draft_hash": t["draft_hash"]})
    assert s == 202 and again["state"] == "processing", "a second done while it builds starts nothing"
    release.set()
    for _ in range(100):
        if threaded("GET", f"/api/intake/{t['staged_id']}/status")[1]["state"] == "done":
            break
        threading.Event().wait(0.1)
    assert threaded("GET", f"/api/intake/{t['staged_id']}/status")[1]["state"] == "done" and len(em.store.list_plans()) == 1


def test_a_full_queue_says_so_instead_of_growing():
    from eventmate.web.commit_queue import CommitQueue

    gate = threading.Event()
    q = CommitQueue(workers=1, waiting=1)
    assert q.submit(lambda: gate.wait(10)) and q.submit(lambda: None)
    assert not q.has_room() and q.submit(lambda: None) is False
    gate.set()


def test_a_job_that_raises_does_not_take_its_worker_down():
    from eventmate.web.commit_queue import CommitQueue

    q = CommitQueue(inline=True)
    assert q.submit(lambda: 1 / 0) and q.has_room()


def test_the_finished_payload_carries_the_graph(api):
    _, t = api.say(FULL)
    api.commit(t)
    res = api.call("GET", f"/api/intake/{t['staged_id']}/status")[1]["result"]
    assert res["graph"]["nodes"] and res["graph"]["edges"] and res["plan_id"]
