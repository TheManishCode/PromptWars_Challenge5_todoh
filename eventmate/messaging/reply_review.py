"""Replies the app will not guess an event for.

A reply with none of our thread headers is matched by the address it came from. If that address was written to for one event, the reply is filed there and labelled "matched by sender,
not thread". If it was written to for two or more events the sender's address cannot say which one the reply answers, so it waits here and the user picks (audit A2). WhatsApp has no
thread headers at all, so every WhatsApp reply takes this road. Only the words and the choices are kept; an attachment on a held reply is not read.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from collections.abc import Callable
from datetime import datetime, timedelta, UTC
from pathlib import Path

from eventmate.jsonl import InterProcessLock, JsonlLog
from eventmate.schemas import ReplyCandidate, ReplyReview


class _File(JsonlLog[ReplyReview]):
    channel = "reply_review"
    model = ReplyReview


LEASE = timedelta(minutes=5)  # a claim that was never finished (the process died between claiming and filing) is assignable again after this, to the same event only


class ReplyReviewStore:
    def __init__(self, root: Path, now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.root = Path(root) / "reply_reviews"
        self.now = now
        self._lock = InterProcessLock(self.root / ".lock")

    def _file(self) -> _File:
        return _File(self.root, run_id="all")

    def all(self) -> list[ReplyReview]:
        if not (self.root / "reply_review-all.jsonl").exists():
            return []
        latest: dict[str, ReplyReview] = {}
        for r in self._file().read():
            latest[r.review_id] = r
        return list(latest.values())

    def _lapsed(self, r: ReplyReview) -> bool:
        return r.status == "assigning" and (r.claimed_at is None or self.now() - r.claimed_at > LEASE)

    def open(self) -> list[ReplyReview]:
        """What the user still has to decide: replies nobody has claimed, and claims that lapsed."""
        return [r for r in self.all() if r.status == "open" or self._lapsed(r)]

    def get(self, review_id: str) -> ReplyReview | None:
        return next((r for r in self.all() if r.review_id == review_id), None)

    def add(self, channel: str, sender: str, received_at: datetime, external_id: str, subject: str, body: str, candidates: Sequence[ReplyCandidate]) -> ReplyReview | None:
        """None when this message is already here, in any state: a poll that sees it again, or after the user has dealt with it, adds nothing."""
        with self._lock():
            if any((r.channel, r.external_id) == (channel, external_id) for r in self.all()):
                return None
            r = ReplyReview(review_id=uuid.uuid4().hex, channel=channel, sender=sender[:254], received_at=received_at, external_id=external_id[:400], subject=subject[:200], body=body[:4000],
                            candidates=tuple(candidates))
            return self._file()._append(r, r.review_id)

    def seen(self, channel: str, external_id: str) -> bool:
        return any((r.channel, r.external_id) == (channel, external_id) for r in self.all())

    def claim(self, review_id: str, plan_id: str) -> ReplyReview:
        """Compare-and-set, under the store's lock across processes: only one caller gets to file this reply. A fresh claim by someone else, or a reply already dealt with, is refused. A lapsed claim
        can be taken again, but only for the event it named, so a retry after a crash finishes the same choice and never files the reply under two events."""
        with self._lock():
            cur = self.get(review_id)
            if cur is None:
                raise ValueError("no such reply")
            if cur.status in ("assigned", "dismissed"):
                raise ValueError(f"this reply is already {cur.status}")
            if cur.status == "assigning" and not self._lapsed(cur):
                raise ValueError("this reply is being filed right now")
            if cur.status == "assigning" and cur.assigned_plan_id not in (None, plan_id):
                raise ValueError("an earlier attempt was filing this reply under another event; finish that one")
            return self._file()._append(cur.model_copy(update={"status": "assigning", "assigned_plan_id": plan_id, "claimed_at": self.now()}), review_id)

    def resolve(self, review_id: str, status: str, assigned_plan_id: str | None = None) -> ReplyReview:
        """The end of a claim (``assigned``) or the user's decision that none of the events is right (``dismissed``, only while nobody holds a claim)."""
        with self._lock():
            cur = self.get(review_id)
            if cur is None:
                raise ValueError(f"unknown reply {review_id!r}")
            if status == "assigned" and cur.status != "assigning":
                raise ValueError(f"this reply is {cur.status}, not being filed")
            if status == "dismissed" and cur.status != "open" and not self._lapsed(cur):
                raise ValueError(f"this reply is already {cur.status}" if cur.status != "assigning" else "this reply is being filed right now")
            return self._file()._append(cur.model_copy(update={"status": status, "assigned_plan_id": assigned_plan_id if status == "assigned" else cur.assigned_plan_id}), review_id)
