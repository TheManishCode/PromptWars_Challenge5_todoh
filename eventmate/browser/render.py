"""Render a public page in Chromium with the network taken away from the browser.

Every request the page makes is answered by us: the address is checked, the body is fetched over the same pinned connection ``SafeFetcher`` uses, and the answer
is handed to Chromium with only its type and, for a redirect, its target. Chromium itself cannot resolve a name (``MAP * ~NOTFOUND``), so a request that
slips past the handler fails instead of going out. A fresh context per page: no cookies, no storage, no downloads, no permissions, no service workers, popups
closed, dialogs dismissed. Nothing is clicked or typed. The text is read from the serialised page by the same parser as a static fetch.
"""

from __future__ import annotations

import os
import shutil
import threading
from collections.abc import Callable

from eventmate.browser import policy
from eventmate.integrations.web_access import USER_AGENT, FetchError, Page, SafeFetcher, extract_text, pinned_get
from eventmate.untrusted import UntrustedTextRejected, normalize_untrusted

LAUNCH_ARGS = (
    "--host-resolver-rules=MAP * ~NOTFOUND",
    "--disable-gpu",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-sync",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
)


def chromium_path() -> str | None:
    return shutil.which("chromium") or shutil.which("google-chrome-stable")


def available() -> bool:
    """Opt-in (EVENTMATE_BROWSER=1), Playwright importable and a Chromium present."""
    if os.environ.get("EVENTMATE_BROWSER") != "1" or chromium_path() is None:
        return False
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


class _Budget:
    def __init__(self) -> None:
        self.requests = 0
        self.bytes = 0


class RenderingFetcher:
    """``fetch(url) -> Page`` like ``SafeFetcher``: the static page first, and the rendered one only when the static page has almost no text."""

    def __init__(self, static: SafeFetcher | None = None, enabled: Callable[[], bool] = available, transport=pinned_get, executable: str | None = None):
        self.static = static or SafeFetcher()
        self.enabled, self.transport, self.executable = enabled, transport, executable
        self._lock = threading.Lock()  # Playwright's sync API is not thread-safe; deals fetches pages in a pool

    def fetch(self, url: str) -> Page:
        page = self.static.fetch(url)  # a refusal (unsafe address, robots.txt, bad type) stops here: the browser is never a way around it
        if len(page.text) >= policy.THIN_TEXT_CHARS or not self.enabled():
            return page
        try:
            rendered = self.render(page.url)
        except FetchError:
            return page
        return rendered if len(rendered.text) > len(page.text) else page

    # ------------------------------------------------------------ the browser

    def _answer(self, route, request, budget: _Budget) -> None:
        try:
            subframe = request.frame.parent_frame is not None
        except Exception:  # a navigation whose frame does not exist yet is a popup opening: not followed
            return route.abort()
        why = policy.refusal(request.method, request.resource_type, subframe)
        if why or budget.requests >= policy.MAX_REQUESTS or budget.bytes >= policy.MAX_TOTAL_BYTES:
            return route.abort()
        budget.requests += 1
        try:
            url, _host, ips = self.static._check(request.url)
            status, headers, body = self.transport(url, ips[0], self.static._headers(), self.static.timeout, policy.MAX_RESOURCE_BYTES)
        except Exception:  # a failed or refused request is an aborted one; the reason stays out of the page's reach
            return route.abort()
        budget.bytes += len(body)
        route.fulfill(status=status, headers=policy.relayed(headers), body=body)

    def render(self, url: str) -> Page:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright

        exe = self.executable or chromium_path()
        if exe is None:
            raise FetchError("no browser is available")
        args = list(LAUNCH_ARGS) + (["--no-sandbox"] if os.environ.get("EVENTMATE_BROWSER_NO_SANDBOX") == "1" else [])
        budget = _Budget()
        with self._lock, sync_playwright() as p:
            try:
                browser = p.chromium.launch(executable_path=exe, args=args)
            except PlaywrightError:
                raise FetchError("the browser could not be started") from None
            try:
                ctx = browser.new_context(accept_downloads=False, service_workers="block", permissions=[], user_agent=USER_AGENT, java_script_enabled=True, locale="en-IN")
                ctx.route("**/*", lambda route, request: self._answer(route, request, budget))
                main: list = []
                ctx.on("page", lambda extra: extra.close() if main else None)  # any page after the first is a popup
                page = ctx.new_page()
                main.append(page)
                page.on("dialog", lambda d: d.dismiss())
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=policy.RENDER_TIMEOUT_MS)
                    try:
                        page.wait_for_load_state("networkidle", timeout=policy.SETTLE_TIMEOUT_MS)
                    except PlaywrightError:
                        pass  # a page that never goes quiet is read as it stands
                    final, html = page.url, page.content()
                except PlaywrightError:
                    raise FetchError("the page could not be rendered") from None
            finally:
                browser.close()
        try:
            self.static._check(final)
        except FetchError:
            raise FetchError("the page ended somewhere that is not allowed") from None
        title, text = extract_text(html)
        try:
            text = normalize_untrusted(text, max_len=len(text) + 1)
        except UntrustedTextRejected:
            raise FetchError("the page hides text") from None
        return Page(url=final, title=title, text=text)
