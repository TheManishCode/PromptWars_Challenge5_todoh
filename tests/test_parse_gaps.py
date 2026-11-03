"""Parser gaps found by the golden journeys: the shapes people actually write, and the look-alikes that must not be read as a budget, a headcount or a city."""

import time

import pytest

from eventmate.agents import manager as m


def budget(text):
    return m._parse_budget(text, [])


def guests(text):
    return m._parse_guests(text, [])


def city(text, known=()):
    return m._parse_city(text, known, [])


@pytest.mark.parametrize("text,expected", [
    ("reduce budget to 1,80,000", 180_000), ("budget now 2.5 lakh", 250_000), ("budget is now 3 lakh", 300_000), ("budget at around 90k", 90_000),
    ("1,80,000 budget", 180_000), ("2 lakh budget", 200_000), ("budget 5L", 500_000), ("budget: Rs 1,80,000", 180_000),
])
def test_a_budget_is_read_when_the_cue_comes_with_a_connector_or_after_the_number(text, expected):
    assert budget(text) == expected


@pytest.mark.parametrize("text", [
    "kids under 12 and 120 guests", "under 1,80,000", "December 12 budget", "on 2027-02-10 budget", "12/02 budget", "it is 2026 budget",
    "December 12, 2026 budget", "120 guests",
])
def test_a_date_a_year_or_a_bare_number_is_never_a_budget(text):
    assert budget(text) is None


def test_the_date_before_the_word_budget_does_not_steal_the_cue_from_the_amount():
    assert budget("a party in Pune on December 12 budget 175000") == 175_000
    assert budget("reception on 2027-02-10 budget 5L") == 500_000


@pytest.mark.parametrize("text,expected", [("a wedding for 120", 120), ("a wedding for 120, Pune", 120), ("dinner for about 40 on Friday", 40), ("for approx 75 people", 75)])
def test_a_headcount_written_as_for_n_is_read_when_nothing_else_says_guests(text, expected):
    assert guests(text) == expected


@pytest.mark.parametrize("text", ["budget for 2 lakh", "for 12 Dec", "for 3 days", "for 1,20,000", "for 5%", "for 2 nights", "for 40000 rupees", "for 1.5"])
def test_for_followed_by_a_price_a_date_or_a_length_of_time_is_not_a_headcount(text):
    assert guests(text) is None


def test_an_explicit_count_beats_for_n():
    assert guests("a party for 200 with 120 guests") == 120


@pytest.mark.parametrize("text,expected", [("50 students, Bangalore, veg", "Bengaluru"), ("party BLR 40 people", "Bengaluru"), ("a do, Poona, 30 people", "Pune"), ("wedding for 120, Pune, 2 lakh", "Pune"),
                                           ("meet in Bombay", "Mumbai"), ("Delhi office party", "Delhi")])
def test_a_bare_city_name_is_read_without_a_preposition(text, expected):
    assert city(text) == expected


@pytest.mark.parametrize("text", ["the bom of materials", "blr is a code", "hyd pressure", "a party", "bring Pune-style snacks"])
def test_look_alikes_and_codes_in_lower_case_are_not_cities(text):
    result = city(text)
    assert result in (None, "Pune") and (result is None or "Pune" in text)  # "Pune-style" does name Pune; the lower-case codes and plain words never do


def test_a_known_city_in_the_vendor_data_is_found_bare_too():
    assert city("party for 80, Nagpur, Friday", known=("Nagpur",)) == "Nagpur"


def test_two_cities_still_conflict():
    problems = []
    assert m._parse_city("in Pune or in Bangalore", (), problems) is None and "multiple cities" in problems[0]


@pytest.mark.parametrize("text", ["budget" + " " * 30_000 + "x", "budget of " * 4_000 + "x", "budget is now at " * 3_000, "1 budget " * 6_000, "for " + "1" * 20_000, "for 12 " * 8_000, "Bangalore " * 6_000 + "BLR " * 6_000])
def test_the_new_patterns_are_linear_on_hostile_input(text):
    started = time.perf_counter()
    budget(text)
    guests(text)
    city(text)
    assert time.perf_counter() - started < 1.0
