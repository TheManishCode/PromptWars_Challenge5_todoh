"""Replies the app will not guess an event for.

A reply with none of our thread headers is matched by the address it came from. If that address was written to for one event, the reply is filed there and labelled "matched by sender,
not thread". If it was written to for two or more events the sender's address cannot say which one the reply answers, so it waits here and the user picks (audit A2). WhatsApp has no
thread headers at all, so every WhatsApp reply takes this road. Only the words and the choices are kept; an attachment on a held reply is not read.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from eventmate.jsonl import InterProcessLock, JsonlLog
from eventmate.schemas import ReplyCandidate, ReplyReview


class _File(JsonlLog[ReplyReview]):
    channel = "reply_review"
    model = ReplyReview


class ReplyReviewStore:
    def __init__(self, root: Path):
        self.root = Path(root) / "reply_reviews"
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

    def open(self) -> list[ReplyReview]:
        return [r for r in self.all() if r.status == "open"]

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

    def resolve(self, review_id: str, status: str, assigned_plan_id: str | None = None) -> ReplyReview:
        with self._lock():
            cur = self.get(review_id)
            if cur is None:
                raise ValueError(f"unknown reply {review_id!r}")
            if cur.status != "open":
                raise ValueError(f"this reply is already {cur.status}")
            return self._file()._append(cur.model_copy(update={"status": status, "assigned_plan_id": assigned_plan_id}), review_id)
