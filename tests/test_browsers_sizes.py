"""The front door in more than one engine and at the three widths people use: Chromium and Firefox, a phone (390), a tablet (820) and a desktop (1440).
On every page and every /plan room: no axe violation, nothing wider than the screen, and no error in the console. Skipped for an engine that is not installed."""

import pytest

from tests import test_plan_page
from tests.browser_helpers import BROWSER_ARGS, CHROMIUM, NOISE, axe_violations, fmt_axe
from tests.test_brief_sourcing import MYSURU

pw = pytest.importorskip("playwright.sync_api")
stack = test_plan_page.stack  # the same server, fake channel and test vendors as the page tests
SIZES = [(390, 800), (820, 1180), (1440, 900)]
STEPS = ("brief", "vendors", "message", "replies", "compare", "plan", "overview")


def _launch(p, engine):
    if engine == "chromium":
        if CHROMIUM is None:
            pytest.skip("no Chromium on this machine")
        return p.chromium.launch(executable_path=CHROMIUM, args=BROWSER_ARGS)
    try:
        return p.firefox.launch()
    except Exception as exc:  # the Playwright Firefox build is a separate download (python -m playwright install firefox)
        pytest.skip(f"no Playwright Firefox: {str(exc)[:80]}")


def _check(page, where, width, found):
    page.wait_for_timeout(400)
    wide = page.evaluate("Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - window.innerWidth")
    if wide > 1:
        found.append(f"{where} @{width}: the page is {wide}px wider than the screen")
    v = axe_violations(page)
    if v:
        found.append(f"{where} @{width}:\n{fmt_axe(v)}")


@pytest.mark.parametrize("engine", ["chromium", "firefox"])
@pytest.mark.parametrize("width,height", SIZES)
def test_every_page_and_room_holds_up_in_each_engine_and_width(stack, engine, width, height):
    url, _ = stack
    found, console = [], []
    with pw.sync_playwright() as p:
        b = _launch(p, engine)
        page = b.new_context(viewport={"width": width, "height": height}).new_page()
        page.on("console", lambda m: console.append(m.text) if m.type == "error" and not any(n in m.text for n in NOISE) else None)
        page.on("pageerror", lambda e: console.append(str(e)))
        for path in ("/", "/app"):
            page.goto(url + path, wait_until="networkidle")
            _check(page, path, width, found)
        page.goto(url + "/plan", wait_until="networkidle")
        _check(page, "/plan opening", width, found)
        page.click(".skip")
        page.wait_for_selector("#idea:focus")
        page.fill("#idea", MYSURU)
        page.press("#idea", "Enter")
        page.wait_for_selector(".review-room:not([inert])")
        _check(page, "/plan review", width, found)
        page.click("#answer-yes")
        page.click("#review-done")
        page.wait_for_selector(".finding-room:not([inert])")
        for step in STEPS:
            page.goto(f"{url}/plan#findings/{step}", wait_until="networkidle")
            page.wait_for_selector(".finding-room:not([inert])")
            _check(page, f"/plan {step}", width, found)
        b.close()
    assert not found and not console, "\n".join(found + [f"console: {c}" for c in console])
