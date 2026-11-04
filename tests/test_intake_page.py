"""The New plan dialog in a real browser: the parts the reader tests cannot prove.

The reader tests (tests/test_intake_reader.py) show what the reader makes of a message. These show that the page sends the right state with it (the saved draft, the question that
was open, the option the user pressed), shows the explanation the reader gave, says what a correction changed, keeps "Build my plan" away while anything is unresolved, and
survives a long conversation. Nothing is built or sent here: every test stops at the review card.
"""

import json
import threading

import pytest

from eventmate.service import EventMate
from eventmate.web.app import create_app
from tests.browser_helpers import BROWSER_ARGS, CHROMIUM, NOISE
from tests.conftest import TODAY

pw = pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.skipif(CHROMIUM is None, reason="no Chromium on this machine")

FULL = "wedding for 100 guests in Pune on 12 December 2026, budget 2 lakh"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    from werkzeug.serving import make_server

    root = tmp_path_factory.mktemp("intake_page")
    em = EventMate(log_dir=root / "l", plan_dir=root / "p", run_id="intake-page")
    srv = make_server("127.0.0.1", 0, create_app(em, today_fn=lambda: TODAY, poll_seconds=0, rate_limit_per_minute=10_000), threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROMIUM, args=BROWSER_ARGS)
        yield b
        b.close()


@pytest.fixture
def dialog(browser, server):
    """The New plan dialog, open. ``dialog.say(text)`` types and reads; ``dialog.sent`` holds the body of every /api/intake call the page made."""
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    page.sent, page.problems, page.tradeoffs = [], [], []
    page.on("console", lambda m: page.problems.append(m.text) if m.type in ("error", "warning") and not any(n in m.text for n in NOISE) and "422" not in m.text else None)
    page.on("pageerror", lambda e: page.problems.append(str(e)))
    page.on("request", lambda r: page.sent.append(json.loads(r.post_data)) if r.url.endswith("/api/intake") and r.method == "POST" else None)
    page.on("response", lambda r: page.tradeoffs.append((json.loads(r.request.post_data), r.json())) if r.url.endswith("/api/tradeoffs") else None)
    page.goto(server + "/app", wait_until="networkidle")
    page.click("#new-plan")

    def say(text, wait=True):
        page.fill("#brief-input", text)
        page.click("#read-brief")
        if wait:
            page.wait_for_function("() => !document.querySelector('#read-brief').disabled")
        return card(page)

    page.say = say
    yield page
    ctx.close()


def card(page):
    return " | ".join(x.strip() for x in page.inner_text("#understood").split("\n") if x.strip())


def row(page, label):
    """The value shown next to a label of the review card ("City" -> "Pune"), or None."""
    lines = [x.strip() for x in page.inner_text("#understood").split("\n") if x.strip()]
    return lines[lines.index(label) + 1] if label in lines else None


def can_build(page):
    return page.locator("#build").count() == 1


# ------------------------------------------------------------------ the loop the audit found (F1, F3)


def test_a_city_typo_is_explained_suggested_and_confirmed_with_one_press(dialog):
    page = dialog
    page.say("wedding for 100 guests on 12 December 2026, budget 2 lakh")
    assert "Which city is it in?" in card(page) and not can_build(page)
    page.say("Banglore")
    text = card(page)
    assert "Banglore" in text and "Bengaluru" in text, text  # it says what it saw and what it thinks you meant
    assert row(page, "City") is None and not can_build(page), "a suggestion is not applied until it is confirmed"
    page.get_by_role("button", name="Bengaluru").click()
    page.wait_for_selector("#build")
    assert row(page, "City") == "Bengaluru" and "Banglore" not in card(page).replace("Banglore (as written)", "")


def test_the_city_question_does_not_repeat_itself_wordlessly(dialog):
    page = dialog
    page.say("wedding for 100 guests on 12 December 2026, budget 2 lakh")
    first = card(page)
    page.say("???")
    second = card(page)
    assert "???" in second and second != first, "the answer that was not understood is named, so it is not the same screen twice"
    page.say("thane")
    assert row(page, "City") == "Thane" and can_build(page) and "no vendor records for Thane" in card(page)


def test_bare_answers_fill_the_fields_the_page_asked_about(dialog):
    page = dialog
    page.say("wedding in Pune")
    assert "How many guests" in card(page)
    page.say("100")
    assert row(page, "Guests") == "100" and "What date" in card(page)
    page.say("12 Dec 2026")
    assert row(page, "Date") == "12 December 2026" and "total budget" in card(page)
    page.say("200000")
    assert row(page, "Budget") == "₹2,00,000" and can_build(page)
    assert [b.get("expecting") for b in page.sent] == [None, "guest_count", "event_date", "budget_total"], "the page tells the server which question was open"


# ------------------------------------------------------------------ corrections say what they changed


def test_a_correction_that_is_a_bare_number_asks_which_fact_and_then_says_what_changed(dialog):
    page = dialog
    page.say(FULL)
    page.say("make it 150")
    assert row(page, "Guests") == "100" and not can_build(page), "not guessed, not applied"
    page.get_by_role("button", name="150 guests").click()
    page.wait_for_selector("#build")
    assert row(page, "Guests") == "150" and "Guests changed from 100 to 150" in card(page)


def test_a_city_correction_by_negation_replaces_the_city_and_says_so(dialog):
    page = dialog
    page.say(FULL)
    page.say("not Pune, Mumbai")
    assert row(page, "City") == "Mumbai" and "City changed from Pune to Mumbai" in card(page)


def test_a_message_that_changes_nothing_says_that(dialog):
    page = dialog
    page.say(FULL)
    page.say("sounds good")
    assert "could not find a change" in card(page).lower() and row(page, "Guests") == "100"


# ------------------------------------------------------------------ Build stays away while anything is unresolved


OPEN_QUESTIONS = [
    ("wedding in Pune on 12 December 2026, 100 guests, budget 20 lakhh", "20 lakh"),
    ("wedding in Pune on 12 December 2026, 100 guests, budget $5000", "rupees"),
    ("wedding in Pune on 12 December 2026, 100 guests, budget 2000 per person", "per person"),
    ("wedding in Pune on 12 December 2026, 100-150 guests, budget 2 lakh", "100"),
    (FULL + ", allergic to penuts", "peanut"),
]


@pytest.mark.parametrize("text,question", OPEN_QUESTIONS, ids=[q[1] for q in OPEN_QUESTIONS])
def test_build_is_not_offered_while_a_question_is_open(dialog, text, question):
    page = dialog
    page.say(text)
    assert not can_build(page), card(page)
    assert question.lower() in card(page).lower(), card(page)
    page.say("sounds good")  # a message that answers nothing leaves the question open
    assert not can_build(page) and question.lower() in card(page).lower(), card(page)


def test_pressing_the_suggested_unit_fixes_the_budget_and_returns_build(dialog):
    page = dialog
    page.say("wedding for 100 guests in Pune on 12 December 2026, budget 20 lakhh")
    assert row(page, "Budget") is None and not can_build(page)
    page.get_by_role("button", name="20 lakh").click()
    page.wait_for_selector("#build")
    assert row(page, "Budget") == "₹20,00,000"


def test_an_allergy_typo_blocks_build_until_the_user_answers(dialog):
    page = dialog
    page.say(f"{FULL}, one guest is alergic to peanuts")
    assert not can_build(page) and "peanut" in card(page).lower() and row(page, "Allergies").startswith("None stated")
    page.get_by_role("button", name="peanut").first.click()
    page.wait_for_selector("#build")
    assert row(page, "Allergies") == "Peanut"


# ------------------------------------------------------------------ a long conversation (the 20-message cap)


def test_a_long_conversation_never_hits_the_message_cap_and_the_page_only_sends_the_newest_message(dialog):
    page = dialog
    page.say(FULL)
    for i in range(25):
        page.say(f"thanks {i}")
    assert "invalid input" not in card(page).lower() and row(page, "Guests") == "100" and can_build(page)
    assert all(len(body["messages"]) == 1 for body in page.sent), "the server reads only the newest message; the page no longer re-sends the whole history"
    assert page.sent[-1]["draft"]["guest_count"] == 100, "the saved draft travels with every message"
    assert page.problems == [], page.problems


def test_a_message_the_server_refuses_shows_the_reason_it_gave(dialog):
    page = dialog
    page.evaluate("() => { document.querySelector('#brief-input').maxLength = 100000; }")
    page.say("x" * 2100)
    assert "exceeds 2000 characters" in card(page), card(page)
    assert page.sent and page.sent[-1]["messages"] == ["x" * 2100]


def test_an_empty_message_is_answered_not_ignored(dialog):
    page = dialog
    n = len(page.sent)
    page.say("   ", wait=False)
    page.wait_for_timeout(300)
    assert len(page.sent) == n, "nothing is sent for a blank message"
    assert "type" in card(page).lower() or "describe" in card(page).lower(), card(page)


# ------------------------------------------------------------------ the round trip in the page: tabs, reload, trade-offs

TIGHT = "wedding for 120 guests in Pune on 12 December 2026, budget 60 thousnd, vegetarian"


def test_a_reload_throws_the_draft_away_and_nothing_can_be_built_from_the_empty_page(dialog, browser, server):
    page = dialog
    page.say(FULL + ", one guest is alergic to peanuts")
    assert not can_build(page)
    page.reload(wait_until="networkidle")
    page.click("#new-plan")
    assert page.locator("#understood").inner_text().strip() == "" and not can_build(page)


def test_a_second_tab_keeps_its_own_open_question_and_a_press_there_does_not_touch_the_first(browser, server):
    ctx = browser.new_context(viewport={"width": 1200, "height": 800})
    a, b = ctx.new_page(), ctx.new_page()
    for p in (a, b):
        p.goto(server + "/app", wait_until="networkidle")
        p.click("#new-plan")
    for p in (a, b):
        p.fill("#brief-input", "wedding for 100 guests in Pune on 12 December 2026, budget 20 lakhh")
        p.click("#read-brief")
        p.wait_for_selector("#understood button")
    a.get_by_role("button", name="20 lakh").click()
    a.wait_for_selector("#build")
    a.fill("#brief-input", "sorry, 150 guests")
    a.click("#read-brief")
    a.wait_for_function("() => document.querySelector('#understood').innerText.includes('150')")
    assert not can_build(b) and row(b, "Budget") is None, "tab B still shows its own open question"
    b.get_by_role("button", name="20 lakh").click()
    b.wait_for_selector("#build")
    assert row(b, "Guests") == "100" and row(b, "Budget") == "₹20,00,000", "tab B got only its own answer, not tab A's guest change"
    assert row(a, "Guests") == "150"
    ctx.close()


def test_trade_offs_keep_the_unit_the_user_pressed(dialog):
    page = dialog
    page.say(TIGHT)
    page.get_by_role("button", name="60 thousand").click()
    page.wait_for_selector("#build")
    page.click("#build")
    page.wait_for_selector("text=Show verified trade-offs", timeout=20000)
    page.click("text=Show verified trade-offs")
    page.wait_for_selector("text=Use this option", timeout=20000)
    asked_with, got = page.tradeoffs[-1]
    assert asked_with["draft"]["budget_total"] == 60_000 and asked_with["draft"]["unresolved"] == []
    assert got["request"]["budget_total"] == 60_000, "the typed words alone read as ₹60"


def test_the_card_always_says_when_no_allergy_was_captured_so_a_miss_does_not_look_like_none(dialog):
    page = dialog
    page.say(FULL)
    assert row(page, "Allergies").startswith("None stated") and "write it above" in row(page, "Allergies")
    page.say("one guest is allergic to peanuts")
    assert row(page, "Allergies") == "Peanut"


# ------------------------------------------------------------------ what the reader keeps instead of choosing (tests/test_intake_next_reader.py, on the page)


def test_a_diet_about_some_guests_is_asked_and_only_some_keeps_the_words(dialog):
    page = dialog
    page.say("party in Pune for 40 people on 12 December 2026, budget 1 lakh, a few guests are vegan")
    assert "Is everyone vegan" in card(page) and row(page, "Food") is None and not can_build(page)
    page.get_by_role("button", name="Only some guests").click()
    page.wait_for_selector("#build")
    assert row(page, "Food") is None and "a few guests are vegan" in card(page), "nothing is set for everyone, and the words are kept for the caterer"


def test_a_range_of_dates_offers_both_ends_and_the_pressed_one_is_set(dialog):
    page = dialog
    page.say("wedding in Pune for 100 guests, 4-6 may 2027, budget 3 lakh")
    assert row(page, "Date") is None and not can_build(page)
    assert page.get_by_role("button", name="4 May 2027").count() == 1
    page.get_by_role("button", name="6 May 2027").click()
    page.wait_for_selector("#build")
    assert row(page, "Date") == "6 May 2027"


def test_a_correction_with_no_field_word_is_asked_while_another_question_is_open(dialog):
    page = dialog
    page.say("wedding for 100 guests on 12 December 2026, budget 2 lakh")
    assert "Which city is it in?" in card(page)
    page.say("ok its 150 now since more cousins said yes")
    assert row(page, "Guests") == "100" and page.get_by_role("button", name="150 guests").count() == 1 and not can_build(page)
    page.get_by_role("button", name="150 guests").click()
    page.wait_for_function("() => !document.querySelector('#read-brief').disabled")
    assert row(page, "Guests") == "150" and "Which city is it in?" in card(page) and not can_build(page), "the city question is still open"


def test_an_allergy_that_cannot_be_named_is_asked_before_build_is_offered(dialog):
    page = dialog
    page.say("party in Pune for 40 people on 12 December 2026, budget 1 lakh, my aunt is allergic to kiwi")
    assert not can_build(page) and page.get_by_role("button", name="Keep as written").count() == 1
    page.get_by_role("button", name="Keep as written").click()
    page.wait_for_selector("#build")
    assert "kiwi" in card(page)
