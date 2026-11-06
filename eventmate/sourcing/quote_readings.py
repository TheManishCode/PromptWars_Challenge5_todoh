"""A vendor's reply as a quote: what each field says, what the user has confirmed, how complete it is, and what does not fit the event.

The reply is read by rules (``quote_template.read_message``) and never changed. Nothing it says counts until the user confirms that field: a confirmation is its own append-only record
(who, when, the value, whether they typed a different one), the latest per field wins, and taking one back is a record too. An amount (the price, the deposit) is confirmed one at a time;
the rest can be confirmed together. Completeness is the share of required fields the user has confirmed, with missing and unclear ones left out, never filled in. A quote is ``comparable``
only when every required field is confirmed, the vendor said it is available, and nothing confirmed contradicts the event. Fit issues are shown beside a read value too, marked tentative.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, UTC
from pathlib import Path
from typing import Literal

from pydantic import Field

from eventmate.jsonl import InterProcessLock, JsonlLog
from eventmate.schemas import InboundMessage, VendorCategory, _Model
from eventmate.sourcing import quote_template as qt

FieldState = Literal["confirmed", "read", "unclear", "missing"]


class QuoteConfirmation(_Model):
    confirmation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    plan_key: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")  # a plan id, or brief_<staged id>
    message_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    vendor_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    field: str = Field(max_length=30)
    value: str | None = Field(default=None, max_length=300)  # the confirmed value, normalised; None when taken back
    confirmed_by: str = Field(min_length=1, max_length=60)
    confirmed_at: datetime
    edited: bool = False  # the user confirmed a different value than the one read (or typed one where none could be read)
    retracted: bool = False


class _File(JsonlLog[QuoteConfirmation]):
    channel = "quote_confirmation"
    model = QuoteConfirmation


class QuoteConfirmationStore:
    def __init__(self, root: Path, now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.root = Path(root) / "quote_confirmations"
        self.now = now
        self._lock = InterProcessLock(self.root / ".lock")

    def _file(self) -> _File:
        return _File(self.root, run_id="all")

    def history(self, plan_key: str, message_id: str) -> list[QuoteConfirmation]:
        if not (self.root / "quote_confirmation-all.jsonl").exists():
            return []
        return [c for c in self._file().read() if c.plan_key == plan_key and c.message_id == message_id]

    def latest(self, plan_key: str, message_id: str) -> dict[str, QuoteConfirmation]:
        out: dict[str, QuoteConfirmation] = {}
        for c in self.history(plan_key, message_id):
            if c.retracted:
                out.pop(c.field, None)
            else:
                out[c.field] = c
        return out

    def add(self, plan_key: str, message_id: str, vendor_id: str, field: str, value: str | None, by: str, edited: bool = False, retracted: bool = False) -> QuoteConfirmation:
        rec = QuoteConfirmation(confirmation_id=uuid.uuid4().hex, plan_key=plan_key, message_id=message_id, vendor_id=vendor_id, field=field, value=value, confirmed_by=by.strip(),
                                confirmed_at=self.now(), edited=edited, retracted=retracted)
        with self._lock():
            return self._file()._append(rec, rec.confirmation_id)


class FieldView(_Model):
    key: str
    label: str
    money: bool
    state: FieldState
    value: str | None = None  # what stands: the confirmed value, else the one read
    read_value: str | None = None
    evidence: str = ""
    note: str = ""
    source: Literal["template", "free_text"] = "template"
    edited: bool = False
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None


class Issue(_Model):
    field: str
    text: str
    tentative: bool  # judged from a value the user has not confirmed yet
    blocking: bool = True  # False for a note (taxes are extra): it changes what the price means, not whether the vendor fits the event


class QuoteAssessment(_Model):
    message_id: str
    vendor_id: str
    category: VendorCategory
    received_at: datetime
    template_used: bool
    fields: tuple[FieldView, ...]
    required_total: int
    confirmed_required: int
    read_required: int  # confirmed, or read and waiting for the user
    index: float  # confirmed required fields over required fields
    comparable: bool
    issues: tuple[Issue, ...] = ()


def _issues(category: VendorCategory, by: dict[str, FieldView], guests: int, diets: tuple[str, ...]) -> list[Issue]:
    out: list[Issue] = []

    def have(key: str):
        v = by.get(key)
        return (v.value, v.state != "confirmed") if v is not None and v.value is not None and v.state in ("confirmed", "read") else (None, True)

    value, tentative = have("available")
    if value == "no":
        out.append(Issue(field="available", text="the vendor says it is not available on your date", tentative=tentative))
    value, tentative = have("taxes_included")
    if value == "no":
        out.append(Issue(field="taxes_included", text="taxes are extra: add them before comparing this price with another", tentative=tentative, blocking=False))
    if category == VendorCategory.CATERER:
        lo, t1 = have("min_guests")
        hi, t2 = have("max_guests")
        if lo is not None and guests < int(lo):
            out.append(Issue(field="min_guests", text=f"their minimum is {lo} guests and you have {guests}", tentative=t1))
        if hi is not None and guests > int(hi):
            out.append(Issue(field="max_guests", text=f"their maximum is {hi} guests and you have {guests}", tentative=t2))
        offered, t3 = have("diets")
        if offered is not None:
            missing = sorted(set(diets) - set(offered.split(",")))
            if missing:
                out.append(Issue(field="diets", text="they do not list: " + ", ".join(d.replace("_", " ") for d in missing) + ", which your brief needs", tentative=t3))
    if category == VendorCategory.VENUE:
        cap, tentative = have("capacity")
        if cap is not None and guests > int(cap):
            out.append(Issue(field="capacity", text=f"their capacity is {cap} guests and you have {guests}", tentative=tentative))
    return out


def assess(msg: InboundMessage, category: VendorCategory, confirmations: dict[str, QuoteConfirmation], guests: int, diets: tuple[str, ...]) -> QuoteAssessment:
    reading = qt.read_message(msg, category)
    views: list[FieldView] = []
    for key in qt.ASKED[category]:
        r, c = reading.fields[key], confirmations.get(key)
        label, kind = qt.FIELDS[key]
        views.append(FieldView(
            key=key, label=label, money=key in qt.MONEY or kind == "money", state="confirmed" if c else r.state, value=c.value if c else r.value, read_value=r.value, evidence=r.evidence, note=r.note,
            source=r.source, edited=bool(c and c.edited), confirmed_by=c.confirmed_by if c else None, confirmed_at=c.confirmed_at if c else None,
        ))
    by = {v.key: v for v in views}
    required = qt.REQUIRED[category]
    confirmed = sum(1 for k in required if by[k].state == "confirmed")
    read = sum(1 for k in required if by[k].state in ("confirmed", "read"))
    issues = _issues(category, by, guests, diets)
    comparable = confirmed == len(required) and by["available"].value == "yes" and not any(i.blocking and not i.tentative for i in issues)
    return QuoteAssessment(
        message_id=msg.message_id, vendor_id=msg.vendor_id, category=category, received_at=msg.received_at, template_used=reading.template_used, fields=tuple(views), required_total=len(required),
        confirmed_required=confirmed, read_required=read, index=round(confirmed / len(required), 3), comparable=comparable, issues=tuple(issues),
    )


def parse_typed(category: VendorCategory, key: str, text: str) -> str:
    """A value the user typed for a field, through the same parser as a vendor's: it must read safely, or it is refused with why."""
    if key not in qt.ASKED[category]:
        raise ValueError(f"{key} is not asked of a {category.value}")
    value, why = qt._parse(key, qt.FIELDS[key][1], text or "")
    if value is None:
        raise ValueError(f"{qt.FIELDS[key][0]}: {why or 'that cannot be used'}")
    return value
