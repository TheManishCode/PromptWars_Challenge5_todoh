"""A diet written about some of the guests is a group, not a question and not a note.

"40 are Jain and 25 are vegan" says who needs what. It is kept as two groups with their counts, and each group's diet is a diet the caterer must offer (the vendor check reads
``request.dietary`` as the options a caterer must cover). "Everyone is vegetarian except 5 who eat chicken" is a whole-event diet plus an exception group of 5. Nothing asks whether
everyone is vegan: changing the whole event to vegan is a separate edit, said as one ("make it vegan for everyone"), and it replaces the group.

The tests read through the real ``intake.propose``, and through ``EventMate.confirm_intake`` to show a group changes which caterer can be chosen. Nothing here sends anything.
"""

from datetime import date

import pytest

from eventmate.logs import RunLogs
from eventmate.planning import intake
from eventmate.schemas import DietaryRequirement as D
from eventmate.service import EventMate

TODAY = date(2026, 10, 30)
CITIES = ("Pune", "Mumbai", "Bengaluru", "Hyderabad", "Chennai", "Kolkata", "Delhi", "Jaipur", "Ahmedabad")
BASE = "engagement party in Pune for 120 guests on 12 December 2026, budget 2 lakh"


@pytest.fixture
def logs(tmp_path):
    return RunLogs.create(log_dir=tmp_path, run_id="diet-groups")


@pytest.fixture
def talk(logs):
    def run(*messages, known=CITIES):
        turn = None
        for m in messages:
            turn = intake.propose([m], turn.draft if turn else None, TODAY, known, logs, None, expecting=turn.asking if turn else None)
        return turn

    return run


@pytest.fixture(scope="module")
def em(tmp_path_factory):
    root = tmp_path_factory.mktemp("diet_groups")
    return EventMate(log_dir=root / "l", plan_dir=root / "p", run_id="dg")


def groups(t):
    return {(g.diet, g.count, g.share, g.exception) for g in t.draft.diet_groups}


def open_fields(t):
    return [u.field for u in t.draft.unresolved]


# ===================================================================== what is read: a group, with what was said about its size

SOME = [
    ("a few", "a few guests are vegan", {D.VEGAN}, {(D.VEGAN, None, "a few", False)}),
    ("percent", "about 30% of the guests are jain", {D.JAIN}, {(D.JAIN, None, "30%", False)}),
    ("half", "half of them eat only veg", {D.VEGETARIAN}, {(D.VEGETARIAN, None, "half", False)}),
    ("most", "most of us are vegetarian", {D.VEGETARIAN}, {(D.VEGETARIAN, None, "most", False)}),
    ("count of them", "12 of them are vegan", {D.VEGAN}, {(D.VEGAN, 12, None, False)}),
    ("count are", "15 are jain", {D.JAIN}, {(D.JAIN, 15, None, False)}),
    ("one", "one guest is vegan", {D.VEGAN}, {(D.VEGAN, 1, None, False)}),
    ("two words", "two of the guests are jain", {D.JAIN}, {(D.JAIN, 2, None, False)}),
    ("some", "some relatives are jain", {D.JAIN}, {(D.JAIN, None, "some", False)}),
    ("two groups", "40 are jain and 25 are vegan", {D.JAIN, D.VEGAN}, {(D.JAIN, 40, None, False), (D.VEGAN, 25, None, False)}),
    ("both halves", "half are non veg and half are veg", {D.NON_VEGETARIAN, D.VEGETARIAN}, {(D.NON_VEGETARIAN, None, "half", False), (D.VEGETARIAN, None, "half", False)}),
    ("beside a whole-event diet", "vegetarian food for everyone and a few guests are vegan", {D.VEGETARIAN, D.VEGAN}, {(D.VEGAN, None, "a few", False)}),
]


@pytest.mark.parametrize("label,phrase,diets,expected", SOME, ids=[c[0] for c in SOME])
def test_a_diet_about_some_guests_is_a_group_the_caterer_must_cover_and_nothing_is_asked(talk, label, phrase, diets, expected):
    t = talk(f"{BASE}, {phrase}")
    assert set(t.draft.dietary) == diets, t.draft.dietary
    assert groups(t) == expected, groups(t)
    assert t.ready and not t.draft.unresolved, open_fields(t)


EXCEPTIONS = [
    ("chicken", "everyone is vegetarian except 5 people who eat chicken", {D.VEGETARIAN, D.NON_VEGETARIAN}, {(D.NON_VEGETARIAN, 5, None, True)}),
    ("meat", "all guests are vegetarian apart from 8 who eat meat", {D.VEGETARIAN, D.NON_VEGETARIAN}, {(D.NON_VEGETARIAN, 8, None, True)}),
    ("not a listed diet", "all guests are vegetarian apart from 2 who eat egg", {D.VEGETARIAN}, {(None, 2, None, True)}),
    ("no count", "vegetarian for everyone other than my brothers", {D.VEGETARIAN}, {(None, None, None, True)}),
]


@pytest.mark.parametrize("label,phrase,diets,expected", EXCEPTIONS, ids=[c[0] for c in EXCEPTIONS])
def test_an_exception_to_a_whole_event_diet_is_kept_as_an_exception_group(talk, label, phrase, diets, expected):
    t = talk(f"{BASE}, {phrase}")
    assert set(t.draft.dietary) == diets, t.draft.dietary
    assert groups(t) == expected, groups(t)
    assert t.ready and not t.draft.unresolved


def test_each_group_keeps_only_its_own_words(talk):
    t = talk(f"{BASE}, 40 are jain and 25 are vegan")
    assert {g.words for g in t.draft.diet_groups} == {"40 are jain", "25 are vegan"}, [g.words for g in t.draft.diet_groups]
    t = talk(f"{BASE}, half are non veg and half are veg")
    assert {g.words for g in t.draft.diet_groups} == {"half are non veg", "half are veg"}, [g.words for g in t.draft.diet_groups]


def test_the_groups_keep_the_order_the_user_wrote_them_in(talk):
    t = talk(f"{BASE}, 25 are vegan and 40 are jain")
    assert [g.diet for g in t.draft.diet_groups] == [D.VEGAN, D.JAIN]


def test_an_exception_whose_diet_is_not_listed_is_kept_in_the_users_words_for_the_caterer(talk):
    t = talk(f"{BASE}, all guests are vegetarian apart from 2 who eat egg")
    assert any("apart from 2" in n and "egg" in n for n in t.draft.diet_notes), t.draft.diet_notes


@pytest.mark.parametrize("phrase,diets", [
    ("all guests are vegetarian", {D.VEGETARIAN}),
    ("vegetarian catering please", {D.VEGETARIAN}),
    ("pure veg only", {D.VEGETARIAN}),
    ("halal food only", {D.HALAL}),
    ("vegetarian only, no exceptions", {D.VEGETARIAN}),
    ("one of the caterers should be halal certified", {D.HALAL}),
    ("we want some veg and some non veg dishes", {D.VEGETARIAN, D.NON_VEGETARIAN}),
    ("a few vegan options please", {D.VEGAN}),
])
def test_a_diet_for_the_whole_event_or_the_menu_makes_no_group(talk, phrase, diets):
    t = talk(f"{BASE}, {phrase}")
    assert set(t.draft.dietary) == diets and t.draft.diet_groups == () and t.ready


def test_a_count_beside_the_total_with_a_diet_word_is_a_group_and_the_total_stays(talk):
    t = talk("engagement party in Pune for 120 guests on 12 December 2026, budget 2 lakh, 30 people vegetarian")
    assert t.draft.guest_count == 120 and groups(t) == {(D.VEGETARIAN, 30, None, False)}


def test_a_total_that_says_vegetarian_is_not_a_group(talk):
    t = talk("party in Pune for 100 guests vegetarian on 12 December 2026, budget 2 lakh")
    assert t.draft.guest_count == 100 and t.draft.diet_groups == () and t.draft.dietary == (D.VEGETARIAN,)


# ===================================================================== counts that cannot be right are asked about

def test_a_group_larger_than_the_guest_count_is_asked_about(talk):
    t = talk("engagement party in Pune for 40 guests on 12 December 2026, budget 2 lakh, 60 are vegan")
    assert not t.ready and "guest_count" in open_fields(t)
    q = next(u for u in t.draft.unresolved if u.field == "guest_count")
    assert "60" in q.question and "40" in q.question


def test_the_question_closes_when_the_guest_count_is_corrected(talk):
    t = talk("engagement party in Pune for 40 guests on 12 December 2026, budget 2 lakh, 60 are vegan", "it is 80 guests")
    assert t.draft.guest_count == 80 and t.ready and groups(t) == {(D.VEGAN, 60, None, False)}


def test_groups_that_add_up_to_more_than_the_guests_are_allowed_because_a_guest_can_be_in_two(talk):
    t = talk("engagement party in Pune for 40 guests on 12 December 2026, budget 2 lakh, 30 are jain and 25 are vegan, some are both")
    assert t.ready and len(t.draft.diet_groups) == 2


# ===================================================================== changing the group is an edit, not the default reading

def test_saying_everyone_is_vegan_later_replaces_the_group_with_a_whole_event_diet(talk):
    t = talk(f"{BASE}, a few guests are vegan", "actually make it vegan for everyone")
    assert t.draft.diet_groups == () and t.draft.dietary == (D.VEGAN,)
    assert any("Food groups removed" in c for c in t.changes), t.changes


def test_a_new_count_for_the_same_group_replaces_the_old_one(talk):
    t = talk(f"{BASE}, 12 of them are vegan", "actually 20 are vegan")
    assert groups(t) == {(D.VEGAN, 20, None, False)}


def test_a_later_group_does_not_wipe_an_earlier_whole_event_diet(talk):
    t = talk(f"{BASE}, all guests are vegetarian", "and a few are vegan")
    assert set(t.draft.dietary) == {D.VEGETARIAN, D.VEGAN} and groups(t) == {(D.VEGAN, None, "a few", False)}


def test_a_group_is_said_in_what_a_message_changed(talk):
    t = talk(f"{BASE}", "12 of them are vegan")
    assert any("Food groups added" in c and "Vegan" in c and "12" in c for c in t.changes), t.changes


# ===================================================================== the confirmed request carries the group, and planning uses it

def test_the_request_carries_the_groups_and_their_diets(talk):
    msg = f"{BASE}, 40 are jain and 25 are vegan"
    t = talk(msg)
    request = intake.to_request(t.draft, msg, TODAY, messages=[msg])
    assert set(request.dietary) == {D.JAIN, D.VEGAN}
    assert {(g.diet, g.count) for g in request.diet_groups} == {(D.JAIN, 40), (D.VEGAN, 25)}


@pytest.mark.parametrize("phrase,caterer", [("40 are jain", "Annapurna"), ("25 are vegan", "Spice Route")])
def test_a_group_diet_decides_which_caterer_can_be_chosen(talk, em, phrase, caterer):
    msg = f"{BASE}, {phrase}"  # on 12 December of the seed catalogue's caterers that fit 120 guests, only Annapurna offers jain and only Spice Route offers vegan
    t = talk(msg)
    out = em.confirm_intake(t.draft, msg, today=TODAY, messages=[msg])
    assert out.plan is not None, out.violations
    assert [s.name for s in out.plan.selections if s.category.value == "caterer"] and any(caterer in s.name for s in out.plan.selections if s.category.value == "caterer")
    item = next(u for u in out.plan.validation.unverified if u.name == "diet_groups")
    assert phrase.split()[0] in item.detail and phrase.split()[-1] in item.detail.lower() and not item.blocks_verified


def test_a_group_no_caterer_can_serve_stops_the_plan_like_a_whole_event_diet_would(talk, em):
    msg = f"{BASE}, 40 are halal and 25 are vegan"  # no Pune caterer offers both
    t = talk(msg)
    out = em.confirm_intake(t.draft, msg, today=TODAY, messages=[msg])
    assert out.plan is None and any(v.constraint == "dietary" for v in out.violations), out.violations


# ===================================================================== what these tests do not show
#
# They show the groups read from the phrasings above and that a group's diet is checked against the caterer's options. They do not show that a caterer can cook the quantity of each
# diet (the vendor records list options, not how many plates of each): the plan says so as an unverified item. A group is read from the words around a diet word, so a group written
# in words not listed here is read as the whole event or not at all. Allergy counts ("3 of them are allergic to peanuts") are not stored.
