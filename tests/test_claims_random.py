"""Seeded random vendor replies: a price mixed with deposits, advances, extras, a competitor's or last year's price, phone numbers, dates and guest counts.

The rule is the one test_claims states: a WRONG price must never be proposed. Any claim must be the true price, and only when the reply holds nothing else that reads as money.
The first run of this found deposits, extras and a competitor's quote proposed as the price, and replies whose second amount ("84000 rupaye") the analyser never parsed.
"""

import random
from datetime import datetime, UTC

import pytest

from eventmate.followthrough.claims import extract_claims
from eventmate.messaging.conversations import analyze_reply
from eventmate.schemas import InboundMessage, VendorCategory as C

CHECK = ("service staff", "crockery and cutlery", "serving counters", "taxes", "transport")
NOW = datetime(2026, 10, 30, 9, tzinfo=UTC)


def msg(body):
    return InboundMessage(message_id="m1", plan_id="p", draft_id="d1", vendor_id="cat-pune-rasoi", vendor_name="Rasoi", category=C.CATERER, channel="email", sender="r@x.test",
                          received_at=NOW, external_id="<e1>", body=body, analysis=analyze_reply(body, CHECK))


def bodies(seed, n):
    r = random.Random(seed)

    def fmt(x):
        return r.choice([f"Rs {x:,}", f"INR {x:,}", f"Rs. {x}", f"₹{x:,}", f"Rs {x // 1000}k" if x % 1000 == 0 else f"Rs {x}", f"{x:,} rupees", f"{x} rupaye", f"Rs {x / 100000:g} lakh" if x % 10000 == 0 else f"Rs {x}"])

    distract = [
        lambda: f"Advance of {fmt(r.choice([10000, 20000, 25000]))} to confirm the date.", lambda: f"Refundable deposit {fmt(r.choice([5000, 10000]))}.",
        lambda: f"Decor is extra at {fmt(r.choice([8000, 15000]))}.", lambda: f"Transport {fmt(r.choice([2000, 3000]))} extra.", lambda: f"Call me on 98{r.randint(10000000, 99999999)}.",
        lambda: f"We served {r.choice([150, 300, 500])} guests last week at their wedding on 12/11/2026.", lambda: "GST 18% extra.",
        lambda: f"Cancellation after 10 days costs {fmt(r.choice([5000, 7500]))}.", lambda: f"Last year it was {fmt(r.choice([60000, 90000]))} but prices went up.",
        lambda: f"Competitor quoted {fmt(r.choice([50000, 60000]))}.", lambda: f"Overtime {fmt(r.choice([1000, 1500]))} per hour.", lambda: f"Extra plates {fmt(r.choice([400, 500]))} per plate beyond 100.",
        lambda: "Valid for 7 days.", lambda: "Thanks and regards",
    ]
    true = [lambda t: f"Our price is {fmt(t)} for the event.", lambda t: f"Total {fmt(t)} for the event, all included.", lambda t: f"We can do {fmt(t)} total.", lambda t: f"Quote: {fmt(t)} flat for the whole event."]
    for _ in range(n):
        t = r.choice([45000, 60000, 78000, 84000, 95000, 120000, 150000])
        parts = [r.choice(distract)() for _ in range(r.randint(0, 3))]
        price = r.choice(true)(t) if r.random() < .7 else None
        if price:
            parts.insert(r.randint(0, len(parts)), price)
        yield " ".join([r.choice(["Hello,", "Namaste sir,"]), *parts, "Regards, Rasoi"]), (t if price else None)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_a_wrong_price_is_never_proposed_from_a_noisy_reply(seed):
    proposed = 0
    for body, truth in bodies(seed, 400):
        claims, _ = extract_claims(msg(body))
        for c in claims:
            assert truth is not None and c.amount_inr == truth, (body, c.amount_inr)
            proposed += 1
    assert proposed >= 20  # it is a test of precision, but a reader that proposes nothing would pass it for the wrong reason


@pytest.mark.parametrize("body", [
    "Refundable deposit Rs 10,000.", "Extra plates Rs. 500 per plate beyond 100.", "Decor is extra at INR 15,000.", "Competitor quoted INR 60,000.", "Overtime 1500 rupaye per hour.",
    "Advance of Rs. 25000 to confirm the date.", "Our price is 120000 rupaye for the event. Refundable deposit Rs 10k.", "Quote: 84000 rupaye flat for the whole event. Competitor quoted INR 50,000.",
])
def test_an_amount_that_is_not_the_price_is_not_proposed_as_the_price(body):
    claims, _ = extract_claims(msg(body))
    assert claims == []  # nothing to confirm; whether the user is told why depends on whether the analyser saw an amount
