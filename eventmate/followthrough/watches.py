"""Page watches: "tell me if this venue lists December dates", "alert me if the decorator's package drops below INR 30,000".

The shape follows openmuse's Monitor (MIT, ``packages/domain/src/agent.ts``: a url, a condition, an interval, a next check time, the last state, a failure count); the
comparison is ``pagediff`` (also from openmuse). A watch only READS a public page, through the same ``SafeFetcher`` as everything else (addresses re-checked on every hop,
robots.txt obeyed, page code never run). What it finds is written to the evidence ledger as ``discovered``: a page said so, nothing more. It never sends anything, never
fills a form and never changes a plan.

Page text is untrusted: it is compared and counted, cut down and stripped of control characters before it is kept, and never sent to a model.
Politeness: at most ``MAX_WATCHES`` in all, ``MAX_PER_HOST`` per site, never more often than ``MIN_INTERVAL_MIN``; a failing page backs off (doubling, at most a day) and is
paused after ``PAUSE_AFTER`` failures in a row. The owner's kill switch stops every check.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import sqlite3
import threading
import urllib.parse
import uuid
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Literal
from collections.abc import Callable, Iterator

from pydantic import Field, model_validator

from eventmate.evidence.ledger import EvidenceLedger, FactState
from eventmate.followthrough.pagediff import describe, diff_page, page_lines
from eventmate.moneytext import negative_before
from eventmate.sqlsafe import scrub, secure
from eventmate.schemas import MAX_BUDGET_INR, _Model

MAX_WATCHES = 20
MAX_PER_HOST = 3
MIN_INTERVAL_MIN, MAX_INTERVAL_MIN, DEFAULT_INTERVAL_MIN = 60, 10_080, 1_440
PAUSE_AFTER = 5
MAX_BACKOFF_MIN = 1_440
KEEP_LINES = 200
CLAIM_MIN = 10  # a claimed watch is not handed to another worker for this long
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_AMOUNT = re.compile(r"(?:₹|\brs\.?|\binr)\s*(\d{1,3}(?:,\d{2,3})+|\d{1,9})(?![\d,]\d)(\.\d{1,2})?\s*(lakhs?|lacs?|l\b|k\b)?", re.I)
_ID = r"^[A-Za-z0-9_:.-]{1,64}$"


class StaleWatch(Exception):
    """Another worker changed this watch first."""


class Watch(_Model):
    watch_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    subject: str = Field(pattern=_ID)  # the vendor, venue or plan the evidence is about
    url: str = Field(min_length=8, max_length=2048, pattern=r"^https?://[^\s\x00-\x1f\x7f]+$")
    condition: Literal["change", "contains", "price_below"]
    text: str | None = Field(default=None, min_length=1, max_length=80)  # contains
    amount_inr: int | None = Field(default=None, ge=1, le=MAX_BUDGET_INR)  # price_below
    interval_minutes: int = Field(default=DEFAULT_INTERVAL_MIN, ge=MIN_INTERVAL_MIN, le=MAX_INTERVAL_MIN)
    status: Literal["active", "paused"] = "active"
    created_at: datetime
    next_check_at: datetime
    last_checked_at: datetime | None = None
    checks: int = 0
    failures: int = 0  # in a row
    last_error: str = Field(default="", max_length=200)
    last_lines: tuple[str, ...] = Field(default=(), max_length=KEEP_LINES)
    last_hit: bool = False  # contains / price_below: was the condition true at the last check
    version: int = 1

    @model_validator(mode="after")
    def _condition_has_its_value(self) -> Watch:
        if (self.condition == "contains") != (self.text is not None):
            raise ValueError("a 'contains' watch needs text, and only that kind takes it")
        if (self.condition == "price_below") != (self.amount_inr is not None):
            raise ValueError("a 'price_below' watch needs an amount, and only that kind takes it")
        for t in (self.created_at, self.next_check_at, self.last_checked_at):
            if t is not None and (t.tzinfo is None or t.utcoffset() is None):
                raise ValueError("timestamps must carry a time zone")
        return self

    @property
    def host(self) -> str:
        return (urllib.parse.urlsplit(self.url).hostname or "").lower()


def _clean(text: str, limit: int) -> str:
    return _CONTROL.sub("", text)[:limit]


def amounts(line: str) -> list[int]:
    """The INR amounts a line states, as whole rupees: "Rs 28,000", "INR 1.5 lakh", "₹30k". Only an amount marked as rupees is read; a bare number is not a price."""
    out = []
    for m in _AMOUNT.finditer(line):
        if negative_before(line, m.start(1)):
            continue  # a minus sign is part of the amount: it is not read as the positive one (F3)
        unit = (m.group(3) or "").lower()
        mult = 100_000 if unit.startswith(("lakh", "lac")) or unit == "l" else (1_000 if unit == "k" else 1)
        out.append(int(float(m.group(1).replace(",", "") + (m.group(2) or "")) * mult))
    return out


class WatchStore:
    """SQLite, owner only. The file is made when the first watch is added, so a workspace that never watches anything has nothing on disk."""

    def __init__(self, root: Path):
        self.path = Path(root) / "watches.db"
        self._lock = threading.RLock()

    @contextlib.contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.close(fd)
        db = secure(sqlite3.connect(self.path, timeout=10, isolation_level=None))
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS watches (watch_id TEXT PRIMARY KEY, subject TEXT NOT NULL, status TEXT NOT NULL, next_check_at TEXT NOT NULL, version INTEGER NOT NULL, data TEXT NOT NULL)")
            yield db
        finally:
            db.close()
            for suffix in ("-wal", "-shm"):
                with contextlib.suppress(OSError):
                    os.chmod(str(self.path) + suffix, 0o600)

    def exists(self) -> bool:
        return self.path.exists()

    def all(self) -> list[Watch]:
        if not self.exists():
            return []
        with self._db() as db:
            return [Watch.model_validate_json(r[0]) for r in db.execute("SELECT data FROM watches ORDER BY rowid")]

    def get(self, watch_id: str) -> Watch:
        found = next((w for w in self.all() if w.watch_id == watch_id), None)
        if found is None:
            raise ValueError("unknown watch")
        return found

    def add(self, subject: str, url: str, condition: str, now: datetime, text: str | None = None, amount_inr: int | None = None,
            interval_minutes: int = DEFAULT_INTERVAL_MIN) -> Watch:
        with self._lock:
            have = self.all()
            w = Watch(watch_id=uuid.uuid4().hex, subject=subject, url=url, condition=condition, text=text, amount_inr=amount_inr, interval_minutes=interval_minutes,
                      created_at=now, next_check_at=now)
            if len(have) >= MAX_WATCHES:
                raise ValueError(f"at most {MAX_WATCHES} pages can be watched; stop one first")
            if sum(1 for x in have if x.host == w.host) >= MAX_PER_HOST:
                raise ValueError(f"at most {MAX_PER_HOST} watches per site ({w.host})")
            if any((x.url, x.condition, x.text, x.amount_inr) == (w.url, w.condition, w.text, w.amount_inr) for x in have):
                raise ValueError("that page is already watched for that")
            with self._db() as db:
                db.execute("INSERT INTO watches (watch_id, subject, status, next_check_at, version, data) VALUES (?,?,?,?,?,?)",
                           (w.watch_id, w.subject, w.status, w.next_check_at.astimezone(UTC).isoformat(), w.version, w.model_dump_json()))
            return w

    def remove(self, watch_id: str) -> bool:
        if not self.exists():
            return False
        with self._lock, self._db() as db:
            gone = db.execute("DELETE FROM watches WHERE watch_id=?", (watch_id,)).rowcount == 1
            if gone:
                scrub(db)  # the removed watch is not left in the write-ahead log
            return gone

    def due(self, now: datetime, limit: int = 5) -> list[Watch]:
        if not self.exists():
            return []
        with self._db() as db:
            rows = db.execute("SELECT data FROM watches WHERE status='active' AND next_check_at <= ? ORDER BY next_check_at LIMIT ?", (now.astimezone(UTC).isoformat(), limit)).fetchall()
        return [Watch.model_validate_json(r[0]) for r in rows]

    def save(self, w: Watch) -> Watch:
        """Compare-and-set on ``version``: only the writer who read the latest wins."""
        new = w.model_copy(update={"version": w.version + 1})
        with self._lock, self._db() as db:
            cur = db.execute("UPDATE watches SET status=?, next_check_at=?, version=?, data=? WHERE watch_id=? AND version=?",
                             (new.status, new.next_check_at.astimezone(UTC).isoformat(), new.version, new.model_dump_json(), new.watch_id, w.version))
            if cur.rowcount != 1:
                raise StaleWatch(w.watch_id)
        return new


class WatchRunner:
    def __init__(self, store: WatchStore, fetcher: Callable[[], object], ledger: EvidenceLedger, killed: Callable[[], bool] = lambda: False,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.store, self.fetcher, self.ledger, self.killed, self.now = store, fetcher, ledger, killed, now

    def tick(self, limit: int = 5) -> dict[str, object]:
        report: dict[str, object] = {"checked": [], "found": [], "errors": []}
        if self.killed():
            report["skipped"] = "the kill switch is on"
            return report
        now = self.now()
        fetch = None
        for w in self.store.due(now, limit):
            try:
                claimed = self.store.save(w.model_copy(update={"next_check_at": now + timedelta(minutes=CLAIM_MIN)}))  # held for a while: no other worker fetches it meanwhile
            except StaleWatch:
                continue
            fetch = fetch or self.fetcher()
            try:
                found = self._check(claimed, fetch, now)
                report["checked"].append(w.watch_id)
                report["found"] += found
            except StaleWatch:
                continue
            except Exception as exc:  # one bad page must not stop the rest
                report["errors"].append(f"{w.watch_id}: {type(exc).__name__}")
        return report

    def _check(self, w: Watch, fetch, now: datetime) -> list[str]:
        try:
            page = fetch.fetch(w.url)
        except Exception as exc:  # FetchError and anything the transport raises: the message is short and holds no page text
            failures = w.failures + 1
            wait = min(w.interval_minutes * 2 ** failures, MAX_BACKOFF_MIN)
            self.store.save(w.model_copy(update={"failures": failures, "last_checked_at": now, "last_error": _clean(f"{type(exc).__name__}: {exc}", 200),
                                                 "next_check_at": now + timedelta(minutes=wait), "status": "paused" if failures >= PAUSE_AFTER else "active"}))
            return []
        lines = page_lines(page.text)
        source, found, hit = "web:" + hashlib.sha256(w.url.encode()).hexdigest()[:16], [], w.last_hit
        if w.condition == "change":
            if w.checks:  # the first reading is the baseline: nothing to compare it with
                diff = diff_page(list(w.last_lines), lines)
                if not diff.empty():
                    summary = _clean(describe(diff), 500)
                    self.ledger.record(w.subject, f"watch:{w.watch_id}:change", summary, FactState.DISCOVERED, source, w.url)
                    found.append(summary)
        elif w.condition == "contains":
            line = next((x for x in lines if w.text.casefold() in x.casefold()), None)
            hit = line is not None
            if hit and not w.last_hit:  # on the way from absent to present
                self.ledger.record(w.subject, f"watch:{w.watch_id}:mention", _clean(line, 300), FactState.DISCOVERED, source, w.url)
                found.append(line)
        else:
            low = min((n for x in lines for n in amounts(x) if n < w.amount_inr), default=None)
            hit = low is not None
            if hit and not w.last_hit:
                self.ledger.record(w.subject, f"watch:{w.watch_id}:price_below", low, FactState.DISCOVERED, source, w.url)
                found.append(f"INR {low:,}")
        self.store.save(w.model_copy(update={"checks": w.checks + 1, "failures": 0, "last_error": "", "last_checked_at": now, "last_lines": tuple(lines[:KEEP_LINES]), "last_hit": hit,
                                             "next_check_at": now + timedelta(minutes=w.interval_minutes)}))
        return found
