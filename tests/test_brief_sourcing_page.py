"""Asking vendors for quotes from the New plan dialog, before any plan exists, in a real browser: find, confirm an address, write the requests, approve one, read the reply.
The vendor is a test vendor whose address is the user's own, and the channel is a fake: nothing leaves the machine."""

import json
import threading
from datetime import timedelta

import pytest

from eventmate.integrations.places import PlacesSource
from eventmate.messaging.channels import Inbound
from eventmate.messaging.conversations import SendPolicy
from eventmate.schemas import VendorCategory as C
from eventmate.service import EventMate
from eventmate.web.app import create_app
from tests.browser_helpers import BROWSER_ARGS, CHROMIUM, NOISE
from tests.conftest import TODAY
from tests.test_brief_sourcing import MYSURU, fake_places
from tests.test_conversations import Clock, FakeChannel

pw = pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.skipif(CHROMIUM is None, reason="no Chromium on this machine")


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    from werkzeug.serving import make_server

    root = tmp_path_factory.mktemp("brief_page")
    chans, clock = {"email": FakeChannel("email"), "whatsapp": FakeChannel("whatsapp", False, False)}, Clock()
    em = EventMate(log_dir=root / "l", plan_dir=root / "p", run_id="brief-page", channels=chans, require_symbolic=False)
    em.places = em.vendor_search.places = PlacesSource(api_key="k" * 30, transport=fake_places)
    em.outreach_store.now = em.conversations.now = clock
    em.conversations.policy = SendPolicy(per_vendor_per_day=20, per_day=200, whatsapp_gap_s=0)
    srv = make_server("127.0.0.1", 0, create_app(em, today_fn=lambda: TODAY, poll_seconds=0, rate_limit_per_minute=10_000), threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", em, chans, clock
    srv.shutdown()


def test_the_whole_ask_from_the_dialog_with_no_plan(stack):
    url, em, chans, clock = stack
    with pw.sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROMIUM, args=BROWSER_ARGS)
        page = b.new_context(viewport={"width": 1440, "height": 900}).new_page()
        problems = []
        page.on("console", lambda m: problems.append(m.text) if m.type in ("error", "warning") and not any(n in m.text for n in NOISE) else None)
        page.on("pageerror", lambda e: problems.append(str(e)))
        page.goto(url + "/app", wait_until="networkidle")
        page.click("#new-plan")
        page.fill("#brief-input", MYSURU)
        page.click("#read-brief")
        page.wait_for_selector("#ask-vendors")
        assert "Your budget is never in it" in page.inner_text("#ask-vendors")
        page.click("#ask-vendors >> text=Find vendors I can email")
        page.wait_for_selector("#ask-list [data-vendor]")
        text = page.inner_text("#ask-list")
        assert "Sri Hari Caterers" in text and "Bengaluru Caterers" not in text, "the card shows the listing as Google has it now"
        assert "fetched just now and not stored" in text and "JP Nagar" in text and "Find email on their website" in text and "Nothing held yet" in text
        staged = [r for r in (em.staging.get(f.name[len("staged-"):-len(".jsonl")]) for f in em.staging.root.glob("staged-*.jsonl")) if r][-1]
        key = "brief_" + staged.staged_id
        em.add_test_vendor("Test Caterer", "Mysuru", C.CATERER, "owner@personal.test", True, "Asha", plan_id=key)
        page.click("#ask-vendors >> text=Look again")
        page.wait_for_selector("#ask-list article:has-text('TEST VENDOR') >> text=owner@personal.test")
        mine = page.inner_text("#ask-list article:has-text('TEST VENDOR')")
        assert "You confirmed this" in mine and "confirmed by you" in mine and "Asha" in mine
        google = page.locator("#ask-list article:has-text('Sri Hari Caterers')").first
        google.get_by_role("button", name="Write to them myself").click()
        google.locator(".contact .exact").wait_for()
        assert "Hello Sri Hari Caterers," in google.inner_text() and "[your name]" in google.inner_text() and "60,000" not in google.inner_text() and "not recorded" in google.inner_text()
        google.get_by_role("button", name="Show phone and WhatsApp").click()
        wa = google.get_by_role("link", name="WhatsApp them")
        wa.wait_for()
        href = wa.get_attribute("href")
        assert href.startswith("https://wa.me/919845012345?text=") and "Mysuru" in href and "60%2C000" not in href
        assert "not stored" in google.inner_text()
        test_card = page.locator("#ask-list article:has-text('TEST VENDOR')")
        test_card.get_by_role("button", name="Write to them myself").click()
        mail = test_card.get_by_role("link", name="Email owner@personal.test")
        mail.wait_for()
        assert mail.get_attribute("href").startswith("mailto:owner@personal.test?subject=Quote%20request")
        page.click("#ask-list >> text=Write the requests")
        page.wait_for_selector("#ask-name:visible")
        assert "Type your name first" in page.inner_text("#ask-vendors")  # no name, no request
        page.fill("#ask-name", "Asha")
        page.click("#ask-list >> text=Write the requests")
        page.wait_for_selector("#ask-drafts [data-draft]")
        draft = page.inner_text("#ask-drafts")
        assert "Mysuru" in draft and "40 guests" in draft and "60,000" not in draft and "budget" not in draft.lower()
        assert chans["email"].sent == [], "writing a request sends nothing"
        page.click("#ask-drafts >> text=Approve exactly this and send")
        page.wait_for_function("() => /Waiting for a reply/.test(document.querySelector('#ask-drafts').innerText)")
        assert [s[0] for s in chans["email"].sent] == ["owner@personal.test"]
        sent = em.outreach(key)[0]
        chans["email"].inbox = [Inbound(channel="email", sender="owner@personal.test", external_id="<r1>", received_at=clock() + timedelta(minutes=5), body="Available. Total INR 52,000 for 40 guests.", in_reply_to=sent.external_id)]
        clock.advance(minutes=10)
        em.poll_conversations()
        page.click("#ask-vendors >> text=Look again")
        page.wait_for_selector("#ask-replies [data-reply]")
        reply = page.inner_text("#ask-replies")
        assert "52,000" in reply and "says it is available" in reply and "check it before you rely on it" in reply
        assert em.store.list_plans() == []
        assert problems == [], problems
        b.close()
