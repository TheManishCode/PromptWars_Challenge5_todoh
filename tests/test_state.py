"""EventState: a read-only view built from the stores. It must be honest about sources, hold no personal details and never act."""

import json
import socket
from datetime import timedelta
from pathlib import Path

import pytest

import eventmate
from eventmate.assistant.agent import Assistant
from eventmate.assistant.identity import LOCAL_ORGANIZER
from eventmate.messaging.channels import Inbound
from eventmate.schemas import VendorCategory as C
from eventmate.service import EventMate
from eventmate.state import CLOSES_WHEN, MAX_CHANGES, build_state
from eventmate.web.app import create_app
from tests.conftest import EXAMPLE, TODAY
from tests.test_assistant import Scripted, answer, tool
from tests.test_conversations import Clock, FakeChannel

CONTACT = "sales@annapurna.in"


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def chans():
    return {"email": FakeChannel("email"), "whatsapp": FakeChannel("whatsapp")}


@pytest.fixture
def em(tmp_path, chans, clock):
    e = EventMate(log_dir=tmp_path / "logs", plan_dir=tmp_path / "plans", run_id="state", channels=chans)
    e.outreach_store.now = e.conversations.now = e.jobs_clock = clock  # the job worker too: its deadline check must use the test clock, not the wall clock
    return e


@pytest.fixture
def pid(em):
    return em.plan(EXAMPLE, today=TODAY).plan.plan_id


def vendor(state, category):
    return next(v for v in state.vendors if v.category == category)


def kinds(state):
    return sorted(i.kind for i in state.open_items)


def ask_and_reply(em, pid, chans, clock, body="Yes, free that day. Rs 650 per plate."):
    d = em.draft_outreach(pid, C.CATERER, "email", CONTACT, "Asha")
    d = em.approve_outreach(pid, d.draft_id, d.content_hash)
    chans["email"].inbox = [Inbound(channel="email", sender=d.recipient, external_id="<v1>", received_at=clock() + timedelta(minutes=10), body=body)]
    clock.advance(minutes=30)
    em.poll_conversations()
    return d


def test_every_fact_names_its_source_time_and_status(em, pid, clock):
    s = build_state(em, pid, now=clock())
    assert s.plan_id == pid and s.plan_version == 1
    by = {f.key: f for f in s.facts}
    assert by["guest_count"].value == 120 and by["guest_count"].source == "user" and by["guest_count"].status == "user_stated"
    assert by["plan_cost_inr"].source == "tool" and by["plan_cost_inr"].status == "pending"  # nothing is booked yet
    assert by["rsvp_pending"].status == "pending"
    assert all(f.as_of.tzinfo is not None for f in s.facts)
    assert [v.conversation for v in s.vendors] == ["none"] * 3 and all(v.quote is None for v in s.vendors)


def test_a_vendor_reply_shows_as_quoted_unverified_with_a_change_entry_and_never_reprices(em, pid, chans, clock):
    before = build_state(em, pid, now=clock())
    ask_and_reply(em, pid, chans, clock)
    s = build_state(em, pid, now=clock())
    caterer = vendor(s, C.CATERER)
    assert caterer.conversation == "replied" and caterer.quote.value == 650 and caterer.quote.status == "quoted_unverified" and caterer.quote.source == "vendor_reply"
    assert any(c.kind == "reply" and "mentioned a price (not recorded)" in c.summary for c in s.changes)
    assert caterer.plan_cost == vendor(before, C.CATERER).plan_cost and {f.key: f.value for f in s.facts}["plan_cost_inr"] == {f.key: f.value for f in before.facts}["plan_cost_inr"]
    assert "unconfirmed_quote" in kinds(s)


def test_recording_the_price_makes_it_yours_and_reading_the_reply_closes_the_item(em, pid, chans, clock):
    ask_and_reply(em, pid, chans, clock)
    em.ledger_add(pid, "quote", C.CATERER, 650, "from their reply")
    q = vendor(build_state(em, pid, now=clock()), C.CATERER).quote
    assert q.source == "user" and q.status == "user_stated" and q.value == 650
    mid = em.outreach_store.inbound(pid)[0].message_id
    em.mark_reply_read(pid, mid)
    assert "unconfirmed_quote" not in kinds(build_state(em, pid, now=clock()))


def test_conversation_status_follows_the_thread(em, pid, chans, clock):
    d = em.draft_outreach(pid, C.CATERER, "email", CONTACT, "Asha")
    s = build_state(em, pid, now=clock())
    assert vendor(s, C.CATERER).conversation == "awaiting_approval" and "approval_waiting" in kinds(s)
    em.approve_outreach(pid, d.draft_id, d.content_hash)
    clock.advance(hours=5)
    s = build_state(em, pid, now=clock())
    item = next(i for i in s.open_items if i.kind == "unanswered_vendor")
    assert vendor(s, C.CATERER).conversation == "asked" and item.age_hours == 5 and item.closes_when == CLOSES_WHEN["unanswered_vendor"]
    chans["email"].inbox = [Inbound(channel="email", sender=d.recipient, external_id="<n>", received_at=clock(), body="Sorry, we are not available on that date.")]
    em.poll_conversations()
    s = build_state(em, pid, now=clock())
    assert vendor(s, C.CATERER).conversation == "unavailable" and "unanswered_vendor" not in kinds(s)


def test_a_stop_request_is_reported_as_stopped(em, pid, chans, clock):
    ask_and_reply(em, pid, chans, clock, body="Please stop contacting us, wrong number.")
    assert vendor(build_state(em, pid, now=clock()), C.CATERER).conversation == "stopped"


def test_an_expired_proposal_is_not_an_open_approval(em, pid, clock):
    em.draft_outreach(pid, C.CATERER, "email", CONTACT, "Asha")
    clock.advance(days=30)
    assert "approval_waiting" not in kinds(build_state(em, pid, now=clock()))


def test_a_new_quote_job_puts_its_waiting_items_in_the_state_without_contact_details(em, pid, clock):
    started_job(em, pid, clock)
    s = build_state(em, pid, now=clock())
    assert "job_item" in kinds(s) and CONTACT not in s.model_dump_json() and any(c.kind == "job" for c in s.changes)


def test_changes_since_keeps_only_newer_entries_newest_first(em, pid, chans, clock):
    ask_and_reply(em, pid, chans, clock)
    everything = build_state(em, pid, now=clock()).changes
    assert [c.at for c in everything] == sorted((c.at for c in everything), reverse=True) and len(everything) >= 4
    cut = sorted(c.at for c in everything)[-2]
    newer = build_state(em, pid, now=clock(), changes_since=cut).changes
    assert newer and all(c.at > cut for c in newer) and len(newer) < len(everything)
    assert build_state(em, pid, now=clock(), changes_since=everything[0].at + timedelta(seconds=1)).changes == ()


def test_the_change_list_is_bounded(em, pid, clock):
    for i in range(MAX_CHANGES + 10):
        em.ledger_add(pid, "payment", C.VENUE, i + 1, "x")
    assert len(build_state(em, pid, now=clock()).changes) == MAX_CHANGES


def test_the_state_holds_no_guest_names_and_no_contact_details(em, chans, clock):
    pid = em.plan(EXAMPLE, today=TODAY, guest_list_id="demo-engagement-120").plan.plan_id  # a named guest list is in play
    ask_and_reply(em, pid, chans, clock, body="Rs 650 per plate. Call me on 9876543210 or mail sales@annapurna.in")
    em.jobs.start_job(pid, C.CATERER, {"cat-pune-annapurna": ("email", CONTACT)}, "get a quote", clock() + timedelta(days=5), "Meera", min_quotes=1)
    blob = build_state(em, pid, now=clock()).model_dump_json()
    seed = json.loads((Path(eventmate.__file__).parent / "data" / "seed_guests.json").read_text())
    names = {g["name"] for rec in seed["records"] for g in rec["guests"]}
    assert len(names) > 10 and not [n for n in names if n in blob or n.split()[0] in blob]
    assert {f.key: f.value for f in build_state(em, pid, now=clock()).facts}["rsvp_yes"] > 0  # the list was really used
    for needle in (CONTACT, "annapurna.in", "9876543210", "@", "Meera"):
        assert needle not in blob, needle


def test_building_state_calls_no_model_and_no_network(em, pid, chans, clock, monkeypatch):
    ask_and_reply(em, pid, chans, clock)

    def no_network(*a, **k):
        raise AssertionError("the state view must not touch the network")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    em.llm = type("Boom", (), {"complete": lambda *a, **k: (_ for _ in ()).throw(AssertionError("no model call allowed"))})()
    assert build_state(em, pid, now=clock()).plan_id == pid


def test_building_state_changes_nothing_on_disk(em, pid, chans, clock):
    ask_and_reply(em, pid, chans, clock)
    snap = {p: p.read_bytes() for p in em.store.root.rglob("*") if p.is_file() and p.suffix in (".json", ".jsonl")}
    build_state(em, pid, now=clock())
    assert {p: p.read_bytes() for p in snap} == snap


def test_two_plans_do_not_mix(em, pid, chans, clock):
    other = em.plan(EXAMPLE, today=TODAY).plan.plan_id
    ask_and_reply(em, pid, chans, clock)
    em.ledger_add(other, "payment", C.VENUE, 7, "other plan only")
    a, b = build_state(em, pid, now=clock()), build_state(em, other, now=clock())
    assert vendor(a, C.CATERER).conversation == "replied" and vendor(b, C.CATERER).conversation == "none" and vendor(b, C.CATERER).quote is None
    assert not any(c.kind == "ledger" for c in a.changes) and any(c.kind == "ledger" for c in b.changes)
    assert "unconfirmed_quote" not in kinds(b)


def test_deleting_a_plan_removes_its_state(em, pid, clock):
    assert build_state(em, pid, now=clock())
    em.delete_plan(pid)
    with pytest.raises(FileNotFoundError):
        build_state(em, pid, now=clock())


def test_an_unknown_plan_is_an_error_not_an_empty_state(em, clock):
    with pytest.raises(FileNotFoundError):
        build_state(em, "nope", now=clock())


# ------------------------------------------------------------------ exact rules (each pinned from both sides)


def started_job(em, pid, clock):
    return em.jobs.start_job(pid, C.CATERER, {"cat-pune-annapurna": ("email", CONTACT)}, "get a quote", clock() + timedelta(days=5), "Meera", min_quotes=1)


def with_jobs(em, monkeypatch, *jobs):
    monkeypatch.setattr(em, "_jobs_of", lambda plan_id: list(jobs))


def asked(job, at, nudges=0, pending=""):
    from eventmate.followthrough.jobs import VendorProgress

    vid, prog = next(iter(job.vendors.items()))
    return job.model_copy(update={"vendors": {vid: VendorProgress(contact_channel="email", recipient=CONTACT, draft_id=prog.draft_id, state="asked", asked_at=at, nudges=nudges, pending_nudge_draft_id=pending)}})


def due(state):
    return [i for i in state.open_items if i.kind == "follow_up_due"]


def test_a_follow_up_is_due_exactly_when_the_reply_window_has_passed(em, pid, clock, monkeypatch):
    job = started_job(em, pid, clock)
    sent = clock()
    window = timedelta(hours=job.reply_window_hours)
    with_jobs(em, monkeypatch, asked(job, sent))
    assert due(build_state(em, pid, now=sent + window - timedelta(seconds=1))) == []
    at = due(build_state(em, pid, now=sent + window))
    assert len(at) == 1 and at[0].since == sent + window and at[0].age_hours == 0 and at[0].closes_when == CLOSES_WHEN["follow_up_due"]
    assert due(build_state(em, pid, now=sent + window + timedelta(hours=3)))[0].age_hours == 3


def test_a_follow_up_is_not_due_once_nudged_out_pending_or_the_job_is_over(em, pid, clock, monkeypatch):
    job = started_job(em, pid, clock)
    late = clock() + timedelta(days=2)
    cases = {
        "max nudges used": asked(job, clock(), nudges=job.max_nudges_per_vendor),
        "one nudge left": asked(job, clock(), nudges=job.max_nudges_per_vendor - 1),
        "a nudge awaits approval": asked(job, clock(), pending="d1"),
    }
    for name, j in cases.items():
        with_jobs(em, monkeypatch, j)
        assert bool(due(build_state(em, pid, now=late))) == (name == "one nudge left"), name
    for status in ("done", "expired", "cancelled"):
        with_jobs(em, monkeypatch, asked(job, clock()).model_copy(update={"status": status}))
        assert build_state(em, pid, now=late).open_items == tuple(i for i in build_state(em, pid, now=late).open_items if i.kind == "pending_rsvps"), status
    other = asked(job, clock()).model_copy(update={"vendors": {"v": asked(job, clock()).vendors[next(iter(job.vendors))].model_copy(update={"state": "replied"})}})
    with_jobs(em, monkeypatch, other)
    assert due(build_state(em, pid, now=late)) == []


def test_only_unresolved_job_items_are_open(em, pid, clock, monkeypatch):
    from eventmate.followthrough.jobs import UserItem

    job = started_job(em, pid, clock)
    done = UserItem(item_id="i1", kind="unclear_price", text="x", resolved=True)
    todo = UserItem(item_id="i2", kind="vendor_question", text="x")
    with_jobs(em, monkeypatch, job.model_copy(update={"items": (done, todo)}))
    items = [i for i in build_state(em, pid, now=clock()).open_items if i.kind == "job_item"]
    assert [i.ref for i in items] == [f"{job.job_id}:i2"] and items[0].label == "A vendor asked you a question"


def test_rsvp_item_appears_only_while_guests_are_pending_and_explains_a_missing_list(em, pid, clock, monkeypatch):
    plan = em.store.latest(pid)

    def with_guests(**changes):
        monkeypatch.setattr(em.store, "latest", lambda p: plan.model_copy(update={"guests": plan.guests.model_copy(update=changes)}))
        return [i for i in build_state(em, pid, now=clock()).open_items if i.kind == "pending_rsvps"]

    assert with_guests(pending=0) == []
    [one] = with_guests(pending=1, guest_list_id=None)
    assert one.label.startswith("1 guest(s) have not answered") and "no guest list is loaded" in one.label
    [two] = with_guests(pending=2, guest_list_id="demo-engagement-120")
    assert two.label == "2 guest(s) have not answered"


def test_the_rsvp_item_ages_from_the_first_version_of_a_replanned_plan(em, pid, clock):
    from eventmate.schemas import Disruption, DisruptionKind

    first = em.store.load(pid, 1).created_at
    em.replan(pid, Disruption(kind=DisruptionKind.BUDGET_CHANGE, new_budget=300_000), today=TODAY)
    s = build_state(em, pid, now=clock())
    assert s.plan_version == 2 and next(i for i in s.open_items if i.kind == "pending_rsvps").since == first
    assert [c.summary.split(":")[0] for c in s.changes if c.kind == "plan_version"] == ["Plan version 2 saved", "Plan version 1 saved"]


def test_guest_facts_say_whether_they_come_from_the_demo_data_or_the_user(em, pid, clock):
    listed = em.plan(EXAMPLE, today=TODAY, guest_list_id="demo-engagement-120").plan.plan_id
    assert {f.key: f.source for f in build_state(em, listed, now=clock()).facts}["rsvp_yes"] == "seed_data"
    assert {f.key: f.source for f in build_state(em, pid, now=clock()).facts}["rsvp_yes"] == "user"


def test_only_a_recorded_quote_counts_as_the_users_price(em, pid, clock):
    em.ledger_add(pid, "payment", C.CATERER, 5000, "a payment is not a quote")
    em.ledger_add(pid, "quote", C.VENUE, 99_999, "a quote for another category")
    assert vendor(build_state(em, pid, now=clock()), C.CATERER).quote is None


def test_a_claim_the_user_confirmed_makes_the_vendors_price_confirmed(em, pid, chans, clock, monkeypatch):
    from eventmate.followthrough.jobs import QuoteClaim

    ask_and_reply(em, pid, chans, clock)
    msg = em.outreach_store.inbound(pid)[0]
    job = started_job(em, pid, clock)

    def quote_status(claim_status):
        c = QuoteClaim(claim_id="c1", message_id=msg.message_id, vendor_id=msg.vendor_id, snippet="Rs 650", amount_inr=650, basis="per_plate", status=claim_status)
        with_jobs(em, monkeypatch, job.model_copy(update={"claims": (c,)}))
        return vendor(build_state(em, pid, now=clock()), C.CATERER).quote.status

    assert quote_status("confirmed") == "confirmed" and quote_status("proposed") == "quoted_unverified" and quote_status("rejected") == "quoted_unverified"


def test_a_price_from_an_unexpected_sender_is_never_shown_as_the_vendors(em, pid, chans, clock):
    d = em.draft_outreach(pid, C.CATERER, "email", CONTACT, "Asha")
    em.approve_outreach(pid, d.draft_id, d.content_hash)
    chans["email"].inbox = [Inbound(channel="email", sender="someone-else@elsewhere.example", external_id="<x>", received_at=clock() + timedelta(minutes=5), body="Rs 1 per plate", in_reply_to=d.external_id)]
    clock.advance(minutes=10)
    em.poll_conversations()
    assert vendor(build_state(em, pid, now=clock()), C.CATERER).quote is None


def test_last_activity_is_the_newest_of_the_send_and_the_replies(em, pid, chans, clock):
    d = ask_and_reply(em, pid, chans, clock)
    v = vendor(build_state(em, pid, now=clock()), C.CATERER)
    reply = em.outreach_store.inbound(pid)[0].received_at
    assert v.last_activity_at == reply and v.last_activity_at > d.sent_at


def test_a_denied_message_is_a_problem_and_a_change_not_an_open_approval(em, pid, clock):
    d = em.draft_outreach(pid, C.CATERER, "email", CONTACT, "Asha")
    em.deny_outreach(pid, d.draft_id)
    s = build_state(em, pid, now=clock())
    assert vendor(s, C.CATERER).conversation == "problem" and "approval_waiting" not in kinds(s)
    assert any("was denied" in c.summary for c in s.changes)


def test_open_items_are_oldest_first(em, pid, chans, clock):
    em.draft_outreach(pid, C.VENUE, "email", "hall@orchid.example", "Asha")
    clock.advance(hours=2)
    em.draft_outreach(pid, C.CATERER, "email", CONTACT, "Asha")
    waiting = [i for i in build_state(em, pid, now=clock()).open_items if i.kind == "approval_waiting"]
    assert len(waiting) == 2 and waiting[0].since < waiting[1].since and waiting[0].age_hours == 2 and waiting[1].age_hours == 0


def test_open_items_of_different_kinds_are_ordered_by_age_not_by_the_order_they_are_found(em, pid, clock):
    em.draft_outreach(pid, C.CATERER, "email", CONTACT, "Asha")  # drafted at the test clock, long before the plan's own (wall-clock) creation time
    items = build_state(em, pid, now=clock()).open_items
    assert [i.kind for i in items] == ["approval_waiting", "pending_rsvps"] and items[0].since < items[1].since


def test_the_documented_bounds_and_defaults():
    from eventmate.state import VendorState

    assert MAX_CHANGES == 50 and VendorState.model_fields["price_stale"].default is False
    assert set(CLOSES_WHEN) == {"pending_rsvps", "approval_waiting", "unanswered_vendor", "unconfirmed_quote", "follow_up_due", "job_item"}


# ------------------------------------------------------------------ API


@pytest.fixture
def client(em):
    return create_app(em, today_fn=lambda: TODAY).test_client()


def test_the_state_endpoint_is_read_only_json_with_changes_since(client, em, pid, chans, clock):
    ask_and_reply(em, pid, chans, clock)
    r = client.get(f"/api/plans/{pid}/state")
    assert r.status_code == 200 and r.is_json
    body = r.get_json()
    assert body["plan_id"] == pid and {"facts", "vendors", "open_items", "changes", "notice"} <= set(body) and CONTACT not in json.dumps(body)
    cut = body["changes"][0]["at"]
    assert client.get(f"/api/plans/{pid}/state", query_string={"changes_since": cut}).get_json()["changes"] == []
    assert client.get(f"/api/plans/{pid}/state", query_string={"changes_since": cut.replace("+00:00", "Z")}).status_code == 200
    assert client.get(f"/api/plans/{pid}/state", query_string={"changes_since": "yesterday"}).status_code == 400
    assert client.get("/api/plans/nope/state").status_code == 404
    assert client.get("/api/plans/..%2Fx/state").status_code in (400, 404)
    assert client.post(f"/api/plans/{pid}/state").status_code in (403, 405)


# ------------------------------------------------------------------ assistant tool


def test_the_assistant_tool_returns_a_bounded_slice_without_names_contacts_or_prices(em, pid, chans, clock):
    ask_and_reply(em, pid, chans, clock)
    model = Scripted(tool("get_event_state"), answer("120 guests have not answered yet.", "get_event_state"))
    a = Assistant(em, {"large": model, "small": model})
    r = a.ask("compare what is still outstanding across my vendors", pid, LOCAL_ORGANIZER)
    assert r.outcome == "answered" and "120" in r.answer
    shown = "\n".join(model.prompts)
    assert "quoted_unverified" in shown and CONTACT not in shown and "650" not in shown and "Asha" not in shown


def test_the_tool_is_visible_to_the_organizer_and_not_to_a_guest():
    from eventmate.assistant.identity import Principal, Role
    from eventmate.assistant.tools import build_tools, visible_tools

    names = lambda p: {t.name for t in visible_tools(build_tools(), p)}  # noqa: E731
    assert "get_event_state" in names(LOCAL_ORGANIZER)
    assert "get_event_state" not in names(Principal(user_id="g", roles=(Role.GUEST,)))
