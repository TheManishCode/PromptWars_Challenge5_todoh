"""City spelling variants are one place (architecture L3), and changing another field never re-asks for the city."""

from datetime import date

import pytest

from eventmate.agents.manager import _parse_city
from eventmate.logs import RunLogs
from eventmate.planning import cities, intake

TODAY = date(2026, 11, 2)


@pytest.mark.parametrize("text,expected", [("party in Bangalore", "Bengaluru"), ("party in BLR", "Bengaluru"), ("party at bengaluru", "Bengaluru"),
                                           ("wedding near Bombay", "Mumbai"), ("a do in Poona", "Pune"), ("in Navi Mumbai", "Navi Mumbai")])
def test_variants_read_as_one_spelling(text, expected):
    assert _parse_city(text, (), []) == expected


def test_two_spellings_of_one_city_are_not_a_conflict_but_two_cities_are():
    assert _parse_city("in Bangalore, I mean near Bengaluru", (), []) == "Bengaluru"
    problems = []
    assert _parse_city("in Pune or in Bangalore", (), problems) is None and "multiple cities" in problems[0]


def test_the_spelling_the_vendor_data_uses_wins_when_it_has_the_city():
    assert _parse_city("in Bangalore", ("Bangalore",), []) == "Bangalore"


def test_canonical_is_total_and_leaves_unknown_names_alone():
    assert cities.canonical("  Nagpur ") == "Nagpur" and cities.canonical(None) is None and cities.canonical("NEW  DELHI") == "Delhi"


def test_changing_the_headcount_never_re_asks_for_the_city(tmp_path):
    logs = RunLogs.create(log_dir=tmp_path, run_id="c")
    first = intake.propose(["50 students in Bangalore, veg, under 40000 rupees"], None, TODAY, ("Pune",), logs)
    assert first.draft.city == "Bengaluru" and first.draft.guest_count == 50
    second = intake.propose(["actually 40 guests"], first.draft, TODAY, ("Pune",), logs)
    assert second.draft.city == "Bengaluru" and second.draft.guest_count == 40
    assert "city" not in second.draft.missing()
