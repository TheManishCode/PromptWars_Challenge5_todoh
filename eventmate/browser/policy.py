"""What a page being rendered may ask the network for. Pure functions, so every rule is tested without a browser.

The page is hostile. It may run scripts, but the only things it can cause are GET requests for a page, a script, a style sheet or a data fetch, each one
checked (public address only, default port, no credentials) and fetched by our pinned connection, not by the browser.
"""

from __future__ import annotations

MAX_REQUESTS = 60  # per page, all kinds
MAX_RESOURCE_BYTES = 1_500_000  # per response
MAX_TOTAL_BYTES = 6_000_000  # per page
RENDER_TIMEOUT_MS = 20_000
SETTLE_TIMEOUT_MS = 4_000  # waiting for the network to go quiet after the page's own load
THIN_TEXT_CHARS = 400  # a fetched page with less text than this is a candidate for rendering
ALLOWED_TYPES = frozenset({"document", "stylesheet", "script", "xhr", "fetch"})
RELAYED_HEADERS = ("content-type", "location")  # nothing else reaches the browser: no cookies, no cache or security headers from a third party


def refusal(method: str, resource_type: str, is_subframe: bool) -> str | None:
    """Why this request is not made, or None. The address itself is checked separately (``check_url``)."""
    if method.upper() != "GET":
        return "only GET requests are made"
    if resource_type not in ALLOWED_TYPES:
        return f"{resource_type} requests are not made"
    if is_subframe:
        return "pages inside the page are not loaded"
    return None


def relayed(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in ((k.lower(), v) for k, v in headers.items()) if k in RELAYED_HEADERS}
