"""The planning page (/plan), in a real browser: an idea is read, checked, and the findings run on the server's own sourcing flow. The vendor a person adds is the one someone else already asked, so
the earlier quote is shown, marked as someone else's and for orientation only, and it stays while their own request is out. Fake channel, nobody's address: nothing leaves the machine."""

import threading

import pytest

from eventmate.messaging.conversations import SendPolicy
from eventmate.service import EventMate
from eventmate.web.app import create_app
from tests.browser_helpers import BROWSER_ARGS, CHROMIUM, NOISE
from tests.conftest import TODAY
from tests.test_brief_sourcing import MYSURU
from tests.test_conversations import Clock, FakeChannel
from tests.test_shared_quotes import ADDRESS, own_vendor, person, quoted

pw = pytest.importorskip("playwright.sync_api")


def test_the_page_and_its_built_files_are_served(tmp_path):
    em = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="plan-static")
    c = create_app(em, today_fn=lambda: TODAY, poll_seconds=0).test_client()
    page = c.get("/plan")
    assert page.status_code == 200 and 'id="root"' in page.get_data(as_text=True)
    assert c.get("/static/plan.js").status_code == 200 and c.get("/static/plan.css").status_code == 200
    assert c.get("/static/plan-instrument-serif-italic.ttf").status_code == 200


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    from werkzeug.serving import make_server

    root = tmp_path_factory.mktemp("plan_page")
    first = person(root, "first")  # someone else, in a workspace of their own, asked the same caterer and confirmed its price
    quoted(*first, own_vendor(first[0], first[3]))
    chans, clock = {"email": FakeChannel("email"), "whatsapp": FakeChannel("whatsapp", False, False)}, Clock()
    em = EventMate(log_dir=root / "ui" / "l", plan_dir=root / "ui" / "p", run_id="plan-page", channels=chans, require_symbolic=False, shared_dir=root / "shared")
    em.outreach_store.now = em.conversations.now = em.quote_confirmations.now = clock
    em.conversations.policy = SendPolicy(per_vendor_per_day=20, per_day=200, whatsapp_gap_s=0)
    srv = make_server("127.0.0.1", 0, create_app(em, today_fn=lambda: TODAY, poll_seconds=0, rate_limit_per_minute=10_000), threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", em
    srv.shutdown()


@pytest.mark.skipif(CHROMIUM is None, reason="no Chromium on this machine")
def test_an_earlier_quote_from_someone_else_stays_in_view_while_the_fresh_one_is_awaited(stack):
    url, em = stack
    with pw.sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROMIUM, args=BROWSER_ARGS)
        page = b.new_context(viewport={"width": 1440, "height": 900}).new_page()
        problems = []
        page.on("console", lambda m: problems.append(m.text) if m.type in ("error", "warning") and not any(n in m.text for n in NOISE) else None)
        page.on("pageerror", lambda e: problems.append(str(e)))
        page.goto(url + "/plan", wait_until="networkidle")
        page.click(".skip")
        page.wait_for_selector("#idea:focus")  # the scroll has arrived at the box
        page.fill("#idea", MYSURU)
        page.click("#idea-form button[type=submit]")
        page.wait_for_selector(".review-room:not([inert])")
        assert "Mysuru" in page.inner_text(".review-room") and "₹60,000" in page.inner_text(".review-room")
        page.click("#answer-yes")
        page.click("#review-done")
        page.wait_for_selector(".finding-room:not([inert])")
        page.click("[data-node=vendors]")
        page.fill("input[placeholder^='Recorded']", "Asha")
        assert page.locator(".note:has-text('Caterer')").count(), "a vegetarian brief needs a caterer, so it is asked for without being added"
        page.locator(".note:has-text('Caterer') >> text=Add one you know").first.click()
        page.fill(".inline-form input[type=text]", "Sri Hari Caterers (Mysuru)")
        page.fill(".inline-form input[type=email]", ADDRESS.upper())
        page.click(".inline-form button[type=submit]")
        card = page.locator("[data-vendor]").first
        card.wait_for()
        text = card.inner_text()
        assert "EARLIER QUOTE, TO SOMEONE ELSE" in text and "₹650 per plate" in text and "orientation only" in text
        assert "Priya" not in text and "Asha" not in text.split("ADDED BY")[0], "nobody's name travels with the quote"
        page.click("[data-node=message]")
        page.click("text=Write one request per business")
        page.click("text=Approve exactly this and send")
        page.wait_for_selector("text=/Approved, sending|· Sent/")
        page.click("[data-node=vendors]")
        page.wait_for_selector("text=Asking for a fresh quote")
        text = page.locator("[data-vendor]").first.inner_text()
        assert "₹650 per plate" in text, "the earlier quote stays while the new one is awaited"
        assert not problems, problems
        b.close()


@pytest.mark.skipif(CHROMIUM is None, reason="no Chromium on this machine")
@pytest.mark.parametrize("width,height", [(1440, 900), (390, 800)])
def test_a_new_person_cannot_skip_the_check_and_sees_the_facts_before_the_question(stack, width, height):
    url, _ = stack
    with pw.sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROMIUM, args=BROWSER_ARGS)
        page = b.new_context(viewport={"width": width, "height": height}).new_page()
        page.goto(url + "/plan", wait_until="networkidle")
        page.click(".skip")
        page.wait_for_selector("#idea:focus")
        page.fill("#idea", MYSURU)
        page.click("#idea-form button[type=submit]")
        page.wait_for_selector(".review-room:not([inert])")
        page.wait_for_timeout(900)
        assert not page.is_enabled("#review-done"), "Done waits for a Yes"
        assert page.locator("#done-why").inner_text().startswith("Say Yes")
        assert "Guests" in page.locator(".json-key").all_inner_texts() and not any('"' in k for k in page.locator(".json-key").all_inner_texts()), "plain words, not JSON keys"
        facts_top = page.locator(".json-lines").bounding_box()["y"]
        question_top = page.locator(".review-question").bounding_box()["y"]
        if width < 700:
            assert facts_top < question_top, "on a phone the facts come before the question about them"
        page.click("#answer-yes")
        assert page.is_enabled("#review-done")
        b.close()


def _to_review(page, url):
    page.goto(url + "/plan", wait_until="networkidle")
    page.click(".skip")
    page.wait_for_selector("#idea:focus")
    page.fill("#idea", MYSURU)
    page.press("#idea", "Enter")  # Enter sends, as the helper line says
    page.wait_for_selector(".review-room:not([inert])")
    page.wait_for_timeout(700)


@pytest.mark.skipif(CHROMIUM is None, reason="no Chromium on this machine")
def test_the_place_survives_a_reload_and_back_and_forward_walk_the_steps(stack):
    url, _ = stack
    with pw.sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROMIUM, args=BROWSER_ARGS)
        page = b.new_context(viewport={"width": 1440, "height": 900}).new_page()
        _to_review(page, url)
        assert page.url.endswith("#review")
        page.reload(wait_until="networkidle")
        page.wait_for_selector(".review-room:not([inert])")
        assert "Mysuru" in page.locator(".json-lines").inner_text(), "a reload picks the brief up from the server"
        page.click("#answer-yes")
        page.click("#review-done")
        page.wait_for_selector(".finding-room:not([inert])")
        assert page.url.endswith("#findings")
        page.locator(".thought-key button").nth(1).click()
        assert page.url.endswith("#findings/vendors")
        page.get_by_role("button", name="Next: The message →").click()
        assert page.url.endswith("#findings/message")
        page.go_back()
        assert page.url.endswith("#findings/vendors") and page.locator(".node-page").inner_text().startswith("WHO TO ASK")
        page.go_back()
        page.go_back()
        page.wait_for_selector(".review-room:not([inert])")
        page.go_forward()
        page.wait_for_selector(".finding-room:not([inert])")
        page.goto(url + "/plan#findings/compare", wait_until="networkidle")  # a deep link, in the same visit
        page.wait_for_selector(".finding-room:not([inert])")
        assert page.locator(".node-page").inner_text().startswith("SIDE BY SIDE")
        b.close()


@pytest.mark.skipif(CHROMIUM is None, reason="no Chromium on this machine")
def test_a_brief_the_server_no_longer_holds_sends_the_person_back_to_the_start_quietly(stack):
    url, _ = stack
    with pw.sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROMIUM, args=BROWSER_ARGS)
        page = b.new_context(viewport={"width": 1440, "height": 900}).new_page()
        page.goto(url + "/plan", wait_until="networkidle")
        page.evaluate("sessionStorage.setItem('todoh.brief', JSON.stringify({stagedId: '0'.repeat(24), words: 'gone'}))")
        page.goto(url + "/plan?again=1#review", wait_until="networkidle")  # a different address: a real load, not a hash change
        page.wait_for_timeout(800)
        assert page.locator(".review-room").count() == 0 and page.locator(".opening-root:not([inert])").count() == 1
        assert page.evaluate("sessionStorage.getItem('todoh.brief')") is None
        b.close()
