"""Accessibility of the real /arcade page in a real Chromium, against the real routes: nothing is faked on either side.

What is proved here, and how:
- axe-core (WCAG 2.0/2.1/2.2 A and AA plus best practice) reports nothing in any state the page can be in, on a laptop and on a phone;
- text contrast is measured from rendered pixels (axe cannot decide it over a canvas), AA everywhere;
- the keyboard reaches every view a tap opens, solves the wall gate, plays a wall round and pages the wall's sections;
- with no WebGL the planner still works end to end (text-only mode);
- touch targets are 44 px on a phone and 24 px anywhere (WCAG 2.2);
- the open paper never overlaps the chat strip, at any window size;
- prefers-reduced-motion stops the camera's drift and glide.
"""

import re
import threading
from datetime import datetime, UTC

import pytest

pw = pytest.importorskip("playwright.sync_api")

from tests.browser_helpers import (  # noqa: E402
    CHROMIUM, GL_ARGS, NOGL_ARGS, NOISE, axe_violations, fmt_axe, fmt_contrast, measure_contrast, measure_contrast_scrolled,
)

pytestmark = pytest.mark.skipif(CHROMIUM is None, reason="no Chromium binary found")

from werkzeug.serving import make_server  # noqa: E402

from eventmate.service import EventMate  # noqa: E402
from eventmate.web.app import create_app  # noqa: E402
from tests.conftest import EXAMPLE, TODAY  # noqa: E402
from tests.test_wall_frontend_e2e import OBSERVE_THREE, until  # noqa: E402
from tests.wall_helpers import make_wall  # noqa: E402

TABS = ["plan", "overview", "ideas", "timeline", "vendors", "budget", "community", "vendorchats", "changes", "audit"]
NAMES = ["Asha", "Bina", "Chetan", "Devika", "Esha", "Farid", "Gita", "Hari", "Indu", "Jai", "Kiran", "Lata", "Mohan", "Nisha"]
COMMUNITY_NOT_APPLICABLE = [(400, "GET", r"/api/plans/[0-9a-f]+/community")]  # an engagement party has no community profile: the tab explains that, the server says 400
PHONE = {"width": 390, "height": 844}
LAPTOP = {"width": 1300, "height": 900}


@pytest.fixture
def world(tmp_path):
    """The real app with a real wall store (12 spots a section, so 14 names make two sections). Yields (base url, store)."""
    store, _ = make_wall(tmp_path, clock=lambda: datetime.now(UTC), section_capacity=12)
    em = EventMate(log_dir=tmp_path / "logs", plan_dir=tmp_path / "plans", run_id="a11y")
    app = create_app(em, today_fn=lambda: TODAY, wall_store=store)
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", store
    srv.shutdown()


def seed_names(store, n):
    for i in range(n):
        cred = f"seed-credential-{i:04d}"
        store.create_session(cred)
        _status, body, _created = store.submit(cred, f"seed-key-{i:04d}", NAMES[i % len(NAMES)])
        store.approve(body["mark_id"])


class Page:
    """One browser page with its console problems collected."""

    def __init__(self, p, base, gl=True, viewport=None, touch=False, reduced=False, observe_three=False, allow=()):
        self.browser = p.chromium.launch(executable_path=CHROMIUM, args=GL_ARGS if gl else NOGL_ARGS)
        self.ctx = self.browser.new_context(
            viewport=viewport or LAPTOP, has_touch=touch, is_mobile=touch, device_scale_factor=1,
            reduced_motion="reduce" if reduced else "no-preference")
        self.page = self.ctx.new_page()
        self.problems = []
        self.http_errors = []
        self.page.on("response", lambda r: self.http_errors.append((r.status, r.request.method, r.url.split("?")[0].replace(base, ""))) if r.status >= 400 else None)
        self.page.on("pageerror", lambda e: self.problems.append(("pageerror", str(e))))
        self.page.on("console", lambda m: self.problems.append(("console", m.text)) if m.type == "error" else None)
        if observe_three:
            self.page.add_init_script(OBSERVE_THREE)
        self.base = base
        self.gl = gl
        self.allow = [(s, m, re.compile(rx)) for s, m, rx in list(allow)]  # nothing else is allowed: any other 4xx/5xx or console error fails the test  # responses a test expects, by status, method and path

    def open(self, settle_ms=None):
        self.page.goto(self.base + "/")
        if self.gl:
            until(self.page, "typeof Arcade3D !== 'undefined' && Arcade3D.cabReady()", 90)
            self.page.wait_for_timeout(settle_ms if settle_ms is not None else 1500)
        else:
            self.page.wait_for_timeout(800)
        return self

    def brief(self):
        self.page.fill("#msg", EXAMPLE)
        self.page.press("#msg", "Enter")
        try:
            self.page.wait_for_selector("#review:not([hidden])", timeout=30000)
        except Exception:
            state = self.page.evaluate("({chat: document.getElementById('chat').className, body: document.body.className, log: document.getElementById('log').innerText.slice(0, 400), review: JSON.stringify(document.getElementById('review').getBoundingClientRect()), problems: 0})")
            raise AssertionError(f"the review card never appeared: {state}; page problems: {self.problems}") from None

    def build(self):
        self.brief()
        self.page.click("#build")
        self.page.wait_for_selector("#flight-layer:not([hidden])", timeout=90000)
        self.page.wait_for_timeout(700)

    def unexpected(self):
        """Console errors and failed requests the page provoked, minus the renderer's own noise."""
        noise = [x for x in self.problems if not any(n in x[1] for n in NOISE) and "status of 4" not in x[1]]
        return noise + [("http", e) for e in self.http_errors if not any(e[0] == s and e[1] == m and rx.fullmatch(e[2]) for s, m, rx in self.allow)]

    def close(self):
        self.browser.close()


def active(page):
    return page.evaluate("(() => { const a = document.activeElement; return a ? (a.id || a.tagName.toLowerCase() + (a.dataset && a.dataset.view ? '[' + a.dataset.view + ']' : '')) : null; })()")


# ----------------------------------------------------------------------------------------------- no WebGL


def test_text_only_mode_runs_the_whole_planner_without_webgl(world):
    base, _ = world
    with pw.sync_playwright() as p:
        pg = Page(p, base, gl=False, viewport={"width": 1100, "height": 800}).open()
        page = pg.page
        notice = page.inner_text("#gl-fallback")
        assert "Text-only mode" in notice and "classic" not in notice.lower()  # the old notice pointed at a page that no longer exists
        assert page.evaluate("document.body.classList.contains('no-gl')")
        assert page.evaluate("document.getElementById('room-nav').hidden")  # there is no room to move around in
        pg.build()
        assert page.evaluate("document.activeElement.id") == "paper"  # focus lands on the page that just appeared
        text = page.inner_text("#paper")
        for word in ("PLAN DOSSIER", "BUDGET", "TIMELINE", "IDEAS", "VENDORS"):
            assert word in text, word
        page.click('.sheet-tab[data-view="timeline"]')
        page.wait_for_timeout(600)
        assert page.evaluate("document.activeElement.classList.contains('cur')")  # the rebuilt tab keeps the keyboard
        page.keyboard.press("Escape")
        assert page.evaluate("document.getElementById('flight-layer').hidden")
        assert active(page) not in (None, "body", "BODY")  # focus went back to a control, not to nowhere
        pg.close()
    assert pg.unexpected() == [], pg.unexpected()


@pytest.mark.parametrize("viewport", [{"width": 1100, "height": 800}, PHONE], ids=["laptop", "phone"])
def test_axe_finds_nothing_in_any_text_only_state(world, viewport):
    base, _ = world
    with pw.sync_playwright() as p:
        touch = viewport is PHONE
        pg = Page(p, base, gl=False, viewport=viewport, touch=touch, allow=COMMUNITY_NOT_APPLICABLE).open()
        page, failures = pg.page, []

        def check(label):
            found = axe_violations(page)
            if found:
                failures.append(f"--- {label} ({viewport['width']}x{viewport['height']})\n{fmt_axe(found)}")

        check("landing")
        page.click("#credits-toggle")
        check("credits panel open")
        page.click("#credits-toggle")
        page.click("#mic-btn")
        page.wait_for_timeout(400)
        check("voice note panel")
        pg.brief()
        check("review card")
        page.click("#build")
        page.wait_for_selector("#flight-layer:not([hidden])", timeout=90000)
        page.wait_for_timeout(700)
        for tab in TABS:
            page.click(f'.sheet-tab[data-view="{tab}"]')
            page.wait_for_timeout(1200)
            check(f"paper tab {tab}")
        pg.close()
    assert not failures, "\n".join(failures)
    assert pg.unexpected() == [], pg.unexpected()


# ----------------------------------------------------------------------------------------------- the room


@pytest.mark.parametrize("viewport", [LAPTOP, PHONE], ids=["laptop", "phone"])
def test_axe_finds_nothing_with_the_room_on_including_the_keyboard_bar(world, viewport):
    base, _ = world
    with pw.sync_playwright() as p:
        pg = Page(p, base, gl=True, viewport=viewport, touch=viewport is PHONE).open()
        page, failures = pg.page, []
        found = axe_violations(page)
        if found:
            failures.append("--- landing\n" + fmt_axe(found))
        # the keyboard bar is hidden from a pointer and shown to the keyboard: it must pass while it is shown
        page.focus('#room-nav button[data-view="wall"]')
        assert page.evaluate("getComputedStyle(document.getElementById('room-nav')).position") == "fixed"
        found = axe_violations(page)
        if found:
            failures.append("--- room nav focused\n" + fmt_axe(found))
        pg.close()
    assert not failures, "\n".join(failures)
    assert pg.unexpected() == [], pg.unexpected()


def test_the_keyboard_reaches_every_room_view_and_back(world):
    base, _ = world
    with pw.sync_playwright() as p:
        pg = Page(p, base, gl=True).open()
        page = pg.page
        # reachable by Tab alone, in a sensible number of presses, and visible once there
        presses = 0
        while presses < 40 and not page.evaluate("!!document.activeElement && !!document.activeElement.closest('#room-nav')"):
            page.keyboard.press("Tab")
            presses += 1
        assert presses < 40, "Tab never reached the room views"
        box = page.evaluate("(() => { const r = document.getElementById('room-nav').getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom, innerWidth, innerHeight]; })()")
        assert 0 <= box[0] and box[2] <= box[4] and 0 <= box[1] and box[3] <= box[5], box  # on screen, not clipped
        assert page.evaluate("document.activeElement.dataset.view") == "wall"
        names = page.evaluate("Array.from(document.querySelectorAll('#room-nav button:not([hidden])')).map(b => b.textContent.trim())")
        assert "Signature wall" in names and "Secret dossier" in names and "Hall of Fame table" not in names  # the Hall stays hidden until it is unlocked

        # the signature wall: Enter opens it, focus moves to Back, Escape closes it and the keyboard returns to where it was
        page.keyboard.press("Enter")
        until(page, "document.body.classList.contains('wall-focus')", 10)
        assert active(page) == "room-back"
        page.keyboard.press("Escape")
        until(page, "!document.body.classList.contains('wall-focus')", 10)
        page.wait_for_timeout(300)
        assert page.evaluate("document.activeElement.dataset.view") == "wall"

        # a vending machine (loaded one by one: wait for the buttons to appear)
        until(page, "document.querySelectorAll('#room-nav-vend button').length >= 2", 60)
        page.focus('#room-nav-vend button')
        page.keyboard.press("Enter")
        until(page, "document.body.classList.contains('vend-focus')", 10)
        until(page, "document.getElementById('sr-live').textContent.toLowerCase().includes('vending machine')", 5)  # announced to a screen reader
        page.click("#room-back")
        until(page, "!document.body.classList.contains('vend-focus')", 10)

        # the dossier on the floor
        until(page, "Arcade3D.dossierReady()", 60)
        page.focus('#room-nav button[data-view="dossier"]')
        page.keyboard.press("Enter")
        until(page, "document.body.classList.contains('dossier-focus')", 10)
        page.keyboard.press("Escape")
        until(page, "!document.body.classList.contains('dossier-focus')", 10)
        assert page.evaluate("document.getElementById('room-back').hidden")
        pg.close()
    assert pg.unexpected() == [], pg.unexpected()


def _open_wall_by_keyboard(page):
    page.focus('#room-nav button[data-view="wall"]')
    page.keyboard.press("Enter")
    until(page, "document.body.classList.contains('wall-focus')", 10)
    page.wait_for_timeout(1200)  # the camera glide


def test_the_wall_gate_round_and_sections_work_by_keyboard(world):
    base, store = world
    seed_names(store, 13)  # 13 names: section 0 is full (12), section 1 holds one; the visitor will be the 14th, in section 1
    with pw.sync_playwright() as p:
        pg = Page(p, base, gl=True, observe_three=True).open()
        page = pg.page
        # the gate: scratches out of line; the arrow keys move them exactly as a drag does
        _open_wall_by_keyboard(page)
        assert page.evaluate("sessionStorage.getItem('em_wall_solved')") is None
        page.keyboard.press("ArrowLeft")  # away from true: said so
        until(page, "document.getElementById('sr-live').textContent.includes('Further')", 5)
        for _ in range(8):
            page.keyboard.press("ArrowRight")
            if page.evaluate("sessionStorage.getItem('em_wall_solved')") == "1":
                break
        assert page.evaluate("sessionStorage.getItem('em_wall_solved')") == "1", "the arrow keys never lined the scratches up"
        page.wait_for_selector("#wall-signbox:not([hidden])", timeout=10000)

        # sign by keyboard
        page.focus("#wall-name")
        page.keyboard.type("Asha Test")
        page.keyboard.press("Enter")
        page.wait_for_selector("#wall-minebox:not([hidden])", timeout=10000)
        mark_id = page.evaluate("wallui.me.mark.mark_id")
        store.approve(mark_id)
        page.keyboard.press("Escape")
        until(page, "!document.body.classList.contains('wall-focus')", 10)
        _open_wall_by_keyboard(page)
        page.wait_for_selector("#wall-challenge:not([hidden])", timeout=10000)

        # sections: the board opens on the section that holds the visitor's own name (1), and the pager walks to section 0
        until(page, "wallui.sectionId === 1 && !document.getElementById('wall-pager').hidden", 10)
        assert page.inner_text("#wall-pageinfo") == "section 2 of 2"
        assert page.is_disabled("#wall-next") and not page.is_disabled("#wall-prev")
        names_section_1 = page.eval_on_selector_all("#wall-names li", "els => els.map(e => e.textContent)")
        assert any("Asha Test" in n for n in names_section_1), names_section_1
        page.click("#wall-prev")
        until(page, "wallui.sectionId === 0 && wallui.section && wallui.section.section_id === 0", 10)
        assert page.inner_text("#wall-pageinfo") == "section 1 of 2"
        names_section_0 = page.eval_on_selector_all("#wall-names li", "els => els.map(e => e.textContent)")
        assert len(names_section_0) == 12 and not any("Asha Test" in n for n in names_section_0), names_section_0
        assert page.evaluate("Arcade3D.wallCells().filter(c => c.name).length") == 12  # the canvas and the list agree

        # a timed round, played with buttons only
        page.click("#wall-start-timed")
        page.wait_for_selector("#wall-readybtn:not([hidden])", timeout=10000)
        assert page.evaluate("document.getElementById('wall-pager').hidden")  # no paging while a round is open: the frozen snapshot is the board
        assert page.evaluate("document.querySelectorAll('#wall-names li').length") == 0  # and no name list: every name must look alike until the reveal
        page.focus("#wall-readybtn")
        page.keyboard.press("Enter")
        page.wait_for_selector("#wall-waitline:not([hidden])", timeout=10000)
        to_slot = page.evaluate("wallui.round.to_slot")
        cells = page.eval_on_selector_all("#wall-cells button", "els => els.map(e => e.textContent)")
        assert len(cells) == page.evaluate("wallui.round.snapshot.capacity") == 12
        target = page.query_selector(f'#wall-cells button:nth-child({to_slot + 1})')
        assert f"spot {to_slot}:" in target.inner_text() and "Asha Test" in target.inner_text(), target.inner_text()
        target.focus()
        chip = target.bounding_box()  # WCAG 2.4.7: the focused spot must be visible to a sighted keyboard user, not a 1 px clipped box
        assert chip and chip["width"] > 60 and chip["height"] >= 40, chip
        assert 0 <= chip["x"] and chip["x"] + chip["width"] <= page.evaluate("innerWidth") and 0 <= chip["y"] and chip["y"] + chip["height"] <= page.evaluate("innerHeight"), chip
        page.keyboard.press("Enter")
        page.wait_for_selector("#wall-resultrow:not([hidden])", timeout=10000)
        assert "FOUND" in page.inner_text("#wall-result")
        assert page.evaluate("document.querySelectorAll('#wall-cells button').length") == 0
        pg.close()
    assert pg.unexpected() == [], pg.unexpected()


@pytest.mark.parametrize("viewport", [LAPTOP, PHONE], ids=["laptop", "phone"])
def test_each_vending_machine_view_frames_the_whole_machine_at_a_useful_size(world, viewport):
    """Open each machine by keyboard; once the camera has settled, the machine is wholly in the picture and fills a real share of its height (laptop composition on a phone)."""
    base, _ = world
    with pw.sync_playwright() as p:
        pg = Page(p, base, gl=True, viewport=viewport, touch=viewport is PHONE).open()
        page = pg.page
        until(page, "document.querySelectorAll('#room-nav-vend button').length >= 2", 90)
        shots = []
        for i in range(2):
            page.focus(f'#room-nav-vend button[data-i="{i}"]')
            page.keyboard.press("Enter")
            until(page, "document.body.classList.contains('vend-focus')", 10)
            # the final state, not a timing: wait (software GL on a loaded machine is slow) until the machine fills a real share of the picture, then check it is whole
            try:
                until(page, f"(() => {{ const f = Arcade3D.vendFraming({i}); return !!f && (f.y1 - f.y0) / 2 >= 0.30; }})()", 120)
            except AssertionError:
                raise AssertionError(f"the camera never framed machine {i}: {page.evaluate(f'Arcade3D.vendFraming({i})')}; stats {page.evaluate('Arcade3D.frameStats()')}") from None
            f = page.evaluate(f"Arcade3D.vendFraming({i})")
            shots.append(f)
            assert not f["behind"], f
            assert -1.0 <= f["x0"] and f["x1"] <= 1.0 and -1.0 <= f["y0"] and f["y1"] <= 1.0, f  # wholly in the picture
            page.keyboard.press("Escape")
            until(page, "!document.body.classList.contains('vend-focus')", 10)
            page.wait_for_timeout(1500)
        pg.close()
    assert len({s["label"] for s in shots}) == 2, shots  # two different machines, not the same one twice


# ----------------------------------------------------------------------------------------------- contrast


def test_text_contrast_meets_aa_over_the_room_and_in_every_text_only_state(world):
    base, _ = world
    failures = []
    with pw.sync_playwright() as p:
        for label, viewport, touch in (("laptop", LAPTOP, False), ("phone", PHONE, True)):
            pg = Page(p, base, gl=True, viewport=viewport, touch=touch).open(settle_ms=4000)
            found = measure_contrast(pg.page, pg.ctx, shots=3)
            if found:
                failures.append(f"--- room, {label}\n{fmt_contrast(found)}")
            pg.close()
        pg = Page(p, base, gl=False, viewport={"width": 1100, "height": 800}).open()
        page = pg.page
        for label, act in (("text-only landing", lambda: None), ("credits panel", lambda: page.click("#credits-toggle"))):
            act()
            found = measure_contrast(page, pg.ctx)
            if found:
                failures.append(f"--- {label}\n{fmt_contrast(found)}")
        page.click("#credits-toggle")
        pg.brief()
        found = measure_contrast(page, pg.ctx)
        if found:
            failures.append(f"--- review card\n{fmt_contrast(found)}")
        page.click("#build")
        page.wait_for_selector("#flight-layer:not([hidden])", timeout=90000)
        page.wait_for_timeout(700)
        for tab in TABS:
            page.click(f'.sheet-tab[data-view="{tab}"]')
            page.wait_for_timeout(1200)
            found = measure_contrast_scrolled(page, pg.ctx)
            if found:
                failures.append(f"--- paper {tab}\n{fmt_contrast(found)}")
        pg.close()
    assert not failures, "\n".join(failures)


@pytest.mark.parametrize("viewport", [LAPTOP, PHONE], ids=["laptop", "phone"])
def test_the_wall_panel_is_readable_in_every_state_over_the_mural(world, viewport):
    """The panel is translucent glass over a painted wall: gate, sign, challenge, a round in each phase, and the result with the Hall offer."""
    base, store = world
    seed_names(store, 5)
    failures = []
    with pw.sync_playwright() as p:
        pg = Page(p, base, gl=True, viewport=viewport, touch=viewport is PHONE).open()
        page = pg.page

        def measure(label):
            found = measure_contrast(page, pg.ctx, shots=2)
            if found:
                failures.append(f"--- wall {label} ({viewport['width']}x{viewport['height']})\n{fmt_contrast(found)}")

        _open_wall_by_keyboard(page)
        measure("gate")
        for _ in range(8):
            page.keyboard.press("ArrowRight")
        page.wait_for_selector("#wall-signbox:not([hidden])", timeout=10000)
        measure("sign form")
        page.fill("#wall-name", "Asha Test")
        page.press("#wall-name", "Enter")
        page.wait_for_selector("#wall-minebox:not([hidden])", timeout=10000)
        measure("name pending")
        store.approve(page.evaluate("wallui.me.mark.mark_id"))
        page.keyboard.press("Escape")
        until(page, "!document.body.classList.contains('wall-focus')", 10)
        _open_wall_by_keyboard(page)
        page.wait_for_selector("#wall-challenge:not([hidden])", timeout=10000)
        measure("challenge")
        page.click("#wall-start-untimed")  # no clock: measuring takes real time and a timed round would expire under it
        page.wait_for_selector("#wall-readybtn:not([hidden])", timeout=10000)
        measure("round issued")
        page.click("#wall-readybtn")
        page.wait_for_selector("#wall-waitline:not([hidden])", timeout=10000)
        measure("round running")
        target = page.query_selector('#wall-cells button:has-text("Asha Test")')
        target.focus()
        page.keyboard.press("Enter")  # the real keyboard path: a hidden button is activated by the keyboard, never by a mouse pointer
        page.wait_for_selector("#wall-halloffer:not([hidden])", timeout=10000)
        measure("found, Hall offered")
        pg.close()
    assert not failures, "\n".join(failures)


# ----------------------------------------------------------------------------------------------- touch targets and geometry

_TARGETS = """
() => {
  const out = [];
  for (const e of document.querySelectorAll('button, a[href], input:not([type=hidden]), select, textarea, summary, [role=button]')) {
    if (e.closest('.sr-only, [hidden]') || e.disabled) continue;
    const c = getComputedStyle(e); if (c.visibility === 'hidden' || c.display === 'none') continue;
    if (e.closest('#room-nav:not(:focus-within)')) continue; // a keyboard bar a pointer never sees
    if (e.tagName === 'A' && e.closest('#credits-panel, .paper p, .paper li')) continue; // a link inside a sentence is exempt (WCAG 2.5.8 inline)
    if (e.type === 'checkbox' && e.closest('label')) continue; // the wrapping label is the target
    const r = e.getBoundingClientRect(); if (r.width < 1 || r.height < 1) continue;
    out.push({ id: e.id || e.className || e.tagName, w: Math.round(r.width), h: Math.round(r.height) });
  }
  return out;
}
"""


@pytest.mark.parametrize("coarse", [True, False], ids=["phone-44", "laptop-24"])
def test_every_control_is_big_enough_to_hit(world, coarse):
    base, _ = world
    need = 44 if coarse else 24
    failures = []
    with pw.sync_playwright() as p:
        pg = Page(p, base, gl=False, viewport=PHONE if coarse else {"width": 1100, "height": 800}, touch=coarse).open()
        page = pg.page
        assert page.evaluate("matchMedia('(pointer: coarse)').matches") is coarse

        def check(label):
            small = [t for t in page.evaluate(_TARGETS) if t["w"] < need or t["h"] < need]
            if small:
                failures.append(f"--- {label} (need {need}px): {small[:8]}")

        check("landing")
        page.click("#mic-btn")
        page.wait_for_timeout(300)
        check("voice panel")
        pg.build()
        for tab in TABS:
            page.click(f'.sheet-tab[data-view="{tab}"]')
            page.wait_for_timeout(1000)
            check(f"paper {tab}")
        pg.close()
    assert not failures, "\n".join(failures)


def test_the_wall_panel_controls_are_big_enough_on_a_phone(world):
    base, store = world
    with pw.sync_playwright() as p:
        pg = Page(p, base, gl=True, viewport=PHONE, touch=True).open()
        page = pg.page
        _open_wall_by_keyboard(page)
        for _ in range(8):
            page.keyboard.press("ArrowRight")
        page.wait_for_selector("#wall-signbox:not([hidden])", timeout=10000)
        small = [t for t in page.evaluate(_TARGETS) if (t["w"] < 44 or t["h"] < 44)]
        assert not small, small
        pg.close()


@pytest.mark.parametrize("size", [(1300, 900), (1024, 600), (800, 500), (390, 844), (360, 640)], ids=lambda s: f"{s[0]}x{s[1]}")
def test_the_open_paper_never_overlaps_the_chat_strip(world, size):
    base, _ = world
    with pw.sync_playwright() as p:
        pg = Page(p, base, gl=False, viewport={"width": size[0], "height": size[1]}, touch=size[0] < 500).open()
        pg.build()
        geo = pg.page.evaluate("""(() => {
          const p = document.getElementById('paper').getBoundingClientRect(), h = document.querySelector('#chat header').getBoundingClientRect();
          return { paperTop: p.top, paperBottom: p.bottom, headerTop: h.top };
        })()""")
        assert geo["paperTop"] >= 0 and geo["paperBottom"] <= geo["headerTop"] + 1, geo
        pg.close()


# ----------------------------------------------------------------------------------------------- reduced motion

_CAM = "(() => { const c = (window.__cams || []).find((x) => x.fov === 55 || x.fov === 65) || (window.__cams || [])[0]; return c ? [c.position.x, c.position.y, c.position.z] : null; })()"


def _camera_activity(world_base, reduced):
    """How far the camera moves between two readings 400 ms apart with nothing asked of it, then 200 ms after opening the wall versus 1.6 s after."""
    with pw.sync_playwright() as p:
        pg = Page(p, world_base, gl=True, reduced=reduced, observe_three=True).open(settle_ms=2500)
        page = pg.page
        a = page.evaluate(_CAM)
        page.wait_for_timeout(400)
        b = page.evaluate(_CAM)
        idle = max(abs(x - y) for x, y in zip(a, b))
        page.focus('#room-nav button[data-view="wall"]')
        page.keyboard.press("Enter")
        until(page, "document.body.classList.contains('wall-focus')", 10)
        page.wait_for_timeout(200)
        early = page.evaluate(_CAM)
        page.wait_for_timeout(1600)
        late = page.evaluate(_CAM)
        glide = max(abs(x - y) for x, y in zip(early, late))
        pg.close()
        return idle, glide


def test_reduced_motion_stops_the_idle_sway_and_the_camera_glide(world):
    base, _ = world
    idle_off, glide_off = _camera_activity(base, reduced=False)
    idle_on, glide_on = _camera_activity(base, reduced=True)
    assert idle_off > 0.0005 and glide_off > 0.05, (idle_off, glide_off)  # the control: with motion allowed, both really happen
    assert idle_on == 0.0, idle_on
    assert glide_on < 0.01, glide_on  # the camera is already where it is going
