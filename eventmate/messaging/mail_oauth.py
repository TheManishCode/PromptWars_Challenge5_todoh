"""OAuth 2.0 (SASL XOAUTH2) sign-in for the mailbox, as an alternative to a password (audit A6).

Official documents this follows, all read on 2026-11-09 (see docs/INTEGRATIONS.md):
* Gmail IMAP/SMTP, XOAUTH2 mechanism: https://developers.google.com/workspace/gmail/imap/xoauth2-protocol
  the initial client response is base64("user=" {user} "\\x01auth=Bearer " {access token} "\\x01\\x01"); the scope is https://mail.google.com/;
  a failure is a base64 JSON challenge to which the client sends an empty response.
* Gmail IMAP/SMTP overview (hosts, ports, session limits): https://developers.google.com/workspace/gmail/imap/imap-smtp
* Google OAuth 2.0 (refresh tokens and when they stop working): https://developers.google.com/identity/protocols/oauth2
  the token endpoint is https://oauth2.googleapis.com/token; the refresh exchange is the standard refresh_token grant (RFC 6749 section 6).

A refresh token, a client id and a client secret are the only secrets; they come from the environment (``EventMate`` never writes them anywhere) and are never put in a log, an error
message or a ``repr``. The access token lives in memory for about an hour. Nothing here opens a network connection until a token is asked for, and the fetch is injectable so tests use no network.
"""

from __future__ import annotations

import http.client
import json
import ssl
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlencode

TOKEN_HOST = "oauth2.googleapis.com"
TOKEN_PATH = "/token"
SCOPE = "https://mail.google.com/"
REFRESH_MARGIN_S = 120  # a token is refreshed this long before it runs out
TIMEOUT_S = 20
MAX_BODY = 65_536


class TokenError(Exception):
    """A usable access token could not be had. The message is a category and what to do about it; it never contains a secret or the provider's own words."""


@dataclass(frozen=True)
class OAuthConfig:
    client_id: str = field(default="", repr=False)
    client_secret: str = field(default="", repr=False)
    refresh_token: str = field(default="", repr=False)

    @property
    def complete(self) -> bool:
        return bool(self.client_id and self.client_secret and self.refresh_token)

    def missing(self) -> list[str]:
        return [n for n, v in (("EVENTMATE_GMAIL_CLIENT_ID", self.client_id), ("EVENTMATE_GMAIL_CLIENT_SECRET", self.client_secret), ("EVENTMATE_GMAIL_REFRESH_TOKEN", self.refresh_token)) if not v]


def xoauth2_string(user: str, access_token: str) -> str:
    """The SASL XOAUTH2 initial client response, before base64 (the libraries encode it)."""
    return f"user={user}\x01auth=Bearer {access_token}\x01\x01"


def _post_token(form: dict[str, str]) -> tuple[int, dict]:
    """The real exchange with Google's token endpoint. Only called when a token is needed and OAuth is the chosen sign-in."""
    conn = http.client.HTTPSConnection(TOKEN_HOST, timeout=TIMEOUT_S, context=ssl.create_default_context())
    try:
        conn.request("POST", TOKEN_PATH, body=urlencode(form), headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
        resp = conn.getresponse()
        raw = resp.read(MAX_BODY + 1)
        try:
            doc = json.loads(raw[:MAX_BODY]) if raw else {}
        except ValueError:
            doc = {}
        return resp.status, doc if isinstance(doc, dict) else {}
    finally:
        conn.close()


class OAuth2TokenSource:
    def __init__(self, config: OAuthConfig, fetch: Callable[[dict[str, str]], tuple[int, dict]] = _post_token, now: Callable[[], float] = time.monotonic):
        self.config, self._fetch, self._now = config, fetch, now
        self._token = ""
        self._expires = 0.0
        self._lock = threading.Lock()

    def token(self) -> str:
        with self._lock:
            if self._token and self._now() < self._expires - REFRESH_MARGIN_S:
                return self._token
            if not self.config.complete:
                raise TokenError("sign-in by OAuth is chosen but not complete: set " + ", ".join(self.config.missing()))
            try:
                status, doc = self._fetch({"client_id": self.config.client_id, "client_secret": self.config.client_secret, "refresh_token": self.config.refresh_token, "grant_type": "refresh_token"})
            except (OSError, ssl.SSLError, http.client.HTTPException):
                raise TokenError("could not reach Google's token service; nothing was sent") from None
            if status == 200 and isinstance(doc.get("access_token"), str) and doc["access_token"]:
                expires_in = doc.get("expires_in")
                self._token = doc["access_token"]
                self._expires = self._now() + (expires_in if isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool) and 60 <= expires_in <= 86_400 else 3000)
                return self._token
            self._token = ""
            error = doc.get("error") if isinstance(doc.get("error"), str) else ""
            if error == "invalid_grant":
                raise TokenError("Google rejected the refresh token: it was revoked, it expired (an app still in 'Testing' gets tokens that last 7 days; a token unused for six months, or after a password change, stops working), "
                                 "or the 100-token limit replaced it. Create a new one (docs/EMAIL_SETUP.md)")
            if error in ("invalid_client", "unauthorized_client"):
                raise TokenError("Google rejected the client id or secret: check EVENTMATE_GMAIL_CLIENT_ID and EVENTMATE_GMAIL_CLIENT_SECRET")
            raise TokenError(f"Google's token service answered HTTP {status}; nothing was sent")
