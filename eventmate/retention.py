"""Deleting a plan, and expiring old logs (docs/RETENTION.md).

Deleting a plan removes everything the plan owns: its versions, ledger, vendor messages, standing permission, jobs, ideas, digests, shortlist
and checklist. Each file is addressed by its exact path (``<store>/<channel>-<plan_id>.jsonl``), never by a pattern, so plan "b" can never touch
the files of plan "a-b". What is *not* per plan is kept on purpose: the do-not-contact lists (forgetting them would let a refused vendor be
messaged again), discovered businesses, and vendor price observations (market facts that carry no plan id).
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

PLAN_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
LOG_FILE = re.compile(r"(?:decisions|tool_calls|llm_traces|validation_failures|assistant_[a-z]+)-[A-Za-z0-9_-]{1,64}\.jsonl")
DEFAULT_LOG_RETENTION_DAYS = 90

# (directory under the plan root, JsonlLog channel): the files keyed by plan id.
PER_PLAN_JSONL = (
    ("outreach", "outreach"), ("inbound", "inbound"), ("mandates", "mandate"), ("ledger", "ledger"), ("leads", "lead"),
    ("ideas", "ideaset"), ("ideas", "ideadecision"), ("ideas", "plannote"), ("digests", "digest"), ("shortlist", "shortlist"), ("community", "checklist"),
)


class PlanBusy(RuntimeError):
    """A message for this plan is being sent right now; deleting it could lose the outcome."""


def _unlink(path: Path) -> bool:
    try:
        os.unlink(path)  # a symlink is removed, never followed
        return True
    except FileNotFoundError:
        return False


def wipe(path: Path) -> bool:
    """Overwrite a small file with zeros and unlink it; a symlink is only unlinked. Returns whether anything was there."""
    try:
        if path.is_symlink():
            path.unlink()
            return True
        size = path.stat().st_size
        with open(path, "r+b") as fh:
            fh.write(b"\x00" * size)
            fh.flush()
            os.fsync(fh.fileno())
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def delete_plan_files(root: Path, plan_id: str) -> int:
    """Remove the per-plan JSONL files under ``root``. Returns how many existed."""
    if not PLAN_ID.fullmatch(plan_id):
        raise ValueError("invalid plan id")
    gone = sum(_unlink(Path(root) / sub / f"{channel}-{plan_id}.jsonl") for sub, channel in PER_PLAN_JSONL)
    return gone + wipe(Path(root) / "guests" / f"guests-{plan_id}.json")  # the imported guest list holds names: overwritten, then removed


def purge_old_logs(log_dir: Path, max_age_days: Optional[int] = None, now: Optional[float] = None) -> List[str]:
    """Delete run logs not modified for ``max_age_days`` (default ``EVENTMATE_LOG_RETENTION_DAYS``, else 90). Returns the names removed."""
    if max_age_days is None:
        max_age_days = int(os.environ.get("EVENTMATE_LOG_RETENTION_DAYS", DEFAULT_LOG_RETENTION_DAYS))
    if max_age_days < 1:
        raise ValueError("retention must be at least 1 day")
    cutoff = (time.time() if now is None else now) - max_age_days * 86400
    removed: List[str] = []
    for p in sorted(Path(log_dir).iterdir()):
        if LOG_FILE.fullmatch(p.name) and not p.is_symlink() and p.is_file() and p.stat().st_mtime < cutoff:
            _unlink(p)
            removed.append(p.name)
    return removed


def write_tombstone(path: Path, kind: str, ident: str, now: Optional[datetime] = None) -> None:
    """One line saying that something was deleted and when: ``{kind, id, deleted_at}`` and nothing else, so the deletion is provable and no content survives in it."""
    line = json.dumps({"kind": kind, "id": ident, "deleted_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")}, sort_keys=True) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def scrub_plan_from_logs(log_dir: Path, plan_id: str) -> int:
    """Remove every log line that mentions a deleted plan (its questions, answers, decisions and traces). The other lines stay, in order. Each file is
    rewritten to a temporary file and moved into place, owner-only. A line appended by another thread during the rewrite of that one file could be lost;
    the lines of a plan being deleted are the ones that matter, and the plan is already gone. Returns how many lines were removed."""
    if not PLAN_ID.fullmatch(plan_id):
        raise ValueError("invalid plan id")
    needle, removed = plan_id.encode("utf-8"), 0
    for path in sorted(Path(log_dir).iterdir()):
        if not LOG_FILE.fullmatch(path.name) or path.is_symlink() or not path.is_file():
            continue
        data = path.read_bytes()
        if needle not in data:
            continue
        kept = [line for line in data.splitlines(keepends=True) if needle not in line]
        removed += len(data.splitlines()) - len(kept)
        tmp = path.with_name(f".scrub-{os.getpid()}-{path.name}")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, b"".join(kept))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
    return removed


def report(versions: int, files: int, jobs: int) -> Dict[str, int]:
    return {"plan_versions": versions, "message_and_ledger_files": files, "jobs": jobs}
