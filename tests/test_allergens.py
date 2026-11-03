"""A stated allergy is never "passed": it is read only from the user's words, kept through every message, and keeps the plan provisional."""

import pytest

from eventmate.agents.manager import parse_allergens
from eventmate.logs import RunLogs
from eventmate.planning import intake
from eventmate.schemas import Allergen as A, Disruption, DisruptionKind, EventRequest
from eventmate.service import EventMate
from tests.conftest import EXAMPLE, TODAY


@pytest.fixture(scope="module")
def em(tmp_path_factory):
    root = tmp_path_factory.mktemp("allergens")
    return EventMate(log_dir=root / "l", plan_dir=root / "p", run_id="al")


@pytest.mark.parametrize("text,want", [
    ("allergic to peanuts and shellfish", {A.PEANUT, A.SHELLFISH}),
    ("my son has a nut allergy", {A.PEANUT, A.TREE_NUT}),  # "nut" does not say which
    ("nut-free desserts please", {A.PEANUT, A.TREE_NUT}),
    ("dairy free and gluten free", {A.DAIRY}),  # gluten-free is a diet, handled there
    ("egg allergy; veg catering, fish curry", {A.EGG}),
    ("allergy: til and soya", {A.SESAME, A.SOY}),
    ("he is allergic to cashews", {A.TREE_NUT}),
    ("not allergic to nuts but allergic to eggs", {A.EGG}),
    ("allergic to eggs, not allergic to nuts", {A.EGG}),
    ("no allergies at all", set()),
    ("not allergic to anything", set()),
    ("I love nuts and fish", set()),
])
def test_allergies_are_read_from_the_users_words_only(text, want):
    assert set(parse_allergens(text)) == want


def plan(em, text):
    out = em.plan(text, today=TODAY)
    assert out.plan is not None, out.violations
    return out.plan


def test_a_plan_with_an_allergy_is_provisional_and_says_what_to_do(em):
    p = plan(em, EXAMPLE + " My mother is allergic to peanuts.")
    assert p.request.allergens == (A.PEANUT,)
    items = [u for u in p.validation.unverified if u.name == "allergens_unconfirmed"]
    assert len(items) == 1 and items[0].blocks_verified and "peanut" in items[0].label and "in writing" in items[0].repair
    assert not p.validation.fully_verified and p.validation.verification_label().startswith("Needs verification")


def test_a_plan_without_an_allergy_is_not_marked(em):
    p = plan(em, EXAMPLE)
    assert p.request.allergens == () and not [u for u in p.validation.unverified if u.name == "allergens_unconfirmed"]


def test_the_allergy_survives_a_replan_and_old_stored_requests_still_load(em):
    p = plan(em, EXAMPLE + " Peanut allergy in the family.")
    moved = em.replan(p.plan_id, Disruption(kind=DisruptionKind.GUEST_COUNT_CHANGE, new_guest_count=100), today=TODAY)
    assert moved.plan is not None and moved.plan.request.allergens == (A.PEANUT,) and any(u.name == "allergens_unconfirmed" for u in moved.plan.validation.unverified)
    old = p.request.model_dump(mode="json")
    old.pop("allergens")
    assert EventRequest.model_validate(old).allergens == ()  # a plan stored before this field


def draft(text, prior=None, llm=None, tmp=None):
    return intake.propose([text], prior, TODAY, ["Pune"], RunLogs.create(log_dir=tmp, run_id="g"), llm=llm).draft


def test_intake_adds_allergies_and_never_drops_one(tmp_path):
    d1 = draft("120 people in Pune on 12 December, budget 2 lakh, peanut allergy", tmp=tmp_path)
    assert d1.allergens == (A.PEANUT,)
    d2 = draft("also my daughter is allergic to eggs", prior=d1, tmp=tmp_path)
    assert set(d2.allergens) == {A.PEANUT, A.EGG}
    d3 = draft("actually nobody has any allergies", prior=d2, tmp=tmp_path)
    assert set(d3.allergens) == {A.PEANUT, A.EGG}  # only the user's review card can remove one; a message cannot


def test_a_model_cannot_state_or_drop_an_allergy(tmp_path, monkeypatch):
    injected = intake.IntakeDraft(guest_count=50, allergens=(A.SOY,))
    monkeypatch.setattr(intake, "_llm_draft", lambda *a, **k: (injected, None))
    d = draft("50 people in Pune on 12 December, budget 1 lakh, peanut allergy", llm=object(), tmp=tmp_path)
    assert d.allergens == (A.PEANUT,)
    d = draft("50 people in Pune on 12 December, budget 1 lakh", llm=object(), tmp=tmp_path)
    assert d.allergens == ()


def test_the_allergy_reader_never_fails_and_never_invents(tmp_path):
    from hypothesis import given, settings, strategies as st

    words = ["allergic", "allergy", "allergies", "nut", "peanut", "free", "-free", "no", "not", "but", ",", ".", "eggs", "til", "fish", "to", "\u200b", "ALLERGIC", "x" * 50]

    @settings(max_examples=300, deadline=None)
    @given(st.lists(st.sampled_from(words), max_size=14).map(" ".join) | st.text(max_size=200))
    def check(text):
        got = parse_allergens(text)
        assert set(got) <= set(A) and len(got) == len(set(got))
        if not any(w in text.lower() for w in ("allerg", "-free", " free")) and "free" not in text.lower():
            assert got == ()  # nothing is read from a text that says neither "allergy" nor "<x>-free"

    check()
