"""A vendor's reply as a quote: the request carries the fixed template, the reply is read field by field, the user confirms each field (an amount on its own), completeness counts only what
they confirmed, and a confirmed price with its validity is recorded as a quote for the guests and date it was for."""

import json
from datetime import timedelta

import pytest

from eventmate.messaging.channels import Inbound
from eventmate.messaging.conversations import SendPolicy
from eventmate.schemas import VendorCategory as C
from eventmate.service import EventMate
from eventmate.web.app import create_app
from tests.conftest import TODAY
from tests.test_brief_sourcing import MYSURU
from tests.test_conversations import Clock, FakeChannel

REPLY = """Hello,
Available on the date (yes/no): yes
Price per plate (INR): 650
Taxes (GST) included (yes/no): no
Minimum guests: 50
Maximum guests: 300
Food options (vegetarian / jain / vegan / non-veg): vegetarian, jain
Deposit to book (INR): 10,000
Quote valid for (days): 10
Included and extra: staff and crockery included, transport extra
Regards"""
OK_FOR_40 = REPLY.replace("Minimum guests: 50", "Minimum guests: 20")


@pytest.fixture
def world(tmp_path):
    clock, chans = Clock(), {"email": FakeChannel("email"), "whatsapp": FakeChannel("whatsapp", False, False)}
    em = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="qr", channels=chans, require_symbolic=False)
    em.outreach_store.now = em.conversations.now = em.quote_confirmations.now = clock
    em.conversations.policy = SendPolicy(per_vendor_per_day=20, per_day=200, whatsapp_gap_s=0)
    brief = "brief_" + em.intake_turn([MYSURU], today=TODAY).staged_id
    biz, _ = em.add_test_vendor("Test Caterer", "Mysuru", C.CATERER, "owner@personal.test", True, "Asha", plan_id=brief)
    d = em.request_all_quotes(brief, "Asha")[0][0]
    em.approve_outreach(brief, d.draft_id, d.content_hash)
    return em, chans, clock, brief, biz


def reply(world, body):
    em, chans, clock, brief, biz = world
    chans["email"].inbox = [Inbound(channel="email", sender="owner@personal.test", external_id="<r%d>" % (len(em.outreach_store.inbound(brief)) + 1), received_at=clock() + timedelta(minutes=5), body=body,
                                    in_reply_to=em.outreach(brief)[0].external_id)]
    clock.advance(minutes=10)
    em.poll_conversations()
    return em.outreach_store.inbound(brief)[-1].message_id


def view(em, brief, mid):
    return next(a for a in em.quote_assessments(brief) if a.message_id == mid)


def field(a, key):
    return next(f for f in a.fields if f.key == key)


def test_the_request_carries_the_fixed_template_and_never_the_budget(world):
    em, chans, clock, brief, biz = world
    body = chans["email"].sent[0][2]
    for label in ("Available on the date (yes/no):", "Price per plate (INR):", "Taxes (GST) included (yes/no):", "Minimum guests:", "Maximum guests:", "Deposit to book (INR):", "Quote valid for (days):", "Included and extra:"):
        assert label in body
    assert "60,000" not in body and "budget" not in body.lower() and "Mysuru" in body and len(body) < 2000


def test_a_filled_reply_is_read_with_nothing_confirmed_and_fit_issues_marked_tentative(world):
    em, chans, clock, brief, biz = world
    a = view(em, brief, reply(world, REPLY))
    assert a.template_used and a.confirmed_required == 0 and a.read_required == a.required_total == 8 and a.index == 0 and not a.comparable
    assert all(f.state == "read" for f in a.fields if f.key != "inclusions") and field(a, "price").value == "650" and field(a, "price").evidence == "Price per plate (INR): 650"
    texts = {i.field: (i.text, i.tentative) for i in a.issues}
    assert texts["min_guests"][1] and "minimum is 50" in texts["min_guests"][0] and "taxes are extra" in texts["taxes_included"][0]
    assert em.observations.quotes(biz.vendor_id) == [], "reading a reply records nothing"


def test_an_amount_is_confirmed_on_its_own_and_a_price_without_its_validity_is_not_yet_a_quote(world):
    em, chans, clock, brief, biz = world
    mid = reply(world, OK_FOR_40)
    with pytest.raises(ValueError, match="on its own"):
        em.confirm_quote_fields(brief, mid, {"price": None, "deposit": None}, "Asha")
    with pytest.raises(ValueError, match="on its own"):
        em.confirm_quote_fields(brief, mid, {"price": None, "min_guests": None}, "Asha")
    assert em.quote_confirmations.latest(brief, mid) == {}, "a refused batch saved nothing"
    a = em.confirm_quote_fields(brief, mid, {"price": None}, "Asha")
    assert field(a, "price").state == "confirmed" and field(a, "price").confirmed_by == "Asha" and a.confirmed_required == 1
    assert em.observations.quotes(biz.vendor_id) == [], "the price is not a recorded quote until its validity is confirmed too"


def test_confirming_the_price_and_its_validity_records_one_quote_for_these_guests_and_this_date(world):
    em, chans, clock, brief, biz = world
    mid = reply(world, OK_FOR_40)
    em.confirm_quote_fields(brief, mid, {"taxes_included": None}, "Asha")
    em.confirm_quote_fields(brief, mid, {"price": None}, "Asha")
    a = em.confirm_quote_fields(brief, mid, {"valid_days": None}, "Asha")
    (q,) = em.observations.quotes(biz.vendor_id)
    assert (q.amount_inr, q.basis, q.guest_count, q.entered_by) == (650, "per_plate", 40, "Asha") and q.quoted_for_date.isoformat() == "2026-11-14"
    assert q.evidence_ref == f"reply {mid}: 650" and q.expires_at == em.outreach_store.inbound(brief)[0].received_at.date() + timedelta(days=10) and q.confidence == 0.7
    assert [h.label for h in q.hidden_costs] == ["Taxes (GST) extra: ask for the amount"]
    em.confirm_quote_fields(brief, mid, {"valid_days": None}, "Asha")  # again: the same quote is not recorded twice
    assert len(em.observations.quotes(biz.vendor_id)) == 1 and a.confirmed_required == 3


def test_the_price_can_be_confirmed_with_its_validity_in_one_call(world):
    em, chans, clock, brief, biz = world
    mid = reply(world, OK_FOR_40.replace("Quote valid for (days): 10", "Quote valid for (days):"))
    with pytest.raises(ValueError, match="nothing was read safely"):
        em.confirm_quote_fields(brief, mid, {"valid_days": None}, "Asha")
    a = em.confirm_quote_fields(brief, mid, {"price": None}, "Asha", valid_days="14")
    assert field(a, "valid_days").value == "14" and field(a, "valid_days").edited and len(em.observations.quotes(biz.vendor_id)) == 1


def test_a_value_the_user_types_goes_through_the_same_parser_and_a_new_amount_is_a_new_quote(world):
    em, chans, clock, brief, biz = world
    mid = reply(world, OK_FOR_40)
    em.confirm_quote_fields(brief, mid, {"valid_days": None}, "Asha")
    em.confirm_quote_fields(brief, mid, {"price": None}, "Asha")
    for bad in ("six hundred", "600-700", "0", "650 and 5000", ""):
        if bad == "":
            continue
        with pytest.raises(ValueError):
            em.confirm_quote_fields(brief, mid, {"price": bad}, "Asha")
    a = em.confirm_quote_fields(brief, mid, {"price": "Rs 700"}, "Asha")
    assert field(a, "price").value == "700" and field(a, "price").edited and field(a, "price").read_value == "650"
    assert [q.amount_inr for q in em.observations.quotes(biz.vendor_id)] == [650, 700], "history is kept; the latest stands"


def test_an_unclear_or_missing_field_can_be_filled_only_by_the_user_typing_it(world):
    em, chans, clock, brief, biz = world
    mid = reply(world, REPLY.replace("Maximum guests: 300", "Maximum guests: 300 or 350").replace("Deposit to book (INR): 10,000", "Deposit to book (INR):"))
    a = view(em, brief, mid)
    assert field(a, "max_guests").state == "unclear" and field(a, "deposit").state == "missing"
    for key in ("max_guests", "deposit"):
        with pytest.raises(ValueError, match="nothing was read safely"):
            em.confirm_quote_fields(brief, mid, {key: None}, "Asha")
    a = em.confirm_quote_fields(brief, mid, {"max_guests": "300"}, "Asha")
    assert field(a, "max_guests").state == "confirmed" and field(a, "max_guests").edited


def test_the_rest_can_be_confirmed_together_and_a_confirmation_can_be_taken_back(world):
    em, chans, clock, brief, biz = world
    mid = reply(world, OK_FOR_40)
    a = em.confirm_quote_fields(brief, mid, {"available": None, "min_guests": None, "max_guests": None, "diets": None, "taxes_included": None}, "Asha")
    assert a.confirmed_required == 5
    a = em.retract_quote_field(brief, mid, "diets", "Asha")
    assert field(a, "diets").state == "read" and a.confirmed_required == 4
    with pytest.raises(ValueError, match="not confirmed"):
        em.retract_quote_field(brief, mid, "diets", "Asha")


def test_a_price_already_recorded_as_a_quote_is_not_taken_back(world):
    em, chans, clock, brief, biz = world
    mid = reply(world, OK_FOR_40)
    em.confirm_quote_fields(brief, mid, {"valid_days": None}, "Asha")
    em.confirm_quote_fields(brief, mid, {"price": None}, "Asha")
    with pytest.raises(ValueError, match="already recorded"):
        em.retract_quote_field(brief, mid, "price", "Asha")


def test_completeness_counts_only_confirmed_fields_and_comparable_needs_all_of_them_and_a_fit(world):
    em, chans, clock, brief, biz = world
    mid = reply(world, OK_FOR_40)
    required = ["available", "taxes_included", "min_guests", "max_guests", "diets"]
    em.confirm_quote_fields(brief, mid, dict.fromkeys(required), "Asha")
    for k in ("price", "deposit", "valid_days"):
        a = em.confirm_quote_fields(brief, mid, {k: None}, "Asha")
    assert a.index == 1.0 and a.comparable and not [i for i in a.issues if not i.tentative and i.field != "taxes_included"]
    mid2 = reply(world, REPLY)  # a second reply whose minimum (50) is above the 40 guests
    for k in ("available", "taxes_included", "min_guests", "max_guests", "diets"):
        em.confirm_quote_fields(brief, mid2, {k: None}, "Asha")
    for k in ("price", "deposit", "valid_days"):
        b = em.confirm_quote_fields(brief, mid2, {k: None}, "Asha")
    assert b.index == 1.0 and not b.comparable and any(i.field == "min_guests" and not i.tentative for i in b.issues), "complete, but confirmed not to fit"


def test_a_vendor_that_says_it_is_not_available_is_never_comparable(world):
    em, chans, clock, brief, biz = world
    mid = reply(world, OK_FOR_40.replace("Available on the date (yes/no): yes", "Available on the date (yes/no): no"))
    for k in ("available", "taxes_included", "min_guests", "max_guests", "diets"):
        em.confirm_quote_fields(brief, mid, {k: None}, "Asha")
    for k in ("price", "deposit", "valid_days"):
        a = em.confirm_quote_fields(brief, mid, {k: None}, "Asha")
    assert a.index == 1.0 and not a.comparable and any("not available" in i.text for i in a.issues)


def test_a_diet_the_brief_needs_that_the_vendor_does_not_list_is_an_issue(world):
    em, chans, clock, brief, biz = world
    em.edit_staged(brief[6:], {"city": "Mysuru"}, em.staging.require(brief[6:]).version, TODAY)  # unchanged: the brief is vegetarian
    mid = reply(world, OK_FOR_40.replace("vegetarian, jain", "non-veg, jain"))
    assert any(i.field == "diets" and "vegetarian" in i.text for i in view(em, brief, mid).issues)


def test_the_confirmations_survive_a_restart_and_the_reply_is_never_changed(world, tmp_path):
    em, chans, clock, brief, biz = world
    mid = reply(world, OK_FOR_40)
    em.confirm_quote_fields(brief, mid, {"min_guests": None}, "Asha")
    again = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="qr2", channels=chans, require_symbolic=False)
    assert field(next(a for a in again.quote_assessments(brief) if a.message_id == mid), "min_guests").state == "confirmed"
    assert again.outreach_store.inbound(brief)[0].body == OK_FOR_40


def test_who_confirmed_is_required_and_a_wrong_message_or_field_is_refused(world):
    em, chans, clock, brief, biz = world
    mid = reply(world, OK_FOR_40)
    for who in ("", "  ", None):
        with pytest.raises(ValueError):
            em.confirm_quote_fields(brief, mid, {"min_guests": None}, who)
    with pytest.raises(ValueError, match="no such reply"):
        em.confirm_quote_fields(brief, "nope", {"min_guests": None}, "Asha")
    with pytest.raises(ValueError, match="not asked"):
        em.confirm_quote_fields(brief, mid, {"capacity": "100"}, "Asha")
    assert em.quote_confirmations.latest(brief, mid) == {}


def test_the_routes(world):
    em, chans, clock, brief, biz = world
    mid = reply(world, OK_FOR_40)
    client = create_app(em, today_fn=lambda: TODAY, rate_limit_per_minute=10_000).test_client()
    token = client.get("/api/session").get_json()["csrf"]

    def post(url, body):
        r = client.post(url, data=json.dumps(body), headers={"X-EventMate-CSRF": token, "Content-Type": "application/json"})
        return r.status_code, r.get_json()

    got = client.get(f"/api/plans/{brief}/quote-readings").get_json()["quotes"]
    assert len(got) == 1 and got[0]["required_total"] == 8 and got[0]["index"] == 0
    s, a = post(f"/api/plans/{brief}/quote-readings/{mid}/confirm", {"fields": {"min_guests": None}, "by": "Asha"})
    assert s == 201 and a["confirmed_required"] == 1
    assert post(f"/api/plans/{brief}/quote-readings/{mid}/confirm", {"fields": {"price": None, "deposit": None}, "by": "Asha"})[0] == 400
    assert post(f"/api/plans/{brief}/quote-readings/{mid}/confirm", {"fields": {"price": "abc"}, "by": "Asha"})[0] == 400
    assert post(f"/api/plans/{brief}/quote-readings/{mid}/confirm", {"fields": {}, "by": "Asha"})[0] == 422
    s, r = post(f"/api/plans/{brief}/quote-readings/{mid}/retract", {"field": "min_guests", "by": "Asha"})
    assert s == 200 and r["confirmed_required"] == 0
    assert client.get("/api/plans/brief_nope/quote-readings").status_code in (400, 404, 410)
