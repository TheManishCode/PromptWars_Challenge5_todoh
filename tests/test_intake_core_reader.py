"""Which number belongs to which fact: a date's year is not money, a subgroup is not the guest total, an option list holds only values of its own field.

Written from the 3dab38c page tests (rule reader, no model). Each case runs the real ``intake.propose``; nothing here sends anything.

1. money and dates: "2 feb 2027 rs 10,00,000" read the year as the budget (2,027) and lost the real amount; the currency word after a number belongs to the amount that follows it;
2. total and subgroup guests: "100 students ... 3 people having alergy to peanuts" read 3 guests; a count that says who it is about (allergic, diabetic, "of them") is not the total;
3. options: "rupees 50000 and 6 guests are diabetic" offered "6 guests / 50000 guests", the amount having been read as the start of a range.

The limits are written down at the end of the file: what these tests do not show.
"""

from datetime import date

import pytest

from eventmate.logs import RunLogs
from eventmate.planning import intake
from eventmate.schemas import Allergen

TODAY = date(2026, 10, 30)
CITIES = ("Pune", "Mumbai", "Bengaluru", "Hyderabad", "Chennai", "Kolkata", "Delhi", "Jaipur", "Ahmedabad")


@pytest.fixture
def logs(tmp_path):
    return RunLogs.create(log_dir=tmp_path, run_id="core-reader")


@pytest.fixture
def talk(logs):
    """talk("first", "second", ...) -> the last turn, each message sent the way the page sends it."""

    def run(*messages, known=CITIES):
        turn = None
        for m in messages:
            turn = intake.propose([m], turn.draft if turn else None, TODAY, known, logs, None, expecting=turn.asking if turn else None)
        return turn

    return run


def open_fields(turn):
    return [u.field for u in turn.draft.unresolved]


def asked(turn, field):
    return next(u for u in turn.draft.unresolved if u.field == field)


def pick_values(turn, field):
    return sorted(int(p.value) for p in asked(turn, field).picks)


# ===================================================================== 1. a date's numbers are the date's

DATES = [
    ("2 feb 2027", date(2027, 2, 2)),
    ("2nd February 2027", date(2027, 2, 2)),
    ("feb 2 2027", date(2027, 2, 2)),
    ("feb 2, 2027", date(2027, 2, 2)),
    ("22 dec 2026", date(2026, 12, 22)),
    ("December 22, 2026", date(2026, 12, 22)),
    ("22/12/2026", date(2026, 12, 22)),
    ("2027-02-02", date(2027, 2, 2)),
    ("5 nov 2026", date(2026, 11, 5)),
]
MONEY = [
    ("rs 10,00,000", 1_000_000),
    ("rupees 50000", 50_000),
    ("50000 rupees", 50_000),
    ("50000 rs", 50_000),
    ("rs. 50k", 50_000),
    ("10,00,000 rs", 1_000_000),
    ("10 lakh rs", 1_000_000),
    ("₹2,00,000", 200_000),
    ("INR 75,000", 75_000),
    ("75000 inr", 75_000),
    ("Rs 2 lakh.", 200_000),
    ("budget rs 50000", 50_000),
    ("budget of 2 lakh", 200_000),
]
DATE_IDS = [d for d, _ in DATES]
MONEY_IDS = [m for m, _ in MONEY]


@pytest.mark.parametrize("money,amount", MONEY, ids=MONEY_IDS)
@pytest.mark.parametrize("written,when", DATES, ids=DATE_IDS)
def test_the_amount_after_a_date_is_the_budget_and_the_date_stays_the_date(talk, written, when, money, amount):
    t = talk(f"wedding in Pune for 100 guests {written} {money}")
    assert (t.draft.budget_total, t.draft.event_date) == (amount, when), f"read budget {t.draft.budget_total}, date {t.draft.event_date}"
    assert t.draft.guest_count == 100 and t.draft.unresolved == () and t.ready


@pytest.mark.parametrize("money,amount", MONEY, ids=MONEY_IDS)
@pytest.mark.parametrize("written,when", DATES, ids=DATE_IDS)
def test_the_amount_before_a_date_is_the_budget_and_the_date_stays_the_date(talk, written, when, money, amount):
    t = talk(f"wedding in Pune for 100 guests, {money} {written}")
    assert (t.draft.budget_total, t.draft.event_date) == (amount, when), f"read budget {t.draft.budget_total}, date {t.draft.event_date}"
    assert t.draft.guest_count == 100 and t.draft.unresolved == () and t.ready


@pytest.mark.parametrize("text", ["wedding in Pune for 100 guests on 2 feb 2027", "wedding in Pune for 100 guests on 22 dec 2026", "wedding in Pune for 100 guests on 2027-02-02"])
def test_a_date_alone_is_never_a_budget(talk, text):
    t = talk(text)
    assert t.draft.budget_total is None and "budget_total" in t.missing and not t.ready


@pytest.mark.parametrize("budget,value", [("budget 2027", 2027), ("rs 2027", 2027), ("2027 rupees", 2027), ("₹2026", 2026), ("budget 2,027", 2027)])
def test_a_number_that_equals_a_year_is_still_a_budget_when_it_is_written_as_one(talk, budget, value):
    t = talk(f"wedding in Pune for 100 guests on 12 December 2026, {budget}")
    assert t.draft.budget_total == value and t.draft.event_date == date(2026, 12, 12) and t.ready


def test_the_same_year_may_be_the_date_and_the_budget(talk):
    t = talk("wedding in Pune for 100 guests on 12 December 2027, rs 2027")
    assert (t.draft.budget_total, t.draft.event_date) == (2027, date(2027, 12, 12))


def test_a_money_marker_belongs_to_the_number_that_follows_it_not_the_one_before(talk):
    t = talk("wedding in Pune for 100 guests, 2027 rupees 50000")
    assert t.draft.budget_total == 50_000


# ===================================================================== 2. total guests and subgroups

WHEN = "on 12 December 2026, budget 2 lakh"
TOTAL_WITH_SUBGROUP = [
    ("original", "get me an event plan for 100 students in banglore with dietary restrictions of vegetarian and some aroound 3 people having alergy to peanuts?", 100),
    ("having", f"wedding in Pune for 100 guests {WHEN}, 3 people having an allergy to peanuts", 100),
    ("subgroup first", f"wedding in Pune {WHEN}, 3 people having an allergy to peanuts, 100 guests in total", 100),
    ("are allergic", f"wedding in Pune for 100 guests {WHEN}, 3 guests are allergic to peanuts", 100),
    ("some", f"wedding in Pune for 100 students {WHEN} and some 3 people allergic to peanuts", 100),
    ("have", f"wedding in Pune {WHEN}. 3 people have a peanut allergy. 100 guests", 100),
    ("who", f"wedding in Pune for 100 guests {WHEN}, 3 persons who are allergic to peanuts", 100),
    ("with", f"wedding in Pune for 100 guests {WHEN}, 3 people with peanut allergy", 100),
    ("diabetic", f"birthday in Pune for 40 persons {WHEN}, 6 guests are diabetic", 40),
    ("of them", f"wedding in Pune for 100 guests {WHEN}, 3 of them are allergic to peanuts", 100),
    ("of the", f"wedding in Pune {WHEN}, 3 of the 100 guests have a peanut allergy", 100),
    ("group noun total", f"party in Pune for 60 friends {WHEN}, 2 people are allergic to peanuts", 60),
]


@pytest.mark.parametrize("label,text,total", TOTAL_WITH_SUBGROUP, ids=[c[0] for c in TOTAL_WITH_SUBGROUP])
def test_a_subgroup_count_is_never_the_guest_total(talk, label, text, total):
    t = talk(text)
    assert t.draft.guest_count == total, f"{text!r} gave guests {t.draft.guest_count}"
    assert "guest_count" not in open_fields(t)


@pytest.mark.parametrize("text", [c[1] for c in TOTAL_WITH_SUBGROUP if "peanut" in c[1]], ids=[c[0] for c in TOTAL_WITH_SUBGROUP if "peanut" in c[1]])
def test_the_allergy_is_kept_when_its_count_is_left_out_of_the_total(talk, text):
    t = talk(text)
    stated = Allergen.PEANUT in t.draft.allergens
    asked_about = any(u.field == "allergens" for u in t.draft.unresolved)  # a misspelt allergy word is asked about, never dropped
    assert stated or asked_about, "the peanut allergy was lost"
    assert not (t.ready and not stated), "Build was offered with the allergy neither recorded nor asked about"


def test_an_unrelated_extra_total_is_still_a_conflict(talk):
    t = talk(f"wedding in Pune for 100 guests {WHEN}, we may be 150 guests")
    assert t.draft.guest_count is None and pick_values(t, "guest_count") == [100, 150]


def test_a_total_that_says_it_is_vegetarian_is_still_the_total(talk):
    t = talk(f"wedding in Pune for 100 guests are vegetarian {WHEN}")
    assert t.draft.guest_count == 100 and t.ready


@pytest.mark.parametrize("text", [
    f"wedding in Pune {WHEN}, some 3 people allergic to peanuts",
    f"wedding in Pune {WHEN}, 3 people having an allergy to peanuts",
])
def test_a_subgroup_with_no_total_in_the_message_asks_for_the_total(talk, text):
    t = talk(text)
    assert t.draft.guest_count is None and not t.ready
    q = asked(t, "guest_count")
    assert "3" in q.said and "guests" in q.question.lower()
    assert all(int(p.value) != 3 for p in q.picks), "the subgroup was offered as the total"


@pytest.mark.parametrize("text", [
    f"wedding in Pune for 100 guests, allergic to peanuts, {WHEN}",
    f"wedding in Pune for 100 guests, some are allergic to peanuts {WHEN}",
    f"wedding in Pune for 100 guests and some are allergic to peanuts {WHEN}",
    f"wedding in Pune for 100 guests {WHEN}. Allergic to peanuts: 2 people",
    f"wedding in Pune for 100 guests {WHEN}, not allergic to anything",
    f"wedding in Pune {WHEN}, peanut allergy and 100 guests",
    f"wedding in Pune {WHEN}, peanut allergy, 100 guests",
])
def test_a_total_followed_by_a_separate_allergy_clause_stays_the_total(talk, text):
    t = talk(text)
    assert t.draft.guest_count == 100 and "guest_count" not in open_fields(t)


def test_a_count_with_no_guest_word_is_not_a_total_and_the_guest_question_is_asked(talk):
    t = talk(f"wedding in Pune {WHEN}, 3 of them are allergic to peanuts")
    assert t.draft.guest_count is None and not t.ready and "guest_count" in t.missing and Allergen.PEANUT in t.draft.allergens


def test_two_diet_groups_with_no_total_ask_for_the_total_and_do_not_offer_either_group(talk):
    t = talk(f"wedding in Pune {WHEN}, 30 guests are vegetarian and 70 guests are non veg")
    assert t.draft.guest_count is None and not t.ready
    assert all(int(p.value) not in (30, 70) for p in asked(t, "guest_count").picks)


def test_a_diet_subgroup_beside_the_total_keeps_the_total(talk):
    t = talk(f"wedding in Pune for 100 guests {WHEN}, 30 guests are vegetarian")
    assert t.draft.guest_count == 100 and "guest_count" not in open_fields(t)


def test_the_total_given_in_a_later_message_settles_a_subgroup_only_first_message(talk):
    t = talk(f"wedding in Pune {WHEN}, some 3 people allergic to peanuts", "100 guests")
    assert t.draft.guest_count == 100 and Allergen.PEANUT in t.draft.allergens and t.ready


def test_a_subgroup_in_a_later_message_does_not_replace_the_reviewed_total(talk):
    t = talk(f"wedding in Pune for 100 guests {WHEN}", "3 people having an allergy to peanuts")
    assert t.draft.guest_count == 100 and Allergen.PEANUT in t.draft.allergens


# ===================================================================== 3. options hold only their own field

def test_an_amount_before_a_guest_count_is_not_the_start_of_a_range(talk):
    t = talk("birthday for 40 persons in Pune on 12 December 2026 rupees 50000 and 6 guests are diabetic")
    assert (t.draft.guest_count, t.draft.budget_total) == (40, 50_000) and t.draft.unresolved == () and t.ready


@pytest.mark.parametrize("text", [
    "wedding in Pune on 12 December 2026, 50000 rupees and 40 guests",
    "wedding in Pune on 12 December 2026, rs 50000 and 40 guests",
    "wedding in Pune on 12 December 2026, ₹50,000 and 40 guests",
    "wedding in Pune on 12 December 2026, budget 50000 and 40 guests",
    "wedding in Pune on 12 December 2026, budget of 50000 or 40 guests",
])
def test_money_next_to_a_guest_count_is_never_a_guest_range(talk, text):
    t = talk(text)
    assert (t.draft.guest_count, t.draft.budget_total) == (40, 50_000) and t.draft.unresolved == ()


def test_a_real_guest_range_is_still_asked_with_both_ends(talk):
    t = talk("wedding in Pune on 12 December 2026, budget 2 lakh, 100 to 150 guests")
    assert t.draft.guest_count is None and pick_values(t, "guest_count") == [100, 150]


def test_each_question_offers_only_values_of_its_own_field(talk):
    t = talk("wedding in Pune for 100 guests or 150 guests on 12 December 2026, rupees 50000 or rupees 60000")
    assert pick_values(t, "guest_count") == [100, 150]
    assert pick_values(t, "budget_total") == [50_000, 60_000]
    assert all("guest" in p.label for p in asked(t, "guest_count").picks)


def test_no_option_is_a_number_that_a_date_or_a_budget_owns(talk):
    t = talk("wedding in Pune for 100 guests or 150 guests on 22 dec 2026, rs 50000")
    assert pick_values(t, "guest_count") == [100, 150], "the year 2026 or the budget 50000 was offered as a guest count"


# ===================================================================== what these tests do not show
#
# They cover the sentences above and the phrasings in the tables, read by the rule reader with no model. They do not show that every wording is read correctly: a subgroup written
# with a verb or noun not listed here can still be read as the total when another count is present, and a number written in words is read only where a unit or guest word follows it.
# A model that proposes values would have to pass through the same checks.
