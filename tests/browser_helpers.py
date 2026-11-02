"""Shared pieces for the browser tests of the pages: one Chromium recipe, an axe-core runner, and a contrast meter that reads real pixels.

axe-core cannot decide text contrast over a canvas or a gradient (it reports "incomplete"), so ``measure_contrast`` does what a person looks at: it hides each
text element's own ink, takes a screenshot, and compares the element's colour with the pixels that were behind it.
"""

import base64
import hashlib
import shutil
from pathlib import Path

CHROMIUM = shutil.which("chromium") or shutil.which("google-chrome-stable")
BROWSER_ARGS = ["--no-sandbox", "--disable-gpu"]

VENDOR = Path(__file__).resolve().parent / "vendor"
AXE_JS = VENDOR / "axe.min.js"
AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"]

# console noise that is the renderer's, not the page's
NOISE = ("GL Driver", "GPU stall", "swiftshader", "ERR_NETWORK_CHANGED", "Error creating WebGL context", "webgl:")


def axe_source() -> str:
    """The vendored axe-core, after checking it is the file the manifest names (a substituted copy must be visible)."""
    manifest = (VENDOR / "MANIFEST.txt").read_text()
    digest = hashlib.sha256(AXE_JS.read_bytes()).hexdigest()
    assert digest in manifest, f"tests/vendor/axe.min.js ({digest}) is not the file tests/vendor/MANIFEST.txt records"
    return AXE_JS.read_text()


_RUN_AXE = """() => axe.run(document, {runOnly: {type: 'tag', values: %s}, resultTypes: ['violations']}).then(r => r.violations.map(v => ({
  id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.map(n => n.target.join(' ') + ' | ' + ((n.any[0] || n.all[0] || n.none[0] || {}).message || '').slice(0, 120))})))"""


def axe_violations(page, ignore=()):
    """Every axe violation (WCAG 2.0/2.1/2.2 A and AA plus best practice) on the page as it is now. The script goes in over the DevTools protocol, so the page's CSP is untouched."""
    page.evaluate(axe_source())
    found = page.evaluate(_RUN_AXE % str(AXE_TAGS))
    return [v for v in found if v["id"] not in ignore]


def fmt_axe(found):
    return "\n".join(f"[{v['impact']}] {v['id']}: {v['help']}\n    " + "\n    ".join(v["nodes"][:5]) for v in found)


_COLLECT = r"""
() => {
  const parse = (s) => { const m = (s || '').match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; };
  const items = [], els = [];
  const visible = (el) => { for (let e = el; e && e !== document.documentElement; e = e.parentElement) { const c = getComputedStyle(e); if (c.display === 'none' || c.visibility === 'hidden') return false; if (e.hidden || e.getAttribute('aria-hidden') === 'true') return false; } return true; };
  const path = (el) => { const bits = []; for (let e = el; e && e !== document.body && bits.length < 4; e = e.parentElement) bits.unshift(e.id ? '#' + e.id : e.tagName.toLowerCase() + (e.className && typeof e.className === 'string' ? '.' + e.className.trim().split(/\s+/)[0] : '')); return bits.join(' > '); };
  const add = (el, text, color, rects0, kind) => {
    let op = 1; for (let e = el; e && e !== document.documentElement; e = e.parentElement) op *= parseFloat(getComputedStyle(e).opacity);
    const cs = getComputedStyle(el);
    const px = parseFloat(cs.fontSize), bold = parseInt(cs.fontWeight) >= 700;
    // 1. fully inside every scrolling or clipping ancestor: a line half scrolled out of its box is not a line anyone reads
    const clips = []; for (let e = el.parentElement; e && e !== document.documentElement; e = e.parentElement) { const c = getComputedStyle(e); if (c.overflowX !== 'visible' || c.overflowY !== 'visible') clips.push(e.getBoundingClientRect()); }
    let rects = rects0.filter((q) => clips.every((c) => q[0] >= c.left - 1 && q[1] >= c.top - 1 && q[0] + q[2] <= c.right + 1 && q[1] + q[3] <= c.bottom + 1));
    // 2. the paper's sticky stamp is a deliberate overlay that crosses whatever scrolls under it: its border is not the background of the text beneath
    const stamps = Array.from(document.querySelectorAll('.stamp')).map((s) => s.getBoundingClientRect());
    if (!el.closest('.stamp')) rects = rects.filter((q) => stamps.every((s) => q[0] > s.right + 2 || q[0] + q[2] < s.left - 2 || q[1] > s.bottom + 2 || q[1] + q[3] < s.top - 2));
    // 3. a rotated box (the stamp) holds its own border inside the bounding rectangle, and an input's border sits inside the input: read the middle only
    let tilted = false; for (let e = el; e && e !== document.documentElement; e = e.parentElement) if (getComputedStyle(e).transform !== 'none') tilted = true;
    if (tilted) rects = rects.map((q) => [q[0] + q[2] * 0.25, q[1] + q[3] * 0.25, q[2] * 0.5, q[3] * 0.5]);
    if (kind === 'placeholder') rects = rects.map((q) => [q[0] + 8, q[1] + 5, Math.max(2, q[2] - 16), Math.max(2, q[3] - 10)]);
    // 4. judged only where every probe point (centre and four inner corners) still belongs to the element: covered text is not read
    const mine = (x, y) => { const t = document.elementFromPoint(Math.min(innerWidth - 1, Math.max(0, x)), Math.min(innerHeight - 1, Math.max(0, y))); return !!t && el.contains(t); }; // an ancestor under the point means the element is clipped or covered there
    const ok = rects.filter((q) => [[0.5, 0.5], [0.08, 0.15], [0.92, 0.15], [0.08, 0.85], [0.92, 0.85]].every(([u, v]) => mine(q[0] + q[2] * u, q[1] + q[3] * v)));
    if (!ok.length) return;
    els.push(el);
    items.push({ i: els.length - 1, text: text.slice(0, 48), path: path(el), color, opacity: op, large: px >= 24 || (px >= 18.66 && bold), px, rects: ok, kind });
  };
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n; (n = walker.nextNode());) {
    const text = n.textContent.replace(/\s+/g, ' ').trim(); if (!text) continue;
    const el = n.parentElement; if (!el || el.closest('script,style,noscript,.sr-only,canvas') || !visible(el) || el.closest(':disabled')) continue;
    const shut = el.closest('details:not([open])'); if (shut && !el.closest('summary')) continue; // the body of a closed <details> is laid out but never painted
    const cs = getComputedStyle(el);
    const color = parse(cs.webkitTextFillColor) || parse(cs.color);
    const r = document.createRange(); r.selectNodeContents(n);
    const rects = Array.from(r.getClientRects()).filter((q) => q.width > 2 && q.height > 2 && q.bottom > 0 && q.right > 0 && q.top < innerHeight && q.left < innerWidth).map((q) => [q.left, q.top, q.width, q.height]);
    if (rects.length && color) add(el, text, color, rects, 'text');
  }
  for (const el of document.querySelectorAll('input[placeholder], textarea[placeholder]')) {
    if (el.value || !visible(el) || el.disabled) continue;
    const color = parse(getComputedStyle(el, '::placeholder').color);
    const q = el.getBoundingClientRect();
    if (color && q.width > 2) add(el, el.placeholder, color, [[q.left, q.top, q.width, q.height]], 'placeholder');
  }
  window.__cmEls = els;
  return items;
}
"""

_HIDE = """(on) => { (window.__cmEls || []).forEach((el) => {
  if (on) { if (el.__cm) return; el.__cm = [el.style.getPropertyValue('color'), el.style.getPropertyValue('-webkit-text-fill-color'), el.style.getPropertyValue('text-shadow'), el.placeholder];
    el.style.setProperty('color', 'transparent', 'important'); el.style.setProperty('-webkit-text-fill-color', 'transparent', 'important'); el.style.setProperty('text-shadow', 'none', 'important'); if (el.placeholder) el.placeholder = ''; }
  else if (el.__cm) { ['color', '-webkit-text-fill-color', 'text-shadow'].forEach((k, j) => el.__cm[j] ? el.style.setProperty(k, el.__cm[j]) : el.style.removeProperty(k)); if (el.__cm[3]) el.placeholder = el.__cm[3]; delete el.__cm; } }); }"""

# runs in a blank page (no CSP): decode the screenshot natively and take, for every element, the low tail of its per-pixel contrast
_ANALYSE = r"""
async ([b64, items]) => {
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const bmp = await createImageBitmap(new Blob([bytes], { type: 'image/png' }));
  const cv = new OffscreenCanvas(bmp.width, bmp.height), g = cv.getContext('2d', { willReadFrequently: true });
  g.drawImage(bmp, 0, 0);
  const lin = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  const lum = (r, gg, b) => 0.2126 * lin(r) + 0.7152 * lin(gg) + 0.0722 * lin(b);
  return items.map((it) => {
    const a = it.color[3] * it.opacity, ratios = [];
    for (const [x, y, w, h] of it.rects) {
      const x0 = Math.max(0, Math.floor(x)), y0 = Math.max(0, Math.floor(y)), x1 = Math.min(bmp.width, Math.ceil(x + w)), y1 = Math.min(bmp.height, Math.ceil(y + h));
      if (x1 <= x0 || y1 <= y0) continue;
      const d = g.getImageData(x0, y0, x1 - x0, y1 - y0).data;
      const step = Math.max(1, Math.floor(((x1 - x0) * (y1 - y0)) / 600));
      for (let p = 0; p < d.length; p += 4 * step) {
        const br = d[p], bg = d[p + 1], bb = d[p + 2];
        const fr = br * (1 - a) + it.color[0] * a, fg = bg * (1 - a) + it.color[1] * a, fb = bb * (1 - a) + it.color[2] * a;
        const L1 = lum(fr, fg, fb), L2 = lum(br, bg, bb);
        ratios.push((Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05));
      }
    }
    ratios.sort((p, q) => p - q);
    return { i: it.i, ratio: ratios.length ? ratios[Math.floor(ratios.length * 0.03)] : null };
  });
}
"""


def measure_contrast(page, ctx, shots=1, pause_ms=120, normal=4.5, large=3.0):
    """Every visible text element whose contrast against the pixels behind it is below WCAG AA (4.5, or 3.0 for large text). Returns [(ratio, needed, path, text)], worst first.

    ``shots`` > 1 repeats the screenshot (the room is animated) and keeps each element's worst reading.
    """
    items = page.evaluate(_COLLECT)
    if not items:
        return []
    blank = ctx.new_page()
    worst = {}
    try:
        page.evaluate(_HIDE, True)
        for _ in range(shots):
            page.wait_for_timeout(pause_ms)
            png = base64.b64encode(page.screenshot()).decode()
            for r in blank.evaluate(_ANALYSE, [png, items]):
                if r["ratio"] is not None:
                    worst[r["i"]] = min(worst.get(r["i"], 99), r["ratio"])
    finally:
        page.evaluate(_HIDE, False)
        blank.close()
    out = []
    for it in items:
        ratio = worst.get(it["i"])
        need = large if it["large"] else normal
        if ratio is not None and ratio < need:
            out.append((round(ratio, 2), need, it["path"], it["text"]))
    return sorted(out)


def measure_contrast_scrolled(page, ctx, selector="#paper", **kw):
    """``measure_contrast`` down a scrolling box (the paper), one screenful at a time, so a normal-sized window can be used."""
    seen = {}
    total, shown = page.evaluate("(s) => { const e = document.querySelector(s); return [e.scrollHeight, e.clientHeight]; }", selector)
    pos = 0
    while True:
        page.evaluate("([s, y]) => { document.querySelector(s).scrollTop = y; }", [selector, pos])
        for f in measure_contrast(page, ctx, **kw):
            seen.setdefault((f[2], f[3]), f)
        if pos + shown >= total:
            break
        pos += max(60, int(shown * 0.7))
    page.evaluate("(s) => { document.querySelector(s).scrollTop = 0; }", selector)
    return sorted(seen.values())


def fmt_contrast(found):
    return "\n".join(f"{r:5.2f} < {n}  {p}  \"{t}\"" for r, n, p, t in found)
