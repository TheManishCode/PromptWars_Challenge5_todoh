"""Parts of the proposed stack that are not built. They raise instead of returning plausible output (AI_CODING_RULES)."""

from __future__ import annotations


def book_event(*_a, **_k):
    """Booking and payment. No payment is ever initiated by this product (ADR-017); this needs its own decision and an approval design."""
    raise NotImplementedError("booking is not implemented: to;doh-the event planner records quotes and messages, it never books or pays")


class BrowserResearcher:
    """Read-only page research by browser-use or Playwright MCP (ADR-010). Not wired: the shipped web access is ``integrations/web_access.py``."""

    def research(self, *_a, **_k):
        raise NotImplementedError("browser research is not implemented (ADR-010 allows only read-only, credential-free discovery)")
