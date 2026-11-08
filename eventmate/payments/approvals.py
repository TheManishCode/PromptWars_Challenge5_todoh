"""A payment is proposed, approved against a hash of exactly what it is, and expires (WP4 b).

The hash covers the plan, the terms (and their version), the kind (deposit or balance), the amount, the currency, who is paid, how, and the quote the money is for. Approve a proposal whose hash does not
match what is stored, or whose terms have changed since, and nothing happens. An approval lasts 24 hours. Every step, including every attempt to carry it out, is one line in an append-only event log.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from eventmate.jsonl import InterProcessLock, JsonlLog
from eventmate.schemas import MAX_BUDGET_INR, _Model

APPROVAL_TTL = timedelta(hours=24)
LIVE = ("proposed", "approved", "executing", "instructed", "submitted")  # a proposal in one of these holds its (terms, kind): no second one is made beside it
Status = Literal["proposed", "approved", "executing", "instructed", "submitted", "paid", "failed", "cancelled", "expired"]


def payment_hash(plan_id: str, terms_id: str, terms_version: int, kind: str, amount_inr: int, currency: str, payee_name: str, payee_method: str, payee_handle: str, quote_message_id: str) -> str:
    doc = {"plan_id": plan_id, "terms_id": terms_id, "terms_version": terms_version, "kind": kind, "amount_inr": amount_inr, "currency": currency, "payee_name": payee_name,
           "payee_method": payee_method, "payee_handle": payee_handle, "quote_message_id": quote_message_id}
    return hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


class PaymentProposal(_Model):
    proposal_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    plan_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    terms_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    terms_version: int = Field(ge=1)
    kind: Literal["deposit", "balance"]
    amount_inr: int = Field(gt=0, le=MAX_BUDGET_INR)
    currency: Literal["INR"] = "INR"
    payee_name: str = Field(min_length=1, max_length=120)
    payee_method: str = Field(min_length=1, max_length=20)
    payee_handle: str = Field(default="", max_length=100)
    quote_message_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Status = "proposed"
    version: int = Field(default=1, ge=1)
    created_at: datetime
    expires_at: datetime
    approved_by: str | None = Field(default=None, min_length=1, max_length=60)
    approved_at: datetime | None = None
    provider: str = Field(default="", max_length=30)
    provider_ref: str = Field(default="", max_length=100)
    instruction: str = Field(default="", max_length=1500)
    paid_by: str | None = Field(default=None, min_length=1, max_length=60)
    paid_at: datetime | None = None
    paid_reference: str = Field(default="", max_length=100)
    error: str = Field(default="", max_length=300)
    notice: str = "Nothing is paid until you pay. A payment is not a booking, and the app does not guarantee that the vendor delivers."

    @field_validator("paid_reference", "error", "instruction")
    @classmethod
    def _text_ok(cls, v: str) -> str:
        return "".join(ch for ch in v if ch in "\n " or ch.isprintable()).strip()

    @model_validator(mode="after")
    def _hash_matches(self) -> PaymentProposal:
        want = payment_hash(self.plan_id, self.terms_id, self.terms_version, self.kind, self.amount_inr, self.currency, self.payee_name, self.payee_method, self.payee_handle, self.quote_message_id)
        if self.content_hash != want:
            raise ValueError("content_hash does not match the content: the record was changed")
        return self


class PaymentEvent(_Model):
    """One thing that happened to a payment, or to the terms it is for. Never edited, never removed: this is the log that says who did what and when."""

    event_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    plan_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    at: datetime
    kind: Literal["terms_proposed", "terms_confirmed", "proposed", "approved", "refused", "attempted", "instructed", "submitted", "paid", "failed", "cancelled", "expired", "reconciled", "refund", "note"]
    by: str = Field(default="", max_length=60)
    proposal_id: str = Field(default="", max_length=32)
    terms_id: str = Field(default="", max_length=32)
    amount_inr: int | None = Field(default=None, ge=0, le=MAX_BUDGET_INR)
    provider: str = Field(default="", max_length=30)
    detail: str = Field(default="", max_length=300)


class _ProposalFile(JsonlLog[PaymentProposal]):
    channel = "payment_proposal"
    model = PaymentProposal


class _EventFile(JsonlLog[PaymentEvent]):
    channel = "payment_event"
    model = PaymentEvent


class ProposalStore:
    def __init__(self, root: Path, now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.root = Path(root) / "payments"
        self.now = now
        self._lock = InterProcessLock(self.root / ".lock")

    def _file(self, plan_id: str) -> _ProposalFile:
        return _ProposalFile(self.root, run_id=plan_id)

    def _events(self, plan_id: str) -> _EventFile:
        return _EventFile(self.root / "events", run_id=plan_id)

    def lock(self):
        return self._lock()

    def all(self, plan_id: str) -> list[PaymentProposal]:
        latest: dict[str, PaymentProposal] = {}
        for p in self._file(plan_id).read():
            if p.proposal_id not in latest or p.version >= latest[p.proposal_id].version:
                latest[p.proposal_id] = p
        return list(latest.values())

    def get(self, plan_id: str, proposal_id: str) -> PaymentProposal | None:
        return next((p for p in self.all(plan_id) if p.proposal_id == proposal_id), None)

    def create(self, plan_id: str, **fields) -> PaymentProposal:
        now = self.now()
        p = PaymentProposal(proposal_id=uuid.uuid4().hex, plan_id=plan_id, created_at=now, expires_at=now + APPROVAL_TTL, **fields)
        return self._file(plan_id)._append(p, f"{p.proposal_id}:1")

    def transition(self, plan_id: str, proposal_id: str, allowed: tuple[str, ...], **changes) -> PaymentProposal:
        """Compare-and-set under the store's cross-process lock: the proposal moves only if it is still in one of the ``allowed`` states."""
        with self._lock():
            cur = self.get(plan_id, proposal_id)
            if cur is None:
                raise ValueError("no such payment")
            if cur.status not in allowed:
                raise ValueError(f"this payment is {cur.status}, not one of {list(allowed)}")
            new = PaymentProposal.model_validate({**cur.model_dump(), **changes, "version": cur.version + 1})
            return self._file(plan_id)._append(new, f"{new.proposal_id}:{new.version}")

    def log(self, plan_id: str, kind: str, by: str = "", proposal_id: str = "", terms_id: str = "", amount_inr: int | None = None, provider: str = "", detail: str = "") -> PaymentEvent:
        e = PaymentEvent(event_id=uuid.uuid4().hex, plan_id=plan_id, at=self.now(), kind=kind, by=by, proposal_id=proposal_id, terms_id=terms_id, amount_inr=amount_inr, provider=provider, detail=detail[:300])
        return self._events(plan_id)._append(e, e.event_id)

    def events(self, plan_id: str) -> list[PaymentEvent]:
        return self._events(plan_id).read()
