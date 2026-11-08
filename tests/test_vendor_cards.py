"""A vendor card holds the user's facts and the vendor's, each with where it came from, and nothing from a Google listing but its place id (Google's policy). Facts that disagree are both shown."""

import json
from datetime import UTC, date, datetime, timedelta

import pytest

from eventmate.integrations.places import PlacesSource
from eventmate.messaging.channels import Inbound
from eventmate.messaging.conversations import SendPolicy
from eventmate.schemas import ExternalBusiness, ObservationSource, VendorCategory as C
from eventmate.service import EventMate
from eventmate.sourcing.externals import placeholder_name
from eventmate.web.app import create_app
from tests.conftest import TODAY
from tests.test_brief_sourcing import MYSURU, fake_places
from tests.test_conversations import Clock, FakeChannel
from tests.test_shortlist import FakeFetcher

SITE = "https://srihari-caterers.example/"
PAGES = {SITE: "<html><body><a href='/contact'>Contact</a><p>hello@srihari-caterers.example</p></body></html>",
         SITE + "contact": "<html><body><a href='mailto:orders@srihari-caterers.example'>Orders</a></body></html>"}
LISTING_WORDS = ("Sri Hari Caterers", "JP Nagar", "570031", "maps.google.com", "581", "4.8")  # what Google's listing says: none of it may be on disk


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def chans():
    return {"email": FakeChannel("email"), "whatsapp": FakeChannel("whatsapp", False, False)}


@pytest.fixture
def em(tmp_path, chans, clock):
    e = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="cards", channels=chans, require_symbolic=False)
    e.places = e.vendor_search.places = PlacesSource(api_key="k" * 30, transport=fake_places)
    e.contact_fetcher = lambda: FakeFetcher(PAGES)
    e.outreach_store.now = e.conversations.now = clock
    e.conversations.policy = SendPolicy(per_vendor_per_day=20, per_day=200, whatsapp_gap_s=0)
    return e


@pytest.fixture
def brief(em):
    return "brief_" + em.intake_turn([MYSURU], today=TODAY).staged_id


@pytest.fixture
def sri(em, brief):
    em.search_vendors(brief, C.CATERER, "places", 10)
    return next(i.business for i in em.shortlist_view(brief) if i.business.external_id == "gplaces:ChIJcaterer00001")


def card(em, brief, vendor_id):
    return next(c for c in em.vendor_cards(brief, TODAY) if c.vendor_id == vendor_id)


def test_a_google_listings_card_has_its_place_id_and_none_of_its_words(em, brief, sri):
    c = card(em, brief, sri.vendor_id)
    assert c.source == "gplaces" and c.place_id == "ChIJcaterer00001" and not c.name_confirmed and c.name.startswith("Google listing")
    assert c.facts == () and not any(w in c.model_dump_json() for w in LISTING_WORDS)


def test_every_held_fact_says_where_it_came_from_when_and_whether_the_user_confirmed_it(em, brief, sri):
    em.find_business_contacts(sri.vendor_id)
    c = card(em, brief, sri.vendor_id)
    emails = [f for f in c.facts if f.field == "contact.email"]
    assert {f.value for f in emails} == {"hello@srihari-caterers.example", "orders@srihari-caterers.example"}
    for f in emails:
        assert f.source_type == "vendor_website" and f.source_ref.startswith(SITE) and f.captured_at and f.status == "found" and not f.user_confirmed and f.candidate_id
    assert c.contact_ready is False
    hello = next(f for f in emails if f.value.startswith("hello"))
    em.confirm_contact(sri.vendor_id, hello.candidate_id, "Asha", business_name="Sri Hari Caterers")
    c = card(em, brief, sri.vendor_id)
    confirmed = [f for f in c.facts if f.status == "confirmed" and f.field == "contact.email"]
    assert len(confirmed) == 1 and confirmed[0].user_confirmed and c.contact_ready and c.name_confirmed and c.name == "Sri Hari Caterers"


def test_two_addresses_that_disagree_are_both_shown_as_a_conflict_until_one_is_rejected(em, brief, sri):
    em.find_business_contacts(sri.vendor_id)
    facts = [f for f in card(em, brief, sri.vendor_id).facts if f.field == "contact.email"]
    assert all(f.conflict for f in facts) and len(facts) == 2
    em.reject_contact(sri.vendor_id, facts[0].candidate_id)
    left = [f for f in card(em, brief, sri.vendor_id).facts if f.field == "contact.email"]
    assert not any(f.conflict for f in left), "a rejected claim no longer disagrees with anything"


def test_confirming_a_google_listed_business_needs_a_name_the_user_types(em, brief, sri):
    em.find_business_contacts(sri.vendor_id)
    cand = em.shortlist.candidates(sri.vendor_id)[0]
    for blank in (None, "", "   "):
        with pytest.raises(ValueError, match="confirm the business name"):
            em.confirm_contact(sri.vendor_id, cand.candidate_id, "Asha", business_name=blank)
    with pytest.raises(ValueError):
        em.confirm_contact(sri.vendor_id, cand.candidate_id, "Asha", business_name="<b>x</b>")
    assert em.outreach_store.contacts().get(sri.vendor_id) is None and em.externals.get(sri.vendor_id).name_source == "placeholder"


def test_the_contact_search_fetches_the_listings_website_now_and_keeps_nothing_of_it(em, brief, sri):
    assert sri.website is None
    res = em.find_business_contacts(sri.vendor_id)
    assert len(res.added) == 2 and em.externals.get(sri.vendor_id).website is None


def test_a_listing_that_google_no_longer_has_is_a_clear_refusal(em, brief):
    em.externals.add("gplaces:ChIJgone0000000", "", "Mysuru", C.CATERER, "gplaces")
    em.shortlist.add_entry(brief, C.CATERER, em.externals.all()[-1].vendor_id)
    with pytest.raises(Exception, match="no longer lists"):
        em.find_business_contacts(em.externals.all()[-1].vendor_id)


def test_an_earlier_quote_a_request_and_a_reply_each_appear_with_their_source(em, brief, sri, chans, clock):
    em.find_business_contacts(sri.vendor_id)
    cand = next(c for c in em.shortlist.candidates(sri.vendor_id) if c.value.startswith("orders"))
    em.confirm_contact(sri.vendor_id, cand.candidate_id, "Asha", business_name="Sri Hari Caterers")
    em.observe_quote(sri.vendor_id, C.CATERER, source_type=ObservationSource.VENDOR_QUOTE, entered_by="asha", observed_at=datetime(2026, 9, 1, tzinfo=UTC), expires_at=date(2026, 12, 1),
                     confidence=0.9, evidence_ref="email 1 Sep", amount_inr=650, basis="per_plate", guest_count=80)
    d = em.request_all_quotes(brief, "Asha")[0][0]
    em.approve_outreach(brief, d.draft_id, d.content_hash)
    sent = em.outreach(brief)[0]
    chans["email"].inbox = [Inbound(channel="email", sender="orders@srihari-caterers.example", external_id="<r1>", received_at=clock() + timedelta(minutes=5), body="Available. Total INR 52,000 for 40 guests.", in_reply_to=sent.external_id)]
    clock.advance(minutes=10)
    em.poll_conversations()
    by = {f.field: f for f in card(em, brief, sri.vendor_id).facts}
    assert by["quote"].source_type == "vendor_quote" and by["quote"].source_ref == "email 1 Sep" and by["quote"].status == "old" and "days old" in by["quote"].note
    assert by["request"].source_type == "app_request" and by["request"].status == "sent" and by["request"].source_ref == d.draft_id
    assert by["reply"].source_type == "vendor_reply" and "52,000" in by["reply"].value and "Available" in by["reply"].evidence and not by["reply"].user_confirmed


def test_a_test_vendor_card_says_it_is_a_test_and_who_owns_the_address(em, brief):
    biz, _ = em.add_test_vendor("Test Caterer", "Mysuru", C.CATERER, "owner@personal.test", True, "Asha", plan_id=brief)
    c = card(em, brief, biz.vendor_id)
    assert c.label == "TEST VENDOR (address owned by Asha)" and c.origin == "test" and c.place_id is None and c.contact_ready
    assert any(f.field == "contact.email" and f.user_confirmed and "Asha" in f.note for f in c.facts)


def test_nothing_from_a_google_listing_is_on_disk_after_the_whole_flow(em, brief, sri, chans, tmp_path):
    em.find_business_contacts(sri.vendor_id)
    cand = em.shortlist.candidates(sri.vendor_id)[0]
    em.confirm_contact(sri.vendor_id, cand.candidate_id, "Asha", business_name="My Caterer")
    d = em.request_all_quotes(brief, "Asha")[0][0]
    em.approve_outreach(brief, d.draft_id, d.content_hash)
    em.vendor_cards(brief, TODAY)
    found = {}
    for path in tmp_path.rglob("*"):
        if path.is_file():
            text = path.read_text(errors="ignore")
            for w in LISTING_WORDS:
                if w in text:
                    found.setdefault(w, []).append(path.name)
    # the vendor's own page url (where an address was read) is provenance of what the vendor published, so its host may appear; the listing's own words may not
    assert found == {}, found


def test_a_record_an_earlier_version_wrote_is_scrubbed_at_start_up(tmp_path, chans):
    e = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="legacy", channels=chans, require_symbolic=False)
    old = ExternalBusiness(vendor_id=__import__("eventmate.schemas", fromlist=["external_vendor_id"]).external_vendor_id("gplaces:ChIJlegacy000001"), external_id="gplaces:ChIJlegacy000001", name="Legacy Listing Name",
                           city="Mysuru", category=C.CATERER, source="gplaces", source_url="https://maps.google.com/?cid=9", website="https://legacy.example/", rating=4.9, created_at=datetime(2026, 10, 1, tzinfo=UTC))
    e.externals._file()._append(old, old.vendor_id)
    assert "Legacy Listing Name" in (tmp_path / "p" / "externals" / "external-all.jsonl").read_text()
    again = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="legacy2", channels=chans, require_symbolic=False)
    text = (tmp_path / "p" / "externals" / "external-all.jsonl").read_text()
    assert "Legacy Listing Name" not in text and "legacy.example" not in text and "maps.google.com" not in text
    biz = again.externals.get(old.vendor_id)
    assert biz.name == placeholder_name("gplaces:ChIJlegacy000001") and biz.name_source == "placeholder" and biz.website is None and biz.rating is None
    assert again.externals.scrub_google_content() == 0, "scrubbing again changes nothing"


def test_the_details_endpoint_and_the_cards_endpoint_and_the_request_text(em, brief, sri):
    client = create_app(em, today_fn=lambda: TODAY, rate_limit_per_minute=10_000).test_client()
    token = client.get("/api/session").get_json()["csrf"]
    h = {"X-EventMate-CSRF": token}
    assert client.get("/api/places/ChIJcaterer00001").status_code == 403, "a lookup that costs money needs the page's token"
    r = client.get("/api/places/ChIJcaterer00001", headers=h)
    assert r.status_code == 200 and r.get_json()["name"] == "Sri Hari Caterers" and r.get_json()["phone"] is None and r.get_json()["website"] == SITE
    p = client.get("/api/places/ChIJcaterer00001?phone=1", headers=h).get_json()
    assert p["phone"] == "+91 98450 12345"
    assert client.get("/api/places/not%20an%20id", headers=h).status_code in (400, 404) and client.get("/api/places/short", headers=h).status_code in (400, 404, 403)
    cards = client.get(f"/api/plans/{brief}/vendor-cards").get_json()["cards"]
    assert [c["place_id"] for c in cards] == ["ChIJcaterer00001"] and "Sri Hari" not in json.dumps(cards)
    t = client.get(f"/api/plans/{brief}/request-text?category=caterer&name=Sri%20Hari%20Caterers&sender=Asha").get_json()
    assert "Hello Sri Hari Caterers," in t["body"] and "Mysuru" in t["body"] and "40 guests" in t["body"] and "60,000" not in t["body"] and "budget" not in t["body"].lower()
    blank = client.get(f"/api/plans/{brief}/request-text?category=caterer&name=&sender=").get_json()["body"]
    assert "Hello there," in blank and "[your name]" in blank, "a blank name is filled in by the user before they send it"
    assert client.get(f"/api/plans/{brief}/request-text?category=nonsense").status_code == 400
    assert client.get("/api/plans/brief_nope/vendor-cards").status_code in (400, 404, 410)
