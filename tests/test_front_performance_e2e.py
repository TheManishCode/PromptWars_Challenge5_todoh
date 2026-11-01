"""Front-end performance of the real /arcade page, as invariants that can fail, not timings that depend on the machine.

This sandbox renders WebGL in software, so frame times here say nothing about a phone's GPU (docs/ARCADE_UI.md says so too). What can be proved anywhere:
- the chat is wired before the room is built, so a message typed in the first moments is not lost;
- the page measures its own frames and reports p50/p95;
- a device that is over budget is always taken down to the light path (never left slow with quality still to shed);
- the 55 MB cat machine is the visitor's choice on a phone;
- the first load stays inside a payload budget (a ratchet: it only ever goes down).
"""

import threading
from datetime import datetime, UTC

import pytest

pw = pytest.importorskip("playwright.sync_api")

from tests.browser_helpers import CHROMIUM, GL_ARGS  # noqa: E402

pytestmark = pytest.mark.skipif(CHROMIUM is None, reason="no Chromium binary found")

from werkzeug.serving import make_server  # noqa: E402

from eventmate.service import EventMate  # noqa: E402
from eventmate.web.app import create_app  # noqa: E402
from tests.conftest import EXAMPLE, TODAY  # noqa: E402
from tests.test_wall_frontend_e2e import until  # noqa: E402
from tests.wall_helpers import make_wall  # noqa: E402

PHONE = {"width": 390, "height": 844}
LAPTOP = {"width": 1300, "height": 900}
BUDGET_MS = 34  # the page's own proposed budget (30 fps): arcade3d.js


@pytest.fixture
def base(tmp_path):
    store, _ = make_wall(tmp_path, clock=lambda: datetime.now(UTC), section_capacity=12)
    em = EventMate(log_dir=tmp_path / "logs", plan_dir=tmp_path / "plans", run_id="perf")
    app = create_app(em, today_fn=lambda: TODAY, wall_store=store)
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def open_page(p, base, viewport, touch=False):
    browser = p.chromium.launch(executable_path=CHROMIUM, args=GL_ARGS)
    ctx = browser.new_context(viewport=viewport, has_touch=touch, is_mobile=touch, device_scale_factor=1)
    return browser, ctx, ctx.new_page()


def test_the_chat_is_wired_before_the_room_is_built_so_an_early_message_is_not_lost(base):
    with pw.sync_playwright() as p:
        browser, ctx, page = open_page(p, base, LAPTOP)
        page.goto(base + "/", wait_until="domcontentloaded")
        page.evaluate("window.__same_page = true")  # a reload (what Enter did before the listener existed) would clear this
        page.fill("#msg", EXAMPLE)
        page.press("#msg", "Enter")
        page.wait_for_selector("#review:not([hidden])", timeout=60000)
        assert page.evaluate("window.__same_page") is True
        until(page, "performance.getEntriesByType('mark').some(m => m.name === 'todoh:room-ready')", 120)
        marks = page.evaluate("Object.fromEntries(performance.getEntriesByType('mark').map(m => [m.name, m.startTime]))")
        assert marks["todoh:ui-ready"] < marks["todoh:room-ready"], marks  # the visitor can act before the room exists
        browser.close()


def test_the_page_reports_its_own_frame_times(base):
    with pw.sync_playwright() as p:
        browser, ctx, page = open_page(p, base, LAPTOP)
        page.goto(base + "/")
        until(page, "(Arcade3D.frameStats() || {n: 0}).n >= 60", 120)
        s = page.evaluate("Arcade3D.frameStats()")
        assert set(s) == {"n", "p50", "p95", "max", "tier", "ladder", "lowFx", "pixelRatio"}, s
        assert 0 < s["p50"] <= s["p95"] <= s["max"], s
        assert s["tier"] == "high" and s["ladder"] in (0, 1, 2)
        browser.close()


@pytest.mark.parametrize("throttle", [4, 8], ids=lambda t: f"cpu-x{t}")
def test_a_device_over_budget_always_ends_on_the_light_path_or_inside_budget(base, throttle):
    """The invariant: never both slow and still carrying quality that could be shed. (Under software GL most runs end on the light path; a fast machine ends inside budget.)"""
    with pw.sync_playwright() as p:
        browser, ctx, page = open_page(p, base, PHONE, touch=True)
        cdp = ctx.new_cdp_session(page)
        cdp.send("Emulation.setCPUThrottlingRate", {"rate": throttle})
        page.goto(base + "/")
        settled = None
        for _ in range(240):  # up to four minutes: the ladder needs 90 frames per step
            page.wait_for_timeout(1000)
            s = page.evaluate("Arcade3D.frameStats()")
            if s and (s["lowFx"] or (s["n"] >= 120 and s["p95"] <= BUDGET_MS and s["ladder"] == 0 and page.evaluate("Arcade3D.cabReady()"))):
                settled = s
                break
        assert settled, f"never settled: {page.evaluate('Arcade3D.frameStats()')}"
        assert settled["lowFx"] or settled["p95"] <= BUDGET_MS, settled
        if settled["lowFx"]:
            assert settled["ladder"] == 2 and settled["pixelRatio"] == 1, settled  # the light path is the whole light path
        browser.close()


def test_the_55mb_cat_machine_is_the_visitors_choice_on_a_phone(base):
    with pw.sync_playwright() as p:
        browser, ctx, page = open_page(p, base, PHONE, touch=True)
        asked = []
        page.on("request", lambda r: asked.append(r.url.split("?")[0].rsplit("/", 1)[-1]))
        page.goto(base + "/")
        until(page, "Arcade3D.cabReady()", 120)
        page.wait_for_timeout(8000)  # long enough for a capable device to have fetched it by itself
        assert "vend_cat.glb" not in asked, asked
        assert "55 MB" in page.get_attribute("#cat-load", "aria-label")  # and the button says what it costs
        page.click("#cat-load")
        assert page.get_attribute("#cat-load", "aria-label").startswith("load")
        until(page, "performance.getEntriesByType('resource').some(r => r.name.includes('vend_cat.glb'))", 30)  # asked for on the click, not before
        assert page.is_disabled("#cat-load")  # and not asked for twice
        browser.close()


# Ratchets: today's numbers, measured on 2026-11-01 (docs/ARCADE_UI.md). A change that makes the first load heavier must lower something else or raise these on purpose.
CODE_BUDGET_BYTES = 330_000  # html + css + js on the wire, gzip (measured 273 KB)
PHONE_MODEL_BUDGET_BYTES = int(8.0 * 1024 * 1024)  # models fetched by a phone in the first 25 s (measured 7.7 MB)


def test_the_first_load_stays_inside_the_payload_budget(base):
    with pw.sync_playwright() as p:
        browser, ctx, page = open_page(p, base, PHONE, touch=True)
        sizes = []

        def on_response(r):
            if r.status < 400 and "/static/" in r.url:
                sizes.append((r.url.split("/static/")[1].split("?")[0], int(r.headers.get("content-length", "0"))))
        page.on("response", on_response)
        page.goto(base + "/")
        until(page, "Arcade3D.cabReady()", 120)
        page.wait_for_timeout(20000)
        code = sum(n for name, n in sizes if not name.endswith(".glb"))
        models = sum(n for name, n in sizes if name.endswith(".glb"))
        assert code <= CODE_BUDGET_BYTES, (code, sorted(sizes, key=lambda x: -x[1])[:6])
        assert models <= PHONE_MODEL_BUDGET_BYTES, (models, sorted(sizes, key=lambda x: -x[1])[:6])
        assert not any(name.endswith(".glb") and not name.endswith(".low.glb") and name != "avatar.glb" for name, _ in sizes), sizes  # a phone is given the low tier
        browser.close()
