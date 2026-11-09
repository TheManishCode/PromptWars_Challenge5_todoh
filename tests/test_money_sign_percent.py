"""A percent is never a rupee amount and a minus sign is never dropped (F8, F3 of the final verification). "25%" read as Rs 25 put a Rs 25 deposit on a Rs 54,000 quote, and "budget -5 lakh" planned on
Rs 5,00,000. Every place money is read refuses both and keeps the person's words."""
from datetime import timedelta

import pytest

from eventmate.agents import manager as mgr
from eventmate.documents.quotes import read_amount
from eventmate.followthrough.watches import amounts
from eventmate.integrations.deals import extract_mentions
from eventmate.messaging.channels import Inbound
from eventmate.moneytext import NEGATIVE_NOTE, PERCENT_NOTE, negative_before, percent_after
from eventmate.planning import reading
from eventmate.schemas import VendorCategory as C
from eventmate.sourcing import quote_template as qt
from eventmate.sourcing.quote_readings import parse_typed
from tests.conftest import EXAMPLE, TODAY
from tests.test_quote_readings import OK_FOR_40
from tests.test_quote_template import msg
from tests.test_shared_quotes import ADDRESS, own_vendor, person

PERCENTS = ["25%", "25 %", "25 percent", "30 per cent advance", "30 per-cent", "25 pct", "12.5%", "25 pc", "25 p.c.", "25 pcs", "100% advance"]
NEGATIVES = ["-5000", "−5000", "minus 5000", "negative 5000", "-Rs 5000", "Rs -5000", "-5 lakh", "minus 5k"]


@pytest.mark.parametrize("text", PERCENTS)
def test_a_percent_on_a_money_line_is_not_a_rupee_amount(text):
    assert qt._parse("deposit", "money", text) == (None, PERCENT_NOTE)
    assert qt._parse("price", "money", text) == (None, PERCENT_NOTE)


@pytest.mark.parametrize("text", NEGATIVES)
def test_shorthand_glued_to_the_number_is_unclear_never_rupees():
    for text in ("25pc advance", "30pct", "25%advance"):
        assert qt._parse("deposit", "money", text)[0] is None, text


def test_a_minus_sign_is_never_dropped_from_a_money_line(text):
    assert qt._parse("deposit", "money", text) == (None, NEGATIVE_NOTE)
    assert qt._parse("price", "money", text) == (None, NEGATIVE_NOTE)


def test_the_ordinary_ways_to_write_an_amount_still_read():
    for text, want in [("Rs 450", "450"), ("10,000", "10000"), ("INR 1.5 lakh", "150000"), ("₹ 95k", "95000"), ("Rs. 450/-", "450")]:
        assert qt._parse("price", "money", text) == (want, "")
    assert qt._parse("price", "money", "450 - 500")[1] == "that is a range, not a price", "a hyphen between two numbers is a range, not a sign"


@pytest.mark.parametrize("text", PERCENTS[:4])
def test_the_reader_leaves_a_vendors_percent_unclear_and_keeps_their_words(text):
    r = qt.read_message(msg(f"Price per plate (INR): 450\nDeposit to book (INR): {text}"), C.CATERER)
    d = r.fields["deposit"]
    assert d.state == "unclear" and d.value is None and d.note == PERCENT_NOTE and d.evidence == f"Deposit to book (INR): {text}"
    assert r.fields["price"].value == "450", "the other lines are read as before"


def test_a_vendors_negative_line_is_unclear_not_the_positive_amount():
    r = qt.read_message(msg("Price per plate (INR): -450\nDeposit to book (INR): 10,000"), C.CATERER)
    assert r.fields["price"].state == "unclear" and r.fields["price"].value is None and r.fields["price"].note == NEGATIVE_NOTE


@pytest.mark.parametrize("text", PERCENTS[:3])
def test_a_typed_percent_is_refused_with_why(text):
    with pytest.raises(ValueError, match="a percentage: type the amount in rupees"):
        parse_typed(C.CATERER, "deposit", text)


def test_a_typed_negative_is_refused_with_why():
    with pytest.raises(ValueError, match="a negative amount"):
        parse_typed(C.CATERER, "price", "-450")


def _world(tmp_path, deposit_line):
    em, chans, clock, brief = person(tmp_path, "pct")
    own_vendor(em, brief)
    d = em.request_all_quotes(brief, "Asha")[0][0]
    em.approve_outreach(brief, d.draft_id, d.content_hash)
    body = OK_FOR_40.replace("Deposit to book (INR): 10,000", deposit_line)
    assert deposit_line in body
    chans["email"].inbox = [Inbound(channel="email", sender=ADDRESS, external_id="<p1>", received_at=clock() + timedelta(minutes=5), body=body, in_reply_to=em.outreach(brief)[0].external_id)]
    clock.advance(minutes=10)
    em.poll_conversations()
    mid = em.outreach_store.inbound(brief)[-1].message_id
    plan_id = em.plan(EXAMPLE, today=TODAY).plan.plan_id
    em.brief_links.link(brief[len("brief_"):], plan_id)
    return em, brief, plan_id, mid


def test_terms_never_carry_a_percent_as_rupees_and_a_typed_amount_makes_the_right_terms(tmp_path):
    em, brief, plan_id, mid = _world(tmp_path, "Deposit to book (INR): 25%")
    em.confirm_quote_fields(brief, mid, dict.fromkeys(("available", "taxes_included", "min_guests", "max_guests", "diets")), "Asha")
    with pytest.raises(ValueError, match="nothing was read safely"):
        em.confirm_quote_fields(brief, mid, {"deposit": None}, "Asha")  # "confirm as read": nothing was read from "25%"
    (a,) = [x for x in em.quote_assessments(brief) if x.message_id == mid]
    assert {f.key: f.note for f in a.fields}["deposit"] == PERCENT_NOTE, "the screen shows why, beside the vendor's words"
    with pytest.raises(ValueError, match="a percentage"):
        em.confirm_quote_fields(brief, mid, {"deposit": "25 percent"}, "Asha")
    em.confirm_quote_fields(brief, mid, {"price": None}, "Asha", valid_days="10")
    with pytest.raises(ValueError, match="confirm every line"):
        em.propose_payment_terms(plan_id, mid, "Asha")  # no deposit was confirmed, so there are no terms and no Rs 25 payment
    em.confirm_quote_fields(brief, mid, {"deposit": "19500"}, "Asha")  # 25% of Rs 78,000, typed by the person
    t = em.propose_payment_terms(plan_id, mid, "Asha")
    assert (t.total_inr, t.deposit_inr, t.balance_inr) == (78_000, 19_500, 58_500)


@pytest.mark.parametrize("text", ["budget -5 lakh", "budget minus 5 lakh", "budget: -Rs 200000", "-5 lakh"])
def test_a_negative_budget_is_refused_not_read_as_positive(text):
    problems: list[str] = []
    assert mgr._parse_budget(text, problems) is None and "negative" in problems[0]
    assert reading.screen_budget(text) is not None and "negative" in reading.screen_budget(text).question


def test_a_unicode_minus_before_a_budget_is_not_read_as_positive():
    assert mgr._parse_budget("budget of −2,00,000", []) is None


def test_a_hyphen_used_as_a_separator_is_not_a_minus():
    assert mgr._parse_budget("Budget - 5 lakh", []) == 500_000 and reading.screen_budget("Budget - 5 lakh") is None
    assert not negative_before("Budget - 5 lakh", len("Budget - ")) and not negative_before("2-5 lakh", 2)
    assert negative_before("budget -5 lakh", len("budget -")) and negative_before("minus 5", len("minus ")) and percent_after("25%", 2) and not percent_after("25 plates", 2)


def test_the_plan_command_stops_on_a_negative_budget(capsys):
    from eventmate.__main__ import main
    rc = main(["plan", "Corporate lunch for 80 guests in Pune on 12 December 2026, budget -5 lakh"])
    assert rc != 0 and "cannot be negative" in capsys.readouterr().err


def test_a_negative_or_percent_amount_is_not_read_from_a_quote_document_a_watch_or_a_page():
    assert read_amount("-2,000") is None and read_amount("minus 2000") is None and read_amount("25%") is None and read_amount("Rs. 450/-") == 450
    assert amounts("was -Rs 500 now Rs 400") == [400]
    assert [m.low for m in extract_mentions("Rs 500 per plate, -Rs 450 per plate, Rs 25%")] == [500]
