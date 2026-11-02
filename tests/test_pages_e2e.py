"""The landing page and the planner in a real browser: they load without a single blocked request, pass axe (WCAG 2.2 AA and best practice) and a contrast check read from
pixels, and the whole flow works against the real server: brief, understanding, plan, every tab, a change applied as version 2, and a brief that cannot be planned."""

import threading

import pytest

from eventmate.service import EventMate
from eventmate.web.app import create_app
from tests.browser_helpers import BROWSER_ARGS, CHROMIUM, NOISE, axe_violations, fmt_axe, measure_contrast
from tests.conftest import TODAY

pw = pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.skipif(CHROMIUM is None, reason="no Chromium on this machine")

BRIEF = "A 120-person engagement party in Pune on 12 December, budget 2 lakh, vegetarian"
TOO_TIGHT = "A wedding in Pune on 12 December for 120 people, budget 60000, vegetarian"


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    from werkzeug.serving import make_server

    root = tmp_path_factory.mktemp("pages")
    em = EventMate(log_dir=root / "l", plan_dir=root / "p", run_id="e2e")
    server = make_server("127.0.0.1", 0, create_app(em, today_fn=lambda: TODAY, poll_seconds=0, rate_limit_per_minute=10_000), threaded=True)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROMIUM, args=BROWSER_ARGS)
        yield b
        b.close()


@pytest.fixture
def ctx(browser):
    c = browser.new_context(viewport={"width": 1280, "height": 900})
    yield c
    c.close()


def opened(ctx, url):
    page = ctx.new_page()
    seen = []
    page.on("console", lambda m: seen.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") and not any(n in m.text for n in NOISE) else None)
    page.on("pageerror", lambda e: seen.append(f"pageerror: {e}"))
    page.on("requestfailed", lambda r: seen.append(f"failed: {r.url} {r.failure}") if not r.url.endswith(("landing-hero.mp4", "landing-poster.jpg")) else None)
    page.goto(url, wait_until="networkidle")
    return page, seen


def test_the_landing_page_loads_clean_reads_well_and_leads_to_the_planner(base, ctx):
    page, seen = opened(ctx, base + "/")
    assert "to;doh" in page.title() and page.locator("h1").inner_text().startswith("Every event")
    page.wait_for_timeout(1200)  # the entrance animation
    assert axe_violations(page) == [] or pytest.fail(fmt_axe(axe_violations(page)))
    bad = measure_contrast(page, ctx)
    assert bad == [], bad[:5]
    assert page.get_by_role("link", name="Start planning").get_attribute("href").startswith("/app")
    assert seen == [], seen


def test_the_planner_loads_clean_and_is_accessible_before_anything_is_typed(base, ctx):
    page, seen = opened(ctx, base + "/app")
    assert page.get_by_role("heading", name="Your event").is_visible() and page.locator("#plan-empty").is_visible()
    found = axe_violations(page)
    assert found == [], fmt_axe(found)
    bad = measure_contrast(page, ctx)
    assert bad == [], bad[:5]
    assert seen == [], seen


def test_the_whole_flow_from_a_brief_to_a_changed_plan(base, ctx):
    page, seen = opened(ctx, base + "/app")
    page.fill("#brief-input", BRIEF)
    page.click("#read-brief")
    page.wait_for_selector("#build")
    understood = page.inner_text("#understood")
    assert "120" in understood and "Pune" in understood and "₹2,00,000" in understood and "Vegetarian" in understood  # what it read, in Indian number grouping
    page.click("#build")
    page.wait_for_selector("#plan-view h2")
    assert page.inner_text("#plan-view h2") == "Engagement party in Pune"
    assert page.locator(".stamp").inner_text() in ("Verified", "Checked")  # Verified with the solvers present
    assert page.locator(".tile").count() == 4

    for tab, selector, at_least in (("timeline", ".timeline li", 5), ("vendors", ".vendor", 2), ("budget", ".line-item", 3), ("evidence", ".state", 6)):
        page.click(f"#tab-{tab}")
        page.wait_for_selector(selector)
        assert page.locator(selector).count() >= at_least, tab
    page.click("#tab-overview")
    found = axe_violations(page)
    assert found == [], fmt_axe(found)
    bad = measure_contrast(page, ctx)
    assert bad == [], bad[:5]

    page.click("#tab-changes")
    page.fill("#change-input", "now 80 guests")
    page.get_by_role("button", name="Read my change").click()
    page.wait_for_selector("text=Guests become 80")
    found = axe_violations(page)
    assert found == [], fmt_axe(found)
    page.get_by_role("button", name="Apply this change").click()
    page.wait_for_selector("#plan-view h2 + p:has-text('version 2')")  # a selector wait: the strict CSP blocks a string passed to wait_for_function
    assert "80 guests" in page.inner_text("#plan-view .plan-head")
    assert page.locator("#plan-select option").count() >= 2  # the plan is listed so it can be opened again
    assert seen == [], seen


def test_keyboard_users_can_move_between_the_tabs(base, ctx):
    page, _ = opened(ctx, base + "/app")
    page.fill("#brief-input", BRIEF)
    page.click("#read-brief")
    page.wait_for_selector("#build")
    page.click("#build")
    page.wait_for_selector(".tab")
    page.focus("#tab-overview")
    page.keyboard.press("ArrowRight")
    assert page.evaluate("document.activeElement.id") == "tab-timeline" and page.get_attribute("#tab-timeline", "aria-selected") == "true"
    page.keyboard.press("End")
    assert page.evaluate("document.activeElement.id") == "tab-changes"
    page.keyboard.press("Home")
    assert page.evaluate("document.activeElement.id") == "tab-overview"


def test_a_brief_that_cannot_be_planned_says_why_and_offers_verified_trade_offs(base, ctx):
    page, seen = opened(ctx, base + "/app")
    page.fill("#brief-input", TOO_TIGHT)
    page.click("#read-brief")
    page.wait_for_selector("#build")
    page.click("#build")
    page.wait_for_selector("text=What blocked it")
    assert page.inner_text("#plan-view h2") == "No plan yet" and "Not built" in page.inner_text("#plan-view")
    page.click("#tradeoff-btn")
    page.wait_for_selector("#tradeoffs li, #tradeoffs .notice")
    found = axe_violations(page)
    assert found == [], fmt_axe(found)
    if page.locator("#tradeoffs li").count():
        page.get_by_role("button", name="Use this option").first.click()
        page.wait_for_selector(".tabs")
        assert page.locator(".stamp").inner_text() in ("Verified", "Checked")
    assert [m for m in seen if "422" not in m] == [], seen  # the 422 is the server refusing the brief, which the browser logs by itself


def test_nothing_overflows_on_a_phone(base, browser):
    c = browser.new_context(viewport={"width": 375, "height": 800}, device_scale_factor=2)
    try:
        for url in ("/", "/app"):
            page, _ = opened(c, base + url)
            assert page.evaluate("document.documentElement.scrollWidth") <= 376, url
        page.fill("#brief-input", BRIEF)
        page.click("#read-brief")
        page.wait_for_selector("#build")
        page.click("#build")
        page.wait_for_selector(".tabs")
        assert page.evaluate("document.documentElement.scrollWidth") <= 376
    finally:
        c.close()


def test_the_workspace_dialog_opens_exports_and_closes(base, ctx, tmp_path):
    page, seen = opened(ctx, base + "/app")
    page.click("#workspace-open")
    assert page.locator("#workspace-dialog").is_visible() and page.locator("#shared-only").is_hidden()  # a local server has no recovery phrase to make
    with page.expect_download() as d:
        page.click("#export-data")
    assert d.value.suggested_filename == "todoh-export.zip"
    page.click("#workspace-close")
    assert page.locator("#workspace-dialog").is_hidden()
    assert seen == [], seen
