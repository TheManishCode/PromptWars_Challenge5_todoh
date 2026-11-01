"""Backup and restore of a shared-mode data directory (docs/RUNBOOK.md).

A backup is a directory: the workspace database (copied with SQLite's own online backup, so it is consistent while the server runs), every workspace's
files (plan versions are written create-only and logs are append-only, so a copy taken while the server runs is a consistent earlier state), the wall's
database if there is one, the deletion log, and a manifest with the SHA-256 of every file. Nothing in a backup is encrypted: protect it like the data.

Restore refuses to write into a directory that already has anything in it, verifies the manifest first, and then applies the **deletion log**: a plan or
a workspace deleted after the backup was taken is removed again, so restoring an old backup cannot bring deleted data back. The log that matters is the
server's current ``tombstones.jsonl``; ship a copy of it somewhere else as it grows (a backup alone is only as new as the day it was made).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

SCHEMA = 1
DB_FILES = ("workspaces.db", "wall_data/wall.sqlite3")


class BackupError(ValueError):
    pass


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _copy_db(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    source, target = sqlite3.connect(f"file:{src}?mode=ro", uri=True), sqlite3.connect(dst)
    try:
        source.backup(target)  # a consistent snapshot even if the server is writing
        target.execute("PRAGMA journal_mode=DELETE").fetchone()  # the copy is one self-contained file, not a WAL database with sidecars
    finally:
        target.close()
        source.close()
    dst.chmod(0o600)


def _tree(root: Path):
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            continue  # a link in a data directory is never followed into a backup
        yield path


def make_backup(data_dir: Path, destination: Path, now: Optional[datetime] = None) -> Path:
    data_dir, destination = Path(data_dir).resolve(), Path(destination).resolve()
    if not data_dir.is_dir():
        raise BackupError("the data directory does not exist")
    if destination == data_dir or data_dir in destination.parents:
        raise BackupError("a backup must be written outside the data directory")
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    out = destination / f"todoh-backup-{stamp}"
    out.mkdir(parents=True, exist_ok=False, mode=0o700)
    for rel in DB_FILES:
        if (data_dir / rel).is_file():
            _copy_db(data_dir / rel, out / rel)
    workspaces = data_dir / "workspaces"
    if workspaces.is_dir():
        for path in _tree(workspaces):
            target = out / path.relative_to(data_dir)
            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
            elif path.name.endswith(("-wal", "-shm", "-journal")):
                continue  # a SQLite side file is part of its database and is never copied on its own: the database is copied below with SQLite's own backup
            elif path.name.endswith(".db"):
                _copy_db(path, target)  # jobs.db and any other per-workspace database: a raw copy of a live WAL database can mix two moments and be torn (F-017)
            elif path.is_file() and path.suffix == ".db":
                _copy_db(path, target)  # SQLite's own online backup: a byte copy of a database that is being written (WAL mode) can be torn
            elif path.is_file() and path.name.endswith(("-wal", "-shm")):
                continue  # folded into the database by the online backup above
            elif path.is_file():
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copy2(path, target)
    if (data_dir / "tombstones.jsonl").is_file():
        shutil.copy2(data_dir / "tombstones.jsonl", out / "tombstones.jsonl")
    files = [{"path": str(p.relative_to(out)), "sha256": _sha(p), "bytes": p.stat().st_size} for p in sorted(out.rglob("*")) if p.is_file()]
    (out / "MANIFEST.json").write_text(json.dumps({"schema_version": SCHEMA, "created_at": stamp, "files": files, "workspaces": len(list(workspaces.glob("ws-*"))) if workspaces.is_dir() else 0,
                                                    "note": "plaintext; protect it like the data. Restore with the server's current tombstones.jsonl so deleted data stays deleted."}, indent=2))
    (out / "MANIFEST.json").chmod(0o600)
    return out


def verify_backup(backup: Path) -> List[str]:
    """Problems found, or an empty list: every file named in the manifest exists with the recorded hash, and nothing else is in the backup."""
    backup = Path(backup)
    try:
        manifest = json.loads((backup / "MANIFEST.json").read_text())
        listed: Dict[str, str] = {f["path"]: f["sha256"] for f in manifest["files"]}
        if manifest["schema_version"] != SCHEMA:
            return [f"unsupported backup version {manifest['schema_version']}"]
    except (OSError, ValueError, KeyError, TypeError):
        return ["the manifest is missing or unreadable"]
    root = backup.resolve()
    outside = [rel for rel in listed if Path(rel).is_absolute() or ".." in Path(rel).parts or root not in (backup / rel).resolve().parents]
    if outside:
        return [f"the manifest names a file outside the backup: {outside[0][:80]}"]  # nothing outside the backup is ever read or hashed on the manifest's say-so
    problems = [f"a link in the backup: {p.relative_to(backup)}" for p in backup.rglob("*") if p.is_symlink()]  # a backup is files and folders only; restoring a link would copy what it points at
    problems += [f"missing: {rel}" for rel in listed if not (backup / rel).is_file()]
    problems += [f"changed: {rel}" for rel, digest in listed.items() if (backup / rel).is_file() and _sha(backup / rel) != digest]
    present = {str(p.relative_to(backup)) for p in backup.rglob("*") if p.is_file() and p.name != "MANIFEST.json"}
    problems += [f"not in the manifest: {rel}" for rel in sorted(present - set(listed))]
    return problems


def _read_tombstones(path: Optional[Path]) -> List[dict]:
    out = []
    if path and Path(path).is_file():
        for line in Path(path).read_text().splitlines():
            try:
                doc = json.loads(line)
                if isinstance(doc, dict) and doc.get("kind") in ("plan", "workspace") and isinstance(doc.get("id"), str):
                    out.append(doc)
            except ValueError:
                continue
    return out


def restore_backup(backup: Path, into: Path, deletion_log: Optional[Path] = None) -> Dict[str, object]:
    from eventmate.followthrough.jobs import JobStore
    from eventmate.plan_store import PlanStore
    from eventmate.retention import delete_plan_files, scrub_plan_from_logs, wipe, write_tombstone
    from eventmate.workspaces import ID, WorkspaceDirectory

    backup, into = Path(backup), Path(into)
    problems = verify_backup(backup)
    if problems:
        raise BackupError("the backup does not verify: " + "; ".join(problems[:5]))
    if into.exists() and any(into.iterdir()):
        raise BackupError("restore only into an empty directory")
    into.mkdir(parents=True, exist_ok=True, mode=0o700)
    for path in sorted(backup.rglob("*")):
        if path.name == "MANIFEST.json":
            continue
        target = into / path.relative_to(backup)
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True, mode=0o700)
        else:
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copy2(path, target)
    applied: List[dict] = []
    seen = set()
    for doc in _read_tombstones(backup / "tombstones.jsonl") + _read_tombstones(deletion_log):
        key = (doc["kind"], doc["id"], doc.get("workspace"))
        if key in seen:
            continue
        seen.add(key)
        if doc["kind"] == "workspace" and ID.fullmatch(doc["id"]):
            home = into / "workspaces" / doc["id"]
            if home.exists():
                for path in sorted(home.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                    path.chmod(0o700 if path.is_dir() else 0o600)
                    path.rmdir() if path.is_dir() else wipe(path)
                home.rmdir()
            if (into / "workspaces.db").exists():
                WorkspaceDirectory(into).forget(doc["id"])
            applied.append(doc)
        elif doc["kind"] == "plan" and ID.fullmatch(doc.get("workspace", "")):
            plans = into / "workspaces" / doc["workspace"] / "plans"
            if plans.is_dir():
                if (plans / "jobs.db").exists():
                    JobStore(plans).delete_plan(doc["id"])
                delete_plan_files(plans, doc["id"])
                PlanStore(plans).delete(doc["id"])
                scrub_plan_from_logs(plans.parent / "logs", doc["id"])
            applied.append(doc)
    have = {(d["kind"], d["id"], d.get("workspace")) for d in _read_tombstones(into / "tombstones.jsonl")}
    for doc in applied:  # the restored directory remembers them too, so the next restore of this lineage still applies them
        if (doc["kind"], doc["id"], doc.get("workspace")) not in have:
            write_tombstone(into / "tombstones.jsonl", doc["kind"], doc["id"], workspace=doc.get("workspace"))
    return {"restored_files": sum(1 for p in into.rglob("*") if p.is_file()), "tombstones_applied": len(applied), "verified": True}
