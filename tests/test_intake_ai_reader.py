"""The AI reader is only a proposer. These tests give ``intake.propose`` a fake model that answers exactly what a careless or hostile one might, and check that nothing the rules
refuse to guess gets in through it: an amount the rules asked about, an allergy, a diet, a count the user wrote as a range, a city the user said it is not, or the bookkeeping of the
questions themselves. No network and no API credit is used: the model is a class that returns a prepared reply.

Reader labels: every test here is the AI path (``source`` starts with ``llm:``) unless it says otherwise. The rule reader's own results are in test_intake_reader.py.
"""

import json
from datetime import date

import pytest

from eventmate.logs import RunLogs
from eventmate.planning import intake
from eventmate.schemas import Allergen

TODAY = date(2026, 10, 30)
CITIES = ("Pune", "Mumbai", "Bengaluru")
FULL = "wedding for 100 guests in Pune on 12 December 2026, budget 2 lakh"


class FakeModel:
    name = "fake-model"

    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def complete(self, system, user, max_tokens):
        self.calls.append(user)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)


@pytest.fixture
def logs(tmp_path):
    return RunLogs.create(log_dir=tmp_path, run_id="ai")


def ask(logs, text, reply, prior=None, expecting=None):
    model = FakeModel(reply)
    t = intake.propose([text], prior, TODAY, CITIES, logs, model, expecting=expecting)
    assert t.source.startswith("llm:"), t.source  # the model's reply was used, so this really is the AI path
    return t


def keys(t):
    return {u.key for u in t.draft.unresolved}


COMPLETE = {"event_type": "wedding", "guest_count": 100, "city": "Pune", "event_date": "2026-12-12", "budget_total": 200000}


@pytest.mark.parametrize("label,text,model_says,field,keeps_open", [
    ("budget in dollars", FULL.replace("budget 2 lakh", "budget 5000 dollars"), {"budget_total": 5000}, "budget_total", "budget_total"),
    ("misspelt unit", FULL.replace("budget 2 lakh", "budget 20 lakhh"), {"budget_total": 20}, "budget_total", "budget_total"),
    ("model does the per-person arithmetic itself", FULL.replace("budget 2 lakh", "budget 2000 per person"), {"budget_total": 200000}, "budget_total", "budget_total"),
    ("guest range", "wedding for 100-150 guests in Pune on 12 December 2026, budget 2 lakh", {"guest_count": 150}, "guest_count", "guest_count"),
    ("negative guests", "wedding for -5 guests in Pune on 12 December 2026, budget 2 lakh", {"guest_count": 5}, "guest_count", "guest_count"),
    ("date with a misspelt month", "wedding for 100 guests in Pune on 12 Decmber 2026, budget 2 lakh", {"event_date": "2026-12-12"}, "event_date", "event_date"),
    ("city typo", "wedding for 100 guests in Banglore on 12 December 2026, budget 2 lakh", {"city": "Bengaluru"}, "city", "city"),
    ("two cities", "wedding for 100 guests in Pune or Mumbai on 12 December 2026, budget 2 lakh", {"city": "Mumbai"}, "city", "city"),
])
def test_a_model_cannot_fill_a_fact_the_rules_asked_about(logs, label, text, model_says, field, keeps_open):
    t = ask(logs, text, {**COMPLETE, **model_says})
    assert getattr(t.draft, field) is None, f"{label}: the model's {model_says} got in"
    assert keeps_open in keys(t) and not t.ready


def test_a_model_cannot_answer_a_bare_number_for_the_user(logs):
    prior = intake.propose([FULL], None, TODAY, CITIES, logs, None).draft
    t = ask(logs, "make it 150", {**COMPLETE, "guest_count": 150}, prior)
    assert t.draft.guest_count == 100 and "number" in keys(t) and not t.ready


def test_a_model_cannot_apply_the_suggestion_for_a_misspelt_allergy_or_diet(logs):
    t = ask(logs, FULL + ", one guest is alergic to peanuts", {**COMPLETE, "allergens": ["peanut"]})
    assert t.draft.allergens == () and "allergy:alergic" in keys(t) and not t.ready
    d = ask(logs, FULL + ", vegiterian", {**COMPLETE, "dietary": ["vegetarian"]})
    assert d.draft.dietary == () and any(k.startswith("diet:") for k in keys(d)) and not d.ready


def test_a_model_cannot_clear_or_replace_an_allergy_the_rules_read(logs):
    t = ask(logs, FULL + ", allergic to peanuts", {**COMPLETE, "allergens": []})
    assert Allergen.PEANUT in t.draft.allergens
    t = ask(logs, FULL + ", allergic to peanuts", {**COMPLETE, "allergens": ["egg"], "allergy_unclear": [], "diet_notes": []})
    assert t.draft.allergens == (Allergen.PEANUT,)


def test_a_model_cannot_keep_a_city_the_user_said_it_is_not(logs):
    prior = intake.propose([FULL], None, TODAY, CITIES, logs, None).draft
    t = ask(logs, "not Pune, Mumbai", {**COMPLETE, "city": "Pune"}, prior)
    assert t.draft.city == "Mumbai", "the city the user named is not overruled by one they ruled out"


def test_a_model_cannot_set_the_bookkeeping_of_the_questions(logs):
    t = ask(logs, FULL + ", one guest is alergic to peanuts", {**COMPLETE, "unresolved": [], "settled": ["allergy:alergic", "budget_total"]})
    assert "allergy:alergic" in keys(t) and "allergy:alergic" not in t.draft.settled and not t.ready


def test_a_model_cannot_close_a_question_by_answering_it_in_json_after_the_user_pressed_nothing(logs):
    t = ask(logs, FULL.replace("budget 2 lakh", "budget 20 lakhh"), {**COMPLETE, "budget_total": 2_000_000})
    assert t.draft.budget_total is None and "budget_total" in keys(t)
    again = ask(logs, "sounds good", {**COMPLETE, "budget_total": 2_000_000}, t.draft, t.asking)
    assert again.draft.budget_total is None and "budget_total" in keys(again), "the model repeating its own guess on the next turn does not close it either"


@pytest.mark.parametrize("reply", ["not json", "[]", '{"guest_count": "many"}', '{"city": "<b>x</b>"}', {"guest_count": -3}, {"unknown_field": 1}, RuntimeError("provider down")])
def test_an_unusable_model_reply_falls_back_to_the_rules_with_a_notice(logs, reply):
    model = FakeModel(reply)
    t = intake.propose([FULL], None, TODAY, CITIES, logs, model)
    assert t.draft.guest_count == 100 and t.draft.budget_total == 200_000 and t.ready and t.source == "rule:parser"
    assert any("basic reader" in n for n in t.notes)


def test_a_model_value_the_rules_could_not_find_is_flagged_for_the_user_to_check(logs):
    t = ask(logs, "wedding in Pune on 12 December 2026, budget 2 lakh", {**COMPLETE, "guest_count": 80})
    assert t.draft.guest_count == 80 and "guest_count" in t.flags


def test_a_model_value_that_is_impossible_never_displaces_what_the_rules_read(logs):
    t = ask(logs, FULL, {"event_date": "2020-01-01", "guest_count": 7, "budget_total": 5, "city": "Mumbai"})
    assert (t.draft.guest_count, t.draft.budget_total, t.draft.city, t.draft.event_date) == (100, 200_000, "Pune", date(2026, 12, 12)) and t.ready
