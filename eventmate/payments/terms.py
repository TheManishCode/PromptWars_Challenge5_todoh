"""The money terms of a quote the user confirmed: how much is due when, to whom, by what method (WP4 a).

Terms are *proposed* from the vendor's reply (the confirmed price and deposit, with the vendor's own sentence about the deposit) and become *confirmed* only when the user adds who is paid, how,
and the due dates. No bank account number or card number is ever stored: a payee is a name, a method and, where the method needs one, the public handle the vendor gave (a UPI id or a PayPal email).
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections.abc import Callable
from datetime import date, datetime, UTC
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from eventmate.jsonl import InterProcessLock, JsonlLog
from eventmate.schemas import MAX_BUDGET_INR, VendorCategory, _Model

Method = Literal["bank_transfer", "upi", "paypal", "cash", "cheque", "card_link", "other"]
Status = Literal["proposed", "confirmed", "deposit_paid", "paid", "refunded", "cancelled"]
_UPI = re.compile(r"^[A-Za-z0-9._-]{2,64}@[A-Za-z][A-Za-z0-9]{1,31}$")
_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")
_LONG_DIGITS = re.compile(r"\d[\d\s-]{7,}\d")  # eight or more digits in a row: an account, card or phone number, none of which this keeps


def _clean(v: str) -> str:
    return "".join(ch for ch in v if ch == " " or unicodedata.category(ch)[0] != "C").strip()


class PaymentTerms(_Model):
    terms_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    plan_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    version: int = Field(ge=1)  # each change is a new line with the next version; the highest is current
    category: VendorCategory
    vendor_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    vendor_name: str = Field(min_length=1, max_length=120)
    message_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")  # the vendor's reply the quote came from
    currency: Literal["INR"] = "INR"
    total_inr: int = Field(gt=0, le=MAX_BUDGET_INR)
    deposit_inr: int = Field(ge=0, le=MAX_BUDGET_INR)
    balance_inr: int = Field(ge=0, le=MAX_BUDGET_INR)  # total minus deposit
    deposit_due: date | None = None
    balance_due: date | None = None
    payee_name: str = Field(default="", max_length=120)
    payee_method: Method | None = None
    payee_handle: str = Field(default="", max_length=100)
    source_sentence: str = Field(default="", max_length=300)  # the vendor's own words about the deposit
    status: Status = "proposed"
    paid_inr: int = Field(default=0, ge=0, le=MAX_BUDGET_INR)
    refunded_inr: int = Field(default=0, ge=0, le=MAX_BUDGET_INR)
    proposed_at: datetime
    confirmed_by: str | None = Field(default=None, min_length=1, max_length=60)
    confirmed_at: datetime | None = None
    notice: str = "A quote is not a booking and a payment instruction is not a payment. to;doh-the event planner does not guarantee that a vendor delivers."

    @field_validator("payee_name", "payee_handle", "source_sentence")
    @classmethod
    def _text_ok(cls, v: str) -> str:
        return _clean(v)

    @model_validator(mode="after")
    def _consistent(self) -> PaymentTerms:
        if self.deposit_inr > self.total_inr or self.deposit_inr + self.balance_inr != self.total_inr:
            raise ValueError("the deposit and the balance must add up to the total")
        if self.status != "proposed":
            if not (self.payee_name and self.payee_method and self.confirmed_by):
                raise ValueError("confirmed terms name who is paid, how, and who confirmed them")
        if self.payee_handle and _LONG_DIGITS.search(self.payee_handle):
            raise ValueError("no account, card or phone numbers are kept here: say how to pay in words (the method) and keep the number in the vendor's own message")
        if self.payee_method == "upi" and not _UPI.fullmatch(self.payee_handle or ""):
            raise ValueError("a UPI payee needs the vendor's UPI id, like name@bank")
        if self.payee_method == "paypal" and not _EMAIL.fullmatch(self.payee_handle or ""):
            raise ValueError("a PayPal payee needs the vendor's PayPal email address")
        if self.deposit_due and self.balance_due and self.balance_due < self.deposit_due:
            raise ValueError("the balance cannot be due before the deposit")
        return self


class _File(JsonlLog[PaymentTerms]):
    channel = "payment_terms"
    model = PaymentTerms


class TermsStore:
    def __init__(self, root: Path, now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.root = Path(root) / "payment_terms"
        self.now = now
        self._lock = InterProcessLock(self.root / ".lock")

    def _file(self, plan_id: str) -> _File:
        return _File(self.root, run_id=plan_id)

    def all(self, plan_id: str) -> list[PaymentTerms]:
        """The current version of each set of terms, in the order they were first proposed."""
        latest: dict[str, PaymentTerms] = {}
        for t in self._file(plan_id).read():
            if t.terms_id not in latest or t.version >= latest[t.terms_id].version:
                latest[t.terms_id] = t
        return list(latest.values())

    def get(self, plan_id: str, terms_id: str) -> PaymentTerms | None:
        return next((t for t in self.all(plan_id) if t.terms_id == terms_id), None)

    def for_message(self, plan_id: str, message_id: str) -> PaymentTerms | None:
        return next((t for t in self.all(plan_id) if t.message_id == message_id and t.status != "cancelled"), None)

    def propose(self, plan_id: str, **fields) -> PaymentTerms:
        """New terms for a reply, or the ones already there: asking twice never makes two."""
        with self._lock():
            have = self.for_message(plan_id, fields["message_id"])
            if have is not None:
                return have
            t = PaymentTerms(terms_id=uuid.uuid4().hex, plan_id=plan_id, version=1, proposed_at=self.now(), **fields)
            return self._file(plan_id)._append(t, f"{t.terms_id}:1")

    def update(self, plan_id: str, terms_id: str, expect_version: int | None = None, **changes) -> PaymentTerms:
        """The next version of these terms. ``expect_version`` refuses a change made against terms that have since moved on (two tabs, two people)."""
        with self._lock():
            cur = self.get(plan_id, terms_id)
            if cur is None:
                raise ValueError("no such payment terms")
            if expect_version is not None and expect_version != cur.version:
                raise ValueError("these terms changed in the meantime: reload them and try again")
            new = PaymentTerms.model_validate({**cur.model_dump(), **changes, "version": cur.version + 1})
            return self._file(plan_id)._append(new, f"{new.terms_id}:{new.version}")
