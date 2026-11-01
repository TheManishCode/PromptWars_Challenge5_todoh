"""Owner tools for the wall. There is no web route for any of this and no admin login to build:  python -m eventmate.wall_admin <command>

  list-pending | list --status visible|hidden|removed|pending
  approve <id> | reject <id> | hide <id> | restore <id> | remove <id>
  export-public --out FILE      only visible marks as {section_id, slot, name}; anything already published cannot be recalled
  backup --out FILE             SQLite online backup, integrity-checked (never a raw copy of the live file)
  restore-backup FILE           put a backup in place of an empty or --force'd database, after an integrity check
  stats | sweep | accepting on|off | new-layout N
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from eventmate.plan_store import DEFAULT_PLAN_DIR
from eventmate.wall.store import WallConfig, WallError, WallStore

DEFAULT_DB = DEFAULT_PLAN_DIR.parent / "wall_data" / "wall.sqlite3"


def db_path(arg: str | None) -> Path:
    return Path(arg or os.environ.get("EVENTMATE_WALL_DB") or DEFAULT_DB)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def restore_backup(src: Path, dest: Path, force: bool = False) -> None:
    """Copy a backup over ``dest``. The backup is opened read-only and integrity-checked first; a live file is only replaced with ``force``."""
    conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        ok = conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        has_meta = conn.execute("SELECT 1 FROM sqlite_master WHERE name='meta'").fetchone() is not None
    finally:
        conn.close()
    if not ok or not has_meta:
        raise ValueError(f"{src} is not an intact wall database")
    if dest.exists() and dest.stat().st_size > 0 and not force:
        raise FileExistsError(f"{dest} already exists; pass --force to replace it (take a backup first)")
    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    for suffix in ("-wal", "-shm"):
        Path(str(dest) + suffix).unlink(missing_ok=True)  # a stale journal of the old file must not be replayed onto the restored one
    tmp = dest.with_name(dest.name + ".restoring")
    shutil.copyfile(src, tmp)
    os.chmod(tmp, 0o600)
    os.replace(tmp, dest)
    WallStore(dest)  # opens it once: refuses a schema newer than this build


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eventmate.wall_admin", description="Owner tools for the wall.")
    ap.add_argument("--db", help="wall database (default: EVENTMATE_WALL_DB or eventmate/wall_data/wall.sqlite3)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list-pending")
    ls = sub.add_parser("list")
    ls.add_argument("--status", choices=["pending", "visible", "hidden", "removed"], default="visible")
    for name in ("approve", "reject", "hide", "restore", "remove"):
        p = sub.add_parser(name)
        p.add_argument("mark_id", type=int)
        if name in ("reject", "hide", "remove"):
            p.add_argument("--reason", default="")
    for name in ("export-public", "backup"):
        sub.add_parser(name).add_argument("--out", required=True)
    rb = sub.add_parser("restore-backup")
    rb.add_argument("file")
    rb.add_argument("--force", action="store_true")
    sub.add_parser("stats")
    sub.add_parser("sweep")
    acc = sub.add_parser("accepting")
    acc.add_argument("state", choices=["on", "off"])
    nl = sub.add_parser("new-layout")
    nl.add_argument("version", type=int)
    a = ap.parse_args(argv)
    path = db_path(a.db)
    try:
        if a.cmd == "restore-backup":
            restore_backup(Path(a.file), path, a.force)
            print(f"restored {a.file} to {path}")
            return 0
        store = WallStore(path, WallConfig())
        if a.cmd == "list-pending":
            a.cmd, a.status = "list", "pending"
        if a.cmd == "list":
            for m in store.list_marks(a.status):
                print(f"{m['mark_id']:>6}  {m['status']:<8} {m['created_at']}  section={m['section_id']} slot={m['slot']}  {m['name']!r}")
        elif a.cmd == "approve":
            print(json.dumps(store.approve(a.mark_id)))
        elif a.cmd == "reject":
            store.reject(a.mark_id, a.reason)
            print("rejected")
        elif a.cmd == "hide":
            store.hide(a.mark_id, a.reason)
            print("hidden")
        elif a.cmd == "restore":
            store.restore(a.mark_id)
            print("restored")
        elif a.cmd == "remove":
            store.remove(a.mark_id, a.reason)
            print("removed (the name is blanked; older backups keep it until they age out)")
        elif a.cmd == "export-public":
            marks = store.export_public()
            _atomic_write(Path(a.out), json.dumps({"marks": marks}, ensure_ascii=False, indent=1) + "\n")
            print(f"wrote {len(marks)} visible marks to {a.out}. Anything you publish cannot be recalled.")
        elif a.cmd == "backup":
            print(f"backup written to {store.backup(Path(a.out))}")
        elif a.cmd == "stats":
            print(json.dumps(store.stats(), indent=1))
        elif a.cmd == "sweep":
            print(json.dumps(store.sweep()))
        elif a.cmd == "accepting":
            store.set_accepting(a.state == "on")
            print(f"accepting {a.state}")
        elif a.cmd == "new-layout":
            store.new_layout(a.version)
            print(f"new sections use layout {a.version}")
    except (WallError, ValueError, OSError, sqlite3.Error) as exc:
        print(f"error: {getattr(exc, 'message', exc)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
