"""Vendors asked about a brief that has no plan. In a city with no vendor records a plan cannot be built until quotes are in, so the sourcing steps (find, contact, draft, approve, read the reply)
run against the staged brief, filed under ``brief_<staged id>``, with the same approvals as for a plan."""

import json
from datetime import timedelta

import pytest

from eventmate.messaging.channels import Inbound
from eventmate.messaging.conversations import SendPolicy
from eventmate.integrations.places import PlacesSource
from eventmate.planning.staging import StageGone
from eventmate.schemas import VendorCategory as C
from eventmate.service import EventMate
from eventmate.web.app import create_app
from tests.conftest import TODAY
from tests.test_conversations import Clock, FakeChannel

MYSURU = "birthday for 40 guests in Mysuru on 14 November 2026, veg, budget 60000"


PLACES = [
    {"id": "ChIJcaterer00001", "displayName": {"text": "Sri Hari Caterers"}, "formattedAddress": "JP Nagar, Mysuru, Karnataka 570031", "rating": 4.8, "userRatingCount": 581, "googleMapsUri": "https://maps.google.com/?cid=1",
     "websiteUri": "https://srihari-caterers.example/"},
    {"id": "ChIJcaterer00002", "displayName": {"text": "Bengaluru Caterers"}, "formattedAddress": "Kothanur, Bengaluru, Karnataka 560077", "rating": 5.0, "userRatingCount": 90},
]


def fake_places(url, headers, data, timeout):
    """Google, faked: a search answers with the list, a details request (no body) with one place, and the phone only when the field is asked for."""
    if data is None:
        hit = next((p for p in PLACES if p["id"] == url.rsplit("/", 1)[-1]), None)
        if hit is None:
            import urllib.error
            raise urllib.error.HTTPError(url, 404, "not found", {}, None)
        if "internationalPhoneNumber" in headers["X-Goog-FieldMask"]:
            hit = hit | {"internationalPhoneNumber": "+91 98450 12345"}
        return json.dumps(hit).encode()
    return json.dumps({"places": PLACES}).encode()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def chans():
    return {"email": FakeChannel("email"), "whatsapp": FakeChannel("whatsapp", False, False)}


@pytest.fixture
def em(tmp_path, chans, clock):
    e = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="brief", channels=chans, require_symbolic=False)
    e.places = e.vendor_search.places = PlacesSource(api_key="k" * 30, transport=fake_places)
    e.outreach_store.now = e.conversations.now = clock
    e.conversations.policy = SendPolicy(per_vendor_per_day=20, per_day=200, whatsapp_gap_s=0)
    return e


@pytest.fixture
def brief(em):
    turn = em.intake_turn([MYSURU], today=TODAY)
    assert turn.ready and turn.no_vendors
    return "brief_" + turn.staged_id


def test_businesses_are_found_for_a_brief_with_no_plan_and_none_is_made(em, brief):
    out = em.search_vendors(brief, C.CATERER, "places", 10)
    assert out.city == "Mysuru" and out.added == 1, "the listing in Bengaluru was left out, so only Mysuru's caterer is shortlisted"
    assert [i.business.external_id for i in em.shortlist_view(brief)] == ["gplaces:ChIJcaterer00001"]
    assert em.store.list_plans() == [], "finding vendors made no plan"
    assert all(b.origin == "discovered" for b in em.externals.all())


def test_an_unknown_hostile_or_timed_out_brief_is_refused(em, brief):
    for bad in ("brief_" + "0" * 24, "brief_nope", "brief_../x"):
        with pytest.raises((StageGone, ValueError)):
            em.search_vendors(bad, C.CATERER, "places", 5)
    later = em.staging.require(brief[6:]).expires_at + timedelta(seconds=1)
    em.staging.now = lambda: later
    with pytest.raises(StageGone):
        em.shortlist_view(brief)


def test_a_brief_needs_a_city_a_date_and_a_guest_count_before_a_vendor_is_asked(em):
    turn = em.intake_turn(["birthday party"], today=TODAY)
    with pytest.raises(ValueError, match="city, a date and a guest count"):
        em.search_vendors("brief_" + turn.staged_id, C.CATERER, "places", 5)


def test_a_quote_request_for_a_brief_states_only_the_events_facts_and_waits_for_approval(em, brief, chans):
    biz, _ = em.add_test_vendor("Test Caterer", "Mysuru", C.CATERER, "owner@personal.test", True, "Asha", plan_id=brief)
    made, skipped = em.request_all_quotes(brief, "Asha")
    assert skipped == [] and len(made) == 1
    d = made[0]
    assert d.plan_id == brief and d.status == "prepared" and d.vendor_id == biz.vendor_id
    assert "Mysuru" in d.body and "2026-11-14" in d.body and "40 guests" in d.body and "vegetarian" in d.body
    assert "60000" not in d.body and "60,000" not in d.body and "budget" not in d.body.lower(), "the budget is never in a request"
    assert chans["email"].sent == [], "nothing leaves until it is approved"
    em.approve_outreach(brief, d.draft_id, d.content_hash)
    assert [s[0] for s in chans["email"].sent] == ["owner@personal.test"]
    assert em.store.list_plans() == []


def test_asking_twice_asks_once(em, brief):
    em.add_test_vendor("Test Caterer", "Mysuru", C.CATERER, "owner@personal.test", True, "Asha", plan_id=brief)
    assert len(em.request_all_quotes(brief, "Asha")[0]) == 1
    made, skipped = em.request_all_quotes(brief, "Asha")
    assert made == [] and skipped == ["Test Caterer: already asked"]


def test_a_business_without_a_confirmed_contact_is_not_asked(em, brief):
    em.search_vendors(brief, C.CATERER, "places", 10)
    made, skipped = em.request_all_quotes(brief, "Asha")
    assert made == [] and any("no confirmed contact" in s for s in skipped)


def test_a_reply_is_filed_under_the_brief_and_read_as_untrusted_text(em, brief, chans, clock):
    em.add_test_vendor("Test Caterer", "Mysuru", C.CATERER, "owner@personal.test", True, "Asha", plan_id=brief)
    d = em.request_all_quotes(brief, "Asha")[0][0]
    em.approve_outreach(brief, d.draft_id, d.content_hash)
    sent = em.outreach(brief)[0]
    chans["email"].inbox = [Inbound(channel="email", sender="owner@personal.test", external_id="<r1>", received_at=clock() + timedelta(minutes=5), body="Available. Total INR 52,000 for 40 guests, valid 10 days.", in_reply_to=sent.external_id)]
    clock.advance(minutes=10)
    em.poll_conversations()
    thread = em.inbox(brief).threads[0]
    assert len(thread.replies) == 1 and 52000 in [a.low_inr for a in thread.replies[0].analysis.amounts]
    assert em.store.list_plans() == []


def test_a_brief_with_requests_out_outlives_its_day(em, brief):
    row = em.staging.require(brief[6:])
    em.add_test_vendor("Test Caterer", "Mysuru", C.CATERER, "owner@personal.test", True, "Asha", plan_id=brief)
    em.request_all_quotes(brief, "Asha")
    kept = em.staging.require(brief[6:])
    assert kept.expires_at > row.expires_at + timedelta(days=10) and kept.version == row.version and kept.draft == row.draft


def test_changing_the_events_facts_makes_a_waiting_request_stale(em, brief):
    em.add_test_vendor("Test Caterer", "Mysuru", C.CATERER, "owner@personal.test", True, "Asha", plan_id=brief)
    d = em.request_all_quotes(brief, "Asha")[0][0]
    em.edit_staged(brief[6:], {"guest_count": 90}, em.staging.require(brief[6:]).version, TODAY)
    assert em.conversations.stale_reason(em.outreach(brief)[0]) == "the event details in the text changed"
    with pytest.raises(ValueError):
        em.approve_outreach(brief, d.draft_id, d.content_hash)


def test_a_plans_own_rules_are_unchanged(em):
    plan_id = em.plan("Plan a 150-person college fest in Pune on December 12 under INR 250,000. Venue and catering.", today=TODAY).plan.plan_id
    biz, _ = em.add_test_vendor("Test Photographer", "Pune", C.PHOTOGRAPHER, "owner@personal.test", True, "Asha")
    with pytest.raises(ValueError, match="not in this plan"):
        em.draft_outreach(plan_id, C.PHOTOGRAPHER, "email", "owner@personal.test", "Asha", vendor_id=biz.vendor_id)  # only a brief may ask a vendor outside its choices


def test_the_routes_work_with_a_brief_id(em, brief):
    client = create_app(em, today_fn=lambda: TODAY, rate_limit_per_minute=10_000).test_client()
    token = client.get("/api/session").get_json()["csrf"]
    r = client.post(f"/api/plans/{brief}/shortlist/search", data=json.dumps({"category": "caterer", "source": "places", "limit": 5}), headers={"X-EventMate-CSRF": token, "Content-Type": "application/json"})
    assert r.status_code == 200, r.get_json()
    body = client.get(f"/api/plans/{brief}/shortlist").get_data(as_text=True)
    assert "gplaces:ChIJcaterer00001" in body and "Sri Hari" not in body
    assert client.get("/api/plans/brief_nope/shortlist").status_code in (400, 404, 410)
