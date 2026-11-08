"""A fully confirmed quote as a vendor record the planner can choose.

This is the one way a discovered business becomes plannable (the exception to ADR-028 and ADR-049): the user asks for it, every required field of the reply is already confirmed by them,
the vendor said it is available, and nothing confirmed contradicts the event. The record is for this city, this date and nothing else, its source names the reply it came from, and its price
still comes from the recorded quote (``VendorRepository.price``), so when the quote runs out the planner asks for a new one instead of using old money. Nothing here is read from a listing.
Rating is 0.0: a vendor that answered a request has no rating we hold, and an unrated record must not look better than a rated one. A venue's indoor or outdoor setting is not asked of vendors,
so the user says it when they promote a venue.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date, datetime, UTC
from pathlib import Path

from pydantic import Field

from eventmate.jsonl import InterProcessLock, JsonlLog
from eventmate.schemas import DietaryRequirement, Vendor, VendorCategory, Venue, _Model
from eventmate.sourcing.quote_readings import QuoteAssessment


class QuotedProvider(_Model):
    record_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    message_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    promoted_by: str = Field(min_length=1, max_length=60)
    promoted_at: datetime
    provider: Venue | Vendor
    withdrawn_at: datetime | None = None  # the user took the choice back: the planner no longer sees this record (the line stays, so the history does)
    withdrawn_by: str | None = Field(default=None, min_length=1, max_length=60)


class _File(JsonlLog[QuotedProvider]):
    channel = "quoted_provider"
    model = QuotedProvider


class QuotedProviderStore:
    def __init__(self, root: Path, now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.root = Path(root) / "quoted_providers"
        self.now = now
        self._lock = InterProcessLock(self.root / ".lock")
        self._seen: tuple[int, int] | None = None
        self._cache: tuple[Venue | Vendor, ...] = ()

    def _path(self) -> Path:
        return self.root / "quoted_provider-all.jsonl"

    def add(self, message_id: str, provider: Venue | Vendor, by: str) -> QuotedProvider:
        rec = QuotedProvider(record_id=uuid.uuid4().hex, message_id=message_id, promoted_by=by.strip(), promoted_at=self.now(), provider=provider)
        with self._lock():
            return _File(self.root, run_id="all")._append(rec, rec.record_id)

    def _live(self) -> list[QuotedProvider]:
        """The records still in force, in the order they were last written: for each reply its newest line, none that was withdrawn."""
        path = self._path()
        if not path.exists():
            return []
        newest: dict[str, QuotedProvider] = {}
        for rec in _File(self.root, run_id="all").read():
            newest.pop(rec.message_id, None)  # re-inserted below, so the dict stays in order of last write
            newest[rec.message_id] = rec
        return [r for r in newest.values() if r.withdrawn_at is None]

    def withdraw(self, message_id: str, by: str) -> QuotedProvider | None:
        """Take a choice back. None if that reply was not chosen (or already taken back). The record is not deleted: a line saying it was withdrawn, and by whom, is added."""
        with self._lock():
            rec = next((r for r in self._live() if r.message_id == message_id), None)
            if rec is None:
                return None
            gone = rec.model_copy(update={"record_id": uuid.uuid4().hex, "withdrawn_at": self.now(), "withdrawn_by": by.strip()})
            return _File(self.root, run_id="all")._append(gone, gone.record_id)

    def message_ids(self) -> frozenset[str]:
        """The replies the user has chosen to plan with, and has not taken back."""
        return frozenset(r.message_id for r in self._live())

    def providers(self) -> tuple[Venue | Vendor, ...]:
        """The newest record in force of each business, in file order. Re-read only when the file changes."""
        path = self._path()
        if not path.exists():
            return ()
        st = path.stat()
        if self._seen != (st.st_mtime_ns, st.st_size):
            latest: dict[str, Venue | Vendor] = {}
            for rec in self._live():
                latest[rec.provider.id] = rec.provider
            self._cache, self._seen = tuple(latest.values()), (st.st_mtime_ns, st.st_size)
        return self._cache


def to_provider(a: QuoteAssessment, vendor_id: str, name: str, city: str, event_date: date, setting: str | None) -> Venue | Vendor:
    """The record for a comparable quote. Refuses, naming what to do, when anything it needs is not confirmed."""
    if not a.comparable:
        raise ValueError("confirm every required field of this reply first, and the vendor must say it is available on your date")
    by = {f.key: f.value for f in a.fields if f.state == "confirmed"}
    common = {"id": vendor_id, "name": name, "city": city, "available_dates": (event_date,), "rating": 0.0, "source": f"database:quote/{a.message_id}"}
    if a.category == VendorCategory.VENUE:
        if setting not in ("indoor", "outdoor"):
            raise ValueError("say whether the venue is indoor or outdoor: a vendor's reply does not tell us")
        return Venue(**common, setting=setting, capacity=int(by["capacity"]), flat_fee=int(by["price_total"]))
    if a.category == VendorCategory.CATERER:
        diets = tuple(DietaryRequirement(d) for d in by["diets"].split(",") if d)
        return Vendor(**common, category=a.category, price_per_plate=int(by["price"]), min_guests=int(by["min_guests"]), max_guests=int(by["max_guests"]), dietary_options=diets)
    return Vendor(**common, category=a.category, flat_fee=int(by["package"]))

