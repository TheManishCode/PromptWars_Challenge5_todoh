"""Checkpoint 6: a change in the user's words is read into a delta (nothing changes), then applied only when the user confirms it (architecture L5)."""

import json

import pytest

from eventmate.service import EventMate
from eventmate.web.app import create_app
from tests.conftest import EXAMPLE, TODAY
from tests.workspace_helpers import Visitor, shared_app


@pytest.fixture
def em(tmp_path):
    return EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="chg")


@pytest.fixture
def web(em):
    c = create_app(em, today_fn=lambda: TODAY, poll_seconds=0).test_client()
    token = c.get("/api/session").get_json()["csrf"]

    def call(method, url, body=None, csrf=token):
        h = {"X-EventMate-CSRF": csrf, "Content-Type": "application/json"} if method == "post" else {}
        return getattr(c, method)(url, data=json.dumps(body) if body is not None else None, headers=h)

    return call


@pytest.fixture
def pid(em):
    out = em.plan(EXAMPLE, today=TODAY)
    assert out.plan is not None, "needs the solvers: set EVENTMATE_FD and EVENTMATE_VAL"
    return out.plan.plan_id


def test_a_message_becomes_a_delta_and_changes_nothing(web, em, pid):
    r = web("post", f"/api/plans/{pid}/changes", {"message": "make it 80 guests and the budget 1.5 lakh"})
    doc = r.get_json()
    assert r.status_code == 200 and doc["delta"]["set_guest_count"] == 80 and doc["delta"]["set_budget_inr"] == 150_000 and doc["source"] == "rule:parser"
    assert em.store.latest(pid).version == 1  # reading a change is not making it


def test_the_confirmed_delta_is_applied_as_one_new_version(web, em, pid):
    delta = web("post", f"/api/plans/{pid}/changes", {"message": "now 80 guests, budget 1.5 lakh"}).get_json()["delta"]
    r = web("post", f"/api/plans/{pid}/changes/apply", {"delta": delta})
    assert r.status_code == 200 and r.get_json()["status"] == "replanned"
    plan = em.store.latest(pid)
    assert plan.version == 2 and plan.request.guest_count == 80 and plan.request.budget_total == 150_000


def test_a_new_step_goes_through_both_calls_and_lands_on_its_day(web, em, pid):
    turn = web("post", f"/api/plans/{pid}/changes", {"message": "please add a mehendi the night before"}).get_json()
    assert turn["delta"]["add_tasks"] == [] and turn["questions"][0]["field"] == "message"  # no model: the rules do not invent a task, they ask
    delta = {"add_tasks": [{"name": "Mehendi", "duration_minutes": 180, "days_before_event": 1}]}
    r = web("post", f"/api/plans/{pid}/changes/apply", {"delta": delta})
    assert r.status_code == 200
    assert any(i.task_id.startswith("extra_mehendi") for i in em.store.latest(pid).items)


def test_work_the_user_says_is_done_is_held_while_the_rest_moves_to_today(em, pid):
    from datetime import timedelta

    c = create_app(em, today_fn=lambda: TODAY + timedelta(days=10), poll_seconds=0).test_client()  # ten days on: an unpinned venue booking would have to move
    token = c.get("/api/session").get_json()["csrf"]
    venue = next(i for i in em.store.latest(pid).items if i.task_id == "book_venue")
    r = c.post(f"/api/plans/{pid}/changes/apply", data=json.dumps({"delta": {"set_guest_count": 80}, "done": ["book_venue"]}), headers={"X-EventMate-CSRF": token, "Content-Type": "application/json"})
    assert r.status_code == 200
    items = {i.task_id: i for i in em.store.latest(pid).items}
    assert items["book_venue"].start == venue.start < items["book_caterer"].start  # it happened, so it stays; what is left starts from today


def test_refusals_are_422_with_a_named_reason_and_save_nothing(web, em, pid):
    r = web("post", f"/api/plans/{pid}/changes/apply", {"delta": {"add_tasks": [{"name": "Mehendi"}]}})
    assert r.status_code == 422 and r.get_json()["violations"][0]["constraint"] == "new_task_needs_a_length"
    r = web("post", f"/api/plans/{pid}/changes/apply", {"delta": {"set_guest_count": 80}, "done": ["book_ghost"]})
    assert r.status_code == 422 and r.get_json()["violations"][0]["constraint"] == "unknown_task"
    assert em.store.latest(pid).version == 1


def test_bad_input_never_reaches_the_planner(web, em, pid):
    for url, body in [
        (f"/api/plans/{pid}/changes", {"message": ""}), (f"/api/plans/{pid}/changes", {"message": "x" * 2001}), (f"/api/plans/{pid}/changes", {"message": "ok", "extra": 1}),
        (f"/api/plans/{pid}/changes/apply", {"delta": {"set_guest_count": 0}}), (f"/api/plans/{pid}/changes/apply", {"delta": {"send_email": "x"}}),
        (f"/api/plans/{pid}/changes/apply", {"delta": {}, "done": ["../x"]}), (f"/api/plans/{pid}/changes/apply", {"delta": {}, "in_flight": {"book_venue": 0}}),
        (f"/api/plans/{pid}/changes/apply", {"delta": {}, "in_flight": {"book_venue": True}}), (f"/api/plans/{pid}/changes/apply", {"delta": {}, "done": ["a"] * 21}),
    ]:
        assert web("post", url, body).status_code == 422, (url, body)
    assert web("post", "/api/plans/bad.id/changes", {"message": "hi"}).status_code == 400
    assert web("post", "/api/plans/nope/changes", {"message": "hi"}).status_code == 404
    assert em.store.latest(pid).version == 1


def test_a_write_needs_the_csrf_token(web, pid):
    assert web("post", f"/api/plans/{pid}/changes", {"message": "80 guests"}, csrf="nope").status_code == 403
    assert web("post", f"/api/plans/{pid}/changes/apply", {"delta": {"set_guest_count": 80}}, csrf="nope").status_code == 403


def test_the_model_stage_is_traced_and_the_trace_holds_hashes_not_the_message(tmp_path):

    class Fake:
        name = "fake"

        def complete(self, system, user, max_tokens):
            return '{"set_guest_count": 80}'

    e = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="trace", llm=Fake())
    p = e.plan(EXAMPLE, today=TODAY).plan
    t = e.change_turn(p.plan_id, "now 80 guests, call me on 9876543210", TODAY)
    assert t.delta.set_guest_count == 80
    (entry,) = [x for x in e.logs.llm_traces.read() if x.prompt_id == "delta.extract"]
    assert entry.accepted and entry.input_text is None and "9876543210" not in entry.model_dump_json() and entry.input_sha256


def test_one_workspace_cannot_read_or_change_anothers_plan(tmp_path):
    app, _ = shared_app(tmp_path)
    a, b = Visitor(app, "198.51.100.1"), Visitor(app, "198.51.100.2")
    a.start(), b.start()
    plan = a.make_plan()
    assert a.post(f"/api/plans/{plan}/changes", {"message": "now 80 guests"}).status_code == 200
    assert b.post(f"/api/plans/{plan}/changes", {"message": "now 80 guests"}).status_code == 404
    assert b.post(f"/api/plans/{plan}/changes/apply", {"delta": {"set_guest_count": 80}}).status_code == 404
    assert a.get(f"/api/plans/{plan}").get_json()["request"]["guest_count"] == 120  # untouched
