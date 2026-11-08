"""A reply is filed under the event it answers, or it waits for the user to say; the address alone never picks between events (audit A2), and a brief that became a plan keeps its replies (A4).
Every stored reply says how it was matched."""
from datetime import UTC, datetime, timedelta

from eventmate.messaging.channels import Inbound
from tests import test_conversations as tc
from tests.conftest import EXAMPLE, TODAY
from tests.test_quote_readings import OK_FOR_40
from tests.test_shared_quotes import ADDRESS, own_vendor, person

em, pid, chans, clock = tc.em, tc.pid, tc.chans, tc.clock  # the conversation tests' fixtures


def second_event(em, pid, clock):
    """Event B: the same caterer address asked again, an hour after event A."""
    a = tc.draft(em, pid)
    em.approve_outreach(pid, a.draft_id, a.content_hash)
    b_pid = em.plan(EXAMPLE.replace("120-person", "90-person"), today=TODAY).plan.plan_id
    clock.advance(hours=1)
    b = tc.draft(em, b_pid)
    em.approve_outreach(b_pid, b.draft_id, b.content_hash)
    return a, b, b_pid


def test_a_fresh_reply_from_an_address_two_events_wrote_to_is_held_not_guessed(em, pid, chans, clock):
    a, b, b_pid = second_event(em, pid, clock)
    chans["email"].inbox.append(tc.reply(a, "For your 120-guest party: Price per plate (INR): 650", ext="<fresh1@v.test>", when=clock() + timedelta(minutes=5)))
    res = em.poll_conversations()
    assert [p for p in (pid, b_pid) if em.outreach_store.inbound(p)] == [], "filed under neither: it must wait for the user to say which event"
    assert res.held == 1 and em.poll_conversations().held == 0, "held once, not again on the next poll"
    (held,) = em.reply_reviews_open()
    assert {c.plan_id for c in held.candidates} == {pid, b_pid} and "650" in held.body


def test_the_user_assigns_a_held_reply_to_the_event_it_answered(em, pid, chans, clock):
    a, b, b_pid = second_event(em, pid, clock)
    chans["email"].inbox.append(tc.reply(a, "For your 120-guest party: Price per plate (INR): 650", ext="<fresh1@v.test>", when=clock() + timedelta(minutes=5)))
    em.poll_conversations()
    (held,) = em.reply_reviews_open()
    msg = em.assign_reply(held.review_id, pid)
    assert msg.plan_id == pid and msg.match_method == "assigned" and not em.outreach_store.inbound(b_pid)
    assert em.reply_reviews_open() == [] and em.poll_conversations().held == 0
    chans["email"].inbox.append(tc.reply(a, "Another", ext="<fresh2@v.test>", when=clock() + timedelta(minutes=9)))
    em.poll_conversations()
    (second,) = em.reply_reviews_open()
    em.dismiss_reply_review(second.review_id)
    assert em.reply_reviews_open() == []


def test_a_reply_with_our_headers_is_matched_by_them_even_when_two_events_wrote_to_the_address(em, pid, chans, clock):
    a, b, b_pid = second_event(em, pid, clock)
    sent_a = em.outreach(pid)[0]
    chans["email"].inbox.append(tc.reply(a, "Price per plate (INR): 650", ext="<thread1@v.test>", when=clock() + timedelta(minutes=5), in_reply_to=sent_a.external_id))
    em.poll_conversations()
    (m,) = em.outreach_store.inbound(pid)
    assert m.match_method == "header" and not em.outreach_store.inbound(b_pid) and em.reply_reviews_open() == []


def test_a_single_candidate_is_matched_by_sender_and_labelled_so(em, pid, chans, clock):
    a = tc.draft(em, pid)
    em.approve_outreach(pid, a.draft_id, a.content_hash)
    chans["email"].inbox.append(tc.reply(a, "Price per plate (INR): 650", ext="<fresh1@v.test>", when=clock() + timedelta(minutes=5)))
    em.poll_conversations()
    (m,) = em.outreach_store.inbound(pid)
    assert m.match_method == "sender", "no thread headers: matched by the sender's address, and it says so"


def test_a_whatsapp_reply_is_held_when_two_events_wrote_to_the_number_and_matched_by_sender_when_one_did(em, pid, chans, clock):
    tc_kw = {"channel": "whatsapp", "recipient": "+91 98765 43210"}
    a = tc.draft(em, pid, **tc_kw)
    em.approve_outreach(pid, a.draft_id, a.content_hash)
    chans["whatsapp"].inbox.append(tc.reply(a, "650 per plate", ext="w1", when=clock() + timedelta(minutes=5)))
    em.poll_conversations()
    assert [m.match_method for m in em.outreach_store.inbound(pid)] == ["sender"]
    clock.advance(hours=1)
    b_pid = em.plan(EXAMPLE.replace("120-person", "90-person"), today=TODAY).plan.plan_id
    b = tc.draft(em, b_pid, **tc_kw)
    em.approve_outreach(b_pid, b.draft_id, b.content_hash)
    chans["whatsapp"].inbox.append(tc.reply(b, "700 per plate", ext="w2", when=clock() + timedelta(minutes=5)))
    res = em.poll_conversations()
    assert res.held == 1 and len(em.outreach_store.inbound(pid)) == 1 and not em.outreach_store.inbound(b_pid)


def test_a_reply_that_arrives_after_the_brief_became_a_plan_is_the_plans_reply(tmp_path):
    em, chans, clock, brief = person(tmp_path, "linked")
    biz = own_vendor(em, brief)
    d = em.request_all_quotes(brief, "Asha")[0][0]
    em.approve_outreach(brief, d.draft_id, d.content_hash)
    plan_id = em.plan(EXAMPLE, today=TODAY).plan.plan_id  # the brief is built into a plan (the page does this through the commit queue)
    em.brief_links.link(brief[len("brief_"):], plan_id)
    assert [x.draft_id for x in em.outreach(plan_id)] == [d.draft_id], "the plan sees what was asked under the brief"
    assert em._scope(brief).plan_id == plan_id, "and the brief's id now reads as the plan"
    chans["email"].inbox = [Inbound(channel="email", sender=ADDRESS, external_id="<late1>", received_at=clock() + timedelta(days=3), body=OK_FOR_40, in_reply_to=em.outreach(brief)[0].external_id)]
    clock.advance(days=3, minutes=10)
    em.poll_conversations()
    (m,) = em.outreach_store.inbound(plan_id)
    assert m.vendor_id == biz.vendor_id and em.outreach_store.inbound(brief) == em.outreach_store.inbound(plan_id)
    assert any(a.message_id == m.message_id for a in em.quote_assessments(plan_id)), "and it can be read and confirmed from the plan"
    em.confirm_quote_fields(plan_id, m.message_id, {"available": None}, "Asha")
    assert em.quote_confirmations.latest(brief, m.message_id).keys() == em.quote_confirmations.latest(plan_id, m.message_id).keys() == {"available"}


def test_a_brief_with_a_request_still_unanswered_is_not_swept(tmp_path):
    em, chans, clock, brief = person(tmp_path, "kept")
    own_vendor(em, brief)
    d = em.request_all_quotes(brief, "Asha")[0][0]
    em.approve_outreach(brief, d.draft_id, d.content_hash)
    staged = brief[len("brief_"):]
    row = em.staging.require(staged)
    em.staging.now = lambda: datetime.now(UTC) + timedelta(days=20)  # past the 14 days a request keeps a pending brief
    em.staging.create(row.draft)  # creating another sweeps
    assert em.staging.require(staged).state == "pending_validation", "a request is out and unanswered: the brief stays, so a late reply has somewhere to land"


def test_the_routes_list_assign_and_dismiss_held_replies(em, pid, chans, clock):
    from eventmate.web.app import create_app

    a, b, b_pid = second_event(em, pid, clock)
    chans["email"].inbox.append(tc.reply(a, "Price per plate (INR): 650", ext="<fresh1@v.test>", when=clock() + timedelta(minutes=5)))
    em.poll_conversations()
    client = create_app(em, today_fn=lambda: TODAY, poll_seconds=0).test_client()
    token = client.get("/api/session").get_json()["csrf"]
    held = client.get("/api/reply-reviews").get_json()
    assert len(held) == 1 and len(held[0]["candidates"]) == 2 and all("guests" in c["event"] for c in held[0]["candidates"]), held
    url = f"/api/reply-reviews/{held[0]['review_id']}"
    assert client.post(url + "/assign", json={"plan_id": "nope"}, headers={"X-EventMate-CSRF": token}).status_code == 400
    ok = client.post(url + "/assign", json={"plan_id": b_pid}, headers={"X-EventMate-CSRF": token})
    assert ok.status_code == 201 and ok.get_json()["plan_id"] == b_pid and ok.get_json()["match_method"] == "assigned"
    assert client.get("/api/reply-reviews").get_json() == []
    assert client.post(url + "/dismiss", json={}, headers={"X-EventMate-CSRF": token}).status_code == 400, "already dealt with"


def test_deleting_a_plan_built_from_a_brief_removes_what_was_filed_under_the_brief(tmp_path):
    em, chans, clock, brief = person(tmp_path, "gone")
    own_vendor(em, brief)
    d = em.request_all_quotes(brief, "Asha")[0][0]
    em.approve_outreach(brief, d.draft_id, d.content_hash)
    plan_id = em.plan(EXAMPLE, today=TODAY).plan.plan_id
    em.brief_links.link(brief[len("brief_"):], plan_id)
    assert (em.store.root / "outreach" / f"outreach-{brief}.jsonl").exists()
    em.delete_plan(plan_id)
    assert not (em.store.root / "outreach" / f"outreach-{brief}.jsonl").exists() and em.outreach_store.list(plan_id) == []
