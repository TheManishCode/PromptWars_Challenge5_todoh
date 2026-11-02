"""Evidence ledger: every fact the product shows has one explicit state, from "a model suggested it" to "the user says it is booked".

An append-only JSONL file holds the records, so nothing is edited or deleted; the answer for a (subject, field) is recomputed from the file. Who may
create which state is enforced here by the source prefix, not left to callers: a model can only suggest, a page or tool can only discover or extract,
a person confirms or books, and only code verifies (``verify``, after a check passes). Lower evidence never replaces higher evidence.
"""

from __future__ import annotations

import uuid
from datetime import datetime, UTC
from enum import Enum
from pathlib import Path
from collections.abc import Callable

from pydantic import Field, JsonValue, model_validator

from eventmate.jsonl import InterProcessLock, JsonlLog
from eventmate.schemas import _Model

_ID = r"^[A-Za-z0-9_:.-]{1,64}$"


class FactState(str, Enum):
    SUGGESTED = "suggested"  # a model's idea: never feeds a hard constraint
    DISCOVERED = "discovered"  # a listing or page says it exists
    EXTRACTED = "extracted"  # a value read from a page, PDF or reply, unreviewed
    USER_CONFIRMED = "user_confirmed"  # the user said it is right
    VERIFIED = "verified"  # confirmed and re-checked by code
    BOOKED = "booked"  # the user states it is booked


RANK = {s: i for i, s in enumerate(FactState)}  # declaration order is the order of trust
HARD = frozenset({FactState.VERIFIED, FactState.BOOKED})  # what a hard constraint may rest on (seed data is tagged as such by its source)

# which states a source may create; the longest matching prefix wins
ALLOWED: dict[str, frozenset] = {
    "llm:": frozenset({FactState.SUGGESTED}),
    "tool:": frozenset({FactState.DISCOVERED, FactState.EXTRACTED}),
    "web:": frozenset({FactState.DISCOVERED, FactState.EXTRACTED}),
    "doc:": frozenset({FactState.EXTRACTED}),
    "seed:": frozenset({FactState.VERIFIED}),
    "user_input": frozenset({FactState.USER_CONFIRMED, FactState.BOOKED}),
    "code:": frozenset({FactState.VERIFIED}),
}


def allowed_states(source: str) -> frozenset:
    hits = [p for p in ALLOWED if source.startswith(p)]
    return ALLOWED[max(hits, key=len)] if hits else frozenset()


class EvidenceRecord(_Model):
    id: str = Field(pattern=_ID)
    subject: str = Field(pattern=_ID)  # vendor id, venue id or plan id
    field: str = Field(pattern=_ID)  # "price_per_plate_inr", "capacity", "email"
    value: JsonValue | None = None  # None means explicitly unknown
    state: FactState
    source: str = Field(min_length=1, max_length=120)  # "tool:osm", "web:<url-hash>", "doc:<message id>", "user_input", "llm:<model>"
    url: str | None = Field(default=None, max_length=500)
    captured_at: datetime
    expires_at: datetime | None = None  # quote validity, page freshness
    supersedes: str | None = Field(default=None, pattern=_ID)

    @model_validator(mode="after")
    def _source_may_create_this_state(self) -> EvidenceRecord:
        if self.state not in allowed_states(self.source):
            raise ValueError(f"a source '{self.source}' cannot create a '{self.state.value}' fact")
        if self.state in HARD and self.value is None:
            raise ValueError("an unknown value cannot be verified or booked")
        return self

    def expired(self, now: datetime) -> bool:
        return self.expires_at is not None and self.expires_at <= now


class _EvidenceFile(JsonlLog[EvidenceRecord]):
    channel = "evidence"
    model = EvidenceRecord


class EvidenceLedger:
    def __init__(self, root: Path, now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.root = Path(root) / "evidence"
        self.now = now
        self._lock = InterProcessLock(self.root / ".lock")  # creates nothing until used: a workspace that never learns a fact has no evidence directory

    @property
    def _file(self) -> _EvidenceFile:
        return _EvidenceFile(self.root, run_id="ledger")

    def records(self, subject: str | None = None, field: str | None = None) -> list[EvidenceRecord]:
        if not self.root.exists():
            return []
        return [r for r in self._file.read() if (subject is None or r.subject == subject) and (field is None or r.field == field)]

    def record(self, subject: str, field: str, value: JsonValue, state: FactState, source: str, url: str | None = None,
               expires_at: datetime | None = None) -> EvidenceRecord:
        with self._lock():
            prior = self.best(subject, field)
            if prior is not None and (prior.value, prior.state, prior.source, prior.url) == (value, state, source, url):
                return prior  # the same fact again (a re-run of discovery) adds nothing
            rec = EvidenceRecord(id=uuid.uuid4().hex, subject=subject, field=field, value=value, state=state, source=source, url=url,
                                 captured_at=self.now(), expires_at=expires_at,
                                 supersedes=prior.id if prior and prior.value != value and RANK[prior.state] <= RANK[state] else None)
            return self._file._append(rec, rec.id)

    def best(self, subject: str, field: str) -> EvidenceRecord | None:
        """The most trusted record that has not expired; among equals the latest. Lower evidence never displaces higher."""
        now = self.now()
        live = [r for r in self.records(subject, field) if not r.expired(now)]
        return max(live, key=lambda r: (RANK[r.state], r.captured_at), default=None) if live else None

    def verify(self, subject: str, field: str, check: Callable[[EvidenceRecord], bool], note: str) -> EvidenceRecord:
        """Raise a user-confirmed fact to verified once code has re-checked it. ``note`` names the check (it becomes the source: ``code:<note>``)."""
        with self._lock():
            cur = self.best(subject, field)
            if cur is None or cur.state is not FactState.USER_CONFIRMED:
                raise ValueError("only a fact the user has confirmed can be verified")
            if not check(cur):
                raise ValueError(f"the check '{note}' failed, so the fact stays 'user_confirmed'")
            return self.record(subject, field, cur.value, FactState.VERIFIED, f"code:{note}", cur.url, cur.expires_at)

    def usable_for_hard_constraint(self, subject: str, field: str) -> EvidenceRecord | None:
        """The record a validator may rely on, or None. 'User confirmed' is deliberately not enough: show it with a "you told us" label instead."""
        r = self.best(subject, field)
        return r if r is not None and r.state in HARD else None

    def by_state(self, subject: str | None = None) -> dict[str, list[EvidenceRecord]]:
        """What the Evidence tab shows: the current record per (subject, field), grouped by state."""
        cur: dict[tuple, EvidenceRecord] = {}
        for r in self.records(subject):
            b = self.best(r.subject, r.field)
            if b is not None:
                cur[(b.subject, b.field)] = b
        groups: dict[str, list[EvidenceRecord]] = {s.value: [] for s in FactState}
        for r in cur.values():
            groups[r.state.value].append(r)
        return groups
