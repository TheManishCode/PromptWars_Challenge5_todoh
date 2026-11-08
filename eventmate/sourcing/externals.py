"""Businesses found by discovery, kept apart from the vendor database.

An external business has its own identity (``ext_...``). It is never merged with, renamed to, or bound to a seed vendor, and it never appears in the
vendor repository. Where it came from (discovered, or a test vendor whose address the user owns) travels with it.
"""

from __future__ import annotations

import fcntl
import os
import re
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path
from collections.abc import Callable

from eventmate.evidence.ledger import FactState
from eventmate import migrations
from eventmate.jsonl import InterProcessLock, JsonlLog
from eventmate.schemas import ExternalBusiness, VendorCategory, VendorRef, external_vendor_id, _EMAIL


@dataclass(frozen=True)
class ResolvedVendor:
    ref: VendorRef
    name: str
    category: VendorCategory
    origin: str  # seed | discovered | test | user
    source_url: str | None = None


class _ExternalFile(JsonlLog[ExternalBusiness]):
    channel = "external"
    model = ExternalBusiness


def placeholder_name(external_id: str) -> str:
    """What stands for a Google listing's name until the user confirms one: nothing of the listing, only the tail of its place id."""
    return "Google listing ·" + external_id[-6:]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")[:30] or "vendor"


class ExternalStore:
    def __init__(self, root: Path, now: Callable[[], datetime] = lambda: datetime.now(UTC), evidence=None):
        self.root = Path(root) / "externals"
        self.now = now
        self.evidence = evidence  # an EvidenceLedger: what a listing says is written there as discovered
        self._lock = InterProcessLock(self.root / ".lock")

    def _file(self) -> _ExternalFile:
        return _ExternalFile(self.root, run_id="all")

    def all(self) -> list[ExternalBusiness]:
        latest: dict[str, ExternalBusiness] = {}
        for b in self._file().read():
            latest[b.vendor_id] = b
        return list(latest.values())

    def get(self, vendor_id: str) -> ExternalBusiness | None:
        return next((b for b in self.all() if b.vendor_id == vendor_id), None)

    def add(self, external_id: str, name: str, city: str, category: VendorCategory, source: str, source_url: str | None = None,
            website: str | None = None, rating: float | None = None, owner_confirmed_by: str | None = None) -> ExternalBusiness:
        """Record a business once. The same source reference is the same business; its first record stays."""
        vid = external_vendor_id(external_id)
        if source == "gplaces":  # Google's policy lets only the place id be stored: the name, link, website and rating are fetched again whenever a card is shown
            name, source_url, website, rating = placeholder_name(external_id), None, None, None
        with self._lock():
            have = self.get(vid)
            if have is not None:
                return have
            biz = ExternalBusiness(
                vendor_id=vid, external_id=external_id, name=name, city=city, category=category, source=source, source_url=source_url, website=website,
                rating=rating, is_test=source == "test", origin={"test": "test", "user": "user"}.get(source, "discovered"), owner_confirmed_by=owner_confirmed_by, created_at=self.now(),
                name_source="placeholder" if source == "gplaces" else ("user_confirmed" if source == "user" else "listing"),
            )
            self._file()._append(biz, vid)
        if self.evidence is not None and not biz.is_test and biz.source not in ("gplaces", "user"):  # a listing's words are discovered, never verified; a test vendor was not discovered, and what a user typed is theirs
            for field, value in (("name", biz.name), ("website", biz.website), ("rating", biz.rating)):
                if value is not None:
                    self.evidence.record(vid, field, value, FactState.DISCOVERED, f"tool:{biz.source}", biz.source_url)
        return biz

    def confirm_name(self, vendor_id: str, name: str) -> ExternalBusiness:
        """The name the user confirms for a Google-listed business, for their own record and the emails they approve. It is theirs, not a copy of the listing, and they can type another."""
        with self._lock():
            cur = self.get(vendor_id)
            if cur is None:
                raise ValueError(f"unknown business {vendor_id!r}")
            if cur.source != "gplaces":
                return cur
            name = " ".join("".join(c for c in (name or "") if c.isprintable()).split())[:120]
            if not name or "<" in name or ">" in name:
                raise ValueError("confirm the business name: type it as you want it in your own record")
            new = ExternalBusiness.model_validate({**cur.model_dump(), "name": name, "name_source": "user_confirmed"})
            return self._file()._append(new, vendor_id)

    def scrub_google_content(self) -> int:
        """Remove what an earlier version stored from Google listings (name, link, website, rating), now that only the place id may be kept. Rewrites the file; returns how many businesses were changed."""
        if not (self.root / "external-all.jsonl").exists():
            return 0  # an empty workspace makes no directory just to look for something to scrub
        with self._lock():
            f = self._file()
            rows = f.read()
            if not any(b.source == "gplaces" and b.name_source == "listing" for b in rows):
                return 0
            changed, keep = 0, []
            for b in rows:
                if b.source == "gplaces" and b.name_source == "listing":
                    b = b.model_copy(update={"name": placeholder_name(b.external_id), "name_source": "placeholder", "source_url": None, "website": None, "website_source": "listing", "rating": None})
                    changed += 1
                keep.append(b)
            tmp = f.path.with_suffix(".jsonl.scrub")
            with open(tmp, "w", encoding="utf-8") as out:
                fcntl.flock(out, fcntl.LOCK_EX)
                for b in keep:
                    out.write(migrations.stamp(f.channel, b.model_dump_json()) + "\n")
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, f.path)
            return changed

    def set_website(self, vendor_id: str, url: str) -> ExternalBusiness:
        """A website the user typed for a business whose listing had none. It is marked as the user's, not the listing's."""
        with self._lock():
            cur = self.get(vendor_id)
            if cur is None:
                raise ValueError(f"unknown business {vendor_id!r}")
            new = ExternalBusiness.model_validate({**cur.model_dump(), "website": url, "website_source": "user_input"})
            return self._file()._append(new, vendor_id)


def clean_email(value: str) -> str:
    value = (value or "").strip()
    if not _EMAIL.fullmatch(value):
        raise ValueError("not a valid email address (one address, no names or extra parameters)")
    return value
