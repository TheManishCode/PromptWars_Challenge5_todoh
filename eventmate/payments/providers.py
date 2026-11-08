"""How an approved payment is carried out (WP4 b, c).

``PaymentProvider`` has three jobs: ``create_instruction`` (carry out, or say exactly how to carry out, one approved payment), ``status`` (what the provider says now) and ``reconcile`` (turn that
into what the app should record). Two exist:

* ``ManualProvider`` (the default, always available): moves nothing. It writes down exactly what to pay, to whom and how, for the user to pay in their own bank or app, and the user then says it was paid.
* ``PayPalSandboxProvider``: behind ``EVENTMATE_PAYPAL_MODE=sandbox`` (off by default), sandbox only. The code refuses any other host. It uses PayPal's Payouts API, which sends money out of the account's own balance;
  PayPal's country table lists India as "Receive and withdraw" only, so an account registered in India cannot send payouts, and nothing here decides whether Payouts is the right route (docs/INTEGRATIONS.md,
  docs/BLOCKED.md). Official pages, read 2026-11-09: https://developer.paypal.com/docs/payouts/standard/ , https://developer.paypal.com/payouts/supported-features ,
  https://developer.paypal.com/api/rest/authentication/ , https://developer.paypal.com/docs/api/payments.payouts-batch/v1/ . The request body fields below follow PayPal's public Payouts reference
  but were not re-read from the spec page here; the sandbox run (not run) is what verifies them.

UPI links and gateway payment links are NOT built: the official UPI linking specification could not be read (the page answered HTTP 403) and the gateway pages describe a merchant collecting money, not an
app paying a vendor. They are listed as unverified in docs/INTEGRATIONS.md.
"""

from __future__ import annotations

import base64
import http.client
import json
import ssl
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlencode

SANDBOX_HOST = "api-m.sandbox.paypal.com"  # the only host this adapter will talk to


class ProviderError(Exception):
    """A provider could not carry out or report a payment. The message is fixed wording: no secret, none of the provider's own text."""


@dataclass(frozen=True)
class PaymentRequest:
    """Exactly what was approved. A provider is handed this and nothing else."""

    proposal_id: str
    plan_id: str
    kind: str  # "deposit" | "balance"
    amount_inr: int
    currency: str
    payee_name: str
    payee_method: str
    payee_handle: str
    quote_message_id: str
    note: str = ""


@dataclass(frozen=True)
class Instruction:
    provider: str
    reference: str  # the provider's id for this payment; empty for a manual one
    text: str  # what the user is told, in words
    state: str  # "instructed" (the user must pay) | "submitted" (the provider accepted it) | "failed"
    moved_money: bool = False  # True only when a provider reports that it sent money


@dataclass(frozen=True)
class ProviderStatus:
    reference: str
    state: str  # "pending" | "completed" | "failed" | "unknown"
    detail: str = ""


class PaymentProvider(Protocol):
    name: str

    def create_instruction(self, req: PaymentRequest) -> Instruction: ...

    def status(self, reference: str) -> ProviderStatus: ...

    def reconcile(self, req: PaymentRequest, reference: str) -> tuple[str, str]: ...


class ManualProvider:
    name = "manual"

    def create_instruction(self, req: PaymentRequest) -> Instruction:
        how = {"bank_transfer": "by bank transfer", "upi": f"by UPI to {req.payee_handle}", "paypal": f"by PayPal to {req.payee_handle}", "cash": "in cash", "cheque": "by cheque",
               "card_link": "through the card payment link the vendor gave you", "other": "the way you and the vendor agreed"}.get(req.payee_method, "the way you and the vendor agreed")
        text = (f"Pay INR {req.amount_inr:,} ({req.kind}) to {req.payee_name} {how}. Use the details in the vendor's own message, and check them with the vendor before you pay. "
                "to;doh-the event planner does not move this money. When you have paid, press “I paid” and enter the reference you were given. "
                "A payment is not a booking, and the app does not guarantee that the vendor delivers.")
        return Instruction(provider=self.name, reference="", text=text, state="instructed")

    def status(self, reference: str) -> ProviderStatus:
        return ProviderStatus(reference=reference, state="unknown", detail="a manual payment is confirmed by you")

    def reconcile(self, req: PaymentRequest, reference: str) -> tuple[str, str]:
        return "unknown", "only you can say whether a manual payment was made"


@dataclass(frozen=True)
class PayPalConfig:
    mode: str = "off"  # "off" (default) or "sandbox"; there is no live mode in this code
    client_id: str = field(default="", repr=False)
    client_secret: str = field(default="", repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> PayPalConfig:
        mode = env.get("EVENTMATE_PAYPAL_MODE", "").strip().lower()
        return cls(mode="sandbox" if mode == "sandbox" else "off", client_id=env.get("EVENTMATE_PAYPAL_CLIENT_ID", "").strip(), client_secret=env.get("EVENTMATE_PAYPAL_CLIENT_SECRET", "").strip())

    @property
    def ready(self) -> bool:
        return self.mode == "sandbox" and bool(self.client_id and self.client_secret)


Transport = Callable[[str, str, dict[str, str], bytes | None], tuple[int, dict]]  # (method, path, headers, body) -> (HTTP status, JSON body); the host is fixed inside


def _https(method: str, path: str, headers: dict[str, str], body: bytes | None) -> tuple[int, dict]:
    conn = http.client.HTTPSConnection(SANDBOX_HOST, timeout=20, context=ssl.create_default_context())
    try:
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        raw = resp.read(65_537)[:65_536]
        try:
            doc = json.loads(raw) if raw else {}
        except ValueError:
            doc = {}
        return resp.status, doc if isinstance(doc, dict) else {}
    finally:
        conn.close()


class PayPalSandboxProvider:
    name = "paypal_sandbox"

    def __init__(self, config: PayPalConfig, transport: Transport = _https, now: Callable[[], float] = time.monotonic):
        self.config, self._call, self._now = config, transport, now
        self._token, self._expires = "", 0.0
        self._lock = threading.Lock()

    def _require(self) -> None:
        if not self.config.ready:
            raise ProviderError("the PayPal sandbox is switched off (EVENTMATE_PAYPAL_MODE=sandbox and the sandbox client id and secret are needed)")

    def _access_token(self) -> str:
        with self._lock:
            if self._token and self._now() < self._expires - 60:
                return self._token
            basic = base64.b64encode(f"{self.config.client_id}:{self.config.client_secret}".encode()).decode()
            try:
                status, doc = self._call("POST", "/v1/oauth2/token", {"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
                                         urlencode({"grant_type": "client_credentials"}).encode())
            except (OSError, ssl.SSLError, http.client.HTTPException):
                raise ProviderError("could not reach PayPal's sandbox") from None
            token, ttl = doc.get("access_token"), doc.get("expires_in")
            if status != 200 or not isinstance(token, str) or not token:
                raise ProviderError("PayPal's sandbox rejected the client id or secret" if status in (400, 401) else f"PayPal's sandbox answered HTTP {status}")
            self._token, self._expires = token, self._now() + (ttl if isinstance(ttl, (int, float)) and not isinstance(ttl, bool) and 60 <= ttl <= 86_400 else 3000)
            return self._token

    def create_instruction(self, req: PaymentRequest) -> Instruction:
        self._require()
        if req.payee_method != "paypal" or not req.payee_handle:
            raise ProviderError("PayPal can only pay a payee whose PayPal email address is on the terms")
        body = json.dumps({
            "sender_batch_header": {"sender_batch_id": req.proposal_id, "email_subject": "Payment", "email_message": "A payment you agreed with the person who asked for your quote."},  # the proposal id makes a retry the same batch
            "items": [{"recipient_type": "EMAIL", "receiver": req.payee_handle, "amount": {"value": f"{req.amount_inr}.00", "currency": req.currency}, "note": f"{req.kind} for the agreed quote"[:200],
                       "sender_item_id": req.proposal_id}],
        }).encode()
        try:
            status, doc = self._call("POST", "/v1/payments/payouts", {"Authorization": f"Bearer {self._access_token()}", "Content-Type": "application/json", "Accept": "application/json",
                                                                         "PayPal-Request-Id": req.proposal_id}, body)
        except (OSError, ssl.SSLError, http.client.HTTPException):
            raise ProviderError("the connection to PayPal's sandbox failed while submitting: check its status before trying again") from None
        batch = (doc.get("batch_header") or {}).get("payout_batch_id") if isinstance(doc.get("batch_header"), dict) else None
        if status in (200, 201) and isinstance(batch, str) and batch:
            return Instruction(provider=self.name, reference=batch[:100], text="Submitted to PayPal's sandbox. No real money moved.", state="submitted", moved_money=False)
        raise ProviderError("PayPal's sandbox refused the payment" if status in (400, 403, 422) else f"PayPal's sandbox answered HTTP {status}")

    def status(self, reference: str) -> ProviderStatus:
        self._require()
        if not reference or not all(c.isalnum() or c in "-_" for c in reference):
            raise ProviderError("that is not a PayPal batch id")
        try:
            status, doc = self._call("GET", f"/v1/payments/payouts/{reference}", {"Authorization": f"Bearer {self._access_token()}", "Accept": "application/json"}, None)
        except (OSError, ssl.SSLError, http.client.HTTPException):
            raise ProviderError("could not reach PayPal's sandbox") from None
        header = doc.get("batch_header") if isinstance(doc.get("batch_header"), dict) else {}
        word = str(header.get("batch_status", "")).upper()
        if status != 200:
            return ProviderStatus(reference, "unknown", f"PayPal's sandbox answered HTTP {status}")
        return ProviderStatus(reference, {"SUCCESS": "completed", "PENDING": "pending", "PROCESSING": "pending", "DENIED": "failed", "CANCELED": "failed"}.get(word, "unknown"), word.lower()[:40])

    def reconcile(self, req: PaymentRequest, reference: str) -> tuple[str, str]:
        st = self.status(reference)
        return st.state, st.detail


def provider_from_env(env: Mapping[str, str], transport: Transport | None = None) -> dict[str, PaymentProvider]:
    """The providers that are available: the manual one always, the PayPal sandbox only when its flag and sandbox credentials are set."""
    out: dict[str, PaymentProvider] = {"manual": ManualProvider()}
    cfg = PayPalConfig.from_env(env)
    if cfg.ready:
        out["paypal_sandbox"] = PayPalSandboxProvider(cfg, transport or _https)
    return out
