"""The staged brief in a real browser: values typed onto the card, a reload that finds the brief again, and a build that runs on the queue while the page follows it."""

import threading

import pytest

from eventmate.service import EventMate
from eventmate.web.app import create_app
from tests.browser_helpers import BROWSER_ARGS, CHROMIUM, NOISE
from tests.conftest import TODAY

pw = pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.skipif(CHROMIUM is None, reason="no Chromium on this machine")

BRIEF = "wedding for 100 guests in Pune on 12 December 2026, budget 2 lakh"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    from werkzeug.serving import make_server

    root = tmp_path_factory.mktemp("staging_page")
    em = EventMate(log_dir=root / "l", plan_dir=root / "p", run_id="staging-page")
    srv = make_server("127.0.0.1", 0, create_app(em, today_fn=lambda: TODAY, poll_seconds=0, rate_limit_per_minute=10_000), threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", em
    srv.shutdown()


@pytest.fixture
def page(server):
    with pw.sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROMIUM, args=BROWSER_ARGS)
        pg = b.new_context(viewport={"width": 1440, "height": 900}).new_page()
        pg.problems = []
        pg.on("console", lambda m: pg.problems.append(m.text) if m.type in ("error", "warning") and not any(n in m.text for n in NOISE) else None)
        pg.on("pageerror", lambda e: pg.problems.append(str(e)))
        pg.goto(server[0] + "/app", wait_until="networkidle")
        pg.click("#new-plan")
        pg.fill("#brief-input", BRIEF)
        pg.click("#read-brief")
        pg.wait_for_selector("#build")
        yield pg
        b.close()


def card(page):
    return page.inner_text("#understood")


def test_a_value_typed_onto_the_card_replaces_the_staged_one_and_a_bad_one_is_refused_in_place(page, server):
    page.get_by_role("button", name="Edit guests").click()
    page.fill("#understood input[aria-label='Guests']", "90")
    page.get_by_role("button", name="Save").click()
    page.wait_for_function("() => /90/.test(document.querySelector('#understood').innerText)")
    assert "Guests changed from 100 to 90" in card(page)
    page.get_by_role("button", name="Edit date").click()
    page.fill("#understood input[aria-label='Date']", "2020-01-01")
    page.get_by_role("button", name="Save").click()
    page.wait_for_selector("#understood [role=alert]:not([hidden])")
    assert "in the past" in page.inner_text("#understood [role=alert]")
    assert [p for p in page.problems if "400" not in p] == [], page.problems  # the refused edit is the one request the browser logs


def test_a_reloaded_page_can_pick_the_staged_brief_up_again_and_it_is_the_edited_one(page, server):
    page.get_by_role("button", name="Edit guests").click()
    page.fill("#understood input[aria-label='Guests']", "70")
    page.get_by_role("button", name="Save").click()
    page.wait_for_function("() => /70/.test(document.querySelector('#understood').innerText)")
    staged = server[1].staging
    (latest,) = [r for r in (staged.get(p.name[len("staged-"):-len(".jsonl")]) for p in (staged.root).glob("staged-*.jsonl")) if r and r.draft.guest_count == 70]
    got = page.evaluate("async (id) => (await (await fetch('/api/intake/' + id)).json())", latest.staged_id)
    assert got["draft"]["guest_count"] == 70 and got["state"] == "pending_validation" and got["ready"]


def test_done_builds_on_the_queue_and_the_page_follows_it_to_the_plan(page, server):
    page.click("#build")
    page.wait_for_selector("#eventline:has-text('version 1')", timeout=30000)
    assert not page.problems, page.problems
    states = [r.state for r in (server[1].staging.get(p.name[len("staged-"):-len(".jsonl")]) for p in server[1].staging.root.glob("staged-*.jsonl")) if r]
    assert "done" in states
