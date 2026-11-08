"""An earlier quote is shown while a fresh one is awaited: what the vendor said, when, and why it may no longer hold. It is never priced from."""

from datetime import UTC, date, datetime

import pytest

from eventmate.service import EventMate
from eventmate.pricing.observations import old_reasons
from eventmate.schemas import ObservationSource, QuoteObservation, VendorCategory
from eventmate.web.app import create_app

TODAY = date(2026, 11, 6)
PLACE = "gplaces:ChIJabcdefgh12345"


def quote(**kw):
    base = {
        "observation_id": "o1", "vendor_id": "ext_" + "0" * 24, "category": VendorCategory.CATERER, "source_type": ObservationSource.VENDOR_QUOTE, "entered_by": "asha",
        "observed_at": datetime(2026, 11, 1, tzinfo=UTC), "expires_at": date(2026, 12, 1), "confidence": 0.9, "evidence_ref": "email", "amount_inr": 650, "basis": "per_plate", "guest_count": 80,
    }
    return QuoteObservation(**{**base, **kw})


def test_a_recent_quote_for_the_same_event_is_not_called_old():
    assert old_reasons(quote(), TODAY, 80, None) == ()
    assert old_reasons(quote(), TODAY) == ()


@pytest.mark.parametrize("change,guests,when,word", [
    ({"expires_at": date(2026, 11, 5)}, None, None, "expired"),
    ({"observed_at": datetime(2026, 9, 1, tzinfo=UTC)}, None, None, "days old"),
    ({}, 120, None, "80 guests, not 120"),
    ({"quoted_for_date": date(2026, 12, 12)}, None, date(2026, 12, 19), "2026-12-12, not 2026-12-19"),
])
def test_each_reason_is_said_in_words(change, guests, when, word):
    assert any(word in r for r in old_reasons(quote(**change), TODAY, guests, when))


def test_a_small_change_in_headcount_does_not_make_a_quote_old():
    assert old_reasons(quote(), TODAY, 90, None) == ()  # 12% away: within the drift allowed


@pytest.fixture
def em(tmp_path):
    return EventMate(log_dir=tmp_path / "logs", plan_dir=tmp_path / "plans", run_id="earlier")


def test_the_last_quote_of_each_business_in_the_city_is_listed_with_its_history(em):
    biz = em.externals.add(PLACE, "Sri Hari Caterers", "Mysuru", VendorCategory.CATERER, "gplaces", source_url="https://maps.google.com/?cid=1")
    em.externals.add("gplaces:ChIJzzzzzzzz99999", "Elsewhere Hall", "Pune", VendorCategory.VENUE, "gplaces", source_url="https://maps.google.com/?cid=2")
    em.externals.add("gplaces:ChIJyyyyyyyy88888", "Never Replied", "Mysuru", VendorCategory.VENUE, "gplaces", source_url="https://maps.google.com/?cid=3")
    for amount in (600, 650):
        em.observe_quote(biz.vendor_id, VendorCategory.CATERER, source_type=ObservationSource.VENDOR_QUOTE, entered_by="asha", observed_at=datetime(2026, 11, 1, tzinfo=UTC),
                         expires_at=date(2026, 12, 1), confidence=0.9, evidence_ref="email", amount_inr=amount, basis="per_plate", guest_count=80)
    (row,) = em.earlier_quotes("mysuru", TODAY, 200, None)
    assert row["name"].startswith("Google listing") and row["external_id"] == PLACE  # the listing's name is not stored: the page shows it from the place id and row["amount_inr"] == 650 and row["earlier"] == 1
    assert row["old"] and "80 guests, not 200" in row["old"][0]


def test_the_endpoint_reads_and_refuses_bad_input(em):
    client = create_app(em, today_fn=lambda: TODAY).test_client()
    assert client.get("/api/earlier-quotes?city=Mysuru").get_json() == {"quotes": []}
    assert client.get("/api/earlier-quotes").status_code == 400
    assert client.get("/api/earlier-quotes?city=Mysuru&guests=many").status_code == 400
    assert client.get("/api/earlier-quotes?city=Mysuru&date=tomorrow").status_code == 400


@pytest.mark.parametrize("query", ["city=Mysuru&guests=0", "city=Mysuru&guests=-5", "city=Mysuru&guests=99999999999999999999", "city=%00", "city=%3Cscript%3E"])
def test_the_endpoint_refuses_values_no_event_has(em, query):
    assert create_app(em, today_fn=lambda: TODAY).test_client().get("/api/earlier-quotes?" + query).status_code == 400
