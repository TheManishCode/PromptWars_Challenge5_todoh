"""Is what is on disk still readable and consistent? (v6 prompt 1.3 and 1.4)

``scan`` reads every store without changing anything and reports, in words, which file and which line cannot be read, which plan's history has a version
missing, altered or rolled back, and which record files belong to a plan that is not there. ``quarantine`` and ``apply_repair`` are the only way a damaged file
is ever replaced, and neither does it silently.

Stores come in two kinds. **Authoritative** ones hold money and send state: the ledger, vendor messages and their replies, the standing permissions and
contacts, the do-not-contact lists and the job database. While any of them is damaged, nothing is sent and nothing is approved in that workspace (``guard``):
a send cap, a "this vendor was already asked" or "this was refused" that cannot be read must not be guessed. **Derived** ones (traces, digests, ideas,
shortlists, quote observations, notification logs) can be reset after quarantine without losing anything the application depends on for safety.

The normal read path still raises on a corrupt line (it fails closed); nothing here relaxes that. A repaired file is a *candidate*: it is never swapped in
until the operator types the number of lines that were set aside, and the original is kept beside it, read-only.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import shutil
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pydantic import ValidationError

from eventmate import migrations
from eventmate.plan_store import PlanStore

AUTHORITATIVE = migrations.AUTHORITATIVE
PLAN_KEYED = ("outreach", "inbound", "mandate", "ledger", "lead", "ideaset", "ideadecision", "plannote", "digest", "shortlist", "checklist")
JSONL_NAME = re.compile(r"([a-z_]+)-([A-Za-z0-9_-]{1,64})\.jsonl")


class StoreDamaged(ValueError):
    """Sending and approving are stopped for this workspace because an authoritative file cannot be read. The message says what to run."""


@dataclass
class Damage:
    path: str  # relative to the store root
    kind: str  # "authoritative" or "derived"
    line: Optional[int]
    reason: str

    def __str__(self) -> str:
        return f"{self.path}" + (f" line {self.line}" if self.line else "") + f": {self.reason}"


@dataclass
class Report:
    damaged: List[Damage] = field(default_factory=list)
    plan_problems: List[str] = field(default_factory=list)
    orphans: List[str] = field(default_factory=list)
    files_checked: int = 0

    @property
    def ok(self) -> bool:
        return not (self.damaged or self.plan_problems or self.orphans)

    @property
    def authoritative_damage(self) -> List[Damage]:
        return [d for d in self.damaged if d.kind == "authoritative"]

    def lines(self) -> List[str]:
        out = [f"damaged ({d.kind}): {d}" for d in self.damaged] + [f"plan: {p}" for p in self.plan_problems] + [f"orphan: {o}" for o in self.orphans]
        return out or [f"ok: {self.files_checked} file(s) checked"]


def _models() -> Dict[str, type]:
    """channel name -> record model, from every store the application defines."""
    importlib.import_module("eventmate.service")  # importing it loads every store class
    from eventmate.jsonl import JsonlLog

    found: Dict[str, type] = {}
    todo = list(JsonlLog.__subclasses__())
    while todo:
        cls = todo.pop()
        todo.extend(cls.__subclasses__())
        channel, model = getattr(cls, "channel", None), getattr(cls, "model", None)
        if isinstance(channel, str) and model is not None and channel != "log":
            found[channel] = model
    return found


def check_jsonl(path: Path, channel: str, model) -> List[Tuple[Optional[int], str]]:
    """Every unreadable line of one file as (line number, reason). The content of a line is never part of a reason."""
    problems: List[Tuple[Optional[int], str]] = []
    try:
        raw = path.read_bytes()
    except OSError:
        return [(None, "cannot be read")]
    lines = raw.split(b"\n")
    torn_tail = bool(raw) and not raw.endswith(b"\n")
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            model.model_validate(migrations.parse_line(channel, line, path.name, n))
        except migrations.SchemaTooNew:
            problems.append((n, "written by a newer version of the application"))
        except migrations.StoreCorrupt as exc:
            problems.append((n, "torn last line (the write was cut off)" if torn_tail and n == len(lines) else exc.reason))
        except ValidationError as exc:
            problems.append((n, "does not match the record format: " + "; ".join(".".join(map(str, e["loc"])) or "record" for e in exc.errors()[:3])))
    return problems


def jsonl_files(root: Path, log_dir: Optional[Path] = None) -> List[Path]:
    files = [p for p in sorted(Path(root).rglob("*.jsonl")) if not p.is_symlink() and JSONL_NAME.fullmatch(p.name)]
    if log_dir and Path(log_dir).is_dir():
        files += [p for p in sorted(Path(log_dir).glob("*.jsonl")) if not p.is_symlink() and JSONL_NAME.fullmatch(p.name)]
    return files


def scan(root: Path, log_dir: Optional[Path] = None) -> Report:
    """Read every store under ``root`` (and the logs in ``log_dir``). Changes nothing."""
    root, report, models = Path(root), Report(), _models()
    for path in jsonl_files(root, log_dir):
        m = JSONL_NAME.fullmatch(path.name)
        channel = m.group(1)
        model = models.get(channel)
        if model is None:
            continue  # a file this application does not write (a quarantined copy, a candidate): not a store
        report.files_checked += 1
        kind = "authoritative" if channel in AUTHORITATIVE else "derived"
        rel = str(path.relative_to(root)) if root in path.parents else path.name
        report.damaged += [Damage(rel, kind, n, why) for n, why in check_jsonl(path, channel, model)]
    jobs = root / "jobs.db"
    if jobs.exists():
        report.files_checked += 1
        try:
            db = sqlite3.connect(f"file:{jobs}?mode=ro", uri=True)
            try:
                result = db.execute("PRAGMA integrity_check").fetchone()[0]
                if result != "ok":
                    report.damaged.append(Damage("jobs.db", "authoritative", None, "the database failed its own integrity check"))
                else:
                    from eventmate.followthrough.jobs import _load_job

                    for (data,) in db.execute("SELECT data FROM jobs"):
                        try:
                            _load_job(data)
                        except (ValueError, ValidationError) as exc:
                            report.damaged.append(Damage("jobs.db", "authoritative", None, "a job row cannot be read: " + (exc.reason if isinstance(exc, migrations.StoreCorrupt) else "does not match the record format")))
            finally:
                db.close()
        except sqlite3.Error:
            report.damaged.append(Damage("jobs.db", "authoritative", None, "cannot be opened as a database"))
    store = PlanStore(root)
    plans = store.list_plans()
    for plan_id in plans:
        report.files_checked += len(store._version_numbers(plan_id))
        try:
            store.versions(plan_id)
        except Exception as exc:  # PlanIntegrityError, a schema from the future, an unreadable file: all are "this plan cannot be trusted"
            report.plan_problems.append(f"plan {plan_id}: {str(exc)[:200]}")
        report.plan_problems += store.check_chain(plan_id)
    known = set(plans)
    for path in jsonl_files(root):
        m = JSONL_NAME.fullmatch(path.name)
        if m.group(1) in PLAN_KEYED and m.group(2) not in known:
            report.orphans.append(f"{path.relative_to(root)} belongs to plan {m.group(2)}, which has no versions")
    guests = root / "guests"
    if guests.is_dir():
        for path in sorted(guests.glob("guests-*.json")):
            if path.stem[len("guests-"):] not in known:
                report.orphans.append(f"{path.relative_to(root)} belongs to plan {path.stem[len('guests-'):]}, which has no versions")
    if jobs.exists():
        from eventmate.followthrough.jobs import JobStore

        try:
            for job in JobStore(root).list():
                if job.plan_id not in known:
                    report.orphans.append(f"a job refers to plan {job.plan_id}, which has no versions")
        except Exception:
            pass  # already reported as damage above
    return report


# ---------------------------------------------------------------------- the guard


class Guard:
    """Remembers what ``scan`` found for the authoritative stores and re-reads only the files that changed (by size and time), so asking before every send is cheap."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._signature: Optional[tuple] = None
        self._damage: List[Damage] = []
        self._lock = threading.Lock()

    def _files(self) -> List[Path]:
        return [p for p in jsonl_files(self.root) if JSONL_NAME.fullmatch(p.name).group(1) in AUTHORITATIVE] + ([self.root / "jobs.db"] if (self.root / "jobs.db").exists() else [])

    def damage(self) -> List[Damage]:
        with self._lock:
            files = self._files()
            signature = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in files)
            if signature != self._signature:
                models, found = _models(), []
                for p in files:
                    if p.name == "jobs.db":
                        r = scan_jobs_only(self.root)
                        found += r
                        continue
                    channel = JSONL_NAME.fullmatch(p.name).group(1)
                    found += [Damage(str(p.relative_to(self.root)), "authoritative", n, why) for n, why in check_jsonl(p, channel, models[channel])] if channel in models else []
                self._damage, self._signature = found, signature
            return list(self._damage)

    def blocker(self) -> Optional[str]:
        """Why nothing may be sent or approved in this workspace right now, or None."""
        found = self.damage()
        if not found:
            return None
        names = sorted({d.path for d in found})[:3]
        return ("a stored record file is damaged (" + ", ".join(names) + "), so nothing can be sent or approved until the owner repairs it: run `python -m eventmate verify`")


def scan_jobs_only(root: Path) -> List[Damage]:
    report = Report()
    jobs = Path(root) / "jobs.db"
    try:
        db = sqlite3.connect(f"file:{jobs}?mode=ro", uri=True)
        try:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                return [Damage("jobs.db", "authoritative", None, "the database failed its own integrity check")]
        finally:
            db.close()
    except sqlite3.Error:
        return [Damage("jobs.db", "authoritative", None, "cannot be opened as a database")]
    return report.damaged


# ---------------------------------------------------------------------- repair (never silent, never in place)


@dataclass
class Quarantine:
    source: Path
    damaged_copy: Path
    candidate: Path
    kept: int
    dropped: List[Tuple[int, str]]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def quarantine(path: Path, now: Optional[datetime] = None) -> Quarantine:
    """Copy a damaged file aside (read-only), and write the lines that can be read to a separate candidate file. The live file is not touched."""
    path = Path(path)
    m = JSONL_NAME.fullmatch(path.name)
    if m is None or path.is_symlink() or not path.is_file():
        raise ValueError("not a store file")
    channel, model = m.group(1), _models().get(m.group(1))
    if model is None:
        raise ValueError("not a store file this application writes")
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    raw = path.read_bytes()
    problems = dict((n, why) for n, why in check_jsonl(path, channel, model) if n is not None)  # the same reasons verify gives
    dropped, kept = [], []
    for n, line in enumerate(raw.split(b"\n"), 1):
        if not line.strip():
            continue
        if n in problems:
            dropped.append((n, problems[n]))
        else:
            kept.append(line)
    damaged = path.with_name(f"{path.name}.damaged-{stamp}")
    candidate = path.with_name(f"{path.name}.repaired-{stamp}")
    shutil.copy2(path, damaged)
    os.chmod(damaged, 0o444)
    fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(b"".join(line + b"\n" for line in kept))
    meta = {"source": path.name, "original_sha256": _sha(raw), "candidate_sha256": _sha(candidate.read_bytes()), "kept": len(kept), "dropped": [{"line": n, "reason": r} for n, r in dropped]}
    mfd = os.open(str(candidate) + ".json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(mfd, "w") as fh:
        json.dump(meta, fh, indent=2)
    return Quarantine(path, damaged, candidate, len(kept), dropped)


def apply_repair(candidate: Path, confirm_dropped: int) -> Path:
    """Replace the live file with the candidate. Needs the exact number of lines that were set aside (the operator reads it from the report and types it),
    and refuses if the live file has changed since it was quarantined: a repair of an old picture would lose what was written since."""
    candidate = Path(candidate)
    meta_path = Path(str(candidate) + ".json")
    try:
        meta = json.loads(meta_path.read_text())
        live = candidate.with_name(meta["source"])
    except (OSError, ValueError, KeyError):
        raise ValueError("this is not a repair candidate (its record file is missing)") from None
    if candidate.name.rsplit(".repaired-", 1)[0] != meta["source"]:
        raise ValueError("the candidate does not belong to that record file")
    if _sha(candidate.read_bytes()) != meta["candidate_sha256"]:
        raise ValueError("the candidate was changed after it was made; quarantine again")
    if not live.exists() or _sha(live.read_bytes()) != meta["original_sha256"]:
        raise ValueError("the live file changed after it was quarantined; quarantine it again")
    if not isinstance(confirm_dropped, int) or isinstance(confirm_dropped, bool) or confirm_dropped != len(meta["dropped"]):
        raise ValueError(f"this drops {len(meta['dropped'])} line(s); confirm with that number to proceed")
    os.chmod(candidate, 0o600)
    os.replace(candidate, live)
    meta_path.rename(str(meta_path) + ".applied")
    return live


def reset_derived(path: Path, now: Optional[datetime] = None) -> Path:
    """Start a derived store afresh: the file is kept aside (read-only) and the live name is freed. Refused for an authoritative store."""
    m = JSONL_NAME.fullmatch(Path(path).name)
    if m is None or m.group(1) in AUTHORITATIVE:
        raise ValueError("only a derived store can be reset; repair an authoritative one with quarantine and apply")
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    aside = Path(path).with_name(f"{Path(path).name}.damaged-{stamp}")
    os.replace(path, aside)
    os.chmod(aside, 0o444)
    return aside
