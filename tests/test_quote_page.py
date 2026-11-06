"""Confirming a vendor's quote from the card, in a real browser: the reply read field by field with the vendor's own line, an amount confirmed on its own, the price recorded only with its
validity, and the quote comparable only when every required field is confirmed. Test vendor and fake channel: nothing leaves the machine."""

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
from tests.test_quote_readings import OK_FOR_40

pw = pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.skipif(CHROMIUM is None, reason="no Chromium on this machine")


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    from werkzeug.serving import make_server

    root = tmp_path_factory.mktemp("quote_page")
    chans, clock = {"email": FakeChannel("email"), "whatsapp": FakeChannel("whatsapp", False, False)}, Clock()
    em = EventMate(log_dir=root / "l", plan_dir=root / "p", run_id="quote-page", channels=chans, require_symbolic=False)
    em.places = em.vendor_search.places = PlacesSource(api_key="k" * 30, transport=fake_places)
    em.outreach_store.now = em.conversations.now = em.quote_confirmations.now = clock
    em.conversations.policy = SendPolicy(per_vendor_per_day=20, per_day=200, whatsapp_gap_s=0)
    srv = make_server("127.0.0.1", 0, create_app(em, today_fn=lambda: TODAY, poll_seconds=0, rate_limit_per_minute=10_000), threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", em, chans, clock
    srv.shutdown()


def test_confirm_a_quote_field_by_field_and_it_becomes_comparable_only_when_complete(stack):
    url, em, chans, clock = stack
    with pw.sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROMIUM, args=BROWSER_ARGS)
        page = b.new_context(viewport={"width": 1440, "height": 1000}).new_page()
        problems = []
        page.on("console", lambda m: problems.append(m.text) if m.type in ("error", "warning") and not any(n in m.text for n in NOISE) else None)
        page.on("pageerror", lambda e: problems.append(str(e)))
        page.goto(url + "/app", wait_until="networkidle")
        page.click("#new-plan")
        page.fill("#brief-input", MYSURU)
        page.click("#read-brief")
        page.wait_for_selector("#ask-vendors")
        page.click("#ask-vendors >> text=Find vendors I can email")
        page.wait_for_selector("#ask-list [data-vendor]")
        staged = [r for r in (em.staging.get(f.name[len("staged-"):-len(".jsonl")]) for f in em.staging.root.glob("staged-*.jsonl")) if r][-1]
        key = "brief_" + staged.staged_id
        biz, _ = em.add_test_vendor("Test Caterer", "Mysuru", C.CATERER, "owner@personal.test", True, "Asha", plan_id=key)
        d = em.request_all_quotes(key, "Asha")[0][0]
        em.approve_outreach(key, d.draft_id, d.content_hash)
        chans["email"].inbox = [Inbound(channel="email", sender="owner@personal.test", external_id="<r1>", received_at=clock() + timedelta(minutes=5), body=OK_FOR_40, in_reply_to=em.outreach(key)[0].external_id)]
        clock.advance(minutes=10)
        em.poll_conversations()
        page.click("#ask-vendors >> text=Look again")
        block = page.locator(".qblock")
        block.wait_for()
        assert "0 of 8 required fields confirmed" in block.inner_text() and "not comparable yet" in block.inner_text()
        assert "Price per plate (INR): 650" in block.inner_text(), "the vendor's own line is shown as the evidence"
        block.locator("li[data-field=min_guests]").get_by_role("button", name="Confirm").click()
        page.wait_for_selector(".qblock:has-text('Type your name first'), #ask-vendors:has-text('Type your name first')")
        page.fill("#ask-name", "Asha")
        block.locator("li[data-field=min_guests]").get_by_role("button", name="Confirm").click()
        page.wait_for_selector(".qblock:has-text('1 of 8 required fields confirmed')")
        page.locator(".qblock").get_by_role("button", name="Confirm the 4 other fields as read").click()
        page.wait_for_selector(".qblock:has-text('5 of 8 required fields confirmed')")
        price = page.locator(".qblock li[data-field=price]")
        price.get_by_role("button", name="Confirm ₹650 per plate").click()  # an amount, alone: its validity is asked with it
        page.fill(".qblock input[aria-label='How many days the price is valid']", "10")
        page.get_by_role("button", name="Confirm price and validity").click()
        page.wait_for_selector(".qblock:has-text('7 of 8 required fields confirmed')")
        assert len(em.observations.quotes(biz.vendor_id)) == 1, "the confirmed price with its validity is a recorded quote"
        assert "not comparable yet" in page.inner_text(".qblock"), "the deposit is still unconfirmed"
        page.locator(".qblock li[data-field=deposit]").get_by_role("button", name="Confirm ₹10,000").click()
        page.wait_for_selector(".qblock:has-text('8 of 8 required fields confirmed')")
        assert "comparable" in page.inner_text(".qblock") and "not comparable yet" not in page.inner_text(".qblock")
        card = page.inner_text("#ask-list article:has-text('TEST VENDOR')")
        assert "Quote:" in card and "INR 650 per plate for 40 guests" in card, "the recorded quote now shows among what the card holds"
        assert problems == [], problems
        b.close()
