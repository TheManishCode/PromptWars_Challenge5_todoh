"""What the rule reader keeps instead of choosing: a range, an amount that is not the budget, a diet that is only some guests', a correction with no field word, a name-less allergy.

Written after the manual pack of 72e1862 (24 messages, 8 chats). The sentences here are new wordings of the same problems, not the pack's own sentences: the pack was shared, so it
is no longer held out and is kept only as a before-and-after record. Each case runs the real ``intake.propose`` with no model; nothing here sends anything.

1. a name-less allergy is a question that keeps Build away, and "so the kitchen must be careful" or "everyone else eats anything" is never an allergy;
2. an amount that was already paid (advance, deposit) is not a budget, and no question offers it as one;
3. a range of dates or of money is asked about with both ends, never reduced to one end;
4. a diet that is said about some of the guests is not set for all of them: the user says which;
5. a message that changes a number without saying what it counts is asked about, also while another question is open.

The limits are written at the end of the file.
"""

from datetime import date

import pytest

from eventmate.logs import RunLogs
from eventmate.planning import intake
from eventmate.schemas import Allergen, DietaryRequirement

TODAY = date(2026, 10, 30)
CITIES = ("Pune", "Mumbai", "Bengaluru", "Hyderabad", "Chennai", "Kolkata", "Delhi", "Jaipur", "Ahmedabad")
BASE = "party in Pune for 40 people on 12 December 2026, budget 1 lakh"


@pytest.fixture
def logs(tmp_path):
    return RunLogs.create(log_dir=tmp_path, run_id="next-reader")


@pytest.fixture
def talk(logs):
    def run(*messages, known=CITIES):
        turn = None
        for m in messages:
            turn = intake.propose([m], turn.draft if turn else None, TODAY, known, logs, None, expecting=turn.asking if turn else None)
        return turn

    return run


@pytest.fixture
def press(logs):
    """press(turn, field, label_part) -> the turn after the user pressed the option of the open ``field`` question whose label contains label_part."""

    def run(turn, field, label_part):
        item = next(u for u in turn.draft.unresolved if u.field == field)
        index = next(i for i, p in enumerate(item.picks) if label_part.lower() in p.label.lower())
        return intake.propose([item.picks[index].label], turn.draft, TODAY, CITIES, logs, None, expecting=turn.asking, answer=intake.IntakeAnswer(key=item.key, pick=index))

    return run


def open_fields(turn):
    return [u.field for u in turn.draft.unresolved]


def asked(turn, field):
    return next(u for u in turn.draft.unresolved if u.field == field)


# ===================================================================== 1. allergies: what is one, what is not

NOT_AN_ALLERGY_WORDS = [
    ("others eat anything", f"{BASE}, my niece is allergic to eggs, the others eat everything"),
    ("everyone else fine", f"{BASE}, she is allergic to eggs, everyone else is fine with anything"),
    ("so the venue", f"{BASE}, 2 guests have a severe egg allergy so the venue kitchen must be careful"),
    ("so please", f"{BASE}, one guest has an egg allergy, so please make sure the chef knows"),
    ("because", f"{BASE}, he is allergic to eggs because of a bad reaction last year, rest of us are fine"),
]


@pytest.mark.parametrize("label,text", NOT_AN_ALLERGY_WORDS, ids=[c[0] for c in NOT_AN_ALLERGY_WORDS])
def test_words_after_a_named_allergy_are_not_another_allergy(talk, label, text):
    t = talk(text)
    assert Allergen.EGG in t.draft.allergens
    assert t.draft.allergy_unclear == () and "allergens" not in open_fields(t), f"read as an unnamed allergy: {t.draft.allergy_unclear} {open_fields(t)}"
    assert t.ready


@pytest.mark.parametrize("food", ["kiwi", "strawberries", "mango"])
def test_an_allergy_the_reader_cannot_name_is_a_question_and_not_ready(talk, food):
    t = talk(f"{BASE}, my aunt is allergic to {food}")
    assert not t.ready and "allergens" in open_fields(t)
    q = asked(t, "allergens")
    assert food in q.said.lower() and any(p.keep_words for p in q.picks), "it can be kept as written"
    assert t.draft.allergens == ()


def test_pressing_keep_as_written_keeps_the_words_for_the_plan_and_makes_the_draft_ready(talk, press):
    t = talk(f"{BASE}, my aunt is allergic to kiwi")
    t = press(t, "allergens", "keep")
    assert t.ready and any("kiwi" in w.lower() for w in t.draft.allergy_unclear)


def test_naming_the_allergy_in_a_later_message_closes_the_question(talk):
    t = talk(f"{BASE}, my aunt is allergic to kiwi", "peanuts")
    assert Allergen.PEANUT in t.draft.allergens and "allergens" not in open_fields(t)


def test_a_list_of_named_allergies_is_still_a_list(talk):
    t = talk(f"{BASE}, allergic to peanuts, cashews and eggs")
    assert {Allergen.PEANUT, Allergen.EGG} <= set(t.draft.allergens) and t.draft.allergy_unclear == () and t.ready


def test_the_confirm_gate_refuses_a_nameless_allergy_that_was_never_answered(talk):
    t = talk(f"{BASE}, my aunt is allergic to kiwi")
    open_draft = t.draft.model_copy(update={"unresolved": (), "settled": ()})  # the question lost on the way
    with pytest.raises(intake.IntakeError):
        intake.to_request(open_draft, "x", TODAY, messages=[f"{BASE}, my aunt is allergic to kiwi"])


# ===================================================================== 2. an amount already paid is not the budget

PAID = [
    ("advance after", "wedding in Pune for 120 guests on 12 December 2026, total budget 6 lakh, we have already paid rs 40,000 as advance"),
    ("advance before", "wedding in Pune for 120 guests on 12 December 2026, advance of 25000 paid, total budget 6 lakh"),
    ("deposit", "wedding in Pune for 120 guests on 12 December 2026, budget 6 lakh and ₹30,000 deposit already given"),
    ("paid to", "wedding in Pune for 120 guests on 12 December 2026, 20000 is already paid to the hall, budget is 6 lakh"),
    ("booking amount", "wedding in Pune for 120 guests on 12 December 2026, 6 lakh in total, booking amount of rs 15000 paid"),
    ("token", "wedding in Pune for 120 guests on 12 December 2026, budget 6 lakh, paid 10k as token"),
]


@pytest.mark.parametrize("label,text", PAID, ids=[c[0] for c in PAID])
def test_an_amount_that_was_already_paid_is_not_a_competing_budget(talk, label, text):
    t = talk(text)
    assert t.draft.budget_total == 600_000 and "budget_total" not in open_fields(t) and t.ready
    assert any("paid" in n.lower() or "advance" in n.lower() or "deposit" in n.lower() for n in t.notes), "what was left out is said"


def test_a_paid_amount_alone_is_not_a_budget_either(talk):
    t = talk("wedding in Pune for 120 guests on 12 December 2026, budget not fixed yet, 20000 already paid to the hall")
    assert t.draft.budget_total is None and "budget_total" in t.missing


def test_a_budget_written_after_its_amount_is_still_read(talk):
    t = talk("wedding in Pune for 120 guests on 12 December 2026, rs 6 lakh budget")
    assert t.draft.budget_total == 600_000


# ===================================================================== 3. ranges keep both ends

DATE_RANGES = [
    ("hyphen", "wedding in Pune for 100 guests, 4-6 may 2027, budget 3 lakh", (date(2027, 5, 4), date(2027, 5, 6))),
    ("to", "wedding in Pune for 100 guests, 7 to 9 feb 2027, budget 3 lakh", (date(2027, 2, 7), date(2027, 2, 9))),
    ("and", "wedding in Pune for 100 guests, 3 and 4 march 2027, budget 3 lakh", (date(2027, 3, 3), date(2027, 3, 4))),
    ("ordinals", "wedding in Pune for 100 guests, 7th & 8th jan 2027, budget 3 lakh", (date(2027, 1, 7), date(2027, 1, 8))),
    ("month first", "wedding in Pune for 100 guests, feb 7 to 9 2027, budget 3 lakh", (date(2027, 2, 7), date(2027, 2, 9))),
    ("from", "wedding in Pune for 100 guests, from 20 to 22 december 2026, budget 3 lakh", (date(2026, 12, 20), date(2026, 12, 22))),
    ("en dash", "wedding in Pune for 100 guests, 4–6 may 2027, budget 3 lakh", (date(2027, 5, 4), date(2027, 5, 6))),
]


def test_both_ends_of_a_range_carry_the_year_that_was_written(talk):
    t = talk("wedding in Pune for 100 guests, feb 7 to 9 2028, budget 3 lakh")
    assert sorted(p.value for p in asked(t, "event_date").picks) == ["2028-02-07", "2028-02-09"]


def test_a_range_without_a_year_is_asked_with_both_ends_in_the_same_year(talk):
    t = talk("wedding in Pune for 100 guests, 4-6 may, budget 3 lakh")
    assert [p.value for p in asked(t, "event_date").picks] == ["2027-05-04", "2027-05-06"]


@pytest.mark.parametrize("label,text,ends", DATE_RANGES, ids=[c[0] for c in DATE_RANGES])
def test_a_range_of_dates_is_asked_with_both_ends_and_no_date_is_chosen(talk, label, text, ends):
    t = talk(text)
    assert t.draft.event_date is None and not t.ready
    q = asked(t, "event_date")
    assert sorted(p.value for p in q.picks) == [d.isoformat() for d in ends]


def test_pressing_one_end_of_a_range_sets_that_date(talk, press):
    t = talk("wedding in Pune for 100 guests, 4-6 may 2027, budget 3 lakh")
    t = press(t, "event_date", "6 May")
    assert t.draft.event_date == date(2027, 5, 6) and t.ready


@pytest.mark.parametrize("text,date_set", [
    ("wedding in Pune for 100 guests, 12 December 2026, budget 3 lakh", date(2026, 12, 12)),
    ("wedding in Pune for 100 guests, 12-12-2026, budget 3 lakh", date(2026, 12, 12)),
    ("wedding in Pune for 100 guests on 2026-12-12, budget 3 lakh", date(2026, 12, 12)),
    ("wedding in Pune for 100 to 150 guests on 12 December 2026 budget 3 lakh", date(2026, 12, 12)),  # a guest range is not a date range
])
def test_dates_that_are_not_ranges_are_unchanged(talk, text, date_set):
    t = talk(text)
    assert t.draft.event_date == date_set


BUDGET_RANGES = [
    ("to", "wedding in Pune for 100 guests on 12 December 2026, budget is flexible, 8 to 10 lakh", (800_000, 1_000_000)),
    ("between", "wedding in Pune for 100 guests on 12 December 2026, somewhere between 4 and 5 lakh", (400_000, 500_000)),
    ("hyphen", "wedding in Pune for 100 guests on 12 December 2026, 3-4 lakh", (300_000, 400_000)),
    ("rupees", "wedding in Pune for 100 guests on 12 December 2026, rs 2 to 3 lakh", (200_000, 300_000)),
    ("k", "wedding in Pune for 100 guests on 12 December 2026, 50 to 70k", (50_000, 70_000)),
]


@pytest.mark.parametrize("label,text,ends", BUDGET_RANGES, ids=[c[0] for c in BUDGET_RANGES])
def test_a_range_of_money_is_asked_with_both_ends_and_no_amount_is_chosen(talk, label, text, ends):
    t = talk(text)
    assert t.draft.budget_total is None and not t.ready
    assert sorted(int(p.value) for p in asked(t, "budget_total").picks) == list(ends)


# ===================================================================== 4. a diet about some guests is not a diet for all

SOME_GUESTS = [
    ("a few", "a few guests are vegan", "vegan"),
    ("percent", "about 30% of the guests are jain", "jain"),
    ("half", "half of them eat only veg", "vegetarian"),
    ("most", "most of us are vegetarian", "vegetarian"),
    ("number of them", "12 of them are vegan", "vegan"),
    ("one", "one guest is vegan", "vegan"),
    ("some", "some relatives are jain", "jain"),
]


@pytest.mark.parametrize("label,phrase,diet", SOME_GUESTS, ids=[c[0] for c in SOME_GUESTS])
def test_a_diet_said_about_some_guests_is_asked_and_not_set_for_everyone(talk, label, phrase, diet):
    t = talk(f"{BASE}, {phrase}")
    assert t.draft.dietary == () and not t.ready
    q = asked(t, "dietary")
    assert diet in q.question.lower() and len(q.picks) >= 2


def test_everyone_sets_the_diet_and_only_some_keeps_the_words_without_setting_it(talk, press):
    t = talk(f"{BASE}, a few guests are vegan")
    everyone = press(t, "dietary", "everyone")
    assert everyone.draft.dietary == (DietaryRequirement.VEGAN,) and everyone.ready
    some = press(t, "dietary", "only some")
    assert some.draft.dietary == () and some.ready and any("vegan" in w.lower() for w in some.draft.diet_notes), "the words are kept as a food note the caterer must confirm"


def test_the_words_kept_for_some_guests_reach_the_request_with_their_number(talk, press):
    msg = f"{BASE}, 12 of them are vegan"
    t = press(talk(msg), "dietary", "only some")
    request = intake.to_request(t.draft, msg, TODAY, messages=[msg])
    assert request.dietary == () and any("12" in n and "vegan" in n.lower() for n in request.diet_notes)


@pytest.mark.parametrize("phrase,diets", [
    ("all guests are vegetarian", (DietaryRequirement.VEGETARIAN,)),
    ("vegetarian catering please", (DietaryRequirement.VEGETARIAN,)),
    ("pure veg only", (DietaryRequirement.VEGETARIAN,)),
    ("everyone eats jain food", (DietaryRequirement.JAIN,)),
    ("halal food only", (DietaryRequirement.HALAL,)),
])
def test_a_diet_for_everyone_is_still_set_with_no_question(talk, phrase, diets):
    t = talk(f"{BASE}, {phrase}")
    assert t.draft.dietary == diets and "dietary" not in open_fields(t) and t.ready


def test_a_diet_for_the_whole_event_stays_beside_a_diet_for_some(talk):
    t = talk(f"{BASE}, vegetarian food for everyone and a few guests are vegan")
    assert t.draft.dietary == (DietaryRequirement.VEGETARIAN,) and "dietary" in open_fields(t) and not t.ready


def test_two_groups_with_counts_ask_about_each(talk):
    t = talk(f"{BASE}, 15 are jain and 6 are vegan")
    assert t.draft.dietary == () and sorted(u.key for u in t.draft.unresolved if u.field == "dietary") == ["diet_group:jain", "diet_group:vegan"]


def test_the_total_with_a_diet_beside_it_is_still_the_total(talk):
    t = talk("party in Pune for 100 guests vegetarian on 12 December 2026, budget 1 lakh")
    assert t.draft.guest_count == 100 and t.draft.dietary == (DietaryRequirement.VEGETARIAN,) and t.ready


# ===================================================================== 5. a correction with no field word

def test_a_new_number_with_no_word_for_what_it_counts_is_asked_not_guessed(talk, press):
    t = talk(f"{BASE}", "ok its 55 now since more cousins said yes")
    assert t.draft.guest_count == 40 and not t.ready
    q = asked(t, "number")
    assert "55" in q.said and any(p.field == "guest_count" and p.value == "55" for p in q.picks)
    t = press(t, "number", "55 guests")
    assert t.draft.guest_count == 55 and t.ready


@pytest.mark.parametrize("message", ["actually make that 25, a few dropped out", "change it to 70 please, my brother is joining with family", "now 90 because the office is coming too"])
def test_other_ways_of_changing_a_number_are_asked_too(talk, message):
    t = talk(f"{BASE}", message)
    assert t.draft.guest_count == 40 and "number" in open_fields(t) and not t.ready


def test_the_same_correction_is_asked_while_another_question_is_open(talk, press):
    t = talk("wedding for 100 guests on 12 December 2026, budget 2 lakh", "ok its 150 now, more cousins said yes")
    assert "city" in open_fields(t) or "city" in t.missing
    assert "number" in open_fields(t) and t.draft.guest_count == 100
    t = press(t, "number", "150 guests")
    assert t.draft.guest_count == 150 and not t.ready, "the city question is still open"


def test_a_fact_with_its_word_is_read_while_a_question_is_open(talk):
    t = talk("wedding for 100 guests on 12 December 2026, budget 2 lakh", "the budget is 3 lakh now")
    assert t.draft.budget_total == 300_000


def test_a_sentence_with_no_number_in_it_is_not_a_correction(talk):
    t = talk(f"{BASE}", "thanks, that looks good")
    assert t.draft.guest_count == 40 and "number" not in open_fields(t)


def test_a_number_that_belongs_to_a_date_or_an_amount_is_not_a_bare_number(talk):
    t = talk(f"{BASE}", "ok move it to 19 December 2026 now")
    assert t.draft.event_date == date(2026, 12, 19) and "number" not in open_fields(t)


# ===================================================================== 6. a question about who a diet is for does not hide the next fact, and no message is dropped silently

def test_a_bare_city_is_read_as_the_city_while_a_diet_question_is_open(talk):
    t = talk("birthday for 30 guests on 15 December 2026, budget 40k, some of them are vegetarian", "Thane")
    assert t.draft.city == "Thane" and "dietary" in open_fields(t) and not t.ready


def test_a_message_that_answers_nothing_is_said_not_to_be_used_while_a_question_is_open(talk):
    t = talk("birthday in Pune for 30 guests on 15 December 2026, budget 40k, some of them are vegetarian", "like 10")
    assert any("like 10" in n and "could not" in n.lower() for n in t.notes), t.notes
    assert "dietary" in open_fields(t) and t.draft.dietary == ()


def test_the_question_quotes_only_the_clause_about_the_diet(talk):
    t = talk(f"{BASE}, 4 of them are vegan and one girl cannot have gluten")
    q = asked(t, "dietary")
    assert "vegan" in q.said and "girl" not in q.said and "gluten" not in q.said


def test_a_question_raised_by_the_message_is_not_also_reported_as_unused(talk):
    t = talk(f"{BASE}", "its 55 now")
    assert "number" in open_fields(t) and not any("could not use" in n.lower() for n in t.notes), t.notes


@pytest.mark.parametrize("phrase,diet", [
    ("one of the caterers should be halal certified", DietaryRequirement.HALAL),
    ("a few vegan options please", DietaryRequirement.VEGAN),
    ("two of the dishes should be jain", DietaryRequirement.JAIN),
])
def test_a_quantity_of_dishes_or_vendors_is_not_a_group_of_guests(talk, phrase, diet):
    t = talk(f"{BASE}, {phrase}")
    assert diet in t.draft.dietary and "dietary" not in open_fields(t) and t.ready


def test_some_veg_and_some_non_veg_dishes_is_a_menu_not_a_split_of_guests(talk):
    t = talk(f"{BASE}, we want some veg and some non veg dishes")
    assert set(t.draft.dietary) == {DietaryRequirement.VEGETARIAN, DietaryRequirement.NON_VEGETARIAN} and "dietary" not in open_fields(t)


@pytest.mark.parametrize("text", [
    "everyone is vegetarian except 5 people who eat chicken, 60 guests, Pune, 12 December 2026, budget 1 lakh",
    "60 guests in Pune on 12 December 2026, budget 1 lakh, apart from 5 people everyone eats veg",
    "60 guests in Pune on 12 December 2026, budget 1 lakh, excluding 4 persons the rest are vegetarian",
])
def test_a_count_after_except_is_not_a_competing_total(talk, text):
    t = talk(text)
    assert t.draft.guest_count == 60 and "guest_count" not in open_fields(t)


# ===================================================================== 7. typed answers to those questions, "except", and what is not planned

@pytest.mark.parametrize("typed", ["keep", "Keep it", "keep as written", "as written", "keep my words"])
def test_typing_keep_answers_the_nameless_allergy_question_and_is_not_itself_an_allergy(talk, typed):
    t = talk(f"{BASE}, my aunt is allergic to kiwi", typed)
    assert t.ready and t.draft.allergy_unclear == ("kiwi",), (t.draft.allergy_unclear, open_fields(t))


@pytest.mark.parametrize("typed", ["only some", "Just some of them", "some of them", "only a few"])
def test_typing_only_some_answers_the_one_open_diet_question(talk, typed):
    t = talk(f"{BASE}, a few guests are vegan", typed)
    assert t.ready and t.draft.dietary == () and any("vegan" in n.lower() for n in t.draft.diet_notes)


@pytest.mark.parametrize("typed", ["everyone", "all of them", "everybody", "all guests"])
def test_typing_everyone_answers_the_one_open_diet_question(talk, typed):
    t = talk(f"{BASE}, a few guests are vegan", typed)
    assert t.ready and t.draft.dietary == (DietaryRequirement.VEGAN,)


def test_typing_only_some_does_not_pick_between_two_open_diet_questions(talk):
    t = talk(f"{BASE}, 15 are jain and 6 are vegan", "only some")
    assert len([u for u in t.draft.unresolved if u.field == "dietary"]) == 2 and not t.ready


@pytest.mark.parametrize("phrase", [
    "everyone is vegetarian except 5 people who eat chicken",
    "all guests are vegetarian apart from 2 who eat egg",
    "vegetarian for everyone other than my brothers",
])
def test_a_diet_with_an_exception_is_asked_and_not_set_for_everyone(talk, press, phrase):
    t = talk(f"{BASE}, {phrase}")
    assert t.draft.dietary == () and "dietary" in open_fields(t) and not t.ready
    some = press(t, "dietary", "only some")
    assert some.ready and any("vegetarian" in n.lower() and ("except" in n.lower() or "apart" in n.lower() or "other than" in n.lower()) for n in some.draft.diet_notes), some.draft.diet_notes


def test_the_whole_clause_with_its_exception_reaches_the_request(talk, press):
    msg = f"{BASE}, everyone is vegetarian except 5 people who eat chicken"
    t = press(talk(msg), "dietary", "only some")
    request = intake.to_request(t.draft, msg, TODAY, messages=[msg])
    assert request.dietary == () and any("except 5 people who eat chicken" in n for n in request.diet_notes), request.diet_notes


def test_a_diet_word_that_only_looks_like_an_exception_is_still_for_everyone(talk):
    t = talk(f"{BASE}, vegetarian only, no exceptions")
    assert t.draft.dietary == (DietaryRequirement.VEGETARIAN,) and t.ready


def test_a_message_that_is_not_a_city_is_not_reported_as_an_unreadable_city(talk):
    t = talk("wedding for 100 guests on 12 December 2026, budget 2 lakh", "sorry its 4 not 3")
    assert not any("as a city" in n for n in t.notes), t.notes
    assert any("could not find a change" in n.lower() or "could not use" in n.lower() for n in t.notes), t.notes


def test_a_message_that_changes_nothing_is_said_so_even_when_the_draft_carries_standing_notes(talk):
    t = talk("engagement for 200 guests on 8 March 2027, budget 6 lakh, no onion garlic", "actually just for the family lunch, others are fine")
    assert t.draft.diet_notes, "the draft carries a standing food note"
    assert any("could not" in n.lower() for n in t.notes if "caterer" not in n), t.notes


def test_a_bare_non_answer_to_the_city_question_is_still_named(talk):
    t = talk("wedding for 100 guests on 12 December 2026, budget 2 lakh", "???")
    assert any("???" in n and "city" in n for n in t.notes), t.notes


@pytest.mark.parametrize("text", [
    f"{BASE}, we need it over 2 days",
    "party in Pune for 40 people from 12 December 2026 for 3 days, budget 1 lakh",
    f"{BASE}, a two-day event",
    f"{BASE}, lunch on both days",
])
def test_a_multi_day_event_is_said_to_be_planned_as_one_day(talk, text):
    t = talk(text)
    assert any("one event day" in n.lower() for n in t.notes), t.notes


def test_a_single_day_event_gets_no_multi_day_note(talk):
    t = talk(f"{BASE}, dinner for the whole family")
    assert not any("one event day" in n.lower() for n in t.notes)


def test_a_range_of_dates_says_the_planner_plans_one_day(talk):
    t = talk("wedding in Pune for 100 guests, 4-6 may 2027, budget 3 lakh")
    q = asked(t, "event_date")
    assert "4-6 may 2027" in q.said.lower() and "one event day" in q.question.lower()


# ===================================================================== what these tests do not show
#
# They show the phrasings above, read by the rule reader with no model. A subgroup, a range, a payment or a correction written with words not listed here can still be read as the
# whole, as one end, as a budget or not at all. A diet said about some guests is kept as the user's words for the caterer, not as a count: the draft has no field for "12 of 40".
