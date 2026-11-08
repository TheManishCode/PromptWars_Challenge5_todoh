"""A vendor's quote is for one event. It may price that event, never another one with a different date or guest count (audit A1)."""
from datetime import UTC, date, datetime

from eventmate.schemas import ObservationSource, VendorCategory as C
from tests import test_conversations as tc
from tests.conftest import EXAMPLE, TODAY

em, chans, clock = tc.em, tc.chans, tc.clock  # the conversation tests' fixtures (em needs the other two)


def _quote(em, guests, on, amount=400):
    em.observations.add_quote("cat-pune-rasoi", C.CATERER, source_type=ObservationSource.VENDOR_CONFIRMATION, entered_by="Asha", observed_at=datetime(2026, 10, 30, tzinfo=UTC),
                              expires_at=date(2027, 6, 1), confidence=1.0, evidence_ref="msg:a", amount_inr=amount, basis="per_plate", guest_count=guests, quoted_for_date=on)


def test_a_quote_for_300_guests_on_one_date_does_not_price_90_guests(em):
    em.plan(EXAMPLE, today=TODAY)
    p = em.repo.get(C.CATERER, "cat-pune-rasoi")
    seed = em.repo.price(p, 90, date(2026, 12, 12))  # (the repository prices an event, so it is given the event's date)
    _quote(em, 300, date(2026, 12, 12))
    assert em.repo.price(p, 90, date(2026, 12, 12)) == seed, "a per-plate price given for 300 guests is not this event's price"


def test_a_quote_for_another_date_does_not_price_this_event(em):
    _quote(em, 120, date(2026, 12, 20))  # same guest count, another date
    pick = em.plan(EXAMPLE, today=TODAY).plan.selection_for(C.CATERER)  # the event is on December 12
    assert not (pick.vendor_id == "cat-pune-rasoi" and pick.cost == 400 * 120), "a price quoted for December 20 does not set the price of a December 12 event"


def test_a_quote_prices_the_event_it_was_given_for_and_only_that_one(em):
    em.plan(EXAMPLE, today=TODAY)
    p = em.repo.get(C.CATERER, "cat-pune-rasoi")
    _quote(em, 300, date(2026, 12, 12))
    assert em.repo.price(p, 300, date(2026, 12, 12)) == 400 * 300, "the same date and the quoted guest count: the quote prices it"
    assert em.repo.price(p, 200, date(2026, 12, 12)) == 400 * 200, "inside the range the vendor itself states (100 to 500): a per-plate price holds"
    assert em.repo.price(p, 300, date(2026, 12, 13)) == p.cost_for(300), "another date: the seed price"
    assert em.repo.price(p, 300) == p.cost_for(300), "no event date: nothing is priced from a quote"
    assert not em.repo.quote_expired(p, 300, date(2026, 12, 13)), "a quote for another event cannot fail this event's vendor either"


def test_an_expired_quote_for_this_event_still_fails_the_vendor_on_quote_valid(em):
    em.plan(EXAMPLE, today=TODAY)
    p = em.repo.get(C.CATERER, "cat-pune-rasoi")
    _quote(em, 120, date(2026, 12, 12))
    em.repo.clock = lambda: date(2027, 7, 1)
    assert em.repo.quote_expired(p, 120, date(2026, 12, 12)) and em.repo.price(p, 120, date(2026, 12, 12)) == p.cost_for(120)
