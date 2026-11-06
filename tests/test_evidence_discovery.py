"""Checkpoint 1: discovery and contact finding write to the evidence ledger, and a quote or price is never invented on the way (architecture L6)."""

import json


from eventmate.evidence.ledger import FactState as S
from eventmate.web.app import create_app
from tests.conftest import TODAY
from tests import test_discovery_e2e as e2e

COLLEGE = e2e.COLLEGE
clock, world, em, pid, shortlisted, candidate = e2e.clock, e2e.world, e2e.em, e2e.pid, e2e.shortlisted, e2e.candidate


def test_a_search_of_google_listings_records_no_evidence_because_nothing_of_a_listing_is_kept(em, pid):
    """Google's policy lets only the place id be stored, so a listing's name, link, website and rating are not recorded as evidence either."""
    items = shortlisted(em, pid)
    sharma = items["Sharma Caterers"].business.vendor_id
    assert em.evidence.records(sharma) == [] and em.evidence.best(sharma, "name") is None
    assert em.evidence.best(sharma, "price_per_plate_inr") is None  # a listing has no price, so none is claimed


def test_found_addresses_are_discovered_with_their_page_and_confirming_one_is_the_users_word(em, pid):
    items = shortlisted(em, pid)
    sharma = items["Sharma Caterers"].business.vendor_id
    cand = candidate(em, sharma, "orders")
    found = [r for r in em.evidence.records(sharma) if r.field.startswith("email_candidate:") and r.value == cand.value]
    assert len(found) == 1 and found[0].state is S.DISCOVERED and found[0].source.startswith("web:") and found[0].url == cand.source_url
    assert em.evidence.best(sharma, "email") is None  # found is not confirmed: the field for the address in use stays empty
    e2e.confirm(em, sharma, cand.candidate_id, edited_value="events@sharma-caterers.example")
    now = em.evidence.best(sharma, "email")
    assert now.value == "events@sharma-caterers.example" and now.state is S.USER_CONFIRMED and now.source == "user_input"
    assert em.evidence.usable_for_hard_constraint(sharma, "email") is None  # "you told us", not verified
    assert len(em.evidence.records(sharma, "email")) == 1 and any(r.value == cand.value for r in em.evidence.records(sharma))


def test_running_discovery_again_adds_no_duplicates(em, pid):
    shortlisted(em, pid)
    n = len(em.evidence.records())
    shortlisted(em, pid)
    assert len(em.evidence.records()) == n


def test_the_evidence_route_groups_by_state_and_rejects_hostile_subjects(em, pid):
    sharma = shortlisted(em, pid)["Sharma Caterers"].business.vendor_id
    e2e.candidate(em, sharma, "orders")
    c = create_app(em, today_fn=lambda: TODAY, poll_seconds=0).test_client()
    doc = c.get(f"/api/evidence?subject={sharma}").get_json()
    assert set(doc) == {s.value for s in S} and any(r["field"].startswith("email_candidate:") for r in doc["discovered"]) and doc["booked"] == []
    assert c.get("/api/evidence?subject=../x").status_code == 400
    assert "ext_" in json.dumps(c.get("/api/evidence").get_json())
