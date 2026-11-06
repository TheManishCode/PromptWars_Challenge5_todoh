"""Where a brief waits between what the reader understood and what the user approved.

A staged intake is the draft the reader proposed, parked in the pending-validation state. The planner (the expensive part) stays dormant while it is there. Each correction
overwrites the draft and bumps a version. The user's "done" moves it to processing in one atomic step, and only if the draft they are looking at is the one stored: the commit names the
version and the SHA-256 of the draft, so a draft changed (in another tab, by another request) between review and commit is refused instead of planned. A second "done" for the same draft
changes nothing and returns the state it is already in.

The store is append-only JSONL, one file per staged intake, like every other store here. The ``StagingStore`` surface (create, get, overwrite, begin_commit, finish) is all the rest of the
app uses, so a Redis or Valkey store with a key expiry can replace it when the app is hosted for many users; locally a file with a lock does the same job with no new service.
"""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Literal

from pydantic import Field

from eventmate.jsonl import InterProcessLock, JsonlLog
from eventmate.planning.intake import MAX_MESSAGES, IntakeDraft
from eventmate.schemas import _Model

STAGED_ID = re.compile(r"[0-9a-f]{24}")
PENDING_TTL = timedelta(hours=24)  # a brief nobody approved is dropped after this
KEEP_FINISHED = timedelta(days=7)  # a finished one is kept this long for the page to read its outcome, then removed
State = Literal["pending_validation", "processing", "done", "failed", "expired"]


# FUTURE(staging-redis): swap this file store for Redis or Valkey (a key per brief, EXPIRE for the 24 h, a Lua or WATCH step for begin_commit) when the app is hosted for many users. Keep the StagingStore surface.
# FUTURE(staging-delete): deleting a plan should also drop the staged brief that built it (retention.PER_PLAN_JSONL is keyed by plan id; staged briefs are keyed by their own id). Today the 7-day sweep does it.
# FUTURE(staging-stream): the page polls /status every 700 ms; the existing /api/pulse/stream could carry the state change instead.


class StageError(ValueError):
    """A staged intake cannot be used as asked. The message is safe to show."""


class StageGone(StageError):
    """No such staged intake, or it timed out."""


class StageConflict(StageError):
    """The draft changed since the caller last saw it."""


class StageClosed(StageError):
    """It is already being built or is finished; it can no longer be corrected."""


class StagedIntake(_Model):
    staged_id: str = Field(pattern=r"^[0-9a-f]{24}$")
    state: State
    version: int = Field(ge=1)
    draft: IntakeDraft
    draft_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    messages: tuple[str, ...] = Field(default=(), max_length=MAX_MESSAGES)  # what the user typed, one message each
    source: str = Field(default="rule:parser", max_length=80, pattern=r"^[A-Za-z0-9:._-]+$")
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    result: dict | None = None  # the outcome of the build: status, plan id, violations, the budget reality
    error: str = Field(default="", max_length=400)


class _StagedFile(JsonlLog[StagedIntake]):
    channel = "staged"
    model = StagedIntake


def draft_hash(draft: IntakeDraft) -> str:
    return hashlib.sha256(draft.model_dump_json().encode()).hexdigest()


class StagingStore:
    def __init__(self, root: Path, now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.root = Path(root) / "staging"
        self.now = now
        self._lock = InterProcessLock(self.root / ".lock")

    def _file(self, staged_id: str) -> _StagedFile:
        if not STAGED_ID.fullmatch(staged_id or ""):
            raise StageGone("no such brief")
        return _StagedFile(self.root, run_id=staged_id)

    def _latest(self, staged_id: str) -> StagedIntake | None:
        rows = self._file(staged_id).read()
        return rows[-1] if rows else None

    def _view(self, row: StagedIntake) -> StagedIntake:
        """A pending one past its time reads as expired; nothing is written for that."""
        if row.state == "pending_validation" and self.now() >= row.expires_at:
            return row.model_copy(update={"state": "expired"})
        return row

    def _write(self, prev: StagedIntake | None, **changes) -> StagedIntake:
        now = self.now()
        base = prev.model_dump() if prev else {}
        row = StagedIntake.model_validate({**base, **changes, "updated_at": now})
        self._file(row.staged_id)._append(row, f"{row.staged_id}:{row.version}:{row.state}")
        return row

    def create(self, draft: IntakeDraft, messages: Sequence[str] = (), source: str = "rule:parser") -> StagedIntake:
        with self._lock():
            self._sweep()
            now = self.now()
            return self._write(None, staged_id=uuid.uuid4().hex[:24], state="pending_validation", version=1, draft=draft, draft_hash=draft_hash(draft), messages=tuple(messages)[-MAX_MESSAGES:],
                               source=source, created_at=now, expires_at=now + PENDING_TTL)

    def get(self, staged_id: str) -> StagedIntake | None:
        row = self._latest(staged_id)
        return self._view(row) if row else None

    def require(self, staged_id: str) -> StagedIntake:
        row = self.get(staged_id)
        if row is None:
            raise StageGone("that brief is no longer here; start it again")
        return row

    def overwrite(self, staged_id: str, draft: IntakeDraft, messages: Sequence[str], source: str, expected_version: int | None = None) -> StagedIntake:
        """Replace the staged draft with a corrected one. Only while it is pending; ``expected_version`` refuses a correction made against a draft that has since changed."""
        with self._lock():
            cur = self.require(staged_id)
            if cur.state == "expired":
                raise StageGone("that brief timed out; start it again")
            if cur.state != "pending_validation":
                raise StageClosed("this brief is already being built, so it can no longer be changed")
            if expected_version is not None and expected_version != cur.version:
                raise StageConflict("the brief changed in the meantime; reload it and correct again")
            return self._write(cur, version=cur.version + 1, draft=draft, draft_hash=draft_hash(draft), messages=tuple(messages)[-MAX_MESSAGES:], source=source,
                               expires_at=self.now() + PENDING_TTL)

    def begin_commit(self, staged_id: str, version: int, digest: str) -> tuple[StagedIntake, bool]:
        """Pending to processing, once. Returns the row and whether this call is the one that started it; a repeat of the same commit returns the row as it is, started=False."""
        with self._lock():
            cur = self.require(staged_id)
            if cur.state == "expired":
                raise StageGone("that brief timed out; start it again")
            if version != cur.version or digest != cur.draft_hash:
                raise StageConflict("the brief is not the one you reviewed; reload it and check it again")
            if cur.state != "pending_validation":
                return cur, False
            return self._write(cur, state="processing"), True

    def keep(self, staged_id: str, days: int = 14) -> StagedIntake:
        """Let a pending brief outlive its day while vendors are being asked about it: the requests and replies are filed under it. The draft and version are not changed."""
        with self._lock():
            cur = self.require(staged_id)
            until = self.now() + timedelta(days=days)
            if cur.state != "pending_validation" or cur.expires_at >= until:
                return cur
            return self._write(cur, expires_at=until)

    def finish(self, staged_id: str, ok: bool, result: dict | None = None, error: str = "") -> StagedIntake:
        with self._lock():
            cur = self.require(staged_id)
            if cur.state != "processing":
                return cur
            return self._write(cur, state="done" if ok else "failed", result=result, error=error[:400])

    def reopen(self, staged_id: str) -> StagedIntake:
        """A build that failed goes back to pending so the user can correct the brief and send it again."""
        with self._lock():
            cur = self.require(staged_id)
            if cur.state != "failed":
                raise StageClosed("only a failed build can be reopened")
            return self._write(cur, state="pending_validation", version=cur.version + 1, result=None, error="", expires_at=self.now() + PENDING_TTL)

    def _sweep(self) -> None:
        """Remove files that are past their time: a pending one past its expiry, a finished one past ``KEEP_FINISHED``. Run when a new one is made, so it costs nothing at rest."""
        if not self.root.is_dir():
            return
        now = self.now()
        for p in self.root.glob("staged-*.jsonl"):
            try:
                row = self._latest(p.name[len("staged-"):-len(".jsonl")])
            except (StageError, ValueError, OSError):
                continue  # a file this version cannot read is left for ``verify`` to report, not deleted here
            if row is None:
                continue
            old = row.expires_at if row.state == "pending_validation" else row.updated_at + KEEP_FINISHED
            if row.state != "processing" and now > old and not p.is_symlink():
                try:
                    os.unlink(p)
                except FileNotFoundError:
                    pass
