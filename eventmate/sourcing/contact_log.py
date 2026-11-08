"""A contact the user made outside the app, written down (``cards-contact-log``).

Writing to a vendor yourself (their own mail, WhatsApp, a call, a visit) happens outside the app: it sends nothing and the reply does not come back by itself. This keeps the user's own note of it,
so the vendor's card says "you contacted them by phone on 12 Nov" and a reply can be pasted in. It is the user's statement, labelled as such; it proves nothing about the vendor.
"""

from __future__ import annotations

import os
import unicodedata
import uuid
from collections.abc import Callable
from datetime import datetime, UTC
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from eventmate.jsonl import InterProcessLock, JsonlLog
from eventmate.schemas import _Model

CHANNELS = ("email", "whatsapp", "phone", "in_person")


class ContactLogEntry(_Model):
    log_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    plan_key: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")  # the brief or plan it was logged under
    vendor_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    channel: Literal["email", "whatsapp", "phone", "in_person"]
    at: datetime  # when the user says they made the contact
    logged_at: datetime
    by: str = Field(min_length=1, max_length=60)
    note: str = Field(default="", max_length=200)  # the user's own words: what was said or asked

    @field_validator("by", "note")
    @classmethod
    def _text_ok(cls, v: str) -> str:
        return "".join(ch for ch in v if ch in " " or unicodedata.category(ch)[0] != "C").strip()


class _File(JsonlLog[ContactLogEntry]):
    channel = "contact_log"
    model = ContactLogEntry


class ContactLogStore:
    def __init__(self, root: Path, now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.root = Path(root) / "contact_log"
        self.now = now
        self.home: Callable[[str], str] = lambda plan_key: plan_key  # a brief that became a plan: both ids name one event (planning/brief_links.py)
        self._lock = InterProcessLock(self.root / ".lock")

    def _all(self) -> list[ContactLogEntry]:
        return _File(self.root, run_id="all").read() if (self.root / "contact_log-all.jsonl").exists() else []

    def add(self, plan_key: str, vendor_id: str, channel: str, by: str, note: str = "", at: datetime | None = None) -> ContactLogEntry:
        when = self.now()
        entry = ContactLogEntry(log_id=uuid.uuid4().hex, plan_key=plan_key, vendor_id=vendor_id, channel=channel, at=min(at, when) if at else when, logged_at=when, by=by.strip(), note=note.strip())
        with self._lock():
            return _File(self.root, run_id="all")._append(entry, entry.log_id)

    def for_vendor(self, plan_key: str, vendor_id: str) -> list[ContactLogEntry]:
        key = self.home(plan_key)
        return [e for e in self._all() if e.vendor_id == vendor_id and self.home(e.plan_key) == key]

    def forget_plan(self, *plan_keys: str) -> int:
        """Remove what was logged under these ids (a plan being deleted). The file is rewritten without them and moved into place; returns how many lines went."""
        with self._lock():
            rows = self._all()
            keep = [e for e in rows if e.plan_key not in plan_keys]
            if len(keep) == len(rows):
                return 0
            f = _File(self.root, run_id="all")
            tmp = f.path.with_suffix(".jsonl.forget")
            from eventmate import migrations

            with open(tmp, "w", encoding="utf-8") as out:
                for e in keep:
                    out.write(migrations.stamp(f.channel, e.model_dump_json()) + "\n")
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, f.path)
            return len(rows) - len(keep)
