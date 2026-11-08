"""The planning page (/plan) is the front door, so it is held to the same axe-core bar as / and /app: no violation in any room, on a phone or a desktop.
Also walked by keyboard alone: a person who cannot use a pointer must be able to reach the review and say Yes."""

import pytest

from tests.browser_helpers import BROWSER_ARGS, CHROMIUM, axe_violations, fmt_axe
from tests.test_brief_sourcing import MYSURU
from tests import test_plan_page
from tests.test_plan_page import _to_review

pw = pytest.importorskip("playwright.sync_api")
stack = test_plan_page.stack  # the same server, fake channel and test vendors as the page tests
SIZES = [(1440, 900), (390, 800)]
STEPS = ("brief", "vendors", "message", "replies", "compare", "plan", "overview")


def _open(p, width, height):
    b = p.chromium.launch(executable_path=CHROMIUM, args=BROWSER_ARGS)
    return b, b.new_context(viewport={"width": width, "height": height}).new_page()


@pytest.mark.skipif(CHROMIUM is None, reason="no Chromium on this machine")
@pytest.mark.parametrize("width,height", SIZES)
def test_every_room_of_the_plan_page_passes_axe(stack, width, height):
    url, _ = stack
    found = {}
    with pw.sync_playwright() as p:
        b, page = _open(p, width, height)
        page.goto(url + "/plan", wait_until="networkidle")
        found["opening"] = axe_violations(page)
        _to_review(page, url)
        found["review"] = axe_violations(page)
        page.click("#answer-yes")
        page.click("#review-done")
        page.wait_for_selector(".finding-room:not([inert])")
        page.wait_for_timeout(700)
        found["findings"] = axe_violations(page)
        for step in STEPS:  # by address, as Back/Forward and a deep link reach them: the step buttons are not shown on a phone
            page.goto(url + "/plan#findings/" + step, wait_until="networkidle")
            page.wait_for_selector(".finding-room:not([inert])")
            page.wait_for_timeout(500)
            found["findings/" + step] = axe_violations(page)
        b.close()
    bad = {room: v for room, v in found.items() if v}
    assert not bad, "\n".join(f"== {room} ({width}px)\n{fmt_axe(v)}" for room, v in bad.items())


@pytest.mark.skipif(CHROMIUM is None, reason="no Chromium on this machine")
def test_the_brief_can_be_written_reviewed_and_accepted_by_keyboard_alone(stack):
    url, _ = stack
    with pw.sync_playwright() as p:
        b, page = _open(p, 1440, 900)
        page.goto(url + "/plan", wait_until="networkidle")
        for _ in range(10):  # Tab to "Tell us your idea" and press Enter; no clicks anywhere in this test
            page.keyboard.press("Tab")
            if page.evaluate("document.activeElement && document.activeElement.classList.contains('skip')"):
                break
        else:
            pytest.fail("'Tell us your idea' cannot be reached with Tab")
        page.keyboard.press("Enter")
        page.wait_for_selector("#idea:focus")
        page.keyboard.type(MYSURU)
        page.keyboard.press("Enter")
        page.wait_for_selector(".review-room:not([inert])")
        page.wait_for_timeout(700)
        for _ in range(60):
            if page.evaluate("document.activeElement && document.activeElement.id") == "answer-yes":
                break
            page.keyboard.press("Tab")
        else:
            pytest.fail("Yes cannot be reached with Tab from the review")
        page.keyboard.press("Enter")
        assert page.is_enabled("#review-done"), "Yes pressed from the keyboard enables Done"
        b.close()
