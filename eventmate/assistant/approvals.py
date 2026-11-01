"""Human approval queue: the only way an assistant answer can change anything.

A mutating tool call from the model becomes a ``Proposal``. It is bound to the plan version it was made against, the
tool and the exact arguments (a content hash). Only a human with the approval permission, through a separate CSRF-protected
route, can apply it, and only while the plan has not changed underneath it. Approve and reject are logged either way.

Proposals live in SQLite (``assistant_approvals.db``), so one waiting for a click survives a restart. A decision is recorded *before* the action runs
(state ``approving``), and the result after. If the app stops in between, the action may or may not have happened, so the proposal becomes
``outcome_unknown`` and is never applied again by the queue: the user checks the plan and ledger, then asks again. This is the same at-most-once rule
the vendor outbox uses for a send. Two processes deciding the same proposal race on one compare-and-set; one wins.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Any, Callable, Iterator

from pydantic import Field

from eventmate.assistant.identity import Perm, Principal
from eventmate.assistant.trace import ProposalLog, sha
from eventmate.schemas import _Model

MAX_PENDING_PER_PLAN = 10
PROPOSAL_TTL = timedelta(days=7)  # a proposal nobody decided by then is expired; the plan-version check usually ends it sooner
APPLY_LEASE = timedelta(seconds=120)  # a proposal still "approving" after this was interrupted: its outcome is unknown
SCHEMA_VERSION = 1
UNKNOWN = "the app stopped while applying this, so it may or may not have happened; check the plan and the ledger before asking again"


class ApprovalError(ValueError):
    """The decision is refused (unknown, already decided, not allowed, or the plan moved on)."""


class Proposal(_Model):
    id: str
    plan_id: str
    plan_version: int
    proposer: str
    tool: str
    args: dict[str, Any]
    summary: str = Field(max_length=300)
    content_sha256: str
    created_at: datetime
    expires_at: datetime | None = None
    status: str = "pending"  # pending | approving | approved | rejected | stale | expired | failed | outcome_unknown
    decided_by: str | None = None
    result: str | None = Field(default=None, max_length=500)


def content_hash(plan_id: str, plan_version: int, tool: str, args: dict[str, Any]) -> str:
    return sha(json.dumps([plan_id, plan_version, tool, args], sort_keys=True, default=str))


_COLUMNS = ("id", "plan_id", "plan_version", "proposer", "tool", "args", "summary", "content_sha256", "created_at", "expires_at", "status", "decided_by", "decided_at", "result")


def _iso(t: datetime | None) -> str | None:
    return None if t is None else t.astimezone(UTC).isoformat()


def _aware(t: datetime | None) -> datetime | None:
    return t if t is None or t.tzinfo else t.replace(tzinfo=UTC)


class ApprovalQueue:
    """``db`` is a file path (durable) or None (in memory: the voice turn's private staging queue, and tests that do not need a restart)."""

    def __init__(self, log: ProposalLog, db: str | Path | None = None, now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.log, self.now = log, now
        self._path = None if db is None else Path(db)
        self._lock = threading.RLock()
        self._mem: sqlite3.Connection | None = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None) if self._path is None else None
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._db() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, plan_version INTEGER NOT NULL, proposer TEXT NOT NULL, tool TEXT NOT NULL, "
                "args TEXT NOT NULL, summary TEXT NOT NULL, content_sha256 TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT, status TEXT NOT NULL, decided_by TEXT, "
                "decided_at TEXT, result TEXT, schema_version INTEGER NOT NULL DEFAULT 1)"
            )
            c.execute("CREATE INDEX IF NOT EXISTS proposals_plan ON proposals (plan_id, status)")
        if self._path is not None:
            for suffix in ("", "-wal", "-shm"):  # proposals hold the arguments of changes the user has not decided yet: owner only
                with contextlib.suppress(OSError):
                    os.chmod(str(self._path) + suffix, 0o600)
        self._recover()

    @contextlib.contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        if self._mem is not None:
            with self._lock:
                yield self._mem
            return
        c = sqlite3.connect(self._path, timeout=10, isolation_level=None)
        try:
            c.execute("PRAGMA journal_mode=WAL")
            yield c
        finally:
            c.close()

    @contextlib.contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """One immediate transaction: the compare-and-set unit. Other writers, in this process or another, wait their turn."""
        with self._db() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
            except BaseException:
                c.execute("ROLLBACK")
                raise
            c.execute("COMMIT")

    @staticmethod
    def _row(r) -> Proposal:
        d = dict(zip(_COLUMNS, r))
        d["args"] = json.loads(d["args"])
        return Proposal.model_validate({k: v for k, v in d.items() if k != "decided_at"})

    def _fetch(self, c: sqlite3.Connection, proposal_id: str) -> Proposal | None:
        r = c.execute(f"SELECT {', '.join(_COLUMNS)} FROM proposals WHERE id = ?", (proposal_id,)).fetchone()  # nosec B608 - the column names are a module constant; every value is bound
        return None if r is None else self._row(r)

    def _recover(self) -> None:
        """A proposal left ``approving`` past its lease, or pending past its expiry, is settled now. Nothing is ever re-applied."""
        now = self.now()
        settled: list[Proposal] = []
        with self._tx() as c:
            for pid, in c.execute("SELECT id FROM proposals WHERE status = 'approving' AND decided_at < ?", (_iso(now - APPLY_LEASE),)).fetchall():
                c.execute("UPDATE proposals SET status='outcome_unknown', result=? WHERE id=? AND status='approving'", (UNKNOWN[:500], pid))
                settled.append(self._fetch(c, pid))
            for pid, in c.execute("SELECT id FROM proposals WHERE status = 'pending' AND expires_at IS NOT NULL AND expires_at <= ?", (_iso(now),)).fetchall():
                c.execute("UPDATE proposals SET status='expired', result='nobody decided this in time; ask again' WHERE id=? AND status='pending'", (pid,))
                settled.append(self._fetch(c, pid))
        for p in settled:
            self.log.write(request_id=p.id, event=p.status, plan_id=p.plan_id, tool=p.tool, actor="system", content_sha256=p.content_sha256, detail=p.result or "")

    def _count_pending(self, c: sqlite3.Connection, plan_id: str) -> int:
        return c.execute("SELECT COUNT(*) FROM proposals WHERE plan_id = ? AND status = 'pending'", (plan_id,)).fetchone()[0]

    def _insert(self, c: sqlite3.Connection, p: Proposal) -> None:
        c.execute(
            "INSERT INTO proposals (id, plan_id, plan_version, proposer, tool, args, summary, content_sha256, created_at, expires_at, status, schema_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (p.id, p.plan_id, p.plan_version, p.proposer, p.tool, json.dumps(p.args, sort_keys=True), p.summary, p.content_sha256, _iso(p.created_at), _iso(p.expires_at), "pending", SCHEMA_VERSION),
        )

    def _file(self, p: Proposal, plan_id: str) -> tuple[Proposal, bool]:
        """Insert unless the plan already has too many waiting, or an identical one is waiting (then that one is returned)."""
        with self._tx() as c:
            dup = c.execute(f"SELECT {', '.join(_COLUMNS)} FROM proposals WHERE status='pending' AND content_sha256=? AND plan_id=?", (p.content_sha256, plan_id)).fetchone()  # nosec B608 - same constant column list; values bound
            if dup:
                return self._row(dup), False  # the same request twice is one proposal
            if self._count_pending(c, plan_id) >= MAX_PENDING_PER_PLAN:
                raise ApprovalError("too many proposals are waiting for a decision; decide on some first")
            self._insert(c, p)
        self.log.write(request_id=p.id, event="proposed", plan_id=p.plan_id, tool=p.tool, actor=p.proposer, content_sha256=p.content_sha256, detail=p.summary)
        return p, True

    def propose(self, *, plan_id: str, plan_version: int, proposer: str, tool: str, args: dict[str, Any], summary: str) -> Proposal:
        self._recover()
        clean = json.loads(json.dumps(args, default=str))
        now = self.now()
        p = Proposal(id=uuid.uuid4().hex[:12], plan_id=plan_id, plan_version=plan_version, proposer=proposer, tool=tool, args=clean, summary=summary[:300],
                     content_sha256=content_hash(plan_id, plan_version, tool, clean), created_at=now, expires_at=now + PROPOSAL_TTL)
        return self._file(p, plan_id)[0]

    def adopt(self, proposal: Proposal) -> tuple[Proposal, bool]:
        """Take a proposal staged during a voice turn into this queue, at the turn's commit barrier. Returns (proposal, created): an identical pending
        proposal that already exists (made in typed chat, say) is returned as is with created False. Nothing ever takes a proposal back out."""
        self._recover()
        fresh = proposal.model_copy(update={"expires_at": proposal.expires_at or self.now() + PROPOSAL_TTL, "status": "pending"})
        return self._file(fresh, proposal.plan_id)

    def pending(self, plan_id: str) -> list[Proposal]:
        self._recover()
        with self._db() as c:
            rows = c.execute(f"SELECT {', '.join(_COLUMNS)} FROM proposals WHERE plan_id = ? AND status = 'pending' ORDER BY created_at", (plan_id,)).fetchall()  # nosec B608 - constant column list, bound values
        return [self._row(r) for r in rows]

    def get(self, proposal_id: str) -> Proposal:
        self._recover()
        with self._db() as c:
            p = self._fetch(c, proposal_id)
        if p is None:
            raise ApprovalError("unknown proposal")
        return p

    def forget_plan(self, plan_id: str) -> int:
        """Remove every proposal of a deleted plan (the file is rewritten so they are not recoverable from it). Returns how many there were."""
        with self._tx() as c:
            n = c.execute("DELETE FROM proposals WHERE plan_id = ?", (plan_id,)).rowcount
        if self._path is not None and n:
            with self._db() as c:
                c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                c.execute("VACUUM")
        return n

    def decide(self, proposal_id: str, approver: Principal, approve: bool, latest_version: Callable[[str], int], apply: Callable[[Proposal], str], needs: Perm) -> Proposal:
        """``apply`` runs the real action and returns a short result; it is only called for a fresh, intact, approved proposal, and at most once: the
        decision is written first, and an apply that is cut short leaves ``outcome_unknown`` rather than being run again."""
        if not approver.can(Perm.APPROVE) or not approver.can(needs):
            raise ApprovalError("you are not allowed to decide this proposal")
        self._recover()
        now = self.now()
        with self._tx() as c:  # step 1: check and record the decision (the compare-and-set)
            p = self._fetch(c, proposal_id)
            if p is None:
                raise ApprovalError("unknown proposal")
            if p.status != "pending":
                raise ApprovalError(f"proposal already {p.status}")
            if content_hash(p.plan_id, p.plan_version, p.tool, p.args) != p.content_sha256:
                raise ApprovalError("proposal content does not match its hash")
            status, result = ("rejected", None) if not approve else ("approving", None)
            if approve and latest_version(p.plan_id) != p.plan_version:
                status, result = "stale", "the plan changed after this was proposed; ask again"
            c.execute("UPDATE proposals SET status=?, decided_by=?, decided_at=?, result=? WHERE id=? AND status='pending'", (status, approver.user_id, _iso(now), result, p.id))
        if status == "approving":  # step 2: the action, outside the transaction (it takes its own locks), then its result
            try:
                result = apply(p)
                status = "approved"
            except Exception as exc:  # the real action refused (a hard constraint, unknown vendor...)
                status, result = "failed", f"{type(exc).__name__}: {exc}"[:500]
            with self._tx() as c:
                c.execute("UPDATE proposals SET status=?, result=? WHERE id=? AND status='approving'", (status, result, p.id))
        with self._db() as c:
            done = self._fetch(c, p.id)
        self.log.write(request_id=p.id, event=done.status, plan_id=p.plan_id, tool=p.tool, actor=approver.user_id, content_sha256=p.content_sha256, detail=done.result or "")
        return done
