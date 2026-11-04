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
    ("allergic to peanuts, cashews and eggs", {A.PEANUT, A.TREE_NUT, A.EGG}),  # a list stays a list
    ("Allergic to gluten", {A.GLUTEN}),
    ("mungfali se allergy hai", {A.PEANUT}),
    ("kaju aur badam se allergy hai", {A.TREE_NUT}),
    ("mujhe anda se allergy hai, veg khana", {A.EGG}),
    ("peanut allergy, shellfish allergy", {A.PEANUT, A.SHELLFISH}),
    ("allergic to peanuts, not cashews", {A.PEANUT}),
    ("lactose intolerant", {A.DAIRY}),
    ("celiac", {A.GLUTEN}),
    ("allergic to sulphites, mustard and celery", {A.SULPHITES, A.MUSTARD, A.CELERY}),
    ("not allergic to nuts but allergic to eggs", {A.EGG}),
    ("allergic to peanuts; cashews and eggs", {A.PEANUT, A.TREE_NUT, A.EGG}),  # a semicolon does not end the list
    ("allergic to peanuts; eggs; shellfish", {A.PEANUT, A.EGG, A.SHELLFISH}),
    ("Allergies: peanuts; eggs", {A.PEANUT, A.EGG}),
    ("Allergies:\n- peanuts\n- eggs", {A.PEANUT, A.EGG}),  # a bulleted list under a heading
    ("Allergies:\npeanuts\neggs", {A.PEANUT, A.EGG}),
    ("allergic to peanuts\ncashews", {A.PEANUT, A.TREE_NUT}),
    ("Allergy: peanut. Also eggs.", {A.PEANUT, A.EGG}),  # a short sentence that continues the list
    ("Allergic to peanuts. Cashews too.", {A.PEANUT, A.TREE_NUT}),
    ("Allergy: peanut. Menu: egg curry, paneer tikka", {A.PEANUT}),  # a longer sentence is a new thought
    ("Allergic to peanuts.\nBudget 2 lakh\nPune", {A.PEANUT}),
    ("allergic to peanuts; not eggs", {A.PEANUT}),
    ("Allergies: none", set()), ("Allergies: nil", set()), ("Allergies: n/a", set()), ("allergies - no", set()), ("Allergies:\n- none", set()),
    ("allergic to eggs, not allergic to nuts", {A.EGG}),
    ("no allergies at all", set()),
    ("no onion no garlic lactose intolerant guests", {A.DAIRY}),  # a "no" for something else does not cancel the allergy
    ("indoor venue no onion no garlic lactose intolerant guests", {A.DAIRY}),
    ("no known allergies", set()),
    ("he doesn't have any allergies", set()),
    ("nobody can come late, lactose intolerant guests", {A.DAIRY}),
    ("no veg, allergic to peanuts", {A.PEANUT}),
    ("nobody is allergic", set()),
    ("none of the guests have any allergies", set()),
    ("koi allergy nahi hai", set()),
    ("allergy nahi hai", set()),
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


def test_the_allergy_reader_never_fails_never_invents_and_never_stays_silent_about_an_allergy(tmp_path):
    import re

    from hypothesis import given, settings, strategies as st

    from eventmate.planning.allergies import read

    words = ["allergic", "allergy", "allergies", "nut", "peanut", "free", "-free", "no", "not", "but", ",", ".", "eggs", "til", "fish", "to", "\u200b", "ALLERGIC", "x" * 50, "kiwi", "and", "gluten", "mungfali", "se", "hai", "nahi"]
    negations = re.compile(r"\b(?:no|not|none|nobody|never|nahi|nahin|without|nope|noone)\b|\bno\s?one\b", re.I)

    @settings(max_examples=500, deadline=None)
    @given(st.lists(st.sampled_from(words), max_size=14).map(" ".join) | st.text(max_size=200))
    def check(text):
        r = read(text)
        assert set(r.allergens) <= set(A) and len(r.allergens) == len(set(r.allergens)) and len(r.unclear) <= 5 and all(0 < len(u) <= 60 for u in r.unclear)
        if not any(w in text.lower() for w in ("allerg", "free", "celiac", "coeliac", "intoleran", "alerji")):
            assert r.allergens == () and r.unclear == ()  # nothing is read from a text with no allergy cue
        if re.search(r"\ballerg(?:y|ies|ic)\b", text, re.I | re.A) and not negations.search(text):
            assert r.allergens or r.unclear, text  # an allergy that was said is never silently dropped

    check()


@pytest.mark.parametrize("text,named,unclear", [
    ("allergic to peanuts and strawberries", {A.PEANUT}, ["strawberries"]),
    ("allergic to peanuts, strawberries", {A.PEANUT}, ["strawberries"]),
    ("strawberry allergy", set(), ["strawberry"]),
    ("he has an allergy", set(), ["he has an allergy"]),
    ("allergic to kiwi", set(), ["kiwi"]),
    ("peanut allergy, vegetarian, indoor, Pune", {A.PEANUT}, []),  # other facts in the list are not allergies
    ("no allergies", set(), []),
])
def test_an_allergy_that_cannot_be_named_is_kept_never_read_as_none(text, named, unclear):
    from eventmate.planning.allergies import read

    r = read(text)
    assert set(r.allergens) == named and list(r.unclear) == unclear


def test_an_unnamed_allergy_keeps_the_plan_provisional_and_tells_the_user(em, tmp_path):
    p = plan(em, EXAMPLE + " Someone is allergic to kiwi.")
    assert p.request.allergens == () and p.request.allergy_unclear == ("kiwi",)
    item = next(u for u in p.validation.unverified if u.name == "allergy_unclear")
    assert item.blocks_verified and "kiwi" in item.label and "not" in item.detail and not p.validation.fully_verified
    d = intake.propose(["120 people in Pune on 12 December, budget 2 lakh, allergic to kiwi"], None, TODAY, ["Pune"], RunLogs.create(log_dir=tmp_path, run_id="g"))
    assert d.draft.allergy_unclear == ("kiwi",) and any("could not name" in n and "kiwi" in n for n in d.notes)
    d2 = intake.propose(["actually nobody is allergic"], d.draft, TODAY, ["Pune"], RunLogs.create(log_dir=tmp_path, run_id="g2"))
    assert d2.draft.allergy_unclear == ("kiwi",)  # a later message cannot clear it


def test_the_users_reported_cases_through_the_whole_pipeline(em):
    for text, kinds in (("allergic to peanuts, cashews and eggs", {A.PEANUT, A.TREE_NUT, A.EGG}), ("allergic to gluten", {A.GLUTEN}), ("mungfali se allergy hai", {A.PEANUT})):
        p = plan(em, EXAMPLE + " " + text)
        assert set(p.request.allergens) == kinds and p.request.allergy_unclear == ()
        assert any(u.name == "allergens_unconfirmed" and u.blocks_verified for u in p.validation.unverified), text


@pytest.mark.parametrize("text,named,unclear", [
    ("Allergies:", set(), ["Allergies"]),  # a heading with nothing under it: said, not named
    ("Allergies:\n\npeanuts", {A.PEANUT}, ["Allergies"]),  # a blank line ends the heading's list: flagged, and the next line is still read
    ("strawberry allergy; eggs", {A.EGG}, ["strawberry"]),
])
def test_a_list_heading_with_nothing_under_it_is_never_read_as_none(text, named, unclear):
    from eventmate.planning.allergies import read

    r = read(text)
    assert set(r.allergens) == named and list(r.unclear) == unclear


def test_every_item_of_a_list_is_read_whatever_separates_them():
    """Random lists of two to four allergens in the ways people write them: commas, semicolons, "and", line breaks and bullets, a heading, or short follow-up sentences."""
    import random

    from eventmate.planning.allergies import read

    words = {"peanuts": A.PEANUT, "eggs": A.EGG, "shellfish": A.SHELLFISH, "sesame": A.SESAME, "soy": A.SOY, "mustard": A.MUSTARD, "gluten": A.GLUTEN, "celery": A.CELERY, "dairy": A.DAIRY, "fish": A.FISH}
    r = random.Random(7)
    layouts = [
        lambda w: "allergic to " + ", ".join(w), lambda w: "allergic to " + "; ".join(w), lambda w: "allergic to " + ", ".join(w[:-1]) + " and " + w[-1], lambda w: "allergic to " + "\n".join(w),
        lambda w: "Allergies: " + "; ".join(w), lambda w: "Allergies:\n- " + "\n- ".join(w), lambda w: "Allergies:\n* " + "\n* ".join(w), lambda w: "Allergy: " + w[0] + ". " + ". ".join(f"Also {x}" for x in w[1:]) + ".",
        lambda w: "allergic to " + w[0] + ". " + ". ".join(f"{x.capitalize()} too" for x in w[1:]) + ".", lambda w: "Allergies - " + " / ".join(w), lambda w: "Allergies (" + ", ".join(w) + ")",
    ]
    tail = ["", " 120 people in Pune on 12 December, budget 2 lakh, vegetarian", "\nBudget 2 lakh\nPune"]
    for _ in range(600):
        chosen = r.sample(sorted(words), r.randint(2, 4))
        text = r.choice(layouts)(chosen) + r.choice(tail)
        got = set(read(text).allergens)
        assert {words[w] for w in chosen} <= got, (text, got)
