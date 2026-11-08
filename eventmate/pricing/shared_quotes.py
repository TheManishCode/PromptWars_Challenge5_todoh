"""What a vendor quoted the last person who asked, for the next person who asks the same vendor.

Each workspace is kept apart from every other (``workspaces.py``); this is the one narrow thing that is not. When a user confirms a vendor's price, the figures are also written here under keys that
say *which vendor* without naming anyone: the Google place id, or a hash of the email or WhatsApp number the vendor was reached on (never the name: names are not unique). Only a reply that came through the app is shared, never words a user typed in. The next user who adds the same vendor
is shown "an earlier quote" while their own request for a fresh one is out. It is shown for orientation only and never used as a price (``earlier_quotes`` in the service).

What is kept: the vendor keys, its kind, the amount and whether it is per plate or in total, the guest count (to the nearest ten) and month it was for (never the day: anyone who knows which vendor a person used could otherwise read the date of their event), the day it was given and until when it holds (dropped 90 days after that), and whether taxes were
included. What is never kept: who confirmed it, the reply's words, the vendor's name or contact details, the brief, and anything from a Google listing but its place id (Google's policy).
Test vendors are never shared. ``EVENTMATE_SHARED_QUOTES=0`` turns the whole thing off.
"""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta, UTC
from pathlib import Path

from pydantic import Field, field_validator

from eventmate import migrations
from eventmate.jsonl import InterProcessLock, JsonlLog
from eventmate.schemas import MAX_BUDGET_INR, MAX_GUESTS, VendorCategory, _Model

KEY = r"^(gplaces:[A-Za-z0-9_-]{8,200}|email:[0-9a-f]{16}|wa:[0-9a-f]{16}|name:[a-z0-9-]{1,40}\|[a-z0-9-]{1,40}\|[a-z]{4,14})$"
MAX_KEYS = 5
KEEP_AFTER_EXPIRY = timedelta(days=90)  # a quote nobody could still rely on is dropped this long after it stopped holding
GUEST_STEP = 10  # guest counts are kept to the nearest ten: enough to say "a different size of event", too coarse to pick out one


def enabled(env: dict[str, str] | None = None) -> bool:
    return (os.environ if env is None else env).get("EVENTMATE_SHARED_QUOTES", "1").strip().lower() not in ("0", "false", "no", "off")


class SharedQuote(_Model):
    quote_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    keys: tuple[str, ...] = Field(min_length=1, max_length=MAX_KEYS)
    category: VendorCategory
    amount_inr: int = Field(gt=0, le=MAX_BUDGET_INR)
    basis: str = Field(pattern=r"^(per_plate|total)$")
    guest_count: int | None = Field(default=None, ge=1, le=MAX_GUESTS)
    quoted_for_date: date | None = None  # always the first of the month it was for: a record with an exact day is read back coarsened
    observed_at: datetime
    expires_at: date
    taxes_included: bool | None = None

    @field_validator("quoted_for_date")
    @classmethod
    def _month_only(cls, v: date | None) -> date | None:
        return v.replace(day=1) if v else v


class _File(JsonlLog[SharedQuote]):
    channel = "shared_quote"
    model = SharedQuote


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")[:40]


def _hash(value: str) -> str:
    return hashlib.sha256(value.strip().casefold().encode("utf-8")).hexdigest()[:16]


def vendor_keys(source: str, external_id: str, name: str, city: str, category: VendorCategory, email: str | None = None, whatsapp: str | None = None) -> tuple[str, ...]:
    """Which vendor this is, in terms that match across people. A Google listing is its place id and nothing else (its name is the listing's, not ours to keep)."""
    if source == "gplaces":
        key = external_id
        return (key,) if re.fullmatch(KEY, key) else ()
    keys = []
    if email:
        keys.append(f"email:{_hash(email)}")
    if whatsapp:
        keys.append(f"wa:{_hash(whatsapp)}")
    # no key from the name: two different businesses can share a name and a city, and a user types the name, so it would also let anyone fish for prices
    return tuple(k for k in keys if re.fullmatch(KEY, k))


class SharedQuoteStore:
    def __init__(self, root: Path, now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.root = Path(root) / "shared_quotes"
        self.now = now
        self._lock = InterProcessLock(self.root / ".lock")

    def _all(self) -> list[SharedQuote]:
        return _File(self.root, run_id="all").read() if (self.root / "shared_quote-all.jsonl").exists() else []

    def add(self, keys: Iterable[str], category: VendorCategory, amount_inr: int, basis: str, guest_count: int | None, quoted_for_date: date | None, observed_at: datetime, expires_at: date,
            taxes_included: bool | None) -> SharedQuote | None:
        keys = tuple(dict.fromkeys(keys))[:MAX_KEYS]
        if not keys:
            return None
        observed_at = observed_at.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)  # the day it was given, not the minute
        if guest_count is not None:
            guest_count = min(MAX_GUESTS, max(GUEST_STEP, round(guest_count / GUEST_STEP) * GUEST_STEP))
        rec = SharedQuote(quote_id=uuid.uuid4().hex, keys=keys, category=category, amount_inr=amount_inr, basis=basis, guest_count=guest_count, quoted_for_date=quoted_for_date, observed_at=observed_at,
                          expires_at=expires_at, taxes_included=taxes_included)
        with self._lock():
            if any((o.amount_inr, o.observed_at, o.basis, o.guest_count) == (rec.amount_inr, rec.observed_at, rec.basis, rec.guest_count) and set(o.keys) & set(rec.keys) for o in self._all()):
                return None  # the same confirmation again (the user confirmed a field twice): one record
            self._prune()
            return _File(self.root, run_id="all")._append(rec, rec.quote_id)

    def _prune(self) -> int:
        """Drop what has been out of date for 90 days. Rewrites the file only when something goes; returns how many were dropped."""
        f = _File(self.root, run_id="all")
        rows = f.read() if f.path.exists() else []
        cutoff = self.now().date() - KEEP_AFTER_EXPIRY
        keep = [q for q in rows if q.expires_at >= cutoff]
        if len(keep) == len(rows):
            return 0
        tmp = f.path.with_suffix(".jsonl.prune")
        with open(tmp, "w", encoding="utf-8") as out:
            for q in keep:
                out.write(migrations.stamp(f.channel, q.model_dump_json()) + "\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, f.path)
        return len(rows) - len(keep)

    def earlier(self, keys: Iterable[str], category: VendorCategory) -> list[SharedQuote]:
        """Every shared quote for this vendor and kind, oldest first."""
        want = set(keys)
        floor = self.now().date() - KEEP_AFTER_EXPIRY  # read-side too, so nothing old is shown even before the next write prunes it
        return sorted((q for q in self._all() if q.category == category and want & set(q.keys) and q.expires_at >= floor), key=lambda q: q.observed_at)


def reasons(q: SharedQuote, today: date, guests: int | None, event_date: date | None) -> list[str]:
    """Why this quote may not hold for the viewer's event, worked out here against the viewer's own date so that no date of the other person's event leaves the store:
    it can say "a different month", never which."""
    from eventmate.pricing.observations import old_reasons

    why = list(old_reasons(q.model_copy(update={"quoted_for_date": None}), today, guests, event_date))  # type: ignore[arg-type]  (a SharedQuote has the fields old_reasons reads)
    if event_date and q.quoted_for_date and (q.quoted_for_date.year, q.quoted_for_date.month) != (event_date.year, event_date.month):
        why.append("it was for a different month than your event")
    return why
