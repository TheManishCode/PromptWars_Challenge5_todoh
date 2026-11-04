"""The whole clarification round trip through the real HTTP routes, not only what the reader makes of a sentence.

A question the reader could not settle lives in the draft the page holds. These tests ask what can go wrong when that draft, an option number or the typed text travels back
to the server: an old or repeated press, a press from a second tab, an invalid option number, a draft that has lost its open question, and the trade-offs call, which used to
re-read the typed words and so could undo a button the user pressed. Every test goes through ``/api/intake``, ``/api/intake/confirm`` or ``/api/tradeoffs`` as the page does.

What this does not claim: the draft is held by the browser, so someone who edits their own request *and* the text they send with it can still say what they like about their own
plan (in shared mode that is only their own workspace). The checks are for the accidental paths (a stale tab, a bug, another client) and for the order of the steps. Closing it
fully would need the server to sign the draft it returns; that is not done. ``settled`` is a record the client carries, not proof of a click. A refresh loses the draft.
"""

import copy

import pytest

from eventmate.service import EventMate
from eventmate.web.app import create_app
from tests.conftest import TODAY
from tests.test_web import post

FULL = "wedding for 100 guests in Pune on 12 December 2026, budget 2 lakh"
TIGHT = "wedding for 120 guests in Pune on 12 December 2026, budget 60 thousnd, vegetarian"  # 60,000 cannot be planned: the trade-offs button appears


@pytest.fixture
def client(tmp_path):
    em = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="roundtrip")
    return create_app(em, today_fn=lambda: TODAY, rate_limit_per_minute=10_000).test_client()


def turn(client, text, draft=None, expecting=None, answer=None):
    body = {"messages": [text], "draft": draft}
    if expecting:
        body["expecting"] = expecting
    if answer:
        body["answer"] = answer
    return post(client, "/api/intake", {k: v for k, v in body.items() if v is not None})


def opened(client, text):
    r = turn(client, text)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def press(client, t, key, label_part):
    item = next(u for u in t["draft"]["unresolved"] if u["key"] == key)
    i = next(i for i, p in enumerate(item["picks"]) if label_part.lower() in p["label"].lower())
    r = turn(client, "x", t["draft"], t.get("asking"), {"key": key, "pick": i})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def confirm(client, draft, raw):
    return post(client, "/api/intake/confirm", {"draft": draft, "raw_text": raw})


# ---------------------------------------------------------------------------------------------- 1. old, repeated and invalid presses


def test_an_option_of_a_question_that_is_no_longer_open_is_refused(client):
    t = opened(client, "wedding for 100 guests in Pune on 12 December 2026, budget 20 lakhh")
    done = press(client, t, "budget_total", "20 lakh")
    assert done["draft"]["budget_total"] == 2_000_000 and done["draft"]["unresolved"] == []
    again = turn(client, "x", done["draft"], None, {"key": "budget_total", "pick": 0})  # the same button, pressed a second time from the page it was drawn on
    assert again.status_code == 400 and "not open any more" in again.get_json()["error"]


def test_an_option_from_an_earlier_question_cannot_answer_a_later_one(client):
    t = opened(client, "wedding for 100 guests in Pune on 12 December 2026, budget 20 lakhh, one guest is alergic to peanuts")
    assert {u["key"] for u in t["draft"]["unresolved"]} >= {"budget_total", "allergy:alergic"}
    after_budget = press(client, t, "budget_total", "20 lakh")
    stale = turn(client, "x", after_budget["draft"], None, {"key": "budget_total", "pick": 0})
    assert stale.status_code == 400
    assert any(u["key"] == "allergy:alergic" for u in after_budget["draft"]["unresolved"]), "answering one question leaves the other open"


@pytest.mark.parametrize("pick", [2, 5, 99])
def test_an_option_number_that_does_not_exist_is_refused_and_changes_nothing(client, pick):
    t = opened(client, "wedding for 100 guests in Pune on 12 December 2026, budget 20 lakhh")
    r = turn(client, "x", t["draft"], None, {"key": "budget_total", "pick": pick})
    assert r.status_code in (400, 422), r.get_json()  # 6 or more is refused by the request shape, a smaller number that is not an option by the reader


@pytest.mark.parametrize("bad", [{"key": "budget_total", "pick": -1}, {"key": "", "pick": 0}, {"key": "budget_total"}, {"key": "budget_total", "pick": "0"}, {"key": "budget_total", "pick": 0, "x": 1}])
def test_a_malformed_answer_is_refused_before_anything_is_read(client, bad):
    t = opened(client, "wedding for 100 guests in Pune on 12 December 2026, budget 20 lakhh")
    assert turn(client, "x", t["draft"], None, bad).status_code == 422


def test_a_second_tab_with_an_older_draft_is_answered_for_its_own_draft_only(client):
    first = opened(client, "wedding for 100 guests in Pune on 12 December 2026, budget 20 lakhh")
    tab_a = press(client, first, "budget_total", "20 lakh")
    tab_a = turn(client, "sorry, 150 guests", tab_a["draft"]).get_json()
    assert tab_a["draft"]["guest_count"] == 150
    tab_b = press(client, first, "budget_total", "20 lakh")  # the other tab still shows the first question
    assert tab_b["draft"]["guest_count"] == 100, "tab A's later change is not in tab B's draft, and tab B's press does not reach it"
    assert tab_b["draft"]["budget_total"] == 2_000_000


def test_an_option_cannot_set_a_fact_other_than_the_one_its_question_is_about(client):
    t = opened(client, "wedding for 100 guests in Pune on 12 December 2026, budget 20 lakhh")
    forged = copy.deepcopy(t["draft"])
    forged["unresolved"][0]["picks"][0]["field"] = "guest_count"  # a "budget" question whose button would set the guest count
    forged["unresolved"][0]["picks"][0]["value"] = "7"
    r = turn(client, "x", forged, None, {"key": "budget_total", "pick": 0})
    assert r.status_code in (400, 422), r.get_json()


@pytest.mark.parametrize("field,value", [("allergens", "peanutz"), ("allergens", ""), ("guest_count", "-5"), ("guest_count", "many"), ("budget_total", "0"), ("event_date", "2020-01-01"), ("event_date", "soon")])
def test_a_value_inside_an_option_is_checked_like_any_typed_value(client, field, value):
    t = opened(client, "wedding for 100 guests in Pune on 12 December 2026, budget 20 lakhh")
    forged = copy.deepcopy(t["draft"])
    forged["unresolved"] = [{"key": "k", "field": field, "said": "x", "question": "q", "picks": [{"label": "pick", "field": field, "value": value}]}]
    r = turn(client, "x", forged, None, {"key": "k", "pick": 0})
    assert r.status_code in (400, 422) or r.get_json()["draft"].get(field) not in (value,), r.get_json()


def test_pressing_not_that_sets_nothing_and_is_remembered_as_answered(client):
    t = opened(client, "wedding for 100 guests in Pune on 12 December 2026, budget 2 lakh, one guest is alergic to peanuts")
    done = press(client, t, "allergy:alergic", "not an allergy")
    assert done["draft"]["allergens"] == [] and done["draft"]["unresolved"] == []
    assert "allergy:alergic" in done["draft"]["settled"]


# ---------------------------------------------------------------------------------------------- 2. a draft that lost its question must not be accepted


UNSAFE = [
    ("allergy typo", FULL + ", one guest is alergic to peanuts", {}),
    ("allergy word typo", FULL + ", allergic to penuts", {}),
    ("serious reaction, nothing named", FULL + ", anaphylaxis", {}),
    ("exclusion", FULL + ", no peanuts please", {}),
    ("diet typo", FULL + ", vegiterian", {}),
    ("per person", "wedding for 100 guests in Pune on 12 December 2026, budget 2000 per person", {"budget_total": 2000}),
    ("dollars", "wedding for 100 guests in Pune on 12 December 2026, budget 5000 dollars", {"budget_total": 5000}),
    ("misspelt unit", "wedding for 100 guests in Pune on 12 December 2026, budget 20 lakhh", {"budget_total": 20}),
    ("guest range", "wedding for 100-150 guests in Pune on 12 December 2026, budget 2 lakh", {"guest_count": 150}),
]


@pytest.mark.parametrize("label,text,forced", UNSAFE, ids=[c[0] for c in UNSAFE])
def test_confirm_refuses_a_draft_whose_open_question_was_dropped(client, label, text, forced):
    t = opened(client, text)
    assert t["draft"]["unresolved"] and not t["ready"]
    stripped = {**t["draft"], "unresolved": [], "settled": [], **forced}  # what a lost flag, a stale page or another client could send
    r = confirm(client, stripped, text)
    assert r.status_code == 400 and "unresolved" in r.get_json()["error"].lower(), (r.status_code, r.get_json())


@pytest.mark.parametrize("label,text,key,label_part", [
    ("allergy: yes peanut", FULL + ", one guest is alergic to peanuts", "allergy:alergic", "peanut"),
    ("allergy: not an allergy", FULL + ", one guest is alergic to peanuts", "allergy:alergic", "not an allergy"),
    ("exclusion: only a preference", FULL + ", no peanuts please", "allergy:no peanuts", "preference"),
    ("diet: yes vegetarian", FULL + ", vegiterian", "diet:vegiterian", "vegetarian"),
    ("budget: suggested unit", "wedding for 100 guests in Pune on 12 December 2026, budget 20 lakhh", "budget_total", "20 lakh"),
    ("guests: chosen from a range", "wedding for 100-150 guests in Pune on 12 December 2026, budget 2 lakh", "guest_count", "150"),
])
def test_confirm_accepts_the_draft_once_the_user_has_pressed_an_answer(client, label, text, key, label_part):
    t = opened(client, text)
    done = press(client, t, key, label_part)
    assert done["draft"]["unresolved"] == [] and done["ready"]
    r = confirm(client, done["draft"], text)
    assert r.status_code in (201, 422) and "unresolved" not in str(r.get_json()).lower(), (r.status_code, r.get_json())


def test_a_question_closed_by_typing_the_value_plainly_is_also_recorded_as_answered(client):
    t = opened(client, "wedding for 100 guests in Pune on 12 December 2026, budget $5000")
    done = turn(client, "2 lakh", t["draft"], t["asking"]).get_json()
    assert done["draft"]["budget_total"] == 200_000 and done["draft"]["unresolved"] == [] and "budget_total" in done["draft"]["settled"]
    r = confirm(client, done["draft"], "wedding for 100 guests in Pune on 12 December 2026, budget $5000\n2 lakh")
    assert r.status_code in (201, 422) and "unresolved" not in str(r.get_json()).lower()


def test_an_old_settled_mark_does_not_cover_a_new_question_with_the_same_key(client):
    t = opened(client, "wedding for 100 guests in Pune on 12 December 2026, budget $5000")
    d = turn(client, "2 lakh", t["draft"], t["asking"]).get_json()
    again = turn(client, "actually budget 5000 dollars", d["draft"]).get_json()
    assert again["draft"]["unresolved"] and "budget_total" not in again["draft"]["settled"] and not again["ready"]


# ---------------------------------------------------------------------------------------------- 3. trade-offs keep what the user chose


def test_tradeoffs_start_from_the_confirmed_draft_not_from_the_typed_words(client):
    t = opened(client, TIGHT)
    assert t["draft"]["budget_total"] is None
    done = press(client, t, "budget_total", "60 thousand")
    assert done["draft"]["budget_total"] == 60_000 and done["ready"]
    r = post(client, "/api/tradeoffs", {"draft": done["draft"], "request": TIGHT})
    assert r.status_code == 200, r.get_json()
    report = r.get_json()
    assert report["request"]["budget_total"] == 60_000, "the typed text alone reads as ₹60; the button the user pressed must survive"
    assert report["request"]["guest_count"] == 120


def test_tradeoffs_keep_a_city_and_a_guest_count_chosen_by_button(client):
    t = opened(client, "wedding for 100-150 guests in Pune on 12 December 2026, budget 60 thousand")
    done = press(client, t, "guest_count", "150")
    r = post(client, "/api/tradeoffs", {"draft": done["draft"], "request": "wedding for 100-150 guests in Pune on 12 December 2026, budget 60 thousand"})
    assert r.status_code == 200 and r.get_json()["request"]["guest_count"] == 150


def test_tradeoffs_refuse_a_draft_with_an_open_question(client):
    t = opened(client, TIGHT)
    r = post(client, "/api/tradeoffs", {"draft": t["draft"], "request": TIGHT})
    assert r.status_code == 400 and "still" in r.get_json()["error"].lower()  # "still needed: budget_total": the open question left it empty


def test_tradeoffs_refuse_a_draft_that_lost_its_question_the_same_way_confirm_does(client):
    t = opened(client, TIGHT)
    stripped = {**t["draft"], "unresolved": [], "settled": [], "budget_total": 60}
    r = post(client, "/api/tradeoffs", {"draft": stripped, "request": TIGHT})
    assert r.status_code == 400


def test_tradeoffs_without_a_draft_still_read_the_text_as_before(client):
    r = post(client, "/api/tradeoffs", {"request": "wedding for 120 guests in Pune on 12 December 2026, budget 60000, vegetarian"})
    assert r.status_code == 200 and r.get_json()["request"]["budget_total"] == 60_000
