"""What the second manual test pack (``todoh-intake-test-pack-batch2``) found: the reader left Build open on a value it had guessed, dropped, or taken from the wrong words.

Every text below is pasted from that pack exactly as a person typed it. The test clock is the pack's own: Friday 6 November 2026. Each case runs the real ``intake.propose``.

1. a value that was guessed: an itemised budget, "19th or 20th", a count for rooms taken as the guest total, a venue the user already has, a weekday that does not match its date;
2. a date written relative to today, which the reader could not work out and so left the old date standing;
3. a message about one fact that overwrote another: "change city to X", a weekday typed as a city, a second capital letter taken into a city name;
4. what was missed: guests written as employees, ladies or kids + adults; cities the vendor data does not hold; "X mein";
5. a question that should not have been asked: "4L" as an allergy, "adults nonveg" as an allergy, a subgroup beside the total taken for a second total.

What this does not show: the vendor data still holds nine cities, so a brief in Mysuru is read correctly and still cannot be built.
"""

from datetime import date

import pytest

from eventmate.logs import RunLogs
from eventmate.planning import allergies, intake
from eventmate.schemas import VendorCategory

TODAY = date(2026, 11, 6)  # a Friday
CITIES = ("Ahmedabad", "Bengaluru", "Chennai", "Delhi", "Hyderabad", "Jaipur", "Kolkata", "Mumbai", "Pune")


@pytest.fixture
def logs(tmp_path):
    return RunLogs.create(log_dir=tmp_path, run_id="batch2")


@pytest.fixture
def talk(logs):
    def run(*messages):
        turn = None
        for m in messages:
            turn = intake.propose([m], turn.draft if turn else None, TODAY, CITIES, logs, None, expecting=turn.asking if turn else None)
        return turn

    return run


def question(turn, field):
    return next((u for u in turn.draft.unresolved if u.field == field), None)


def picks(turn, field):
    q = question(turn, field)
    return [p.value for p in q.picks] if q else []


# ===================================================================== 1. a value that was guessed

def test_two_itemised_amounts_are_not_a_budget_total(talk):
    t = talk("Product launch, 150 attendees, Jan 15 2027, bengaluru, spend 1,50,000 on food and 75k on decor")
    assert t.draft.budget_total is None and not t.ready
    assert {"150000", "75000", "225000"} <= set(picks(t, "budget_total"))


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Maybe 19th or 20th December, not decided yet, college reunion, 80 people, Mysuru, 1 lakh.", ["2026-12-19", "2026-12-20"]),
        ("reunion maybe dec 12 or 13, 70 people, mysuru, 60k", ["2026-12-12", "2026-12-13"]),
    ],
)
def test_either_or_dates_are_a_question(talk, text, expected):
    t = talk(text)
    assert t.draft.event_date is None and not t.ready
    assert picks(t, "event_date") == expected


def test_locking_one_of_the_two_dates_by_its_day(talk):
    t = talk("reunion maybe dec 12 or 13, 70 people, mysuru, 60k", "lets lock 13th")
    assert t.draft.event_date == date(2026, 12, 13) and question(t, "event_date") is None
    t = talk("reunion maybe dec 12 or 13, 70 people, mysuru, 60k", "lets lock 13th", "wait, family says 12th only")
    assert t.draft.event_date == date(2026, 12, 12)


def test_a_count_for_rooms_is_not_the_guest_total(talk):
    t = talk("need to plan a reception", "around 250 people", "hubballi", "budget 3 lakh total", "dec 20", "also we need rooms for 20 outstation guests")
    assert t.draft.guest_count == 250


def test_a_second_figure_to_assume_is_a_question(talk):
    text = "hr said assume 130 to be safe. we are about 120 people in the bengaluru office, dec 12, budget 1.5 lakh"
    t = talk(text)
    assert t.draft.guest_count is None
    assert picks(t, "guest_count") == ["120", "130"]


def test_a_venue_the_user_already_has_is_not_asked_for(talk):
    t = talk("Reunion dinner, 45 people, 14 Feb 2027, Mysuru, thinking 25k for food only, venue we already have")
    assert VendorCategory.VENUE not in t.draft.services
    assert any("already have a venue" in n for n in t.notes)  # the planner still prices one: that is said, not hidden


def test_a_weekday_that_does_not_match_its_date_is_a_question(talk):
    t = talk("Anniversary lunch on saturday 15th nov, 12 guests, mysuru, veg thali, 15k.")
    assert t.draft.event_date is None and not t.ready
    assert "2026-11-14" in picks(t, "event_date") and "2026-11-15" in picks(t, "event_date")


def test_a_weekday_that_matches_its_date_is_left_alone(talk):
    t = talk("Anniversary lunch on saturday 14th nov, 12 guests, mysuru, veg thali, 15k.")
    assert t.draft.event_date == date(2026, 11, 14)


# ===================================================================== 2. dates relative to today


@pytest.mark.parametrize(
    "said, expected",
    [
        ("tomorrow", ["2026-11-07"]),
        ("this sunday", ["2026-11-08"]),
        ("is sunday", ["2026-11-08"]),
        ("next saturday", ["2026-11-07", "2026-11-14"]),
        ("next weekend", ["2026-11-14", "2026-11-15"]),
        ("this weekend", ["2026-11-07", "2026-11-08"]),
        ("next week friday", ["2026-11-13"]),
        ("month end", ["2026-11-30"]),
        ("end of this month", ["2026-11-30"]),
        ("in 10 days", ["2026-11-16"]),
    ],
)
def test_relative_dates_are_offered_never_chosen(talk, said, expected):
    t = talk(f"farewell for 60 students, mysuru, 40k, {said}")
    assert t.draft.event_date is None and not t.ready
    assert picks(t, "event_date") == expected


def test_a_relative_date_replaces_an_old_one_only_when_pressed(talk):
    t = talk("team dinner 25 people nov 20 bengaluru, budget 80k", "and shift the date to next week friday")
    assert t.draft.event_date is None and "2026-11-13" in picks(t, "event_date")  # the old 20 November does not stand in


def test_first_week_is_a_question_with_nothing_to_press(talk):
    t = talk("new year party for the society, 150 people, Bengaluru, budget 90k, sometime in Jan first week")
    assert t.draft.event_date is None and question(t, "event_date") is not None


# ===================================================================== 3. one fact must not overwrite another


def test_change_city_to_a_name_the_data_does_not_hold(talk):
    t = talk("birthday party 30 ppl nov 21 chennai, veg, 30k", "sorry not chennai, change city to coimbatore")
    assert t.draft.city == "Coimbatore" and t.ready


def test_a_weekday_is_not_a_city(talk):
    t = talk("want to plan a small party", "45", "veg only", "sunday")
    assert t.draft.city is None


def test_a_second_capitalised_word_is_not_part_of_the_city(talk):
    t = talk("We need a small engagement ceremony, 80 guests, December 5th 2026 in Mandya. Traditional South Indian veg menu, budget 1.5 lakh.")
    assert t.draft.city != "Mandya Traditional"


# ===================================================================== 4. what was missed


@pytest.mark.parametrize(
    "text, guests",
    [
        ("Company annual day on 18 December 2026, about 200 employees, need full catering and a stage.", 200),
        ("Kitty party, 15 ladies, 14 nov, mysuru, 500 per head ho jaye? veg snacks + lunch", 15),
        ("Birthday 50 kids + 30 adults, nov 21, bengaluru. kids mostly veg, 4 kids egg allergy, adults nonveg. 75k.", 80),
    ],
)
def test_guests_written_as_a_group_of_people(talk, text, guests):
    assert talk(text).draft.guest_count == guests


@pytest.mark.parametrize(
    "text, city",
    [
        ("shaadi ka sangeet plan karo na, 180 mehmaan, 12 dec ko, udupi mein, veg only kyunki family traditional hai, 2L tak chalega", "Udupi"),
        ("mummy ka 60th birthday hai, 50 relatives aayenge, 12 dec ko, hubli, pure veg, 70k budget", "Hubballi"),
        ("Planning a reception for 30 nov, 300 guests, Mangaluru, veg and nonveg.", "Mangaluru"),
        ("Wedding 350 guests dec 27, madikeri. full mix: veg, nonveg. 4L", "Madikeri"),
        ("office ki party hai yaar 20 nov ko, 25 jana, koramangala mein kahi, nonveg mandatory, 20k", "Bengaluru"),
        ("ofice diwali party 90 employes, nov 12th, electronic city, buffet both veg nonveg, aprox 1.2L", "Bengaluru"),
    ],
)
def test_cities_the_vendor_data_does_not_hold(talk, text, city):
    assert talk(text).draft.city == city


# ===================================================================== 5. a question that should not have been asked


def test_a_money_unit_after_a_diet_is_not_an_allergy():
    assert allergies.read("1 guest celiac. 4L").unclear == ()


def test_a_diet_after_an_allergy_is_not_another_allergy():
    assert allergies.read("kids mostly veg, 4 kids egg allergy, adults nonveg. 75k.").unclear == ()


def test_words_after_a_named_allergy_in_brackets_are_not_an_allergy():
    assert allergies.read("one severe nut allergy (carry epipen level), rest eat anything").unclear == ()


def test_a_diet_subgroup_beside_the_total_is_not_a_second_total(talk):
    t = talk("Sangeet 200 guests dec 18, mysore. elders want satvik food (~40 ppl), youngsters want chaat + nonveg starters, 3 gluten free. 1.8L")
    assert t.draft.guest_count == 200 and question(t, "guest_count") is None


def test_a_count_of_students_is_not_a_budget_option(talk):
    t = talk("farewel party 4 college seniers 120 students 27th nov eveng 6pm, need dj+snaks+dinner, 55k max. in mysur")
    assert "120" not in picks(t, "budget_total")
    assert t.draft.budget_total == 55000 or "55000" in picks(t, "budget_total")


# ===================================================================== 6. dead ends, per-person amounts, typos, and what the planner does not cover


def test_a_new_number_replaces_the_one_that_was_asked_about(talk):
    t = talk("farewell for 60 students, nov 27, mysuru, 40k", "make it 75", "ugh ok final list is 90")
    assert [p.value for p in question(t, "number").picks] == ["90"] and "75" not in question(t, "number").said


@pytest.mark.parametrize("answer", ["yes", "yeah", "ok", "haan", "correct"])
def test_yes_presses_the_only_option(talk, answer):
    t = talk("farewell for 60 students, nov 27, mysuru, 40k", "make it 75", answer)
    assert t.draft.guest_count == 75 and not t.draft.unresolved


def test_yes_never_removes_an_allergy(talk):
    t = talk("wedding for 100 guests in Pune on 12 December 2026, budget 2 lakh, allergic to peanuts", "remove the allergy", "yes")
    assert t.draft.allergens or t.draft.unresolved


@pytest.mark.parametrize(
    "text, total",
    [
        ("Kitty party, 15 ladies, 14 nov, mysuru, 500 per head ho jaye? veg snacks + lunch", 7500),
        ("Conference dinner 100 pax dec 10, bengaluru, quote me around 800/plate, nonveg included", 80000),
        ("Team lunch for 18 people in Indiranagar, Bengaluru on Friday 20 November, 1 pm. Mix of veg and non-veg, roughly 700 per head.", 12600),
    ],
)
def test_a_per_person_amount_is_offered_as_a_total_never_applied(talk, text, total):
    t = talk(text)
    assert t.draft.budget_total is None and not t.ready
    assert str(total) in picks(t, "budget_total")


def test_a_misspelt_group_word_after_a_number_is_a_question(talk):
    t = talk("ofice diwali party was cancelled bt now again on, 90 employes, nov 12th, electronic city, buffet both veg nonveg, aprox 1.2L")
    assert t.draft.guest_count is None and picks(t, "guest_count") == ["90"]


def test_what_the_planner_does_not_cover_is_said(talk):
    t = talk("sangeet 150 guests dec 19 mysuru, need dj, decor and dinner, 1.5L")
    note = " ".join(t.notes).lower()
    assert "dj" in note and "decor" in note and "venue, a caterer and a photographer" in note


def test_the_note_is_not_made_when_nothing_is_outside_the_planner(talk):
    assert not any("does not plan" in n for n in talk("wedding for 100 guests in Pune on 12 December 2026, budget 2 lakh, photographer").notes)


# ===================================================================== 7. a model call only when it can add something


class CountingModel:
    name = "counting-model"

    def __init__(self):
        self.calls = 0

    def complete(self, system, user, max_tokens):
        self.calls += 1
        return "{}"


@pytest.mark.parametrize("text, called", [
    ("wedding for 100 guests in Pune on 12 December 2026, budget 2 lakh", False),  # everything a plan needs is read: nothing to ask a model
    ("big wedding in Pune on 12 December 2026, budget 2 lakh", True),  # the guest count is missing and nobody is being asked about it
    ("wedding in Pune on 12 December 2026, budget 2 lakh, 100 to 150 guests", False),  # the missing fact is an open question: a model may not answer it
])
def test_auto_mode_asks_a_model_only_when_it_can_add_something(logs, text, called):
    model = CountingModel()
    intake.propose([text], None, TODAY, CITIES, logs, model, model_when="auto")
    assert (model.calls > 0) is called


def test_the_default_still_asks_the_model_every_time(logs):
    model = CountingModel()
    intake.propose(["wedding for 100 guests in Pune on 12 December 2026, budget 2 lakh"], None, TODAY, CITIES, logs, model)
    assert model.calls == 1


# ===================================================================== 8. event types people name that have no type of their own


@pytest.mark.parametrize(
    "text, kind",
    [
        ("farewell for 60 students, nov 27, mysuru, 40k", "other"),
        ("Small pooja at home, 25 people, Mysuru, nov 30, 10k", "other"),
        ("Anniversary dinner for 2 on 31 December 2026, Bengaluru, budget 8000", "other"),
        ("Housewarming 60 ppl, dec 6, mysuru, 55k", "other"),
        ("Kitty party, 15 ladies, 14 nov, mysuru, 500 per head", "other"),
        ("Team lunch for 18 people in Bengaluru on 20 November, budget 20000", "corporate"),
        ("ok so we are planning our office annual day, 130 people, bengaluru, dec 12, 1.5 lakh", "corporate"),
    ],
)
def test_named_events_are_recognised_without_a_complaint(talk, text, kind):
    t = talk(text)
    assert t.draft.event_type is not None and t.draft.event_type.value == kind
    assert not any("did not recognise the event type" in n for n in t.notes)


def test_other_never_replaces_a_type_already_given(talk):
    assert talk("birthday for 30 guests in Pune on 12 December 2026, budget 50k", "another party please").draft.event_type.value == "birthday"


def test_a_college_farewell_is_still_a_community_event_when_the_words_say_so(talk):
    assert talk("college fest for 200 students in Pune on 12 December 2026, budget 1 lakh, a farewell too").draft.event_type.value == "community"


# ===================================================================== 9. intake uses the light model tier


def test_the_intake_uses_the_light_tier_only_when_asked_to(tmp_path, monkeypatch):
    from eventmate import llm as llm_mod
    from eventmate.service import EventMate

    monkeypatch.setenv("NVIDIA_API_KEY", "k-test-nvidia")
    heavy = llm_mod.client_from_env()
    em = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="tier", llm=heavy)
    assert em._intake_model() is heavy
    monkeypatch.setenv("EVENTMATE_INTAKE_TIER", "light")
    light = em._intake_model()
    assert light is not heavy and light.clients[0].model != heavy.clients[0].model
    assert em._intake_model() is light  # built once


def test_a_model_that_was_handed_in_is_used_as_it_is(tmp_path):
    from eventmate.service import EventMate

    fake = CountingModel()
    assert EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="fake", llm=fake)._intake_model() is fake
