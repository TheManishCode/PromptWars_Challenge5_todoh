"""The landing page and the dashboard in a real browser: they load without a single blocked request, pass axe (WCAG 2.2 AA and best practice) and a contrast check read from
pixels, and the whole flow works against the real server: new plan, calendar, tiles, popover, assistant, a change applied as version 2, an approval shown as its exact text and
hash, the plan dossier, and a brief that cannot be planned."""

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
def server(tmp_path_factory):
    from werkzeug.serving import make_server

    root = tmp_path_factory.mktemp("pages")
    em = EventMate(log_dir=root / "l", plan_dir=root / "p", run_id="e2e")
    srv = make_server("127.0.0.1", 0, create_app(em, today_fn=lambda: TODAY, poll_seconds=0, rate_limit_per_minute=10_000), threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", em
    srv.shutdown()


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROMIUM, args=BROWSER_ARGS)
        yield b
        b.close()


@pytest.fixture
def ctx(browser):
    c = browser.new_context(viewport={"width": 1600, "height": 950})
    yield c
    c.close()


def opened(ctx, url):
    page = ctx.new_page()
    seen = []
    page.on("console", lambda m: seen.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") and not any(n in m.text for n in NOISE) else None)
    page.on("pageerror", lambda e: seen.append(f"pageerror: {e}"))
    page.on("requestfailed", lambda r: seen.append(f"failed: {r.url} {r.failure}") if not (r.url.endswith("/api/pulse/stream") and "ERR_ABORTED" in str(r.failure)) else None)  # closing the event stream is ours, on purpose
    page.goto(url, wait_until="networkidle")
    return page, seen


def new_plan(page, text=BRIEF):
    page.click("#new-plan")
    page.fill("#brief-input", text)
    page.click("#read-brief")
    page.wait_for_selector("#build")
    page.click("#build")
    page.wait_for_selector("#progress li", timeout=5000)  # the real pipeline stages arrive over the event stream while the plan is made
    assert page.locator("#progress li").count() == 10
    page.wait_for_selector("#eventline:has-text('version 1')")


def clean(page, ctx, seen):
    found = axe_violations(page)
    assert found == [], fmt_axe(found)
    bad = measure_contrast(page, ctx)
    assert bad == [], bad[:6]
    assert seen == [], seen


def test_the_landing_page_loads_clean_reads_well_and_leads_to_the_dashboard(server, ctx):
    page, seen = opened(ctx, server[0] + "/")
    assert "to;doh" in page.title() and page.locator("h1").inner_text().startswith("Every event")
    page.wait_for_timeout(1200)
    assert axe_violations(page) == []
    assert measure_contrast(page, ctx) == []
    assert page.get_by_role("link", name="Start planning").get_attribute("href").startswith("/app")
    assert seen == [], seen


def test_the_empty_dashboard_loads_clean_and_says_what_to_do(server, ctx):
    page, seen = opened(ctx, server[0] + "/app")
    assert page.locator("#eventline").inner_text().startswith("No plan yet")
    assert page.locator("#p-venue .empty-d").is_visible() and page.locator("#appr .empty-d").is_visible()
    assert page.locator("[data-find]").first.is_disabled() and page.locator("#get-ideas").is_disabled()
    clean(page, ctx, seen)


def test_a_new_plan_fills_the_calendar_the_tiles_and_the_budget_from_the_real_plan(server, ctx):
    page, seen = opened(ctx, server[0] + "/app")
    new_plan(page)
    assert "Engagement party" in page.inner_text("#eventline") and "Pune" in page.inner_text("#eventline") and "verified" in page.inner_text("#eventline")
    assert page.inner_text("#cal-title") == "December 2026"
    assert page.locator("#cal .cell.event-day").inner_text().startswith("12")  # the event day is marked
    assert page.locator("#cal .ev").count() == 3 and page.locator("#cal .more").inner_text() == "+1 more"  # a cell shows three events, then says how many more
    page.wait_for_selector("#p-venue .place")
    assert page.locator("#p-venue .place").count() >= 1 and "₹" in page.inner_text("#p-venue") and page.locator("#p-caterer .place").count() >= 1
    assert page.locator("#legend li").count() == 3 and "₹2,00,000 cap" in page.inner_text("#n-budget")
    assert page.locator("#evcounts li").count() == 6  # the six states
    page.click("#cal .ev >> nth=0")
    page.wait_for_selector("#pop:not([hidden])")
    facts = page.inner_text("#pop-facts")
    assert "Source:" in facts and page.locator("#pop-facts .chip").count() >= 1  # every fact says where it came from and how sure we are
    page.keyboard.press("Escape")
    clean(page, ctx, seen)


def test_week_and_day_views_place_the_timeline_by_the_hour(server, ctx):
    page, seen = opened(ctx, server[0] + "/app")
    page.wait_for_selector("#plan-select option[value]:not([value=''])", state="attached")
    page.select_option("#plan-select", index=0)
    page.wait_for_selector("#eventline:has-text('Engagement')")
    page.click("[data-view=week]")
    page.wait_for_selector("#cal .blk")
    assert page.locator("#cal .blk").count() >= 3
    page.click("[data-view=day]")
    assert page.locator("#cal .wk__day").count() == 1
    page.click("[data-view=month]")
    page.uncheck("[data-cat=venue]")
    assert page.locator("#cal .ev[data-cat=venue]").count() == 0  # the category filter works
    page.check("[data-cat=venue]")
    clean(page, ctx, seen)


def test_the_assistant_answers_from_the_plan_and_a_change_becomes_version_two(server, ctx):
    page, seen = opened(ctx, server[0] + "/app")
    page.wait_for_selector("#plan-select option[value]:not([value=''])", state="attached")
    page.fill("#ask-in", "what does the caterer cost?")
    page.click("#ask .bar__mic")
    page.wait_for_selector("#msgs .msg--ai:not(:has-text('Thinking'))")
    assert page.locator("#msgs .msg--me").count() == 1 and page.locator("#msgs .msg--ai").count() >= 1
    page.click(".modes [data-mode=change]")
    page.fill("#ask-in", "now 80 guests")
    page.click("#ask .bar__mic")
    page.wait_for_selector("#msgs >> text=Guests become 80")
    page.get_by_role("button", name="Apply this change").click()
    page.wait_for_selector("#eventline:has-text('version 2')")
    assert "80 guests" in page.inner_text("#eventline")
    found = axe_violations(page)
    assert found == [], fmt_axe(found)
    assert seen == [], seen


def test_an_approval_is_shown_as_its_exact_text_and_hash_and_can_be_denied(server, ctx):
    from eventmate.schemas import VendorCategory

    base, em = server
    plan_id = em.store.list_plans()[0]
    d = em.draft_outreach(plan_id, VendorCategory.CATERER, "email", "quotes@example.test", "Asha")
    page, seen = opened(ctx, base + "/app")
    page.wait_for_selector("#plan-select option[value]:not([value=''])", state="attached")
    page.select_option("#plan-select", plan_id)
    page.wait_for_selector("#appr li:has-text('Send')")
    assert "approval" in page.inner_text("#n-appr") and page.locator("#cal .ev, #cal .blk").count() >= 1
    page.click("#review-appr")
    card = page.locator("#msgs .exact")
    card.wait_for()
    assert d.body.split("\n")[0] in card.inner_text() and f"sha256:{d.content_hash}" in page.inner_text("#msgs")
    found = axe_violations(page)
    assert found == [], fmt_axe(found)
    page.get_by_role("button", name="Deny").click()
    page.wait_for_selector("#msgs >> text=Denied. Nothing was sent.")
    assert [x.status for x in em.outreach(plan_id)] == ["denied"]
    assert seen == [], seen


def test_the_plan_dossier_stacks_five_sheets_and_a_click_brings_one_forward(server, ctx):
    page, seen = opened(ctx, server[0] + "/app")
    page.wait_for_selector("#plan-select option[value]:not([value=''])", state="attached")
    page.select_option("#plan-select", index=0)
    page.wait_for_selector("#eventline:has-text('Engagement')")
    page.click("#open-paper")
    page.wait_for_selector(".sheet")
    page.wait_for_timeout(900)
    assert page.locator(".sheet").count() == 5 and page.locator("#stage-l").inner_text().startswith("PLAN DOSSIER")
    page.locator("[data-sheet=budget]").click()
    page.wait_for_selector("#note-card:not([hidden])")
    assert page.inner_text("#note-t") == "Budget" and page.locator("[data-sheet=budget].is-front").count() == 1
    found = axe_violations(page)
    assert found == [], fmt_axe(found)  # the stacked, tilted sheets and the note card are still readable and named
    page.keyboard.press("ArrowRight")
    page.wait_for_selector("#note-t:has-text('Questions')")  # the note card swaps a moment after the sheet
    page.keyboard.press("Escape")
    page.keyboard.press("Escape")
    assert page.locator("#stage").is_hidden()
    assert seen == [], seen


def test_a_brief_that_cannot_be_planned_says_why_and_offers_verified_trade_offs(server, ctx):
    page, seen = opened(ctx, server[0] + "/app")
    page.click("#new-plan")
    page.fill("#brief-input", TOO_TIGHT)
    page.click("#read-brief")
    page.wait_for_selector("#build")
    page.click("#build")
    page.wait_for_selector("text=nothing was stored")
    page.click("text=Show verified trade-offs")
    page.wait_for_selector("#understood .card, #understood .notice:has-text('No single change')")
    found = axe_violations(page)
    assert found == [], fmt_axe(found)
    if page.locator("#understood .card button:has-text('Use this option')").count():
        page.locator("#understood .card button:has-text('Use this option')").first.click()
        page.wait_for_selector("#eventline:has-text('version')")
    assert [m for m in seen if "422" not in m] == [], seen


def test_nothing_overflows_on_a_phone_and_the_panels_slide_in(server, browser):
    c = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
    try:
        page, seen = opened(c, server[0] + "/app")
        page.wait_for_selector("#plan-select option[value]:not([value=''])", state="attached")
        assert page.evaluate("document.documentElement.scrollWidth") <= 391
        page.click("#toggle-right")
        assert page.locator("#right").is_visible() and page.get_attribute("#toggle-right", "aria-expanded") == "true"
        page.click("#close-right")
        page.click("#toggle-left")
        assert page.locator("#left").is_visible()
        assert page.evaluate("document.documentElement.scrollWidth") <= 391
        assert [m for m in seen if "422" not in m] == [], seen
    finally:
        c.close()


def test_the_workspace_dialog_opens_exports_and_closes(server, ctx):
    page, seen = opened(ctx, server[0] + "/app")
    page.click("#open-workspace")
    assert page.locator("#workspace-dialog").is_visible() and page.locator("#shared-only").is_hidden()
    found = axe_violations(page)
    assert found == [], fmt_axe(found)
    with page.expect_download() as d:
        page.click("#export-data")
    assert d.value.suggested_filename == "todoh-export.zip"
    page.click("#workspace-close")
    assert page.locator("#workspace-dialog").is_hidden()
    assert seen == [], seen


def open_vendor_event(page):
    """Open the popover of an event that books a vendor; the others do not offer the quote control."""
    for i in range(page.locator("#cal .ev").count()):  # the events that book a vendor offer the control; the others do not
        page.click(f"#cal .ev >> nth={i}")
        page.wait_for_selector("#pop:not([hidden])")
        if page.locator("#pop-quote:not([hidden])").count():
            break
        page.keyboard.press("Escape")
    else:
        page.click("#cal .more")  # a busy day folds some events away: open the day and look there
        for i in range(page.locator("#cal .ev").count()):
            page.click(f"#cal .ev >> nth={i}")
            page.wait_for_selector("#pop:not([hidden])")
            if page.locator("#pop-quote:not([hidden])").count():
                break
            page.keyboard.press("Escape")
    assert page.locator("#pop-quote:not([hidden])").count() == 1


def test_a_quote_file_added_to_a_vendor_event_shows_its_lines_as_extracted_and_unconfirmed(server, ctx, tmp_path):
    page, seen = opened(ctx, server[0] + "/app")
    new_plan(page)
    open_vendor_event(page)
    quote = tmp_path / "quote.csv"
    quote.write_text("Item,Qty,Rate,Total\nVeg buffet,120,450,54000\nStage,1,,120000\n", encoding="utf-8")
    page.set_input_files("#pop-quote input[type=file]", str(quote))
    page.wait_for_selector("#pop-quote .pop__line")
    text = page.inner_text("#pop-quote")
    assert page.locator("#pop-quote .pop__line").count() == 2 and "Veg buffet" in text and "₹54,000" in text and "not confirmed" in text
    assert page.locator("#pop-quote .pop__line .chip[data-s=extracted]").count() == 2
    page.locator("#pop-quote .pop__line button >> nth=0").click()  # the user's tap is what turns a line into a recorded quote
    page.wait_for_selector("#pop-quote .pop__line .chip[data-s=user_confirmed]")
    assert "recorded as a quote for 14 days" in page.inner_text("#pop-quote") and page.locator("#pop-quote .pop__line .chip[data-s=extracted]").count() == 1
    assert "One line of the quote" in text and "The plan priced this vendor at" in page.inner_text("#pop-quote") and "unchanged until you re-plan" in page.inner_text("#pop-quote")
    page.click("#pop-quote >> text=Re-plan with this price")  # the plan takes the confirmed price as a new version
    page.wait_for_selector("#pop-quote >> text=Re-planned: version 2")
    page.keyboard.press("Escape")  # reopen: the file read a moment ago is listed, and the line the user confirmed shows as confirmed
    open_vendor_event(page)
    page.wait_for_selector("#pop-quote .pop__quote-out .pop__doc")
    assert "Quote files already read" in page.inner_text("#pop-quote") and page.locator("#pop-quote .pop__quote-out .pop__line .chip[data-s=user_confirmed]").count() == 1
    page.set_input_files("#pop-quote input[type=file]", files=[{"name": "bad.exe", "mimeType": "application/octet-stream", "buffer": b"MZ"}])
    page.wait_for_selector("#pop-quote .notice.bad")
    assert "PDF, Word" in page.inner_text("#pop-quote")  # a refused file says what would be accepted
    page.keyboard.press("Escape")
    seen[:] = [s for s in seen if "422" not in s]  # the refusal above is a deliberate 422
    clean(page, ctx, seen)


def test_lite_mode_drops_the_video_and_every_animation_and_still_passes_axe(server, ctx):
    url, _ = server
    for path in ("/?lite=1", "/app?lite=1"):
        page, seen = opened(ctx, url + path)
        assert page.evaluate("document.documentElement.classList.contains('lite')")
        assert page.locator("video").count() == 0
        assert page.evaluate("document.getAnimations().filter(a => a.playState === 'running').length") == 0
        assert axe_violations(page) == [] and seen == []
        page.close()


def test_a_stated_allergy_shows_on_the_review_card_and_keeps_the_plan_unconfirmed(server, ctx):
    page, seen = opened(ctx, server[0] + "/app")
    page.click("#new-plan")
    page.fill("#brief-input", BRIEF + ", my mother is allergic to peanuts")
    page.click("#read-brief")
    page.wait_for_selector("#build")
    assert "Allergies" in page.inner_text("#brief-dialog") and "peanut" in page.inner_text("#brief-dialog").lower()
    page.click("#build")
    page.wait_for_selector("#eventline:has-text('version 1')")
    page.wait_for_selector("text=allergy not confirmed with the caterer")
    page.wait_for_selector("#brief-dialog", state="hidden")  # the closing dialog is half transparent for a moment, which the pixel contrast meter would read
    page.wait_for_timeout(600)
    clean(page, ctx, seen)


def test_an_allergy_the_reader_cannot_name_is_shown_to_the_user_and_keeps_the_plan_unconfirmed(server, ctx):
    page, seen = opened(ctx, server[0] + "/app")
    page.click("#new-plan")
    page.fill("#brief-input", BRIEF + ", my friend is allergic to kiwi")
    page.click("#read-brief")
    page.wait_for_selector("#build")
    dialog = page.inner_text("#brief-dialog")
    assert "Allergy to clarify" in dialog and "kiwi" in dialog and "could not name" in dialog
    page.click("#build")
    page.wait_for_selector("#eventline:has-text('version 1')")
    page.wait_for_selector("text=allergy or food restriction not understood")
    page.wait_for_selector("#brief-dialog", state="hidden")
    page.wait_for_timeout(600)
    clean(page, ctx, seen)


def test_businesses_found_on_openstreetmap_and_google_carry_their_attribution_on_screen(server, ctx):
    """ODbL 1.0 asks for "© OpenStreetMap contributors" wherever its data appears; Google Maps Platform asks for its attribution next to Places results."""
    from eventmate.schemas import VendorCategory

    url, em = server
    page, seen = opened(ctx, url + "/app")
    new_plan(page)
    osm = em.externals.add("osm:node/1", "Lakeside Gardens", "Pune", VendorCategory.VENUE, "osm", source_url="https://www.openstreetmap.org/node/1")
    gplaces = em.externals.add("gplaces:ChIJabcdefgh12345", "Orchid Hall", "Pune", VendorCategory.VENUE, "gplaces", source_url="https://maps.google.com/?cid=1")
    for plan_id in em.store.list_plans():  # the module shares one server: whichever plan the page opens carries them
        em.shortlist.add_entry(plan_id, VendorCategory.VENUE, osm.vendor_id)
        em.shortlist.add_entry(plan_id, VendorCategory.VENUE, gplaces.vendor_id)
    page.reload(wait_until="networkidle")
    page.wait_for_selector("text=Lakeside Gardens", timeout=8000)
    body = page.inner_text("body")
    assert "© OpenStreetMap contributors (ODbL)" in body and "Powered by Google" in body


def test_the_sending_controls_pause_everything_list_do_not_contact_addresses_and_lift_them(server, ctx):
    url, em = server
    page, seen = opened(ctx, url + "/app")
    new_plan(page)
    page.wait_for_selector("#sending-chip:has-text('Sending on')")
    page.click("#sending-chip")
    page.wait_for_selector("#sending-dialog[open]")
    assert "Sending is on" in page.inner_text("#sending-state") and page.is_disabled("#perm-revoke")
    page.click("#sending-toggle")
    page.wait_for_selector("#sending-chip:has-text('Sending paused')")
    assert "Sending is paused" in page.inner_text("#sending-state") and em.outreach_store.paused is True and em.outreach_store.killed is True  # the real switches, not a label
    page.fill("#sup-value", "Stop.Me@Example.com")
    page.fill("#sup-by", "Meera")
    page.fill("#sup-note", "asked us to stop")
    page.click("#sup-form button[type=submit]")
    page.wait_for_selector("#sup-list .sup:has-text('stop.me@example.com')")
    assert em.is_suppressed("email", "stop.me@example.com") is not None
    page.wait_for_timeout(300)
    found = axe_violations(page)
    assert found == [], fmt_axe(found)
    page.click("#sup-list .sup >> text=Lift")
    page.fill("#sup-list .sup-lift input[aria-label='Your name']", "Meera")
    page.fill("#sup-list .sup-lift input[aria-label='Why lift it']", "they wrote back asking for a quote")
    page.check("#sup-list .sup-lift input[type=checkbox]")
    page.click("#sup-list .sup-lift button[type=submit]")
    page.wait_for_selector("#sup-list .sup:has-text('lifted by Meera')")
    assert em.is_suppressed("email", "stop.me@example.com") is None
    page.click("#sending-toggle")
    page.wait_for_selector("#sending-chip:has-text('Sending on')")
    assert em.outreach_store.killed is False and em.outreach_store.paused is False
    assert seen == [], seen
