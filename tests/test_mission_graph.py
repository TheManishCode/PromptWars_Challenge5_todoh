"""The mission graph (ADR-059): discovery to approved outreach, with two human pauses. Real EventMate, fake search, fake pages, fake SMTP."""

import pytest
from pydantic import ValidationError

from eventmate.orchestration import MissionRunner
from eventmate.orchestration.models import ContactDecision, DraftDecision
from eventmate.orchestration.stubs import BrowserResearcher, book_event
from tests.conftest import TODAY
from tests import test_discovery_e2e as e2e

COLLEGE = e2e.COLLEGE
clock, world, em = e2e.clock, e2e.world, e2e.em  # the fixtures of the discovery flow, reused as they are


@pytest.fixture
def runner(em, tmp_path):
    r = MissionRunner(em, tmp_path / "missions.sqlite")
    yield r
    r.close()


def start(runner, **kw):
    return runner.start(COLLEGE, "Asha", ["caterer"], today=TODAY, source="places", mission_id=kw.pop("mission_id", "m1"), **kw)


def confirm_all(view, by="Asha"):
    return {"decisions": [{"vendor_id": b["vendor_id"], "candidate_id": b["candidates"][0]["candidate_id"], "action": "confirm", "by": by} for b in view.prompt["businesses"]]}


def test_a_brief_runs_to_the_contact_review_and_sends_nothing(runner, world):
    v = start(runner)
    assert v.status == "waiting" and v.waiting_for == "contact_review" and v.plan_id
    names = {b["name"] for b in v.prompt["businesses"]}
    assert names == {"Sharma Caterers", "Kapoor Food Services"}
    assert all(c["source_url"].startswith("https://") for b in v.prompt["businesses"] for c in b["candidates"])
    assert world.outbox == []


def test_the_whole_flow_needs_two_human_answers_and_then_sends_exactly_the_approved_text(runner, em, world):
    v = start(runner)
    sharma = next(b for b in v.prompt["businesses"] if b["name"] == "Sharma Caterers")
    pick = {"decisions": [{"vendor_id": sharma["vendor_id"], "candidate_id": sharma["candidates"][0]["candidate_id"], "action": "confirm", "by": "Asha"}]}
    v = runner.resume("m1", pick)
    assert v.waiting_for == "draft_approval" and world.outbox == []
    (d,) = v.prompt["drafts"]
    assert d["vendor_name"] == "Sharma Caterers" and d["content_hash"]  # Kapoor was never confirmed, so it gets no draft
    v = runner.resume("m1", {"decisions": [{"draft_id": d["draft_id"], "action": "approve", "content_hash": d["content_hash"]}]})
    assert v.status == "done" and v.stage == "awaiting_replies"
    (sent,) = world.outbox
    assert sent["To"] == d["recipient"] and d["body"] in sent.get_content()
    assert "250,000" not in sent.get_content()


def test_approving_with_the_wrong_hash_sends_nothing_and_the_mission_keeps_waiting_for_a_valid_answer(runner, world):
    v = runner.resume("m1", confirm_all(start(runner)))
    d = v.prompt["drafts"][0]
    v = runner.resume("m1", {"decisions": [{"draft_id": d["draft_id"], "action": "approve", "content_hash": "0" * 64}]})
    assert world.outbox == [] and any("changed" in t for t in v.trail)


def test_a_denied_draft_is_never_sent(runner, world):
    v = runner.resume("m1", confirm_all(start(runner)))
    v = runner.resume("m1", {"decisions": [{"draft_id": d["draft_id"], "action": "deny"} for d in v.prompt["drafts"]]})
    assert world.outbox == [] and v.status == "done"


def test_rejecting_every_address_ends_without_a_draft(runner, em, world):
    v = start(runner)
    nope = {"decisions": [{"vendor_id": b["vendor_id"], "candidate_id": c["candidate_id"], "action": "reject"} for b in v.prompt["businesses"] for c in b["candidates"]]}
    v = runner.resume("m1", nope)
    assert v.status == "done" and v.stage == "nothing_to_send" and world.outbox == [] and em.outreach(v.plan_id) == []


def test_a_mission_survives_a_restart(em, tmp_path, world):
    db = tmp_path / "again.sqlite"
    first = MissionRunner(em, db)
    v = start(first)
    first.close()
    second = MissionRunner(em, db)
    try:
        assert second.view("m1").waiting_for == "contact_review"
        assert second.resume("m1", confirm_all(v)).waiting_for == "draft_approval"
    finally:
        second.close()


def test_a_confirmed_address_belongs_to_the_business_but_each_mission_drafts_into_its_own_plan_and_sends_nothing(runner, em, world):
    v = runner.resume("m1", confirm_all(start(runner)))
    assert len(v.prompt["drafts"]) == 2
    v2 = runner.start(COLLEGE, "Asha", ["caterer"], today=TODAY, source="places", mission_id="m2")
    assert v2.plan_id != v.plan_id and v2.waiting_for == "draft_approval"  # nothing left to review: the addresses are already confirmed (ADR-049)
    assert {d.plan_id for d in em.outreach(v.plan_id)} == {v.plan_id} and {d.plan_id for d in em.outreach(v2.plan_id)} == {v2.plan_id}
    assert world.outbox == []


# ----------------------------------------------------------------- adversarial


@pytest.mark.parametrize("bad", ["", "../x", "a b", "x" * 65, "m;1", "m\n1"])
def test_hostile_mission_ids_are_refused(runner, bad):
    with pytest.raises(ValueError):
        runner.view(bad)
    with pytest.raises(ValueError):
        runner.start(COLLEGE, "Asha", ["caterer"], today=TODAY, mission_id=bad)


def test_an_unknown_or_duplicate_mission_is_refused(runner):
    with pytest.raises(ValueError, match="unknown"):
        runner.view("nope")
    start(runner)
    with pytest.raises(ValueError, match="already exists"):
        start(runner)


def test_a_decision_for_the_wrong_question_or_with_extra_fields_is_refused_before_the_graph_sees_it(runner, world):
    v = start(runner)
    with pytest.raises(ValidationError):
        runner.resume("m1", {"decisions": [{"draft_id": "d1", "action": "approve", "content_hash": "a" * 64}]})  # an approval offered to a contact review
    with pytest.raises(ValidationError):
        runner.resume("m1", {"decisions": [], "send_now": True})
    assert runner.view("m1").waiting_for == "contact_review" and world.outbox == []
    assert v.waiting_for == "contact_review"


def test_confirming_needs_a_name_and_approving_needs_a_hash():
    with pytest.raises(ValidationError):
        ContactDecision(vendor_id="ext_1", candidate_id="c1", action="confirm")
    with pytest.raises(ValidationError):
        ContactDecision(vendor_id="ext_1", candidate_id="c1", action="reject", by="Asha")
    with pytest.raises(ValidationError):
        DraftDecision(draft_id="d1", action="approve")


def test_a_draft_of_another_plan_cannot_be_approved_through_a_mission(runner, em, world):
    v = runner.resume("m1", confirm_all(start(runner)))
    ids = {d["draft_id"] for d in v.prompt["drafts"]}
    v = runner.resume("m1", {"decisions": [{"draft_id": "someone-elses", "action": "approve", "content_hash": "b" * 64}]})
    assert world.outbox == [] and any("not a draft of this mission" in t for t in v.trail) and ids


def test_a_suppressed_address_is_not_drafted(runner, em, world):
    v = start(runner)
    for b in v.prompt["businesses"]:
        for c in b["candidates"]:
            em.suppress_address("email", c["value"], "Asha", "asked us not to write")
    v = runner.resume("m1", confirm_all(v))
    assert world.outbox == [] and v.status == "done" and v.stage in ("nothing_to_send", "failed") and em.outreach(v.plan_id) == []


def test_a_brief_that_cannot_be_planned_stops_before_any_search(em, tmp_path):
    r = MissionRunner(em, tmp_path / "x.sqlite")
    try:
        v = r.start("Plan a 5000-person wedding in Pune on December 12 under INR 1,000.", "Asha", ["caterer"], today=TODAY, mission_id="bad")
        assert v.status == "failed" and v.failure and "caterer" not in " ".join(v.trail)
        assert em.shortlist.entries(v.plan_id or "none") == [] if v.plan_id else True
    finally:
        r.close()


def test_what_is_not_built_raises():
    with pytest.raises(NotImplementedError):
        book_event()
    with pytest.raises(NotImplementedError):
        BrowserResearcher().research("x")


# ----------------------------------------------------------------------- CLI


def cli(tmp_path, *args):
    from eventmate.__main__ import main

    return main(["--allow-no-symbolic", "--plan-dir", str(tmp_path / "p"), "--log-dir", str(tmp_path / "l"), "mission", *args])


def test_the_cli_refuses_an_unknown_mission_a_missing_name_a_missing_file_and_bad_json(tmp_path, capsys):
    assert cli(tmp_path, "show", "nope") == 2
    assert cli(tmp_path, "start", COLLEGE, "--category", "caterer") == 2
    assert cli(tmp_path, "resume", "nope") == 2
    bad = tmp_path / "d.json"
    bad.write_text("{not json", encoding="utf-8")
    assert cli(tmp_path, "resume", "nope", "--decision", str(bad)) == 2
    assert "error" in capsys.readouterr().err


# ---------------------------------------------------------------- web routes

import json as _json

from eventmate.web.app import create_app


@pytest.fixture
def web(em):
    c = create_app(em, today_fn=lambda: TODAY, poll_seconds=0).test_client()
    token = c.get("/api/session").get_json()["csrf"]

    def call(method, url, body=None, csrf=token):
        h = {"X-EventMate-CSRF": csrf, "Content-Type": "application/json"} if method == "post" else {}
        return getattr(c, method)(url, data=_json.dumps(body) if body is not None else None, headers=h)

    return call


def test_web_start_show_resume_send_only_the_approved_text(web, world):
    r = web("post", "/api/missions", {"brief": COLLEGE, "sender_name": "Asha", "categories": ["caterer"], "source": "places"})
    assert r.status_code == 201 and r.get_json()["waiting_for"] == "contact_review"
    mid = r.get_json()["mission_id"]
    assert web("get", f"/api/missions/{mid}").get_json()["status"] == "waiting"
    r = web("post", f"/api/missions/{mid}/resume", confirm_all(runner_view(r.get_json())))
    assert r.status_code == 200 and r.get_json()["waiting_for"] == "draft_approval" and world.outbox == []
    (d,) = list(r.get_json()["prompt"]["drafts"])[:1]
    r = web("post", f"/api/missions/{mid}/resume", {"decisions": [{"draft_id": d["draft_id"], "action": "approve", "content_hash": d["content_hash"]}]})
    assert r.get_json()["status"] == "done" and len(world.outbox) == 1


def runner_view(doc):
    from eventmate.orchestration.models import MissionView

    return MissionView.model_validate(doc)


def test_web_refuses_without_csrf_and_rejects_bad_input(web, world):
    assert web("post", "/api/missions", {"brief": "x", "sender_name": "A", "categories": ["caterer"]}, csrf="nope").status_code == 403
    assert web("post", "/api/missions", {"brief": "x", "sender_name": "A", "categories": []}).status_code == 422
    assert web("post", "/api/missions", {"brief": "x", "sender_name": "A", "categories": ["caterer"], "extra": 1}).status_code == 422
    assert web("get", "/api/missions/nope").status_code == 400
    assert web("get", "/api/missions/bad.id").status_code == 400
    assert web("post", "/api/missions/nope/resume", {"decisions": []}).status_code == 400
    assert world.outbox == []
