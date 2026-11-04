"""Food requirements the vendor records cannot answer are kept, shown and never read as "none": they keep the plan provisional until the caterer confirms."""

import pytest

from eventmate.agents.manager import parse_request
from eventmate.logs import RunLogs
from eventmate.planning import intake
from eventmate.planning.diets import read
from eventmate.schemas import DietaryRequirement as D
from eventmate.service import EventMate
from tests.conftest import EXAMPLE, TODAY


@pytest.fixture(scope="module")
def em(tmp_path_factory):
    root = tmp_path_factory.mktemp("diets")
    return EventMate(log_dir=root / "l", plan_dir=root / "p", run_id="dt")


@pytest.mark.parametrize("text,notes", [
    ("satvik khana", ["sattvic"]), ("sattvic menu", ["sattvic"]), ("kosher food", ["kosher"]), ("no onion no garlic", ["no onion or garlic"]), ("onion garlic free", ["no onion or garlic"]),
    ("jain, no onion", ["no onion or garlic"]), ("no beef, no pork", ["no beef", "no pork"]), ("diabetic friendly menu", ["diabetic-friendly"]), ("fasting menu navratri", ["fasting menu"]),
    ("eggless cake", ["eggless"]), ("low sodium please", ["low salt or low carb"]), ("dry event", ["no alcohol"]),
    ("no satvik", []), ("pure veg", []), ("vegetarian and jain", []), ("budget 2 lakh", []),
])
def test_requirements_the_records_cannot_answer_are_read(text, notes):
    assert list(read(text).notes) == notes


@pytest.mark.parametrize("text,unclear", [("dietary restrictions: low iodine", ["low iodine"]), ("food requirement is no mushrooms please", ["no mushrooms please"]), ("diet needs - vegetarian", [])])
def test_a_restriction_in_unknown_words_is_kept(text, unclear):
    assert list(read(text).unclear) == unclear


def test_cannot_eat_and_avoid_are_read_as_allergies_the_safer_reading():
    from eventmate.planning.allergies import read as allergy
    from eventmate.schemas import Allergen as A

    assert set(allergy("my guest can't eat nuts").allergens) == {A.PEANUT, A.TREE_NUT}
    assert allergy("guest cannot eat mushrooms").unclear == ("mushrooms",)
    assert allergy("avoid spicy food").unclear == () and allergy("avoid onion").unclear == () and allergy("no need to avoid anything").unclear == ()


def test_diets_the_records_do_list_are_still_hard_checks():
    r = parse_request("Plan a 100-person party in Pune on December 12 under INR 200,000. veggie, halaal, doesn't eat meat", TODAY)
    assert set(r.dietary) == {D.VEGETARIAN, D.HALAL}


def plan(em, text):
    out = em.plan(text, today=TODAY)
    assert out.plan is not None, out.violations
    return out.plan


def test_a_requirement_nobody_can_confirm_keeps_the_plan_provisional(em):
    p = plan(em, EXAMPLE + " We want satvik food with no onion no garlic.")
    assert p.request.diet_notes == ("sattvic", "no onion or garlic")
    item = next(u for u in p.validation.unverified if u.name == "diet_unconfirmed")
    assert item.blocks_verified and "sattvic" in item.label and "in writing" in item.repair and not p.validation.fully_verified


def test_a_restriction_we_could_not_place_is_never_dropped(em, tmp_path):
    p = plan(em, EXAMPLE + " Dietary restrictions: low iodine.")
    assert p.request.diet_unclear == ("low iodine",)
    assert any(u.name == "diet_unclear" and u.blocks_verified for u in p.validation.unverified)
    d = intake.propose(["120 people in Pune on 12 December, budget 2 lakh, dietary restrictions: low iodine"], None, TODAY, ["Pune"], RunLogs.create(log_dir=tmp_path, run_id="g"))
    assert d.draft.diet_unclear == ("low iodine",) and any("could not place" in n for n in d.notes)
    d2 = intake.propose(["no restrictions at all"], d.draft, TODAY, ["Pune"], RunLogs.create(log_dir=tmp_path, run_id="g2"))
    assert d2.draft.diet_unclear == ("low iodine",)  # a later message cannot clear it


def test_a_model_cannot_set_or_clear_food_requirements(tmp_path, monkeypatch):
    monkeypatch.setattr(intake, "_llm_draft", lambda *a, **k: (intake.IntakeDraft(guest_count=50, diet_notes=("kosher",)), None))
    d = intake.propose(["50 people in Pune on 12 December, budget 1 lakh, satvik"], None, TODAY, ["Pune"], RunLogs.create(log_dir=tmp_path, run_id="g"), llm=object())
    assert d.draft.diet_notes == ("sattvic",)


def test_the_reader_never_fails_and_says_something_whenever_a_requirement_word_is_present():
    from hypothesis import given, settings, strategies as st

    words = ["satvik", "kosher", "no", "not", "onion", "garlic", "beef", "pork", "dietary", "restrictions", ":", "x" * 40, ",", ".", "free", "diabetic", "fasting", "​", "and"]

    @settings(max_examples=400, deadline=None)
    @given(st.lists(st.sampled_from(words), max_size=14).map(" ".join) | st.text(max_size=200))
    def check(text):
        r = read(text)
        assert len(r.notes) <= 5 and len(r.unclear) <= 5 and all(0 < len(u) <= 60 for u in r.unclear)
        if any(w in text.lower() for w in ("satvik", "kosher", "diabetic", "fasting")) and not any(n in text.lower() for n in ("no ", "not ", "without")):
            assert r.notes, text

    check()


B = "Plan a 100-person party in Pune on December 12 under INR 200,000. "


@pytest.mark.parametrize("text,diet,notes", [
    ("no onion no garlic vegetarian", [D.VEGETARIAN], ["no onion or garlic"]),  # the "no" belongs to garlic: vegetarian is a request
    ("no pork vegetarian", [D.VEGETARIAN], ["no pork"]),
    ("no onion no garlic satvik", [], ["sattvic", "no onion or garlic"]),
    ("no beef or pork, halal", [D.HALAL], ["no beef", "no pork"]),
    ("without any satvik", [], []),
    ("not jain, vegetarian", [D.VEGETARIAN], []),
    ("not vegetarian", [], []),
    ("no vegetarian food", [], []),
])
def test_a_negation_reaches_only_the_word_it_belongs_to(text, diet, notes):
    r = parse_request(B + text, TODAY)
    assert set(r.dietary) == set(diet) and list(r.diet_notes) == notes


def test_a_negation_still_covers_a_list_joined_by_or():
    r = parse_request("Plan a 100-person party in Pune on December 12 under INR 200,000. Indoor venue, without food or photographer.", TODAY)
    assert [s.value for s in r.services] == ["venue"]
