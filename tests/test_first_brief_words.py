"""F5 of the final verification: an event type the planner has no word for is said out loud, never dropped, and a misspelled city in the very first brief is asked about at once (the web turn) or offered
back by name (the one-shot command), the same way it is later."""
import pytest

from eventmate.service import EventMate
from tests.conftest import TODAY

BRIEF = "Hackathon for 50 students in {city} on 12 December 2026, budget 1.5 lakh"


@pytest.fixture
def em(tmp_path):
    return EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="fb")


def test_an_unknown_event_type_is_said_in_the_first_turn_and_in_the_one_shot_reading(em):
    turn = em.intake_turn([BRIEF.format(city="Pune")], today=TODAY)
    assert turn.draft.event_type is None and any("did not recognise the event type" in n and '"other"' in n for n in turn.notes)
    req = em.manager.parse(BRIEF.format(city="Pune"), today=TODAY)
    assert req.event_type.value == "other" and any("event type not recognised" in a for a in req.assumptions)


def test_a_misspelled_city_in_the_first_message_is_asked_about_at_once(em):
    turn = em.intake_turn([BRIEF.format(city="banglore")], today=TODAY)
    assert turn.draft.city is None and "Did you mean Bengaluru?" in turn.question
    (q,) = [u for u in turn.draft.unresolved if u.key == "city"]
    assert [p.label for p in q.picks] == ["Bengaluru", "Banglore (as written)"], "pressable options: the suggestion is offered, never applied"


def test_the_one_shot_reading_names_the_city_it_thinks_was_meant_and_applies_nothing(em):
    problems: list[str] = []
    from eventmate.agents import manager as mgr

    assert mgr._parse_city(BRIEF.format(city="banglore"), ("Bengaluru", "Pune", "Mumbai"), problems) is None
    assert problems == ["I do not know a city called “banglore”. Did you mean Bengaluru? Say it again with that spelling"]
    problems.clear()
    assert mgr._parse_city(BRIEF.format(city="xyzzyplace"), ("Bengaluru", "Pune"), problems) is None and problems == ["city not found (e.g. 'in Pune')"]
