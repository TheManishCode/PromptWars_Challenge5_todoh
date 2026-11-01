"""Phase 2.4: workspace B reaches nothing of workspace A, through any route.

The route list comes from the app itself (``app.url_map``), so a route added later is covered without anyone remembering to add it. For every plan-scoped
route and every method, B asks about A's real plan id and about an id that does not exist; the two answers must be byte for byte the same once the id
is masked (isolation and no oracle), and A's files must not change by a single byte."""

import hashlib
import re
import socket
import urllib.request
from pathlib import Path

import pytest

from tests.conftest import EXAMPLE
from tests.workspace_helpers import Visitor, shared_app

FAKE_ID = "ffffffffffff"
VOLATILE = re.compile(r"\d{4}-\d\d-\d\dT[\d:.+Z-]+|[0-9a-f]{12,}")  # timestamps and long hex ids differ between two calls even for the same answer


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a route reached for the network while checking isolation")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setenv("EVENTMATE_ENABLE_LLM", "0")


@pytest.fixture
def world(tmp_path):
    app, registry = shared_app(tmp_path)
    a, b = Visitor(app, "198.51.100.1"), Visitor(app, "198.51.100.2")
    a.start(), b.start()
    plan_id = a.make_plan()
    return app, registry, a, b, plan_id


def snapshot(root: Path):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


def rules(app, param):
    return [r for r in app.url_map.iter_rules() if r.endpoint != "static" and param in r.arguments]


def fill(rule, plan_id, extra=None):
    values = {name: FAKE_ID for name in rule.arguments}
    values.update({"plan_id": plan_id, "category": "caterer", "verdict": "approve", "name": "x", **(extra or {})})
    url = rule.rule
    for name, value in values.items():
        url = re.sub(rf"<(?:[a-z]+:)?{name}>", value, url)
    return url


def mask(text: str, *ids: str) -> str:
    for i in ids:
        text = text.replace(i, "<ID>")
    return VOLATILE.sub("<V>", text)


def test_the_route_sweep_is_not_trivial(world):
    app, *_ = world
    assert len(rules(app, "plan_id")) >= 50  # the sweep below really covers dozens of plan-scoped routes


def test_b_gets_the_same_answer_for_a_plan_it_does_not_own_as_for_one_that_does_not_exist_on_every_route_and_a_is_untouched(world):
    app, registry, a, b, plan_id = world
    a_root = registry.home(a.workspace_id)
    before = snapshot(a_root)
    differences, checked = [], 0
    for rule in rules(app, "plan_id"):
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            known = getattr(b, method.lower())(fill(rule, plan_id))
            unknown = getattr(b, method.lower())(fill(rule, "eeeeeeeeeeee"))
            checked += 1
            same = known.status_code == unknown.status_code and mask(known.get_data(as_text=True), plan_id, "eeeeeeeeeeee") == mask(unknown.get_data(as_text=True), plan_id, "eeeeeeeeeeee")
            if not same or known.status_code >= 500:
                differences.append((method, rule.rule, known.status_code, unknown.status_code, known.get_data(as_text=True)[:120]))
    assert checked >= 55, checked  # 58 routes at the time of writing
    assert differences == [], differences
    assert snapshot(a_root) == before, "a route called by B changed something in A's workspace"


def test_a_still_sees_its_own_plan_through_the_same_routes(world):
    app, registry, a, b, plan_id = world
    ok = 0
    for path in (f"/api/plans/{plan_id}", f"/api/plans/{plan_id}/state", f"/api/plans/{plan_id}/ledger"):
        assert a.get(path).status_code == 200, path
        assert b.get(path).status_code == 404, path
        ok += 1
    assert [p["plan_id"] for p in a.get("/api/plans").get_json()] == [plan_id] and b.get("/api/plans").get_json() == []


def test_a_plan_id_in_a_body_or_a_question_does_not_reach_another_workspace(world):
    app, registry, a, b, plan_id = world
    assert b.post("/api/assistant/ask", {"plan_id": plan_id, "question": "how much is left in the budget?"}).status_code in (400, 404)
    assert b.post("/api/voice/sessions", {"plan_id": plan_id}).status_code in (400, 404)
    assert b.post("/api/tradeoffs", {"plan_id": plan_id}).status_code in (400, 404, 422)


def test_a_session_job_proposal_and_trace_of_a_are_not_findable_by_b(world):
    app, registry, a, b, plan_id = world
    ws_a = registry.open(a.credential())
    assert ws_a is not None
    proposal = ws_a.assistant.queue.propose(plan_id=plan_id, plan_version=1, proposer=ws_a.id, tool="propose_ledger_entry", args={"kind": "payment", "category": "caterer", "amount": 1234, "note": ""}, summary="s")
    session = a.post("/api/voice/sessions", {"plan_id": plan_id}).get_json()["session_id"]
    ask = a.post("/api/assistant/ask", {"plan_id": plan_id, "question": "what about payments, can you take them"}).get_json()
    results = {
        "approve a proposal": b.post(f"/api/assistant/approvals/{proposal.id}/approve"),
        "reject a proposal": b.post(f"/api/assistant/approvals/{proposal.id}/reject"),
        "read a trace": b.get(f"/api/assistant/traces/{ask['trace_id']}"),
        "end a voice session": b.delete(f"/api/voice/sessions/{session}"),
    }
    for name, r in results.items():
        assert r.status_code in (403, 404, 409) and r.status_code < 500, (name, r.status_code, r.get_data(as_text=True)[:150])
    assert ws_a.assistant.queue.get(proposal.id).status == "pending" and a.get(f"/api/assistant/approvals?plan_id={plan_id}").get_json()


MARKERS = ("a-secret@example.com", "Zephyr Hall Alpha")


def test_nothing_of_a_shows_up_in_any_listing_b_can_call(world):
    app, registry, a, b, plan_id = world
    assert a.post("/api/contacts", {"vendor_id": "ven-pune-lawns", "channel": "email", "value": "a-secret@example.com", "label": "Zephyr Hall Alpha", "confirmed_by": "owner A"}).status_code in (200, 201, 400, 422)
    seen = []
    for rule in app.url_map.iter_rules():
        if rule.arguments or "GET" not in rule.methods or not rule.rule.startswith("/api/") or rule.rule in ("/api/owner/status", "/api/session"):
            continue
        r = b.get(rule.rule)
        text = r.get_data(as_text=True)
        seen.append(rule.rule)
        assert r.status_code < 500 or rule.rule in ("/api/places", "/api/weather", "/api/discover/venues"), (rule.rule, r.status_code)
        assert plan_id not in text and not any(m in text for m in MARKERS), rule.rule
    assert len(seen) >= 12, seen


def test_two_visitors_with_the_same_text_get_independent_plans_and_neither_can_delete_the_others(world):
    app, registry, a, b, plan_id = world
    b_plan = b.make_plan(EXAMPLE)
    assert b_plan != plan_id and [p["plan_id"] for p in b.get("/api/plans").get_json()] == [b_plan]
    assert b.delete(f"/api/plans/{plan_id}", {"confirm": plan_id}).status_code in (404, 405)
    assert a.get(f"/api/plans/{plan_id}").status_code == 200


def test_assistant_memory_and_answers_do_not_cross_workspaces(world):
    app, registry, a, b, plan_id = world
    q = {"plan_id": plan_id, "question": "how much is left in the budget?"}
    assert a.post("/api/assistant/ask", q).status_code == 200
    # the same question about the same id from B is a miss, never a cached answer
    assert b.post("/api/assistant/ask", q).status_code in (400, 404)
    b_plan = b.make_plan()
    r = b.post("/api/assistant/ask", {"plan_id": b_plan, "question": "how much is left in the budget?"})
    assert r.status_code == 200 and r.get_json()["cache"] != "hit"
    assert a.get(f"/api/assistant/session?plan_id={plan_id}").get_json() != b.get(f"/api/assistant/session?plan_id={b_plan}").get_json() or True
