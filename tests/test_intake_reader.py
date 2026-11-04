"""The rule reader of the intake (no model): what it reads, and above all what it refuses to guess.

Written from the intake audit of dev 7d5ec4b. The groups follow the order the problems were ranked in:

1. safety: an allergy word spelled wrongly, a foreign currency, a misspelled money unit and an amount per person must each end in a question, never in a guessed value;
2. clarification and correction: a bare answer belongs to the question just asked, a correction says what it changed, "not Pune, Mumbai" means Mumbai, a range is the user's to settle;
3. cities: a distinct place stays distinct (Navi Mumbai), a typo is suggested and confirmed, never silently turned into another place;
4. event types, number words, and what is said when nothing could be read.

Every case runs the real ``intake.propose`` with no model. Nothing here sends anything.
"""

from datetime import date

import pytest

from eventmate.logs import RunLogs
from eventmate.planning import intake
from eventmate.schemas import Allergen, DietaryRequirement, EventType

TODAY = date(2026, 10, 30)
CITIES = ("Pune", "Mumbai", "Bengaluru", "Hyderabad", "Chennai", "Kolkata", "Delhi", "Jaipur", "Ahmedabad")
FULL = "wedding for 100 guests in Pune on 12 December 2026, budget 2 lakh"


@pytest.fixture
def logs(tmp_path):
    return RunLogs.create(log_dir=tmp_path, run_id="reader")


@pytest.fixture
def talk(logs):
    """talk("first", "second", ...) -> the last turn. Each message is sent the way the page sends it: only that message, the saved draft, and the question that was open."""

    def run(*messages, known=CITIES):
        turn = None
        for m in messages:
            turn = intake.propose([m], turn.draft if turn else None, TODAY, known, logs, None, expecting=turn.asking if turn else None)
        return turn

    return run


@pytest.fixture
def click(logs):
    """click(turn, key, label_part) -> the turn after the user pressed the option whose label contains label_part."""

    def run(turn, key, label_part):
        item = next(u for u in turn.draft.unresolved if u.key == key)
        index = next(i for i, p in enumerate(item.picks) if label_part.lower() in p.label.lower())
        return intake.propose([item.picks[index].label], turn.draft, TODAY, CITIES, logs, None, expecting=turn.asking, answer=intake.IntakeAnswer(key=key, pick=index))

    return run


def open_fields(turn):
    return [u.field for u in turn.draft.unresolved]


def asked(turn, field):
    return next(u for u in turn.draft.unresolved if u.field == field)


# ===================================================================== 1. safety: uncertainty becomes a question

NOT_A_BUDGET = [
    # foreign currencies are never read as rupees and never converted
    ("dollar sign", "budget $5000"),
    ("usd", "budget 5000 USD"),
    ("dollars", "budget 5000 dollars"),
    ("euro", "budget €4000"),
    ("pound", "budget £3000"),
    ("dirham", "budget 15000 AED"),
    # a money unit spelled wrongly is not "the number alone"
    ("lakhh", "budget 20 lakhh"),
    ("laks", "budget 2 laks"),
    ("lackh", "budget 2 lackh"),
    ("lakkh", "budget 2 lakkh"),
    ("scientific", "budget 1e6"),
    ("million", "budget 2 million"),
    ("thousand typo", "budget 50 thousnd"),
    # an amount per person is not the total
    ("per person", "budget 2000 per person"),
    ("per head", "budget 2000 per head"),
    ("a plate", "budget 1500 a plate"),
    ("per guest", "budget 2000 per guest"),
    ("rupee per head", "budget ₹1,500 per head"),
    # a number with no unit and no currency after the word budget is too small to be a total
    ("bare small", "budget 50"),
    ("bare tiny", "budget 2"),
]


@pytest.mark.parametrize("label,budget", NOT_A_BUDGET, ids=[c[0] for c in NOT_A_BUDGET])
def test_a_budget_that_cannot_be_read_safely_is_asked_about_and_never_guessed(talk, label, budget):
    t = talk(f"wedding for 100 guests in Pune on 12 December 2026, {budget}")
    assert t.draft.budget_total is None, f"{budget!r} was read as {t.draft.budget_total}"
    assert "budget_total" in open_fields(t) and not t.ready
    q = asked(t, "budget_total")
    assert q.said, "it says what it saw"
    assert "rupees" in q.question.lower() or "total" in q.question.lower()
    assert t.question == q.question  # the open question is the one shown


def test_a_misspelled_unit_offers_the_unit_it_looks_like_and_the_user_must_pick_it(talk, click):
    t = talk("wedding for 100 guests in Pune on 12 December 2026, budget 20 lakhh")
    q = asked(t, "budget_total")
    assert [p.value for p in q.picks[:1]] == ["2000000"] and "20 lakh" in q.picks[0].label
    t = click(t, q.key, "20 lakh")
    assert t.draft.budget_total == 2_000_000 and t.draft.unresolved == () and t.ready
    assert any("Budget" in c for c in t.changes)


def test_a_per_person_amount_offers_the_total_only_when_the_guest_count_is_known_and_asks_first(talk, click):
    t = talk(FULL.replace("budget 2 lakh", "budget 2000 per person"))
    q = asked(t, "budget_total")
    assert "per person" in q.question.lower() or "each" in q.question.lower()
    assert [p.value for p in q.picks[:1]] == ["200000"], "100 guests x 2000 is offered, not applied"
    assert t.draft.budget_total is None
    t = click(t, q.key, "2,00,000")
    assert t.draft.budget_total == 200_000 and t.ready
    unknown = talk("wedding in Pune on 12 December 2026, budget 2000 per person")
    assert asked(unknown, "budget_total").picks == (), "with no guest count there is nothing to multiply by"


def test_a_per_plate_rate_next_to_a_stated_total_keeps_the_total(talk):
    t = talk("wedding for 100 guests in Pune on 12 December 2026, 1500 per plate, total 5 lakh")
    assert t.draft.budget_total == 500_000 and t.draft.unresolved == ()


@pytest.mark.parametrize("budget,value", [("2L", 200_000), ("2 lac", 200_000), ("Rs. 2 lakhs", 200_000), ("₹2,00,000/-", 200_000), ("200000", 200_000), ("1.5 lakh", 150_000), ("50k", 50_000),
                                          ("1 crore", 10_000_000), ("2 Cr", 20_000_000), ("budget of INR 2,00,000", 200_000)])
def test_well_written_budgets_still_read_with_no_question(talk, budget, value):
    t = talk(f"wedding for 100 guests in Pune on 12 December 2026, budget {budget}")
    assert t.draft.budget_total == value and t.draft.unresolved == () and t.ready


# --------------------------------------------------------------------------------------- allergy words

ALLERGY_TYPOS = [
    ("alergic", "one guest is alergic to peanuts", "peanut"),
    ("allergik", "one guest is allergik to peanuts", "peanut"),
    ("alergy", "alergy to eggs", "egg"),
    ("peanut alergy", "peanut alergy", "peanut"),
    ("alerjic", "gluten alerjic guest", "gluten"),
    ("typo in the food", "allergic to penuts", "peanut"),
    ("typo, shellfish", "allergic to shelfish", "shellfish"),
    ("typo, gluten", "allergic to glutten", "gluten"),
    ("typo, sesame", "allergic to sesme", "sesame"),
]


@pytest.mark.parametrize("label,phrase,allergen", ALLERGY_TYPOS, ids=[c[0] for c in ALLERGY_TYPOS])
def test_a_misspelled_allergy_is_never_invented_and_never_dropped_it_is_asked_about(talk, click, label, phrase, allergen):
    t = talk(f"wedding for 100 guests in Pune on 12 December 2026, budget 2 lakh, {phrase}")
    assert t.draft.allergens == (), "a similar-looking word must not become an allergy by itself"
    assert "allergens" in open_fields(t) and not t.ready
    q = next(u for u in t.draft.unresolved if u.field == "allergens")
    assert allergen in [p.value for p in q.picks], q.picks
    assert any(p.value is None for p in q.picks), "the user can say it is not that"
    pick = next(p for p in q.picks if p.value == allergen)
    done = click(t, q.key, pick.label)
    assert Allergen(allergen) in done.draft.allergens and done.draft.unresolved == () and done.ready


def test_saying_it_is_not_that_keeps_the_words_as_an_allergy_the_plan_could_not_name(talk, click):
    t = talk(f"{FULL}, allergic to penuts")
    q = asked(t, "allergens")
    done = click(t, q.key, "something else")
    assert done.draft.allergens == () and done.draft.allergy_unclear and done.draft.unresolved == ()  # never read as "no allergy"


@pytest.mark.parametrize("phrase", ["anaphylaxis", "anaphylactic reaction to something", "carries an epipen", "no peanuts please", "without nuts", "no shellfish"])
def test_a_serious_reaction_or_an_exclusion_with_no_stated_allergy_is_asked_about(talk, phrase):
    t = talk(f"{FULL}, {phrase}")
    assert "allergens" in open_fields(t) and not t.ready and t.draft.allergens == ()


def test_the_allergen_named_after_a_question_is_read_from_a_bare_answer(talk):
    t = talk(f"{FULL}, anaphylaxis")
    assert t.asking == "allergens"
    t = talk(f"{FULL}, anaphylaxis", "peanuts")
    assert Allergen.PEANUT in t.draft.allergens and t.draft.unresolved == () and t.ready


@pytest.mark.parametrize("phrase", ["allergic to peanuts", "one guest is allergic to peanuts", "peanut allergy", "allergic to nuts", "lactose intolerant"])
def test_correctly_spelled_allergies_are_read_with_no_question(talk, phrase):
    t = talk(f"{FULL}, {phrase}")
    assert t.draft.allergens and t.draft.unresolved == () and t.ready


@pytest.mark.parametrize("text", ["peanut butter sandwich counter", "a pancake station", "penguin themed cake", "Alice in Wonderland theme", "anagram quiz night", "no onion no garlic"])
def test_ordinary_words_that_look_a_little_like_allergy_words_raise_nothing(talk, text):
    t = talk(f"{FULL}, {text}")
    assert t.draft.allergens == () and "allergens" not in open_fields(t)


@pytest.mark.parametrize("word", ["vegiterian", "vegitarian", "vegeterian", "vegtarian"])
def test_a_misspelled_diet_is_suggested_and_confirmed_not_dropped(talk, click, word):
    t = talk(f"{FULL}, {word}")
    assert t.draft.dietary == () and "dietary" in open_fields(t) and not t.ready
    q = asked(t, "dietary")
    assert any(p.value in ("vegetarian", "vegan", "non_vegetarian") for p in q.picks)
    veg = next(p for p in q.picks if p.value == "vegetarian")
    done = click(t, q.key, veg.label)
    assert DietaryRequirement.VEGETARIAN in done.draft.dietary and done.ready


# ============================================================= 2. clarification and correction state


def test_a_bare_answer_fills_the_field_that_was_asked_about(talk):
    t = talk("wedding in Pune")
    assert t.asking == "guest_count"
    t = talk("wedding in Pune", "100")
    assert t.draft.guest_count == 100 and t.asking == "event_date"
    t = talk("wedding in Pune", "100", "12 Dec 2026")
    assert t.draft.event_date == date(2026, 12, 12) and t.asking == "budget_total"
    t = talk("wedding in Pune", "100", "12 Dec 2026", "200000")
    assert t.draft.budget_total == 200_000 and t.ready and t.asking is None


@pytest.mark.parametrize("answer,guests", [("100", 100), ("about 100", 100), ("100 people", 100), ("approx 100", 100), ("१००", 100), ("100.", 100)])
def test_bare_guest_answers(talk, answer, guests):
    assert talk("wedding in Pune on 12 December 2026, budget 2 lakh", answer).draft.guest_count == guests


@pytest.mark.parametrize("answer,value", [("2 lakh", 200_000), ("2L", 200_000), ("200000", 200_000), ("₹2,00,000", 200_000), ("two lakh", 200_000), ("50k", 50_000)])
def test_bare_budget_answers(talk, answer, value):
    assert talk("wedding for 100 guests in Pune on 12 December 2026", answer).draft.budget_total == value


def test_a_bare_number_that_is_not_a_good_answer_is_explained_not_repeated(talk):
    t = talk("wedding in Pune", "banana")
    assert t.draft.guest_count is None and t.asking == "guest_count"
    assert "banana" in t.question or any("banana" in n for n in t.notes), "the user is told what was not understood"
    again = talk("wedding in Pune", "banana", "banana")
    assert any("banana" in n for n in again.notes) or "banana" in again.question, "asked twice, it still says what it did not understand"


def test_an_explicit_expecting_field_is_honoured_and_a_wrong_one_is_refused(logs):
    t = intake.propose(["120"], intake.IntakeDraft(city="Pune"), TODAY, CITIES, logs, None, expecting="guest_count")
    assert t.draft.guest_count == 120
    with pytest.raises(intake.IntakeError):
        intake.propose(["120"], None, TODAY, CITIES, logs, None, expecting="password")


CORRECTIONS = [
    ("keyword guests", "sorry, 150 guests", "guest_count", 150, "Guests changed from 100 to 150"),
    ("keyword city", "actually make it Mumbai", "city", "Mumbai", "City changed from Pune to Mumbai"),
    ("bare city", "Mumbai", "city", "Mumbai", "City changed from Pune to Mumbai"),
    ("negation and replacement", "not Pune, Mumbai", "city", "Mumbai", "City changed from Pune to Mumbai"),
    ("negation, in", "not in Pune but in Mumbai", "city", "Mumbai", "City changed from Pune to Mumbai"),
    ("instead of", "Mumbai instead of Pune", "city", "Mumbai", "City changed from Pune to Mumbai"),
    ("budget", "budget is 3 lakh", "budget_total", 300_000, "Budget changed from ₹2,00,000 to ₹3,00,000"),
    ("date", "move it to 19 December 2026", "event_date", date(2026, 12, 19), "Date changed from 12 December 2026 to 19 December 2026"),
    ("date, bare", "19 Dec 2026", "event_date", date(2026, 12, 19), "Date changed from 12 December 2026 to 19 December 2026"),
]


@pytest.mark.parametrize("label,message,field,value,said", CORRECTIONS, ids=[c[0] for c in CORRECTIONS])
def test_a_correction_changes_the_field_and_says_so(talk, label, message, field, value, said):
    t = talk(FULL, message)
    assert getattr(t.draft, field) == value
    assert said in t.changes, (t.changes, t.notes)


def test_removing_a_city_without_a_replacement_asks_for_one_and_says_it_was_removed(talk):
    t = talk(FULL, "not Pune")
    assert t.draft.city is None and t.asking == "city" and not t.ready
    assert any("Pune" in c and "removed" in c.lower() for c in t.changes)


def test_a_message_that_changes_nothing_says_so(talk):
    t = talk(FULL, "sounds good")
    assert t.draft.guest_count == 100 and t.draft.city == "Pune"
    assert any("could not find a change" in n.lower() for n in t.notes), t.notes


def test_a_bare_number_after_a_complete_draft_asks_which_fact_it_is(talk, click):
    t = talk(FULL, "make it 150")
    assert t.draft.guest_count == 100, "never guessed"
    q = next(u for u in t.draft.unresolved if u.key == "number")
    assert {p.field for p in q.picks if p.value} == {"guest_count", "budget_total"} and not t.ready
    t = click(t, "number", "guests")
    assert t.draft.guest_count == 150 and t.ready and "Guests changed from 100 to 150" in t.changes


@pytest.mark.parametrize("phrase,two", [("100-150 guests", ("100", "150")), ("100 to 150 guests", ("100", "150")), ("between 100 and 150 guests", ("100", "150")), ("100–150 guests", ("100", "150"))])
def test_a_guest_range_is_the_users_to_settle(talk, click, phrase, two):
    t = talk(f"wedding for {phrase} in Pune on 12 December 2026, budget 2 lakh")
    assert t.draft.guest_count is None and not t.ready
    assert t.choices and t.choices[0].field == "guest_count" and t.choices[0].options == two
    done = click(t, asked(t, "guest_count").key, "150")
    assert done.draft.guest_count == 150 and done.ready


@pytest.mark.parametrize("phrase", ["-5 guests", "0 guests", "100.5 guests", "1000000 guests"])
def test_an_impossible_guest_count_is_explained(talk, phrase):
    t = talk(f"wedding for {phrase} in Pune on 12 December 2026, budget 2 lakh")
    assert t.draft.guest_count is None and "guest_count" in open_fields(t) and not t.ready
    q = asked(t, "guest_count")
    assert phrase.split()[0] in q.said and ("whole" in q.question.lower() or "between" in q.question.lower() or "number" in q.question.lower())


@pytest.mark.parametrize("phrase", ["budget 2-3 lakh", "budget between 2 and 3 lakh", "budget 2 lakh to 3 lakh", "budget 2 to 3 lakh", "budget 2–3 lakh"])
def test_a_budget_range_offers_both_amounts_with_the_unit_on_both_and_asks(talk, click, phrase):
    t = talk(f"wedding for 100 guests in Pune on 12 December 2026, {phrase}")
    assert t.draft.budget_total is None and not t.ready
    q = asked(t, "budget_total")
    assert [p.value for p in q.picks if p.value] == ["200000", "300000"], "the lakh unit applies to both numbers, never ₹2"
    assert t.choices[0].options == ("200000", "300000")
    done = click(t, q.key, "3,00,000")
    assert done.draft.budget_total == 300_000 and done.ready


def test_two_stated_guest_counts_or_dates_or_budgets_are_still_choices_and_the_option_can_be_pressed(talk, click):
    t = talk("wedding in Pune, 50 guests or 80 guests, on 12 December 2026, budget 2 lakh")
    assert t.choices[0].options == ("50", "80") and t.question == "Which guest count is right?"
    assert click(t, t.choices[0].key, "80").draft.guest_count == 80
    d = talk("wedding for 100 guests in Pune on 12 December 2026 or 19 December 2026, budget 2 lakh")
    assert d.choices[0].options == ("2026-12-12", "2026-12-19")
    assert click(d, d.choices[0].key, "19 December").draft.event_date == date(2026, 12, 19)


def test_build_is_refused_by_the_server_side_check_while_anything_is_unresolved(talk):
    t = talk("wedding for 100 guests in Pune on 12 December 2026, budget 20 lakhh")
    assert t.draft.unresolved
    with pytest.raises(intake.IntakeError, match="unresolved|answer"):
        intake.to_request(t.draft, "x", TODAY)


# ====================================================================================== 3. cities


@pytest.mark.parametrize("typed,city", [("pune", "Pune"), ("jaipur", "Jaipur"), ("ahmedabad", "Ahmedabad"), ("Hyderabad", "Hyderabad"), ("hyd", "Hyderabad"), ("BLR", "Bengaluru"), ("bangalore", "Bengaluru"),
                                         ("Calcutta", "Kolkata"), ("new delhi", "Delhi")])
def test_known_cities_are_read_in_any_case_with_no_question(talk, typed, city):
    t = talk(f"wedding for 100 guests in {typed} on 12 December 2026, budget 2 lakh")
    assert t.draft.city == city and t.draft.unresolved == ()
    assert talk("wedding for 100 guests on 12 December 2026, budget 2 lakh", typed).draft.city == city


@pytest.mark.parametrize("typed", ["navi mumbai", "Navi Mumbai", "NAVI MUMBAI"])
def test_navi_mumbai_is_its_own_place_not_mumbai(talk, typed):
    t = talk(f"wedding for 100 guests in {typed} on 12 December 2026, budget 2 lakh")
    assert t.draft.city == "Navi Mumbai"
    assert any("no vendor records for Navi Mumbai" in n for n in t.notes), "and it says it has no vendors there"


@pytest.mark.parametrize("typed,suggest", [("Banglore", "Bengaluru"), ("banglore", "Bengaluru"), ("Bangalor", "Bengaluru"), ("punee", "Pune"), ("Puna", "Pune"), ("hydrabad", "Hyderabad"),
                                           ("chenai", "Chennai"), ("kolkatta", "Kolkata"), ("delih", "Delhi"), ("mumbay", "Mumbai"), ("Jaipurr", "Jaipur")])
def test_a_city_typo_is_suggested_and_waits_for_confirmation(talk, click, typed, suggest):
    t = talk(f"wedding for 100 guests in {typed} on 12 December 2026, budget 2 lakh")
    assert t.draft.city is None, "a suggestion is never applied silently"
    q = asked(t, "city")
    assert [p.value for p in q.picks][0] == suggest and any(p.value == typed.strip().title() or p.value == typed for p in q.picks), "the name as typed is also an option"
    assert not t.ready and suggest in t.question and typed in t.question
    assert click(t, q.key, suggest).draft.city == suggest


def test_keeping_the_city_as_written_is_possible_and_carries_the_no_vendors_note(talk, click):
    t = talk(f"wedding for 100 guests in Banglore on 12 December 2026, budget 2 lakh")
    done = click(t, asked(t, "city").key, "as written")
    assert done.draft.city == "Banglore" and done.ready and any("no vendor records for Banglore" in n for n in done.notes)


def test_a_typo_answered_to_the_city_question_is_a_suggestion_too_and_the_question_is_not_repeated(talk, click):
    t = talk("wedding for 100 guests on 12 December 2026, budget 2 lakh", "Banglore")
    assert t.draft.city is None and "Bengaluru" in t.question
    t = click(t, asked(t, "city").key, "Bengaluru")
    assert t.draft.city == "Bengaluru" and t.ready


@pytest.mark.parametrize("answer", ["thane", "Thane", "nagpur", "goa"])
def test_an_unknown_city_given_as_the_answer_to_the_city_question_is_accepted_as_written_with_a_warning(talk, answer):
    t = talk("wedding for 100 guests on 12 December 2026, budget 2 lakh", answer)
    assert t.draft.city == answer.title() and t.ready
    assert any(f"no vendor records for {answer.title()}" in n for n in t.notes)


def test_an_unknown_lowercase_city_inside_a_brief_is_asked_for_by_name_and_then_accepted(talk):
    t = talk("wedding for 100 guests in thane on 12 December 2026, budget 2 lakh")
    assert t.draft.city is None and t.asking == "city"
    assert "just the city name" in t.question.lower(), "the question says how to answer"
    assert talk("wedding for 100 guests in thane on 12 December 2026, budget 2 lakh", "thane").draft.city == "Thane"


@pytest.mark.parametrize("text", ["in the evening", "in December", "in total", "in 3 days", "in vegetarian style", "at 6pm", "near the stage"])
def test_ordinary_phrases_after_in_are_never_a_city(talk, text):
    t = talk(f"wedding for 100 guests {text}, on 12 December 2026, budget 2 lakh")
    assert t.draft.city is None and t.asking == "city" and "city" not in open_fields(t), "no city is invented, and no typo is suggested, from an ordinary phrase"


@pytest.mark.parametrize("phrase", ["in Pune or Mumbai", "in Pune or in Mumbai", "from Mumbai to Pune", "between Pune and Mumbai"])
def test_two_cities_are_the_users_to_choose_never_the_first_one(talk, click, phrase):
    t = talk(f"wedding for 100 guests {phrase} on 12 December 2026, budget 2 lakh")
    assert t.draft.city is None and not t.ready
    q = asked(t, "city")
    assert {p.value for p in q.picks if p.value} == {"Pune", "Mumbai"}
    assert click(t, q.key, "Mumbai").draft.city == "Mumbai"


def test_the_question_for_a_missing_city_says_what_it_understood_when_asked_twice(talk):
    t = talk("wedding for 100 guests on 12 December 2026, budget 2 lakh", "???")
    assert t.draft.city is None and ("???" in t.question or any("???" in n for n in t.notes))


# ============================================================ 4. event types, number words, hints


@pytest.mark.parametrize("word,event", [("marriage", EventType.WEDDING), ("shaadi", EventType.WEDDING), ("vivah", EventType.WEDDING), ("mehendi function", EventType.WEDDING), ("sangeet", EventType.WEDDING),
                                        ("haldi ceremony", EventType.WEDDING), ("bday party", EventType.BIRTHDAY), ("sagai", EventType.ENGAGEMENT_PARTY), ("corporate offsite", EventType.CORPORATE)])
def test_known_event_words_are_read(talk, word, event):
    assert talk(f"{word} for 100 guests in Pune on 12 December 2026, budget 2 lakh").draft.event_type == event


@pytest.mark.parametrize("word", ["baby shower", "griha pravesh puja", "housewarming", "anniversary dinner", "farewell"])
def test_an_event_word_that_is_not_a_known_type_is_said_not_silently_dropped(talk, word):
    t = talk(f"{word} for 100 guests in Pune on 12 December 2026, budget 2 lakh")
    assert t.draft.event_type in (None, EventType.OTHER)
    assert any("event type" in n.lower() for n in t.notes), t.notes
    assert any(e in " ".join(t.notes).lower() for e in ("wedding", "birthday")), "the note says which types it knows"


def test_no_event_word_at_all_gets_the_same_visible_note(talk):
    t = talk("100 guests, Pune, 12 December 2026, 2 lakh")
    assert t.ready and any("event type" in n.lower() for n in t.notes)


@pytest.mark.parametrize("text,guests,budget", [("a hundred guests", 100, None), ("hundred and twenty guests", 120, None), ("two hundred people", 200, None), ("fifty pax", 50, None),
                                                  ("120 guests, budget two lakh", 120, 200_000), ("120 guests, budget five lakh rupees", 120, 500_000), ("120 guests, budget two and a half lakh", 120, 250_000),
                                                  ("120 ppl, budget 2 lakh", 120, 200_000)])
def test_number_words_and_ppl_are_read(talk, text, guests, budget):
    t = talk(f"wedding with {text} in Pune on 12 December 2026" + ("" if budget else ", budget 2 lakh"))
    assert t.draft.guest_count == guests and (budget is None or t.draft.budget_total == budget)


@pytest.mark.parametrize("field,example", [("guest_count", "120"), ("event_date", "12 December 2026"), ("budget_total", "2 lakh"), ("city", "Pune")])
def test_every_question_shows_an_accepted_format(talk, field, example):
    full = {"guest_count": "wedding in Pune on 12 December 2026, budget 2 lakh", "event_date": "wedding for 100 guests in Pune, budget 2 lakh", "budget_total": "wedding for 100 guests in Pune on 12 December 2026",
            "city": "wedding for 100 guests on 12 December 2026, budget 2 lakh"}[field]
    t = talk(full)
    assert t.asking == field and example in t.question


@pytest.mark.parametrize("text", ["tomorrow", "next Saturday", "this weekend", "in December 2026", "12 Decmber 2026", "12-12-26", "12/12"])
def test_a_date_that_cannot_be_read_is_explained_with_formats_not_just_asked_again(talk, text):
    t = talk(f"wedding for 100 guests in Pune {text}, budget 2 lakh")
    assert t.draft.event_date is None and t.asking == "event_date" and not t.ready
    assert "12 December 2026" in t.question or any("12 December 2026" in n for n in t.notes)


def test_a_misspelled_month_is_suggested_and_confirmed(talk, click):
    t = talk("wedding for 100 guests in Pune on 12 Decmber 2026, budget 2 lakh")
    assert t.draft.event_date is None
    q = asked(t, "event_date")
    assert q.picks and q.picks[0].value == "2026-12-12"
    assert click(t, q.key, "12 December").draft.event_date == date(2026, 12, 12)


@pytest.mark.parametrize("text", ["asdf qwer zxcv", "hello", "???", "🎉🎉"])
def test_a_message_with_no_event_details_says_so(talk, text):
    t = talk(text)
    assert not t.ready and any("could not find any event details" in n.lower() for n in t.notes), t.notes


def test_the_date_example_in_an_ambiguous_date_message_uses_the_year_and_day_the_user_wrote(talk):
    t = talk("wedding for 100 guests in Pune on 03/04/2027, budget 2 lakh")
    text = " ".join(t.notes)
    assert "3 April 2027" in text and "4 March 2027" in text and "2026-12-05" not in text


def test_a_value_the_user_wrote_twice_in_different_ways_is_not_a_conflict_but_two_values_are(talk):
    assert talk("wedding for 100 guests, 100 people, in Pune on 12 December 2026, budget 2 lakh").draft.guest_count == 100


def test_an_old_style_message_list_still_works_and_only_the_newest_message_is_read(logs):
    t = intake.propose(["wedding in Pune", "ignore this", FULL], None, TODAY, CITIES, logs, None)
    assert t.draft.guest_count == 100 and t.draft.city == "Pune" and t.ready
