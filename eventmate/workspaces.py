"""Anonymous workspaces: each visitor gets their own, with no sign-up (Phase 2).

A workspace is a directory of its own with its own ``EventMate`` (plans, ledger, outreach, jobs, guests, quotes, contacts, annotations, traces), its own
``Assistant`` (conversation memory, pending approvals, answer cache) and its own ``VoiceService``. Nothing is shared between two workspaces but the
read-only seed data, so one visitor reaching another's plan is not a permission check that could be forgotten on one route: the other plan is simply
not in this workspace's store, and every lookup ends in the same generic "not found".

The visitor holds a random credential (192 bits, from ``secrets``) in an HttpOnly cookie. Only its SHA-256 is stored; the credential is never stored or
logged. A new workspace is made only by ``GET /api/session`` and is rate limited and capped, so anonymous traffic cannot fill the disk. Recovery of a lost
credential is an owner decision and is not built; ``rotate`` and ``revoke`` are.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from collections.abc import Callable

from eventmate.assistant.identity import Principal, Role
from eventmate.sqlsafe import scrub, secure
from eventmate.web.limits import WindowLimiter

if TYPE_CHECKING:  # types only: importing these at runtime would be circular
    from eventmate.assistant.agent import Assistant
    from eventmate.service import EventMate
    from eventmate.voice.turns import VoiceService

COOKIE = "todoh_ws"
COOKIE_MAX_AGE = 365 * 24 * 3600  # longevity is the goal: a year, renewed on every use
ID = re.compile(r"ws-[0-9a-f]{16}")
CREDENTIAL = re.compile(r"[A-Za-z0-9_-]{32}")  # secrets.token_urlsafe(24); a cookie that does not look like one is never even hashed
TOUCH_EVERY_S = 60
RECOVERY_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32: no I, L, O or U, so a phrase read aloud or copied by hand survives
RECOVERY_CHARS = 16  # 16 x 5 bits = 80 bits


def new_recovery_phrase() -> str:
    raw = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(RECOVERY_CHARS))
    return "-".join(raw[i:i + 4] for i in range(0, RECOVERY_CHARS, 4))


def normalise_phrase(text: object) -> str | None:
    """The phrase as 16 alphabet characters, forgiving about case, dashes, spaces and the look-alikes (O is 0, I and L are 1); None when it is not a phrase at all."""
    if not isinstance(text, str) or len(text) > 40:
        return None
    t = re.sub(r"[\s-]", "", text).upper().replace("O", "0").replace("I", "1").replace("L", "1")
    return t if len(t) == RECOVERY_CHARS and all(c in RECOVERY_ALPHABET for c in t) else None


def _rhash(normalised: str) -> str:
    return hashlib.sha256(b"recovery:" + normalised.encode("ascii")).hexdigest()


class WorkspaceError(Exception):
    """A refusal with an HTTP status and a machine readable code; the message is safe to show."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code = status, code


@dataclass
class Workspace:
    id: str
    em: EventMate
    assistant: Assistant
    voice: VoiceService
    lock: threading.RLock
    principal: Principal
    csrf: str
    created: str
    inflight: int = 0  # requests currently using this workspace: it is never dropped from memory while any is running (F-016)
    pulse: object = field(default_factory=lambda: __import__("eventmate.arcade_pulse", fromlist=["ArcadePulse"]).ArcadePulse())  # this workspace's own run lights for the room (F-014)


def _hash(credential: str) -> str:
    return hashlib.sha256(credential.encode("ascii")).hexdigest()


def csrf_for(secret: str, workspace_id: str) -> str:
    """A CSRF value that belongs to one workspace (HMAC of its id under the server secret), not one value for the whole process."""
    return hmac.new(secret.encode(), b"csrf:" + workspace_id.encode(), hashlib.sha256).hexdigest()[:43]


class WorkspaceDirectory:
    """workspace id, hash of its credential, created, last seen. One SQLite file, small rows (about 200 bytes per workspace until it first saves a plan)."""

    def __init__(self, root: Path, clock: Callable[[], float] = time.time):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "workspaces.db"
        self._clock = clock
        self._lock = threading.Lock()
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS workspaces (id TEXT PRIMARY KEY, cred_hash TEXT UNIQUE, created REAL NOT NULL, last_seen REAL NOT NULL)")
            if "recovery_hash" not in {r[1] for r in db.execute("PRAGMA table_info(workspaces)")}:
                db.execute("ALTER TABLE workspaces ADD COLUMN recovery_hash TEXT")  # added to a directory made before recovery phrases existed
        self.path.chmod(0o600)

    def _db(self):
        return _Tx(secure(sqlite3.connect(self.path, timeout=10)))

    def create(self) -> tuple[str, str]:
        for _ in range(5):
            workspace_id, credential = "ws-" + secrets.token_hex(8), secrets.token_urlsafe(24)
            try:
                with self._lock, self._db() as db:
                    now = self._clock()
                    db.execute("INSERT INTO workspaces (id, cred_hash, created, last_seen) VALUES (?, ?, ?, ?)", (workspace_id, _hash(credential), now, now))
                return workspace_id, credential
            except sqlite3.IntegrityError:
                continue
        raise WorkspaceError(503, "workspace_unavailable", "a workspace could not be created; try again")

    def lookup(self, credential: str) -> str | None:
        """The workspace id this credential opens, or None for an unknown, malformed or revoked one. The credential is compared by its hash."""
        if not isinstance(credential, str) or not CREDENTIAL.fullmatch(credential):
            return None
        digest = _hash(credential)
        with self._db() as db:
            row = db.execute("SELECT id, cred_hash, last_seen FROM workspaces WHERE cred_hash = ?", (digest,)).fetchone()
            if row is None or not hmac.compare_digest(row[1], digest):
                return None
            now = self._clock()
            if now - row[2] > TOUCH_EVERY_S:
                db.execute("UPDATE workspaces SET last_seen = ? WHERE id = ?", (now, row[0]))
        return row[0]

    def exists(self, workspace_id: str) -> bool:
        with self._db() as db:
            return db.execute("SELECT 1 FROM workspaces WHERE id = ?", (workspace_id,)).fetchone() is not None

    def info(self, workspace_id: str) -> dict[str, float] | None:
        with self._db() as db:
            row = db.execute("SELECT created, last_seen FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
        return None if row is None else {"created": row[0], "last_seen": row[1]}

    def rotate(self, workspace_id: str) -> str | None:
        """A new credential; the old one stops working at once."""
        credential = secrets.token_urlsafe(24)
        with self._lock, self._db() as db:
            cur = db.execute("UPDATE workspaces SET cred_hash = ? WHERE id = ? AND cred_hash IS NOT NULL", (_hash(credential), workspace_id))
        return credential if cur.rowcount else None

    def set_recovery(self, workspace_id: str) -> str | None:
        """A new recovery phrase for an open workspace (the old one stops working); only its hash is kept. None for a revoked or unknown workspace."""
        phrase = new_recovery_phrase()
        with self._lock, self._db() as db:
            cur = db.execute("UPDATE workspaces SET recovery_hash = ? WHERE id = ? AND cred_hash IS NOT NULL", (_rhash(normalise_phrase(phrase)), workspace_id))
        return phrase if cur.rowcount else None

    def has_recovery(self, workspace_id: str) -> bool:
        with self._db() as db:
            row = db.execute("SELECT recovery_hash FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
        return bool(row and row[0])

    def recover(self, phrase: object) -> tuple[str, str, str] | None:
        """Use a phrase: (workspace id, a new credential, a new phrase), or None. The old credential and the used phrase stop working in the same step, so a phrase opens a
        workspace once. A revoked workspace cannot be recovered: revoking means no credential, and this would make one."""
        norm = normalise_phrase(phrase)
        if norm is None:
            return None
        credential, fresh = secrets.token_urlsafe(24), new_recovery_phrase()
        with self._lock, self._db() as db:
            row = db.execute("SELECT id, recovery_hash FROM workspaces WHERE recovery_hash = ? AND cred_hash IS NOT NULL", (_rhash(norm),)).fetchone()
            if row is None or not hmac.compare_digest(row[1], _rhash(norm)):
                return None
            db.execute("UPDATE workspaces SET cred_hash = ?, recovery_hash = ? WHERE id = ?", (_hash(credential), _rhash(normalise_phrase(fresh)), row[0]))
        return row[0], credential, fresh

    def revoke(self, workspace_id: str) -> bool:
        """No credential opens the workspace any more. Its data stays until it is deleted."""
        with self._lock, self._db() as db:
            return db.execute("UPDATE workspaces SET cred_hash = NULL WHERE id = ?", (workspace_id,)).rowcount > 0

    def forget(self, workspace_id: str) -> bool:
        with self._lock, self._db() as db:
            gone = db.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,)).rowcount > 0
        if gone:
            with self._lock:
                db = secure(sqlite3.connect(self.path, timeout=10, isolation_level=None))  # autocommit: VACUUM cannot run inside a transaction
                try:
                    scrub(db)
                finally:
                    db.close()
        return gone

    def count(self) -> int:
        with self._db() as db:
            return db.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0]

    def idle_before(self, cutoff: float) -> list[str]:
        with self._db() as db:
            return [r[0] for r in db.execute("SELECT id FROM workspaces WHERE last_seen < ?", (cutoff,)).fetchall()]


class _Tx:
    """sqlite3 connection as a context manager that commits and closes (the module's own ``with`` only commits)."""

    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, exc_type, exc, tb):
        try:
            self.db.commit() if exc_type is None else self.db.rollback()
        finally:
            self.db.close()
        return False


Factory = Callable[[str, Path], tuple[object, object, object]]


def default_factory(workspace_id: str, home: Path) -> tuple[object, object, object]:
    """EventMate, Assistant and VoiceService for one workspace. Channels are unconfigured, so nothing a visitor does can send an email or a message."""
    from eventmate.assistant.agent import Assistant
    from eventmate.llm import client_from_env
    from eventmate.messaging.channels import EmailChannel, EmailConfig, WahaConfig, WhatsAppChannel
    from eventmate.service import EventMate
    from eventmate.voice.turns import VoiceService

    em = EventMate(log_dir=home / "logs", plan_dir=home / "plans", run_id=workspace_id, llm=client_from_env(),
                   channels={"email": EmailChannel(EmailConfig()), "whatsapp": WhatsAppChannel(WahaConfig())},
                   shared_dir=home.parent.parent / "shared")  # the one place two workspaces meet: what a vendor quoted the last person who asked
    assistant = Assistant.from_env(em)
    return em, assistant, VoiceService(assistant, em)


class WorkspaceRegistry:
    """Finds, creates and limits workspaces. ``open`` and ``create`` return a ready ``Workspace``; at most ``max_live`` are kept in memory (the least
    recently used are dropped and rebuilt from disk on their next request; what lives only in memory, such as a pending assistant proposal, is lost with them)."""

    def __init__(self, root: Path, secret: str, factory: Factory = default_factory, max_workspaces: int = 1000, create_per_hour: int = 60,
                 create_per_address_per_hour: int = 5, max_live: int = 128, clock: Callable[[], float] = time.time):
        if len(secret) < 32:
            raise ValueError("the workspace secret must be at least 32 characters")
        self.root, self.secret, self._factory, self.max_workspaces, self.max_live = Path(root), secret, factory, max_workspaces, max_live
        self.directory = WorkspaceDirectory(self.root, clock)
        self._clock = clock
        self._live: OrderedDict[str, Workspace] = OrderedDict()
        self._lock = threading.RLock()
        self._usage_cache: dict[str, tuple[float, int]] = {}
        self._global = WindowLimiter(create_per_hour, 3600.0)
        self._per_address = WindowLimiter(create_per_address_per_hour, 3600.0)
        self._recover_address = WindowLimiter(10, 3600.0)  # 80 bits cannot be guessed, but nobody gets to try freely
        self._recover_global = WindowLimiter(200, 3600.0)

    def home(self, workspace_id: str) -> Path:
        if not ID.fullmatch(workspace_id):
            raise WorkspaceError(404, "not_found", "not found")
        return self.root / "workspaces" / workspace_id

    def _build(self, workspace_id: str) -> Workspace:
        with self._lock:
            if workspace_id in self._live:
                self._live.move_to_end(workspace_id)
                return self._live[workspace_id]
            home = self.home(workspace_id)
            for d in (home.parent, home):  # mkdir(parents=True, mode=0o700) applies the mode to the last directory only: the ones above it would be 0755 and list every workspace id
                d.mkdir(exist_ok=True, mode=0o700)
                d.chmod(0o700)
            em, assistant, voice = self._factory(workspace_id, home)
            info = self.directory.info(workspace_id) or {"created": self._clock()}
            ws = Workspace(workspace_id, em, assistant, voice, threading.RLock(), Principal(user_id=workspace_id, roles=(Role.ORGANIZER,)), csrf_for(self.secret, workspace_id),
                           time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(info["created"])))
            self._live[workspace_id] = ws
            self._evict()
            return ws

    def _evict(self) -> None:
        """Drop the least recently used workspaces beyond ``max_live``, but never one a request is using. Dropping it would let the next request build a second
        ``Workspace`` for the same directory with a second lock, and two requests would then change the same files unsynchronised (F-016). The live set may
        exceed ``max_live`` by the number of requests in flight, and shrinks again as they finish."""
        for workspace_id in list(self._live):
            if len(self._live) <= self.max_live:
                break
            old = self._live[workspace_id]
            if old.inflight > 0:
                continue
            del self._live[workspace_id]
            pool = getattr(old.voice, "_pool", None)
            if pool is not None:
                pool.shutdown(wait=False)

    def acquire(self, ws: Workspace) -> None:
        with self._lock:
            ws.inflight += 1

    def release(self, ws: Workspace) -> None:
        with self._lock:
            ws.inflight = max(0, ws.inflight - 1)
            if len(self._live) > self.max_live:
                self._evict()

    def open(self, credential: str | None) -> Workspace | None:
        workspace_id = self.directory.lookup(credential) if credential else None
        return self._build(workspace_id) if workspace_id else None

    def create(self, address: str) -> tuple[Workspace, str]:
        """A new empty workspace, or a WorkspaceError: 429 when this address or the server as a whole is creating too many, 503 when the cap is reached."""
        if not self._per_address.allow(address or "?") or not self._global.allow("all"):
            raise WorkspaceError(429, "workspace_rate_limited", "too many new workspaces are being created; try again later")
        if self.directory.count() >= self.max_workspaces:
            raise WorkspaceError(503, "workspace_limit", "this server has reached its limit of workspaces; try again later")
        workspace_id, credential = self.directory.create()
        return self._build(workspace_id), credential

    def rotate(self, workspace_id: str) -> str | None:
        return self.directory.rotate(workspace_id)

    def set_recovery(self, workspace_id: str) -> str | None:
        return self.directory.set_recovery(workspace_id)

    def recover(self, address: str, phrase: object) -> tuple[str, str, str]:
        """(workspace id, new credential, new phrase), or a WorkspaceError: 429 when this address or the server is trying too often, 404 for a phrase that opens nothing."""
        if not self._recover_address.allow(address or "?") or not self._recover_global.allow("all"):
            raise WorkspaceError(429, "recovery_rate_limited", "too many recovery attempts; try again later")
        found = self.directory.recover(phrase)
        if found is None:
            raise WorkspaceError(404, "recovery_not_recognised", "that recovery phrase is not recognised")
        return found

    def discard_empty(self, workspace_id: str) -> bool:
        """Drop a workspace that holds nothing (the throwaway one a visitor was given before they recovered their own). Refuses one with data."""
        if self.has_data(workspace_id):
            return False
        with self._lock:
            self._live.pop(workspace_id, None)
        shutil.rmtree(self.home(workspace_id), ignore_errors=True)
        return self.directory.forget(workspace_id)

    def revoke(self, workspace_id: str) -> bool:
        with self._lock:
            self._live.pop(workspace_id, None)
        return self.directory.revoke(workspace_id)

    def has_data(self, workspace_id: str) -> bool:
        plans = self.home(workspace_id) / "plans"
        return plans.exists() and any(plans.iterdir())

    # ---- quotas (Phase 5.1): one visitor must not be able to fill the disk or plan without end

    def _limit(self, name: str, default: int) -> int:
        try:
            return max(1, int(os.environ.get(name, default)))
        except ValueError:
            return default

    def usage(self, workspace_id: str) -> tuple[int, int]:
        """(bytes on disk, plans first saved today) for one workspace. The byte count is cached for a few seconds so a burst of writes does not walk the tree each time."""
        now = self._clock()
        cached = self._usage_cache.get(workspace_id)
        if cached and now - cached[0] < 5:
            size = cached[1]
        else:
            home = self.home(workspace_id)
            size = sum(p.stat().st_size for p in home.rglob("*") if p.is_file()) if home.exists() else 0
            self._usage_cache[workspace_id] = (now, size)
        today = datetime.datetime.fromtimestamp(now, datetime.UTC).date()
        plans = self.home(workspace_id) / "plans"
        started = sum(1 for p in plans.glob("plan-*-v0001.json") if datetime.datetime.fromtimestamp(p.stat().st_mtime, datetime.UTC).date() == today) if plans.exists() else 0
        return size, started

    def check_write(self, workspace_id: str, creating_plan: bool = False, incoming: int = 0) -> None:
        """Raise 507 ``workspace_quota`` when the workspace holds more than ``EVENTMATE_WS_MAX_BYTES`` (default 50 MB), and 429 ``plans_per_day`` when it has
        started ``EVENTMATE_WS_MAX_PLANS_PER_DAY`` (default 20) plans today. Deleting is never refused by this: it is how a visitor makes room."""
        size, started = self.usage(workspace_id)
        if size + incoming >= self._limit("EVENTMATE_WS_MAX_BYTES", 50 * 1024 * 1024):  # ``incoming``: what an import is about to add, so one archive cannot step over the quota (F-013)
            raise WorkspaceError(507, "workspace_quota", "this workspace is full; delete something before adding more")
        if creating_plan and started >= self._limit("EVENTMATE_WS_MAX_PLANS_PER_DAY", 20):
            raise WorkspaceError(429, "plans_per_day", "this workspace has made as many plans as it may today; try again tomorrow")

    def delete(self, workspace_id: str) -> bool:
        """Remove a workspace and everything in it: its credential, its files (each overwritten before it is unlinked, a symlink only unlinked) and
        its row. A tombstone records the id and the time and nothing else. Backups and exported archives are not reached. Returns whether it existed."""
        from eventmate.retention import wipe, write_tombstone

        home = self.home(workspace_id)
        existed = self.directory.exists(workspace_id) or home.exists()
        with self._lock:
            old = self._live.pop(workspace_id, None)
            self._usage_cache.pop(workspace_id, None)
        if old is not None and getattr(old.voice, "_pool", None) is not None:
            old.voice._pool.shutdown(wait=False)
        self.directory.revoke(workspace_id)  # nothing can open it while it is being removed
        if home.is_symlink():
            home.unlink()
        elif home.exists():
            for path in sorted(home.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                if path.is_symlink():
                    path.unlink()
                elif path.is_dir():
                    path.chmod(0o700)
                    path.rmdir()
                else:
                    path.chmod(0o600)
                    wipe(path)
            home.rmdir()
        self.directory.forget(workspace_id)
        if existed:
            write_tombstone(self.root / "tombstones.jsonl", "workspace", workspace_id, datetime.datetime.fromtimestamp(self._clock(), datetime.UTC))
        return existed

    def record_plan_deletion(self, workspace_id: str, plan_id: str) -> None:
        """Add a deleted plan to the server's deletion log (``<data dir>/tombstones.jsonl``), with the workspace it was in. A backup taken before the deletion
        is restored with this log, so the plan does not come back (docs/RUNBOOK.md)."""
        from eventmate.retention import write_tombstone

        write_tombstone(self.root / "tombstones.jsonl", "plan", plan_id, datetime.datetime.fromtimestamp(self._clock(), datetime.UTC), workspace=workspace_id)

    def sweep_idle_empty(self, idle_s: float) -> list[str]:
        """Remove workspaces that never saved anything and have been idle for ``idle_s``. A workspace with any data is never touched here."""
        removed = []
        for workspace_id in self.directory.idle_before(self._clock() - idle_s):
            if self.has_data(workspace_id):
                continue
            with self._lock:
                self._live.pop(workspace_id, None)
            shutil.rmtree(self.home(workspace_id), ignore_errors=True)
            if self.directory.forget(workspace_id):
                removed.append(workspace_id)
        return removed
