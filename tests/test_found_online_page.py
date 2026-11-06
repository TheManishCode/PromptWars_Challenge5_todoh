"""The "Found online" card in a real browser: listings from Places, each with the vendor's earlier quote under it, dated and labelled as possibly out of date.
Places is answered by a fake here; nothing leaves the machine."""

import json
import threading
from datetime import UTC, date, datetime

import pytest

from eventmate.integrations.places import PlacesSource
from eventmate.schemas import ObservationSource, VendorCategory
from eventmate.service import EventMate
from eventmate.web.app import create_app
from tests.browser_helpers import BROWSER_ARGS, CHROMIUM, NOISE
from tests.conftest import TODAY

pw = pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.skipif(CHROMIUM is None, reason="no Chromium on this machine")

PLACE = "ChIJabcdefgh12345"


def fake_places(url, headers, data, timeout):
    return json.dumps({"places": [{"id": PLACE, "displayName": {"text": "Sri Hari Caterers"}, "formattedAddress": "Mysuru", "rating": 4.8, "userRatingCount": 120, "googleMapsUri": "https://maps.google.com/?cid=1"},
                                  {"id": "ChIJnoquote99999", "displayName": {"text": "Quiet Caterers"}, "formattedAddress": "Mysuru"}]}).encode()


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    from werkzeug.serving import make_server

    root = tmp_path_factory.mktemp("found_page")
    em = EventMate(log_dir=root / "l", plan_dir=root / "p", run_id="found-page")
    em.places = PlacesSource(api_key="k" * 30, transport=fake_places)
    biz = em.externals.add("gplaces:" + PLACE, "Sri Hari Caterers", "Mysuru", VendorCategory.CATERER, "gplaces", source_url="https://maps.google.com/?cid=1")
    em.observe_quote(biz.vendor_id, VendorCategory.CATERER, source_type=ObservationSource.VENDOR_QUOTE, entered_by="asha", observed_at=datetime(2026, 10, 20, tzinfo=UTC),
                     expires_at=date(2026, 11, 20), confidence=0.9, evidence_ref="email", amount_inr=650, basis="per_plate", guest_count=40)
    srv = make_server("127.0.0.1", 0, create_app(em, today_fn=lambda: TODAY, poll_seconds=0, rate_limit_per_minute=10_000), threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_a_listing_shows_the_vendors_earlier_quote_and_why_it_may_be_old(server):
    with pw.sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROMIUM, args=BROWSER_ARGS)
        page = b.new_context(viewport={"width": 1440, "height": 900}).new_page()
        problems = []
        page.on("console", lambda m: problems.append(m.text) if m.type in ("error", "warning") and not any(n in m.text for n in NOISE) else None)
        page.goto(server + "/app", wait_until="networkidle")
        page.click("#new-plan")
        page.fill("#brief-input", "birthday for 120 guests in Mysuru on 14 November 2026, veg, budget 2 lakh")
        page.click("#read-brief")
        page.wait_for_selector("#found li[data-place='%s'] small" % PLACE)
        text = page.inner_text("#found")
        assert "Earlier quote: " in text and "650 per plate for 40 guests" in text
        assert "40 guests, not 120" in text and "Confirm with the vendor" in text
        assert page.locator("#found li[data-place='ChIJnoquote99999'] small").count() == 0, "a vendor that never quoted has no note"
        assert page.locator("#build").count() == 0
        assert not problems, problems
        b.close()


def open_brief(server, text, route=None):
    p = pw.sync_playwright().start()
    b = p.chromium.launch(executable_path=CHROMIUM, args=BROWSER_ARGS)
    page = b.new_context(viewport={"width": 1440, "height": 900}).new_page()
    if route:
        page.route("**/api/places*", route)
    page.goto(server + "/app", wait_until="networkidle")
    page.click("#new-plan")
    page.fill("#brief-input", text)
    page.click("#read-brief")
    page.wait_for_selector("#found")
    return p, b, page


BRIEF = "birthday for 120 guests in Mysuru on 14 November 2026, veg, budget 2 lakh"


def test_a_visitor_stuck_on_an_uncovered_city_is_offered_the_cities_that_work(server):
    p, b, page = open_brief(server, BRIEF)
    try:
        assert page.locator("#found button", has_text="Pune").count() == 1
        page.locator("#found button", has_text="Pune").click()
        page.wait_for_selector("#build")
        assert page.locator("#found").count() == 0 and "Pune" in page.inner_text("#understood")
    finally:
        b.close(); p.stop()


def test_a_failed_lookup_never_shows_the_servers_wording_and_can_be_retried(server):
    answers = iter([(503, "places are not configured: add GOOGLE_PLACES_API_KEY to the .env file")] * 3 + [(502, "the Places quota is used up")] * 3)

    def broken(route):
        status, msg = next(answers, (502, "x"))
        route.fulfill(status=status, content_type="application/json", body=json.dumps({"error": msg}))

    p, b, page = open_brief(server, BRIEF, broken)
    try:
        page.wait_for_selector("#found >> text=not switched on here")
        text = page.inner_text("#found")
        assert ".env" not in text and "GOOGLE" not in text and "quota" not in text
        assert page.locator("#build").count() == 0
    finally:
        b.close(); p.stop()
