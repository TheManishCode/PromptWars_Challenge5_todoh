"""The read-only browser: no model, no input, no network of its own, and only checked GET requests answered by our pinned fetch."""

import ast
import re
from pathlib import Path

import pytest

from eventmate.browser import policy
from eventmate.browser.render import RenderingFetcher
from eventmate.integrations.web_access import FetchError, SafeFetcher
from tests.browser_helpers import CHROMIUM

PKG = Path(__file__).resolve().parent.parent / "eventmate/browser"
PUBLIC = "93.184.216.34"


def test_only_get_requests_for_pages_scripts_styles_and_data_are_made():
    assert policy.refusal("GET", "document", False) is None and policy.refusal("get", "script", False) is None
    for method in ("POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"):
        assert policy.refusal(method, "fetch", False)
    for kind in ("image", "media", "font", "websocket", "eventsource", "manifest", "ping", "other", "texttrack"):
        assert policy.refusal("GET", kind, False)
    assert policy.refusal("GET", "document", True)  # a page inside the page


def test_nothing_but_the_content_type_and_a_redirect_target_reaches_the_browser():
    got = policy.relayed({"Content-Type": "text/html", "Set-Cookie": "a=b", "Location": "https://x.example/", "Content-Security-Policy": "x", "X-Evil": "1"})
    assert got == {"content-type": "text/html", "location": "https://x.example/"}


def test_the_package_has_no_model_and_never_acts_on_a_page():
    for f in PKG.glob("*.py"):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            for m in mods:
                assert not re.search(r"\b(llm|assistant|anthropic|openai|messaging|outreach)\b", m), (f.name, m)
        src = f.read_text()
        for verb in (".click(", ".fill(", ".type(", ".press(", ".check(", ".select_option(", ".set_input_files(", ".evaluate(", ".add_init_script(", ".expose_function(", ".dispatch_event("):
            assert verb not in src, (f.name, verb)


# ------------------------------------------------------------- a real Chromium, fed by a fake transport

SITE = "https://vendor.example"
PAGES = {
    "/": ("text/html", b'<html><head><title>Cakes</title></head><body><div id=o></div><script src="/app.js"></script></body></html>'),
    "/app.js": (
        "application/javascript",
        b"""
        fetch('/data').then(r => r.text()).then(t => { document.getElementById('o').textContent = t; });
        fetch('/leak', {method: 'POST', body: 'secret'}).catch(() => {});
        new Image().src = 'http://169.254.169.254/latest/meta-data/';
        fetch('http://10.0.0.5/admin').catch(() => {});
        fetch('http://evil.example:8080/x').catch(() => {});
        window.open('/popup');
        document.cookie = 'k=v';
        alert('hi');
        """,
    ),
    "/data": ("text/plain", b"Photo cakes from Rs 1,200 per kg, Pune, call 9876543210"),
    "/popup": ("text/html", b"<html><body>popup</body></html>"),
}


class Net:
    def __init__(self, pages=PAGES):
        self.pages, self.seen = pages, []

    def __call__(self, url, ip, headers, timeout, max_bytes):
        self.seen.append((url, ip, dict(headers)))
        path = url.split(".example", 1)[1] or "/"
        path = path.split("?")[0]
        if path in self.pages:
            ctype, body = self.pages[path]
            return 200, {"content-type": ctype, "set-cookie": "tracker=1"}, body
        return 404, {}, b""


def resolver(host):
    return [PUBLIC] if host.endswith(".example") else []


@pytest.fixture
def net():
    return Net()


def fetcher(net, enabled=True):
    return RenderingFetcher(SafeFetcher(transport=net, resolver=resolver), enabled=lambda: enabled, transport=net, executable=CHROMIUM)


def test_a_thin_page_is_rendered_and_the_text_the_scripts_wrote_is_returned(net):
    pytest.importorskip("playwright.sync_api")
    if CHROMIUM is None:
        pytest.skip("no Chromium on this machine")
    page = fetcher(net).fetch(SITE + "/")
    assert "Photo cakes from Rs 1,200 per kg" in page.text and page.title == "Cakes"


def test_the_page_can_only_cause_checked_get_requests(net):
    pytest.importorskip("playwright.sync_api")
    if CHROMIUM is None:
        pytest.skip("no Chromium on this machine")
    fetcher(net).fetch(SITE + "/")
    urls = {u for u, _, _ in net.seen}
    assert urls <= {SITE + "/", SITE + "/robots.txt", SITE + "/app.js", SITE + "/data"}, urls  # no POST target, no metadata address, no private address, no other port, no popup
    assert all(ip == PUBLIC for _, ip, _ in net.seen)  # every connection is pinned to the address that was checked
    assert all("cookie" not in {k.lower() for k in h} for _, _, h in net.seen)  # no cookie ever goes out, nor does one come back to the page


def test_a_page_with_enough_text_is_not_rendered_and_a_refusal_is_never_bypassed(net):
    long_page = {"/": ("text/html", b"<html><body>" + b"<p>Wedding cakes in Pune from Rs 900 per kg.</p>" * 30 + b"</body></html>")}
    n = Net(long_page)
    page = fetcher(n).fetch(SITE + "/")
    assert "Wedding cakes" in page.text and [u for u, _, _ in n.seen if u.endswith("/app.js")] == []
    with pytest.raises(FetchError):  # a private address is refused by the static fetch, and the browser is not tried
        fetcher(net).fetch("http://10.0.0.5/")
    assert fetcher(net, enabled=False).fetch(SITE + "/").text == ""  # browser off: the thin static page, as before


def test_a_broken_browser_gives_the_static_page_not_an_error(net):
    page = RenderingFetcher(SafeFetcher(transport=net, resolver=resolver), enabled=lambda: True, transport=net, executable="/nonexistent/chromium").fetch(SITE + "/")
    assert page.title == "Cakes"
