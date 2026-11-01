"""Shared append-only JSONL writer.

Every log channel (decisions, tool calls, LLM traces, validation failures)
goes through this class so they all get the same guarantees: entries are
validated Pydantic models before anything touches disk, files are created
owner-only (0600), symlinks are refused, and writes are serialised.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import re
import threading
import uuid
from datetime import datetime, UTC
from pathlib import Path
from typing import Generic, TypeVar

from pydantic import BaseModel, ValidationError

from eventmate import migrations

M = TypeVar("M", bound=BaseModel)

DEFAULT_LOG_DIR = Path(__file__).resolve().parent / "logs"
ROTATED_SUFFIX = r"\.\d{8}T\d{6}Z-\d{1,4}"  # <name>.jsonl.<UTC time>-<n>: a log that was rotated out
ROTATED = re.compile(ROTATED_SUFFIX + "$")
DEFAULT_MAX_BYTES = 20_000_000
DEFAULT_KEEP = 30
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _int_env(name: str, default: int, lo: int) -> int:
    try:
        return max(lo, int(os.environ.get(name, default)))
    except ValueError:
        return default


class JsonlLog(Generic[M]):
    channel = "log"
    model: type[M]
    rotates = False  # only the operational logs (decisions, tool calls, LLM traces, validation failures, assistant traces) rotate; money and send state never does

    def __init__(self, log_dir: Path | None = None, run_id: str | None = None):
        run_id = run_id if run_id is not None else uuid.uuid4().hex
        if not RUN_ID_PATTERN.fullmatch(run_id):
            raise ValueError("run_id must match [A-Za-z0-9_-]{1,64}")
        self.run_id = run_id
        self.log_dir = Path(log_dir) if log_dir is not None else DEFAULT_LOG_DIR
        self.log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.log_dir / f"{self.channel}-{run_id}.jsonl"
        self.entry_ids: list[str] = []
        self._lock = threading.Lock()

    def _rotate_if_needed(self) -> None:
        """Start a new file when this one is over ``EVENTMATE_LOG_MAX_BYTES`` (default 20 MB) or was last written on an earlier UTC day. The old one is renamed
        ``<name>.jsonl.<UTC time>-<n>`` (atomic, so another process appending at the same moment just starts the new file), and only the newest
        ``EVENTMATE_LOG_KEEP`` (default 30) rotated files of this log are kept. Never applied to a store that holds money or send state."""
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return
        if st.st_size == 0:
            return
        if st.st_size < _int_env("EVENTMATE_LOG_MAX_BYTES", DEFAULT_MAX_BYTES, 1000) and datetime.fromtimestamp(st.st_mtime, UTC).date() >= datetime.now(UTC).date():
            return
        stamp, n = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"), 1
        while os.path.exists(f"{self.path}.{stamp}-{n}"):
            n += 1
        try:
            os.rename(self.path, f"{self.path}.{stamp}-{n}")
        except FileNotFoundError:
            return  # another process rotated it first
        keep = _int_env("EVENTMATE_LOG_KEEP", DEFAULT_KEEP, 1)
        rotated = sorted(p for p in self.log_dir.glob(self.path.name + ".*") if ROTATED.search(p.name) and not p.is_symlink())
        for old in rotated[:-keep]:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(old)

    def _append(self, entry: M, entry_id: str) -> M:
        line = (migrations.stamp(self.channel, entry.model_dump_json()) + "\n").encode("utf-8")  # one line, one write, schema_version first
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        with self._lock:
            if self.rotates:
                self._rotate_if_needed()
            fd = os.open(self.path, flags, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)  # released when the descriptor closes: no other appender, in any process or instance, can be half-way through a line while the end is checked
                tail = self._tail_byte()
                if tail not in (None, b"\n"):  # the file ends in a cut-off line (a crash in the middle of an append)
                    if self.channel in migrations.AUTHORITATIVE:
                        raise migrations.StoreCorrupt(self.path.name, None, "the file ends in a cut-off line; it must be repaired (python -m eventmate verify) before anything is added to it")
                    line = b"\n" + line  # an operational log starts a fresh line instead of gluing this record onto the fragment
                view = memoryview(line)
                while view:
                    view = view[os.write(fd, view):]
                os.fsync(fd)
            finally:
                os.close(fd)
            self.entry_ids.append(entry_id)
        return entry

    def _tail_byte(self) -> bytes | None:
        try:
            with open(self.path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                if fh.tell() == 0:
                    return None
                fh.seek(-1, os.SEEK_END)
                return fh.read(1)
        except FileNotFoundError:
            return None

    def read(self) -> list[M]:
        """Load every entry, each upgraded in memory to the current schema. A corrupt line raises ``StoreCorrupt`` naming the file and line rather than being skipped:
        the application fails closed, and ``python -m eventmate verify`` says exactly what is damaged."""
        if not self.path.exists():
            return []
        out: list[M] = []
        with open(self.path, "r", encoding="utf-8") as fh:
            for number, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                doc = migrations.parse_line(self.channel, line, self.path.name, number)
                try:
                    out.append(self.model.model_validate(doc))
                except ValidationError as exc:
                    exc.add_note(f"in {self.path.name} line {number}")  # the original error stays (callers and tests rely on its type); the note says where
                    raise
        return out


class InterProcessLock:
    """An exclusive lock for read-check-write steps over a JSONL store: a thread lock plus a file lock, so two processes cannot interleave.
    Re-entrant within one thread."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._thread = threading.RLock()
        self._depth = 0

    @contextlib.contextmanager
    def __call__(self):
        with self._thread:
            if self._depth:
                self._depth += 1
                try:
                    yield
                finally:
                    self._depth -= 1
                return
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                self._depth = 1
                try:
                    yield
                finally:
                    self._depth = 0
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
