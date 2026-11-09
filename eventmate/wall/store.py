"""The wall's database: one SQLite file, WAL mode, every state change inside one ``BEGIN IMMEDIATE`` transaction.

Rules the code enforces (and ``tests/test_wall_*.py`` checks): a slot is assigned once, at first approval, and never reused or moved; one credential holds
one mark; a round is decided by the server's clock in the same transaction that records it; removal blanks the name at once; nothing here knows about plans,
guests, drafts or workspaces.
"""

from __future__ import annotations

import contextlib
import json
import os
import random
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Any
from collections.abc import Callable, Iterator, Mapping
from zoneinfo import ZoneInfo

from eventmate.wall import layout
from eventmate.wall.names import NAME_MAX, NAME_MIN, InvalidName, validate

SCHEMA_VERSION = 1
HARD_MAX_CAPACITY = 300
MS = 1000
SQLITE_MAX_INT = 2 ** 62  # far above any real id, below SQLite's 64-bit limit: a larger number is simply not found

SCHEMA = """
CREATE TABLE meta (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  schema_version INTEGER NOT NULL,
  layout_version_current INTEGER NOT NULL,
  accepting INTEGER NOT NULL DEFAULT 1,
  visible_count INTEGER NOT NULL DEFAULT 0,
  accepted_lifetime INTEGER NOT NULL DEFAULT 0,
  hall_unlocked INTEGER NOT NULL DEFAULT 0,
  hall_unlocked_at INTEGER,
  mod_seq INTEGER NOT NULL DEFAULT 0,
  hall_period_id TEXT NOT NULL DEFAULT '',
  last_sweep INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE sections (section_id INTEGER PRIMARY KEY, layout_version INTEGER NOT NULL,
  capacity INTEGER NOT NULL CHECK (capacity BETWEEN 1 AND 300), next_slot INTEGER NOT NULL DEFAULT 0);
CREATE TABLE marks (
  mark_id INTEGER PRIMARY KEY AUTOINCREMENT,
  display_name TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending','visible','hidden','removed')),
  created_at INTEGER NOT NULL,
  approved_at INTEGER,
  section_id INTEGER REFERENCES sections(section_id),
  slot INTEGER,
  found_at INTEGER,
  hall INTEGER NOT NULL DEFAULT 0,
  reason TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX marks_slot ON marks(section_id, slot) WHERE section_id IS NOT NULL;
CREATE INDEX marks_status ON marks(status, created_at);
CREATE INDEX marks_hall ON marks(hall, status, found_at);
CREATE TABLE wall_sessions (cred_hash TEXT PRIMARY KEY, created_at INTEGER NOT NULL, last_seen INTEGER NOT NULL,
  mark_id INTEGER UNIQUE REFERENCES marks(mark_id), recovery_hash TEXT);
CREATE INDEX wall_sessions_recovery ON wall_sessions(recovery_hash) WHERE recovery_hash IS NOT NULL;
CREATE TABLE submissions (cred_hash TEXT NOT NULL, idempotency_key TEXT NOT NULL, mark_id INTEGER NOT NULL, created_at INTEGER NOT NULL,
  http_status INTEGER NOT NULL, body_json TEXT NOT NULL, UNIQUE (cred_hash, idempotency_key));
CREATE TABLE rounds (
  round_id TEXT PRIMARY KEY, cred_hash TEXT NOT NULL, mark_id INTEGER NOT NULL, section_id INTEGER NOT NULL,
  from_slot INTEGER NOT NULL, to_slot INTEGER NOT NULL, mode TEXT NOT NULL CHECK (mode IN ('timed','untimed')),
  issued_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, ready_at INTEGER, deadline_at INTEGER, used_at INTEGER, result TEXT, elapsed_ms INTEGER,
  snapshot_next_slot INTEGER NOT NULL, snapshot_layout_version INTEGER NOT NULL, snapshot_mod_seq INTEGER NOT NULL
);
CREATE INDEX rounds_cred ON rounds(cred_hash, issued_at);
CREATE TABLE round_history (mark_id INTEGER NOT NULL, to_slot INTEGER NOT NULL, issued_at INTEGER NOT NULL);
CREATE INDEX round_history_mark ON round_history(mark_id, issued_at);
CREATE TABLE hall_results (period_id TEXT NOT NULL, mark_id INTEGER NOT NULL, best_elapsed_ms INTEGER NOT NULL, best_elapsed_at INTEGER NOT NULL,
  PRIMARY KEY (period_id, mark_id));
CREATE INDEX hall_results_top ON hall_results(period_id, best_elapsed_ms, best_elapsed_at, mark_id);
"""


class WallError(Exception):
    """An expected refusal: ``status`` and ``code`` are the HTTP answer, ``message`` is safe to show."""

    def __init__(self, status: int, code: str, message: str, **extra: Any):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


class WallSchemaError(RuntimeError):
    """The database file is newer than this build, or not a wall database."""


def _not_found() -> WallError:
    return WallError(404, "not_found", "not found")  # one body for unknown, foreign, wrong code and removed: no oracle


@dataclass(frozen=True)
class WallConfig:
    section_capacity: int = 48  # default; to be confirmed on a real phone before release. Stored per section at creation.
    moderation: str = "pre"  # "pre": a mark is invisible until approved; "post": visible at once, hide must then be fast
    daily_submission_cap: int = 200
    total_cap: int = 10_000
    pending_cap: int = 500
    unclaimed_sessions_max: int = 100_000
    window_ms: int = 5000
    allowance_ms: int = 1500
    round_expiry_s: int = 120
    untimed_hard_s: int = 600
    rounds_per_day: int = 5
    speed_floor_ms: int = 250
    fastest_default: int = 5
    fastest_max: int = 10
    recent_destinations: int = 3
    hall_zone: str = "Asia/Calcutta"
    hall_kind: str = "month"  # or "week"; period ids are strings, so switching needs no schema change
    unclaimed_ttl_days: int = 30
    round_retention_days: int = 30
    submission_ttl_hours: int = 24

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> WallConfig:
        """Settings the owner may change without code. An unset name keeps the default; a bad value stops the app at start, not at the first visitor."""
        ints = {"EVENTMATE_WALL_CAPACITY": "section_capacity", "EVENTMATE_WALL_DAILY_CAP": "daily_submission_cap", "EVENTMATE_WALL_TOTAL_CAP": "total_cap",
                "EVENTMATE_WALL_PENDING_CAP": "pending_cap", "EVENTMATE_WALL_WINDOW_MS": "window_ms", "EVENTMATE_WALL_ALLOWANCE_MS": "allowance_ms",
                "EVENTMATE_WALL_ROUNDS_PER_DAY": "rounds_per_day", "EVENTMATE_WALL_SPEED_FLOOR_MS": "speed_floor_ms"}
        texts = {"EVENTMATE_WALL_MODERATION": "moderation", "EVENTMATE_WALL_ZONE": "hall_zone", "EVENTMATE_WALL_HALL_KIND": "hall_kind"}
        values: dict[str, Any] = {}
        for name, field_name in ints.items():
            raw = environ.get(name, "").strip()
            if raw:
                if not raw.isdigit():
                    raise ValueError(f"{name} must be a whole number")
                values[field_name] = int(raw)
        for name, field_name in texts.items():
            raw = environ.get(name, "").strip()
            if raw:
                values[field_name] = raw
        return cls(**values)

    def __post_init__(self):
        if not 1 <= self.section_capacity <= HARD_MAX_CAPACITY:
            raise ValueError(f"section_capacity must be 1 to {HARD_MAX_CAPACITY}")
        if self.moderation not in ("pre", "post"):
            raise ValueError("moderation must be 'pre' or 'post'")
        if self.hall_kind not in ("month", "week"):
            raise ValueError("hall_kind must be 'month' or 'week'")
        ZoneInfo(self.hall_zone)


def _iso(ms: int | None) -> str | None:
    return None if ms is None else datetime.fromtimestamp(ms / MS, tz=UTC).isoformat().replace("+00:00", "Z")


class WallStore:
    def __init__(self, path: Path, config: WallConfig = WallConfig(), clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 rng: random.Random | None = None):
        self.path, self.config, self.clock = Path(path), config, clock
        self.rng = rng or random.SystemRandom()
        self._zone = ZoneInfo(config.hall_zone)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fresh = not self.path.exists()
        if fresh:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            os.close(fd)
        conn = self._connect()
        try:
            self._open_schema(conn)
        finally:
            conn.close()
        with contextlib.suppress(OSError):
            os.chmod(self.path, 0o600)

    # ------------------------------------------------------------ plumbing

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)  # autocommit; transactions are explicit
        conn.execute("PRAGMA secure_delete=ON")  # a deleted name is overwritten, not left in a free page (the wall package imports nothing outside itself, so no shared helper)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")  # a name is meant to last until the site closes: pay for durability
        return conn

    def _open_schema(self, conn: sqlite3.Connection) -> None:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for stmt in filter(None, (s.strip() for s in SCHEMA.split(";\n"))):
                    conn.execute(stmt)
                conn.execute("INSERT INTO meta(id, schema_version, layout_version_current) VALUES (1, ?, ?)", (SCHEMA_VERSION, layout.CURRENT_LAYOUT))
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            return
        if "meta" not in tables:
            raise WallSchemaError(f"{self.path.name} is not a wall database")
        version = conn.execute("SELECT schema_version FROM meta WHERE id = 1").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise WallSchemaError(f"{self.path.name} has schema version {version}; this build understands up to {SCHEMA_VERSION}. Upgrade the program, do not downgrade the file.")
        for step in range(version, SCHEMA_VERSION):  # upgraders: one function per step, in order; none yet because 1 is the first release
            UPGRADERS[step](conn)

    @contextlib.contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")
        finally:
            conn.close()

    @contextlib.contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    def _now_ms(self) -> int:
        return int(self.clock().timestamp() * MS)

    # ------------------------------------------------------------ periods

    def period_at(self, now_ms: int) -> dict[str, Any]:
        """The ranking period the server clock says it is: id plus absolute UTC bounds, so a visitor in any zone sees the real reset time."""
        local = datetime.fromtimestamp(now_ms / MS, tz=UTC).astimezone(self._zone)
        if self.config.hall_kind == "week":
            start = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=7)
            iso = start.isocalendar()
            pid = f"{iso[0]}-W{iso[1]:02d}"
        else:
            start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end = (start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1))
            pid = f"{start.year}-{start.month:02d}"
        to_utc = lambda d: d.astimezone(UTC).isoformat().replace("+00:00", "Z")
        return {"id": pid, "kind": self.config.hall_kind, "starts_at": to_utc(start), "ends_at": to_utc(end)}

    def _roll_period(self, conn: sqlite3.Connection, now_ms: int) -> str:
        """Ended periods are deleted lazily and idempotently on the first write in a new period; no scheduled job. Names, slots, found_at and the opt-in stay."""
        pid = self.period_at(now_ms)["id"]
        if conn.execute("SELECT hall_period_id FROM meta WHERE id = 1").fetchone()[0] != pid:
            conn.execute("DELETE FROM hall_results WHERE period_id != ?", (pid,))
            conn.execute("UPDATE meta SET hall_period_id = ? WHERE id = 1", (pid,))
        return pid

    def _roll_if_needed(self, now_ms: int) -> None:
        pid = self.period_at(now_ms)["id"]
        with self._read() as c:
            current = c.execute("SELECT hall_period_id FROM meta WHERE id = 1").fetchone()[0]
        if current != pid:
            with self._tx() as c:
                self._roll_period(c, now_ms)

    # ------------------------------------------------------------ sessions

    def create_session(self, cred_hash: str) -> None:
        now = self._now_ms()
        with self._tx() as c:
            if c.execute("SELECT COUNT(*) FROM wall_sessions WHERE mark_id IS NULL").fetchone()[0] >= self.config.unclaimed_sessions_max:
                raise WallError(429, "rate_limited", "too many visitors right now; try again later")
            c.execute("INSERT INTO wall_sessions(cred_hash, created_at, last_seen) VALUES (?, ?, ?)", (cred_hash, now, now))

    def session_exists(self, cred_hash: str) -> bool:
        """Does this credential hash exist? Touches last_seen at most once an hour so reads do not take the write lock."""
        now = self._now_ms()
        with self._read() as c:
            row = c.execute("SELECT last_seen FROM wall_sessions WHERE cred_hash = ?", (cred_hash,)).fetchone()
        if row is None:
            return False
        if now - row[0] > 3600 * MS:
            with self._tx() as c:
                c.execute("UPDATE wall_sessions SET last_seen = ? WHERE cred_hash = ?", (now, cred_hash))
        return True

    def _session(self, c: sqlite3.Connection, cred_hash: str) -> sqlite3.Row:
        row = c.execute("SELECT * FROM wall_sessions WHERE cred_hash = ?", (cred_hash,)).fetchone()
        if row is None:
            raise WallError(401, "unauthorized", "no wall session")
        return row

    # ------------------------------------------------------------ marks

    @staticmethod
    def _mark_summary(row: sqlite3.Row) -> dict[str, Any]:
        if row["status"] == "removed":
            return {"mark_id": row["mark_id"], "status": "removed", "name": None, "section_id": None, "slot": None}
        placed = row["status"] in ("visible", "hidden")
        return {"mark_id": row["mark_id"], "name": row["display_name"], "status": row["status"],
                "section_id": row["section_id"] if placed else None, "slot": row["slot"] if placed else None}

    def _meta(self, c: sqlite3.Connection) -> sqlite3.Row:
        return c.execute("SELECT * FROM meta WHERE id = 1").fetchone()

    def _accepting(self, c: sqlite3.Connection, now_ms: int) -> tuple[bool, str]:
        m = self._meta(c)
        if not m["accepting"]:
            return False, "the wall is not taking names right now"
        day = now_ms - 24 * 3600 * MS
        if c.execute("SELECT COUNT(*) FROM marks WHERE created_at >= ?", (day,)).fetchone()[0] >= self.config.daily_submission_cap:
            return False, "the wall has taken as many names as it can today; try again tomorrow"
        if c.execute("SELECT COUNT(*) FROM marks WHERE status != 'removed' OR section_id IS NOT NULL").fetchone()[0] >= self.config.total_cap:
            return False, "the wall is full"
        if c.execute("SELECT COUNT(*) FROM marks WHERE status = 'pending'").fetchone()[0] >= self.config.pending_cap:
            return False, "too many names are waiting to be reviewed; try again later"
        return True, ""

    def _assign_slot(self, c: sqlite3.Connection, mark_id: int, now_ms: int) -> tuple[int, int]:
        """First approval only. Inside the caller's BEGIN IMMEDIATE: take the open section's next slot, open a new section when it is full. Never reused."""
        sec = c.execute("SELECT section_id, capacity, next_slot FROM sections ORDER BY section_id DESC LIMIT 1").fetchone()
        if sec is None or sec["next_slot"] >= sec["capacity"]:
            sid = 0 if sec is None else sec["section_id"] + 1  # 0-based, contiguous, never deleted or renumbered
            c.execute("INSERT INTO sections(section_id, layout_version, capacity, next_slot) VALUES (?, ?, ?, 0)",
                      (sid, self._meta(c)["layout_version_current"], self.config.section_capacity))
            sec = c.execute("SELECT section_id, capacity, next_slot FROM sections WHERE section_id = ?", (sid,)).fetchone()
        slot = sec["next_slot"]
        c.execute("UPDATE sections SET next_slot = next_slot + 1 WHERE section_id = ?", (sec["section_id"],))
        c.execute("UPDATE marks SET section_id = ?, slot = ?, approved_at = ? WHERE mark_id = ?", (sec["section_id"], slot, now_ms, mark_id))
        return sec["section_id"], slot

    def _make_visible(self, c: sqlite3.Connection, mark_id: int, now_ms: int) -> None:
        self._assign_slot(c, mark_id, now_ms)
        c.execute("UPDATE marks SET status = 'visible' WHERE mark_id = ?", (mark_id,))
        c.execute("UPDATE meta SET visible_count = visible_count + 1, accepted_lifetime = accepted_lifetime + 1 WHERE id = 1")

    def submit(self, cred_hash: str, idempotency_key: str, raw_name: Any) -> tuple[int, dict[str, Any], bool]:
        """(http status, body, created: a new mark was made by this call). One transaction on the credential row: exactly one mark per credential, and a retry with the same key returns the original."""
        try:
            name = validate(raw_name)
        except InvalidName as exc:
            raise WallError(400, "invalid_name", str(exc)) from None
        now = self._now_ms()
        with self._tx() as c:
            ses = self._session(c, cred_hash)
            prior = c.execute("SELECT http_status, body_json FROM submissions WHERE cred_hash = ? AND idempotency_key = ?", (cred_hash, idempotency_key)).fetchone()
            if prior is not None:
                return prior["http_status"], json.loads(prior["body_json"]), False
            if ses["mark_id"] is not None:
                row = c.execute("SELECT status FROM marks WHERE mark_id = ?", (ses["mark_id"],)).fetchone()
                body = {"error": "this browser already has a name on the wall", "code": "already_marked", "mark_id": ses["mark_id"], "status": row["status"]}
                return 409, body, False  # not stored: a replay would give the same answer, and storing every refused key would let one cookie grow the table without limit (F-004)
            ok, why = self._accepting(c, now)
            if not ok:
                raise WallError(503, "wall_closed", why)
            cur = c.execute("INSERT INTO marks(display_name, status, created_at) VALUES (?, 'pending', ?)", (name, now))
            mark_id = cur.lastrowid
            if mark_id is None:
                raise RuntimeError("the mark was not stored")
            if self.config.moderation == "post":
                self._make_visible(c, mark_id, now)
            c.execute("UPDATE wall_sessions SET mark_id = ? WHERE cred_hash = ?", (mark_id, cred_hash))
            status = c.execute("SELECT status FROM marks WHERE mark_id = ?", (mark_id,)).fetchone()["status"]
            body = {"mark_id": mark_id, "status": status}
            c.execute("INSERT INTO submissions VALUES (?, ?, ?, ?, ?, ?)", (cred_hash, idempotency_key, mark_id, now, 201, json.dumps(body)))
            return 201, body, True

    def me(self, cred_hash: str) -> dict[str, Any]:
        now = self._now_ms()
        self._roll_if_needed(now)
        with self._read() as c:
            ses = self._session(c, cred_hash)
            period = self.period_at(now)["id"]
            if ses["mark_id"] is None:
                return {"has_mark": False, "mark": None, "hall": False, "hall_eligible": False, "best_elapsed_ms": None, "has_recovery_code": ses["recovery_hash"] is not None}
            row = c.execute("SELECT * FROM marks WHERE mark_id = ?", (ses["mark_id"],)).fetchone()
            best = c.execute("SELECT best_elapsed_ms FROM hall_results WHERE period_id = ? AND mark_id = ?", (period, row["mark_id"])).fetchone()
            return {"has_mark": True, "mark": self._mark_summary(row), "hall": bool(row["hall"]), "hall_eligible": row["found_at"] is not None,
                    "best_elapsed_ms": best[0] if best else None, "has_recovery_code": ses["recovery_hash"] is not None}

    def session_state(self, cred_hash: str) -> dict[str, Any]:
        with self._read() as c:
            ses = self._session(c, cred_hash)
            status = None
            if ses["mark_id"] is not None:
                status = c.execute("SELECT status FROM marks WHERE mark_id = ?", (ses["mark_id"],)).fetchone()["status"]
            return {"has_mark": ses["mark_id"] is not None, "mark_status": status}

    # ------------------------------------------------------------ recovery and removal (8.5)

    def issue_recovery_code(self, cred_hash: str, code_hash: str, replace_existing: bool) -> None:
        with self._tx() as c:
            ses = self._session(c, cred_hash)
            if ses["mark_id"] is None:
                raise _not_found()
            if ses["recovery_hash"] is not None and not replace_existing:
                raise WallError(409, "code_exists", "a recovery code already exists; replace it only if you did not save it")
            c.execute("UPDATE wall_sessions SET recovery_hash = ? WHERE cred_hash = ?", (code_hash, cred_hash))

    def recover(self, code_hash: str, caller_cred_hash: str, new_cred_hash: str) -> dict[str, Any]:
        """One transaction: find the mark by the code's hash, consume the code, give its row a new credential (the old cookie secret stops working)."""
        with self._tx() as c:
            self._session(c, caller_cred_hash)
            target = c.execute("SELECT cred_hash, mark_id, recovery_hash FROM wall_sessions WHERE recovery_hash = ?", (code_hash,)).fetchone()
            if target is None or not secrets.compare_digest(target["recovery_hash"], code_hash):
                raise _not_found()
            old = target["cred_hash"]
            c.execute("UPDATE wall_sessions SET cred_hash = ?, recovery_hash = NULL, last_seen = ? WHERE cred_hash = ?", (new_cred_hash, self._now_ms(), old))
            c.execute("UPDATE rounds SET cred_hash = ? WHERE cred_hash = ?", (new_cred_hash, old))
            c.execute("UPDATE submissions SET cred_hash = ? WHERE cred_hash = ?", (new_cred_hash, old))
            if caller_cred_hash != old:  # the browser's anonymous bootstrap session is spent; one holding its own mark is left alone
                c.execute("DELETE FROM wall_sessions WHERE cred_hash = ? AND mark_id IS NULL", (caller_cred_hash,))
            row = c.execute("SELECT * FROM marks WHERE mark_id = ?", (target["mark_id"],)).fetchone()
            return {"mark": self._mark_summary(row), "has_recovery_code": False}

    def _remove_mark(self, c: sqlite3.Connection, mark_id: int) -> None:
        row = c.execute("SELECT status FROM marks WHERE mark_id = ?", (mark_id,)).fetchone()
        if row is None or row["status"] == "removed":
            return
        c.execute("UPDATE marks SET display_name = '', status = 'removed', hall = 0 WHERE mark_id = ?", (mark_id,))
        c.execute("DELETE FROM hall_results WHERE mark_id = ?", (mark_id,))
        c.execute("UPDATE rounds SET result = 'cancelled', used_at = ? WHERE mark_id = ? AND result IS NULL", (self._now_ms(), mark_id))
        if row["status"] == "visible":
            c.execute("UPDATE meta SET visible_count = visible_count - 1 WHERE id = 1")
        c.execute("UPDATE meta SET mod_seq = mod_seq + 1 WHERE id = 1")

    def remove_mine(self, cred_hash: str) -> None:
        with self._tx() as c:
            ses = self._session(c, cred_hash)
            if ses["mark_id"] is None:
                raise _not_found()
            self._remove_mark(c, ses["mark_id"])

    def remove_by_code(self, code_hash: str, caller_cred_hash: str) -> None:
        with self._tx() as c:
            self._session(c, caller_cred_hash)
            target = c.execute("SELECT cred_hash, mark_id, recovery_hash FROM wall_sessions WHERE recovery_hash = ?", (code_hash,)).fetchone()
            if target is None or not secrets.compare_digest(target["recovery_hash"], code_hash):
                raise _not_found()
            self._remove_mark(c, target["mark_id"])
            c.execute("UPDATE wall_sessions SET recovery_hash = NULL WHERE cred_hash = ?", (target["cred_hash"],))  # a code-based removal consumes the code

    def set_hall(self, cred_hash: str, opt_in: bool) -> bool:
        now = self._now_ms()
        with self._tx() as c:
            ses = self._session(c, cred_hash)
            if ses["mark_id"] is None:
                raise _not_found()
            pid = self._roll_period(c, now)
            row = c.execute("SELECT * FROM marks WHERE mark_id = ?", (ses["mark_id"],)).fetchone()
            if not opt_in:  # always allowed, idempotent, even with no found round
                c.execute("UPDATE marks SET hall = 0 WHERE mark_id = ?", (row["mark_id"],))
                c.execute("DELETE FROM hall_results WHERE period_id = ? AND mark_id = ?", (pid, row["mark_id"]))
                return False
            if row["found_at"] is None or row["status"] != "visible":
                raise WallError(409, "not_eligible", "find your own name in a round first")
            c.execute("UPDATE marks SET hall = 1 WHERE mark_id = ?", (row["mark_id"],))
            return True

    # ------------------------------------------------------------ public reads (8.6 items 1 to 3)

    def meta(self) -> dict[str, Any]:
        now = self._now_ms()
        self._roll_if_needed(now)
        with self._read() as c:
            m = self._meta(c)
            sections = c.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
            accepting, _why = self._accepting(c, now)
            return {"schema_version": m["schema_version"], "accepting": accepting, "section_count": sections, "visible_count": m["visible_count"],
                    "hall_unlocked": bool(m["hall_unlocked"]), "hall_period": self.period_at(now),
                    "limits": {"name_min": NAME_MIN, "name_max": NAME_MAX},
                    "round": {"window_ms_default": self.config.window_ms, "untimed_available": True}}

    def section(self, section_id: int, after_slot: int = -1, limit: int = 100) -> dict[str, Any]:
        if not 0 <= section_id < SQLITE_MAX_INT:
            raise _not_found()
        limit = max(1, min(int(limit), HARD_MAX_CAPACITY))
        with self._read() as c:
            sec = c.execute("SELECT * FROM sections WHERE section_id = ?", (section_id,)).fetchone()
            if sec is None:
                raise _not_found()
            rows = c.execute("SELECT slot, mark_id, display_name FROM marks WHERE section_id = ? AND status = 'visible' AND slot > ? ORDER BY slot LIMIT ?",
                             (section_id, after_slot, limit + 1)).fetchall()
            more = len(rows) > limit
            rows = rows[:limit]
            return {"section_id": sec["section_id"], "layout_version": sec["layout_version"], "capacity": sec["capacity"],
                    "slots": [{"slot": r["slot"], "mark_id": r["mark_id"], "name": r["display_name"]} for r in rows],
                    "next_after_slot": rows[-1]["slot"] if more and rows else None}

    @staticmethod
    def _encode_cursor(found_at: int, mark_id: int) -> str:
        import base64
        return base64.urlsafe_b64encode(json.dumps([found_at, mark_id]).encode()).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(raw: str) -> tuple[int, int]:
        import base64
        try:
            pad = "=" * (-len(raw) % 4)
            a, b = json.loads(base64.urlsafe_b64decode(raw + pad))
            if not (isinstance(a, int) and isinstance(b, int)) or isinstance(a, bool) or isinstance(b, bool) or not (0 <= a < SQLITE_MAX_INT and 0 <= b < SQLITE_MAX_INT):
                raise ValueError
            return a, b
        except Exception:
            raise WallError(400, "bad_request", "that page marker is not valid") from None

    def hall(self, order: str = "newest", cursor: str | None = None, limit: int | None = None) -> dict[str, Any]:
        if order not in ("newest", "fastest"):
            raise WallError(400, "bad_request", "order must be newest or fastest")
        now = self._now_ms()
        self._roll_if_needed(now)
        period = self.period_at(now)
        with self._read() as c:
            if order == "fastest":
                n = max(1, min(int(limit or self.config.fastest_default), self.config.fastest_max))
                rows = c.execute(
                    "SELECT m.mark_id, m.display_name, h.best_elapsed_ms FROM hall_results h JOIN marks m ON m.mark_id = h.mark_id "
                    "WHERE h.period_id = ? AND m.hall = 1 AND m.status = 'visible' ORDER BY h.best_elapsed_ms, h.best_elapsed_at, h.mark_id LIMIT ?", (period["id"], n)).fetchall()
                return {"entries": [{"mark_id": r["mark_id"], "name": r["display_name"], "elapsed_ms": r["best_elapsed_ms"], "rank": i + 1} for i, r in enumerate(rows)],
                        "next_cursor": None, "period": period}
            n = max(1, min(int(limit or 50), 100))
            base = ("SELECT m.mark_id, m.display_name, m.found_at, h.best_elapsed_ms FROM marks m LEFT JOIN hall_results h ON h.mark_id = m.mark_id AND h.period_id = ? "
                    "WHERE m.hall = 1 AND m.status = 'visible' AND m.found_at IS NOT NULL ")
            order = "ORDER BY m.found_at DESC, m.mark_id DESC LIMIT ?"
            if cursor:
                fa, mid = self._decode_cursor(cursor)
                rows = c.execute(base + "AND (m.found_at < ? OR (m.found_at = ? AND m.mark_id < ?)) " + order, (period["id"], fa, fa, mid, n + 1)).fetchall()
            else:
                rows = c.execute(base + order, (period["id"], n + 1)).fetchall()
            more = len(rows) > n
            rows = rows[:n]
            return {"entries": [{"mark_id": r["mark_id"], "name": r["display_name"], "elapsed_ms": r["best_elapsed_ms"], "rank": None} for r in rows],
                    "next_cursor": self._encode_cursor(rows[-1]["found_at"], rows[-1]["mark_id"]) if more and rows else None}

    # ------------------------------------------------------------ rounds (8.7)

    def _snapshot(self, c: sqlite3.Connection, section_id: int) -> dict[str, Any]:
        sec = c.execute("SELECT * FROM sections WHERE section_id = ?", (section_id,)).fetchone()
        rows = c.execute("SELECT slot, mark_id, display_name FROM marks WHERE section_id = ? AND status = 'visible' ORDER BY slot LIMIT ?", (section_id, HARD_MAX_CAPACITY)).fetchall()
        return {"layout_version": sec["layout_version"], "capacity": sec["capacity"], "next_slot": sec["next_slot"],
                "slots": [{"slot": r["slot"], "mark_id": r["mark_id"], "name": r["display_name"]} for r in rows]}

    def _targets(self, c: sqlite3.Connection, mark: sqlite3.Row, snap: dict[str, Any]) -> list[int]:
        """Positions the owner's name may be shown at: another visible name (a swap), a removed tombstone, or a slot not yet assigned (a move). Never a hidden mark's slot."""
        capacity, next_slot, from_slot = snap["capacity"], snap["next_slot"], mark["slot"]
        hidden = {r[0] for r in c.execute("SELECT slot FROM marks WHERE section_id = ? AND status = 'hidden'", (mark["section_id"],))}
        visible = {s["slot"] for s in snap["slots"]}
        tombstones = {r[0] for r in c.execute("SELECT slot FROM marks WHERE section_id = ? AND status = 'removed' AND slot IS NOT NULL", (mark["section_id"],))}
        pool = [s for s in range(capacity) if s != from_slot and abs(s - from_slot) > 1 and s not in hidden and (s in visible or s in tombstones or s >= next_slot)]
        recent = [r[0] for r in c.execute("SELECT to_slot FROM round_history WHERE mark_id = ? ORDER BY issued_at DESC, rowid DESC LIMIT ?", (mark["mark_id"], self.config.recent_destinations))]
        fresh = [s for s in pool if s not in recent]
        return fresh or pool  # finite slots mean repeats must eventually happen: when only recent ones remain, the bag is refilled

    def issue_round(self, cred_hash: str, mode: str) -> dict[str, Any]:
        if mode not in ("timed", "untimed"):
            raise WallError(400, "bad_request", "mode must be timed or untimed")
        now = self._now_ms()
        with self._tx() as c:
            ses = self._session(c, cred_hash)
            no = lambda reason: {"available": False, "reason": reason}
            mark = c.execute("SELECT * FROM marks WHERE mark_id = ?", (ses["mark_id"],)).fetchone() if ses["mark_id"] is not None else None
            if mark is None or mark["status"] != "visible":
                return no("not_visible")
            c.execute("UPDATE rounds SET result = 'expired', used_at = ? WHERE cred_hash = ? AND result IS NULL AND ("
                      "(ready_at IS NULL AND expires_at < ?) OR (ready_at IS NOT NULL AND ? > COALESCE(deadline_at, ready_at + ?)))",
                      (now, cred_hash, now, now, self.config.untimed_hard_s * MS))
            if c.execute("SELECT 1 FROM rounds WHERE cred_hash = ? AND result IS NULL", (cred_hash,)).fetchone():
                return no("round_in_progress")
            used = c.execute("SELECT COUNT(*) FROM rounds WHERE cred_hash = ? AND issued_at >= ? AND COALESCE(result, '') != 'stale'", (cred_hash, now - 24 * 3600 * MS)).fetchone()[0]
            if used >= self.config.rounds_per_day:
                return no("daily_limit")
            snap = self._snapshot(c, mark["section_id"])
            targets = self._targets(c, mark, snap)
            if not targets:
                return no("no_target")
            to_slot = self.rng.choice(targets)
            meta = self._meta(c)
            round_id = secrets.token_urlsafe(16)
            expires = now + self.config.round_expiry_s * MS
            c.execute("INSERT INTO rounds(round_id, cred_hash, mark_id, section_id, from_slot, to_slot, mode, issued_at, expires_at, snapshot_next_slot, snapshot_layout_version, snapshot_mod_seq) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (round_id, cred_hash, mark["mark_id"], mark["section_id"], mark["slot"], to_slot, mode, now, expires, snap["next_slot"], snap["layout_version"], meta["mod_seq"]))
            c.execute("INSERT INTO round_history VALUES (?, ?, ?)", (mark["mark_id"], to_slot, now))
            return {"available": True, "round_id": round_id, "section_id": mark["section_id"], "from_slot": mark["slot"], "to_slot": to_slot, "snapshot": snap, "mode": mode,
                    "window_ms": self.config.window_ms if mode == "timed" else None, "issued_at": _iso(now), "expires_at": _iso(expires), "server_now": _iso(now)}

    def _own_round(self, c: sqlite3.Connection, cred_hash: str, round_id: str) -> sqlite3.Row:
        row = c.execute("SELECT * FROM rounds WHERE round_id = ?", (round_id,)).fetchone()
        if row is None or not secrets.compare_digest(row["cred_hash"], cred_hash):
            raise _not_found()  # unknown and foreign are the same answer
        return row

    def ready_round(self, cred_hash: str, round_id: str) -> dict[str, Any]:
        now = self._now_ms()
        refusal: WallError | None = None
        with self._tx() as c:  # a refusal that changes the round is raised after this block commits: raising inside would roll the change back
            self._session(c, cred_hash)
            r = self._own_round(c, cred_hash, round_id)
            if r["ready_at"] is not None and r["result"] is None:  # idempotent: the same values, the timer never resets
                return {"ready_at": _iso(r["ready_at"]), "deadline_at": _iso(r["deadline_at"]), "server_now": _iso(now)}
            stale = WallError(409, "round_stale", "the wall changed; ask for a new round")
            if r["result"] == "stale":
                refusal = stale
            elif r["result"] == "expired" or (r["result"] is None and now > r["expires_at"]):
                if r["result"] is None:
                    c.execute("UPDATE rounds SET result = 'expired', used_at = ? WHERE round_id = ?", (now, round_id))
                refusal = WallError(409, "round_expired", "this round ran out of time before it started")
            elif r["result"] is not None:
                refusal = WallError(409, "round_used", "this round is over")
            else:
                meta = self._meta(c)
                sec = c.execute("SELECT layout_version FROM sections WHERE section_id = ?", (r["section_id"],)).fetchone()
                mark = c.execute("SELECT status FROM marks WHERE mark_id = ?", (r["mark_id"],)).fetchone()
                if meta["mod_seq"] != r["snapshot_mod_seq"] or sec["layout_version"] != r["snapshot_layout_version"] or mark["status"] != "visible":
                    c.execute("UPDATE rounds SET result = 'stale', used_at = ? WHERE round_id = ?", (now, round_id))  # not consumed, not counted against the daily limit
                    refusal = stale
                else:
                    deadline = now + self.config.window_ms + self.config.allowance_ms if r["mode"] == "timed" else None
                    c.execute("UPDATE rounds SET ready_at = ?, deadline_at = ? WHERE round_id = ?", (now, deadline, round_id))
                    return {"ready_at": _iso(now), "deadline_at": _iso(deadline), "server_now": _iso(now)}
        raise refusal

    def tap_round(self, cred_hash: str, round_id: str, slot: int | None, mark_id: int | None) -> dict[str, Any]:
        now = self._now_ms()
        with self._tx() as c:
            self._session(c, cred_hash)
            r = self._own_round(c, cred_hash, round_id)
            timed = r["mode"] == "timed"
            if (timed and (slot is None or mark_id is not None)) or (not timed and (mark_id is None or slot is not None)):
                raise WallError(400, "bad_request", "a timed round takes a slot, an untimed round takes a mark_id")  # not consumed
            if r["result"] is not None:
                raise WallError(409, "round_used", "this round is over")
            if r["ready_at"] is None:
                raise WallError(409, "not_ready", "start the round first")  # never a win, not consumed
            limit = r["deadline_at"] if timed else r["ready_at"] + self.config.untimed_hard_s * MS
            mark = c.execute("SELECT * FROM marks WHERE mark_id = ?", (r["mark_id"],)).fetchone()
            reply = lambda result, **kw: {"result": result, "hall_offer": False, "elapsed_ms": None, "ranked": False, "server_now": _iso(now), **kw}
            if now > limit or mark["status"] != "visible":
                c.execute("UPDATE rounds SET result = 'expired', used_at = ? WHERE round_id = ?", (now, round_id))
                return reply("expired")
            hit = (slot == r["to_slot"]) if timed else (mark_id == r["mark_id"])
            if not hit:
                c.execute("UPDATE rounds SET result = 'missed', used_at = ? WHERE round_id = ?", (now, round_id))
                return reply("missed")
            elapsed = now - r["ready_at"] if timed else None
            ranked = bool(timed and elapsed is not None and elapsed >= self.config.speed_floor_ms)
            pid = self._roll_period(c, now)
            c.execute("UPDATE rounds SET result = 'found', used_at = ?, elapsed_ms = ? WHERE round_id = ?", (now, elapsed, round_id))
            if mark["found_at"] is None:
                c.execute("UPDATE marks SET found_at = ? WHERE mark_id = ?", (now, mark["mark_id"]))
            c.execute("UPDATE meta SET hall_unlocked_at = COALESCE(hall_unlocked_at, ?), hall_unlocked = 1 WHERE id = 1", (now,))  # any found tap, ranked or not, opted in or not
            if ranked:
                prev = c.execute("SELECT best_elapsed_ms FROM hall_results WHERE period_id = ? AND mark_id = ?", (pid, mark["mark_id"])).fetchone()
                if prev is None:
                    c.execute("INSERT INTO hall_results VALUES (?, ?, ?, ?)", (pid, mark["mark_id"], elapsed, now))
                elif elapsed < prev[0]:
                    c.execute("UPDATE hall_results SET best_elapsed_ms = ?, best_elapsed_at = ? WHERE period_id = ? AND mark_id = ?", (elapsed, now, pid, mark["mark_id"]))
            return reply("found", hall_offer=not mark["hall"], elapsed_ms=elapsed, ranked=ranked)

    def cancel_round(self, cred_hash: str, round_id: str) -> bool:
        now = self._now_ms()
        with self._tx() as c:
            self._session(c, cred_hash)
            r = self._own_round(c, cred_hash, round_id)
            if r["result"] is not None:
                return False
            c.execute("UPDATE rounds SET result = 'cancelled', used_at = ? WHERE round_id = ?", (now, round_id))
            return True

    # ------------------------------------------------------------ owner tools (wall_admin CLI; there is no web route for any of these)

    def list_marks(self, status: str, limit: int = 200) -> list[dict[str, Any]]:
        with self._read() as c:
            rows = c.execute("SELECT * FROM marks WHERE status = ? ORDER BY created_at, mark_id LIMIT ?", (status, limit)).fetchall()
        return [{"mark_id": r["mark_id"], "name": r["display_name"], "status": r["status"], "created_at": _iso(r["created_at"]), "section_id": r["section_id"], "slot": r["slot"]} for r in rows]

    def _mark_for_admin(self, c: sqlite3.Connection, mark_id: int) -> sqlite3.Row:
        row = c.execute("SELECT * FROM marks WHERE mark_id = ?", (mark_id,)).fetchone()
        if row is None:
            raise WallError(404, "not_found", f"no mark {mark_id}")
        return row

    def approve(self, mark_id: int) -> dict[str, Any]:
        now = self._now_ms()
        with self._tx() as c:
            row = self._mark_for_admin(c, mark_id)
            if row["status"] == "visible":
                return self._mark_summary(row)
            if row["status"] != "pending":
                raise WallError(409, "bad_request", f"a {row['status']} mark cannot be approved (use restore for a hidden one)")
            self._make_visible(c, mark_id, now)
            return self._mark_summary(self._mark_for_admin(c, mark_id))

    def reject(self, mark_id: int, reason: str = "") -> None:
        with self._tx() as c:
            row = self._mark_for_admin(c, mark_id)
            if row["status"] != "pending":
                raise WallError(409, "bad_request", "only a pending mark can be rejected (use remove)")
            c.execute("UPDATE marks SET reason = ? WHERE mark_id = ?", (reason[:200], mark_id))
            self._remove_mark(c, mark_id)

    def hide(self, mark_id: int, reason: str = "") -> None:
        with self._tx() as c:
            row = self._mark_for_admin(c, mark_id)
            if row["status"] == "hidden":
                return
            if row["status"] != "visible":
                raise WallError(409, "bad_request", "only a visible mark can be hidden")
            c.execute("UPDATE marks SET status = 'hidden', reason = ? WHERE mark_id = ?", (reason[:200], mark_id))  # keeps its slot: a restore puts it back
            c.execute("UPDATE meta SET visible_count = visible_count - 1, mod_seq = mod_seq + 1 WHERE id = 1")
            c.execute("UPDATE rounds SET result = 'cancelled', used_at = ? WHERE mark_id = ? AND result IS NULL", (self._now_ms(), mark_id))

    def restore(self, mark_id: int) -> None:
        with self._tx() as c:
            row = self._mark_for_admin(c, mark_id)
            if row["status"] == "visible":
                return
            if row["status"] != "hidden":
                raise WallError(409, "bad_request", "only a hidden mark can be restored")
            c.execute("UPDATE marks SET status = 'visible', reason = '' WHERE mark_id = ?", (mark_id,))
            c.execute("UPDATE meta SET visible_count = visible_count + 1, mod_seq = mod_seq + 1 WHERE id = 1")

    def remove(self, mark_id: int, reason: str = "") -> None:
        with self._tx() as c:
            self._mark_for_admin(c, mark_id)
            c.execute("UPDATE marks SET reason = ? WHERE mark_id = ?", (reason[:200], mark_id))
            self._remove_mark(c, mark_id)

    def set_accepting(self, accepting: bool) -> None:
        with self._tx() as c:
            c.execute("UPDATE meta SET accepting = ? WHERE id = 1", (1 if accepting else 0,))

    def new_layout(self, version: int) -> None:
        """New sections use this layout version from now on; existing sections keep theirs. Open rounds become stale."""
        if version not in layout.LAYOUTS:
            raise WallError(400, "bad_request", f"layout {version} is not defined in this build")
        with self._tx() as c:
            c.execute("UPDATE meta SET layout_version_current = ?, mod_seq = mod_seq + 1 WHERE id = 1", (version,))

    def export_public(self) -> list[dict[str, Any]]:
        """Only what is already public: visible marks as {section_id, slot, name}. No hashes, sessions, hidden or removed names."""
        with self._read() as c:
            rows = c.execute("SELECT section_id, slot, display_name FROM marks WHERE status = 'visible' ORDER BY section_id, slot").fetchall()
        return [{"section_id": r["section_id"], "slot": r["slot"], "name": r["display_name"]} for r in rows]

    def stats(self) -> dict[str, Any]:
        with self._read() as c:
            m = self._meta(c)
            counts = {r[0]: r[1] for r in c.execute("SELECT status, COUNT(*) FROM marks GROUP BY status")}
            return {"schema_version": m["schema_version"], "visible_count": m["visible_count"], "accepted_lifetime": m["accepted_lifetime"], "marks_by_status": counts,
                    "sections": c.execute("SELECT COUNT(*) FROM sections").fetchone()[0], "hall_unlocked": bool(m["hall_unlocked"]), "accepting": bool(m["accepting"]),
                    "unclaimed_sessions": c.execute("SELECT COUNT(*) FROM wall_sessions WHERE mark_id IS NULL").fetchone()[0]}

    def backup(self, dest: Path) -> Path:
        """SQLite's online backup API into a new file (never a raw copy of a live file). The result is integrity-checked before this returns."""
        dest = Path(dest)
        if dest.exists():
            raise FileExistsError(f"{dest} already exists")
        dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        src = self._connect()
        try:
            out = sqlite3.connect(dest)
            try:
                src.backup(out)
                if out.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("the backup failed its integrity check")
            finally:
                out.close()
        finally:
            src.close()
        os.chmod(dest, 0o600)
        return dest

    def sweep(self) -> dict[str, int]:
        """Housekeeping, safe to run any time: expired idempotency keys, long-unclaimed sessions, old rounds, ended periods. Never touches a mark."""
        now = self._now_ms()
        with self._tx() as c:
            self._roll_period(c, now)
            subs = c.execute("DELETE FROM submissions WHERE created_at < ?", (now - self.config.submission_ttl_hours * 3600 * MS,)).rowcount
            ses = c.execute("DELETE FROM wall_sessions WHERE mark_id IS NULL AND last_seen < ?", (now - self.config.unclaimed_ttl_days * 86400 * MS,)).rowcount
            rnd = c.execute("DELETE FROM rounds WHERE issued_at < ?", (now - self.config.round_retention_days * 86400 * MS,)).rowcount
            c.execute("DELETE FROM round_history WHERE issued_at < ?", (now - self.config.round_retention_days * 86400 * MS,))
            c.execute("UPDATE meta SET last_sweep = ? WHERE id = 1", (now,))
        return {"submissions": subs, "unclaimed_sessions": ses, "rounds": rnd}

    def maybe_sweep(self) -> None:
        """Called opportunistically from writes: at most once an hour."""
        now = self._now_ms()
        with self._read() as c:
            last = self._meta(c)["last_sweep"]
        if now - last > 3600 * MS:
            self.sweep()


UPGRADERS: dict[int, Callable[[sqlite3.Connection], None]] = {}  # step n upgrades schema n to n+1; empty until a second schema exists
