"""The owner's commands for workspaces:  python -m eventmate.workspaces_admin <command>

Reads ``EVENTMATE_DATA_DIR`` and ``EVENTMATE_SECRET`` from the environment (the same two the server needs in shared mode).
  new                         make an empty workspace and print its id and credential once (the credential is not stored; only its hash is)
  list                        ids, when created, when last used, whether anything is saved in each
  rotate ID                   a new credential for ID; the old one stops working
  revoke ID                   no credential opens ID any more (its data stays until it is deleted)
  delete ID --confirm ID      remove ID and everything in it, leaving a tombstone
  sweep --idle-days N         remove workspaces that never saved anything and have been idle for N days
  backup --to DIR             write a verified copy of the data directory into DIR (outside the data directory) while the server runs
  verify-backup PATH          check a backup against its manifest
  restore PATH --into DIR [--deletion-log FILE]   restore into an EMPTY directory, then re-apply deletions made after the backup (use the server's current tombstones.jsonl)
  import-legacy ID --plan-dir DIR   bring the plans of an existing single-user install into workspace ID (hash-checked, ids that collide are replaced); never automatic
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from eventmate.transfer import TransferError, export_workspace, import_archive
from eventmate.workspaces import ID, WorkspaceRegistry


def _registry() -> WorkspaceRegistry:
    data_dir, secret = os.environ.get("EVENTMATE_DATA_DIR", ""), os.environ.get("EVENTMATE_SECRET", "")
    if not data_dir or len(secret) < 32:
        raise SystemExit("set EVENTMATE_DATA_DIR and EVENTMATE_SECRET (at least 32 characters) first")
    return WorkspaceRegistry(Path(data_dir), secret, create_per_address_per_hour=10**6, create_per_hour=10**6)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="eventmate.workspaces_admin", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("new")
    sub.add_parser("list")
    for name in ("rotate", "revoke"):
        sub.add_parser(name).add_argument("id")
    d = sub.add_parser("delete")
    d.add_argument("id")
    d.add_argument("--confirm", required=True)
    sw = sub.add_parser("sweep")
    sw.add_argument("--idle-days", type=int, required=True)
    bk = sub.add_parser("backup")
    bk.add_argument("--to", required=True)
    sub.add_parser("verify-backup").add_argument("path")
    rs = sub.add_parser("restore")
    rs.add_argument("path")
    rs.add_argument("--into", required=True)
    rs.add_argument("--deletion-log")
    il = sub.add_parser("import-legacy")
    il.add_argument("id")
    il.add_argument("--plan-dir", required=True)
    a = ap.parse_args(argv)
    if hasattr(a, "id") and not ID.fullmatch(a.id):
        print("that is not a workspace id", file=sys.stderr)
        return 2
    if a.cmd in ("verify-backup", "restore", "backup"):
        return _backup_command(a)
    reg = _registry()
    if a.cmd == "new":
        ws, credential = reg.create("admin")
        print(f"workspace {ws.id}\ncredential {credential}\n(shown once; give it to the person as the cookie value `todoh_ws`, or rotate it later)")
    elif a.cmd == "list":
        for (wid,) in _ids(reg):
            info = reg.directory.info(wid)
            print(f"{wid}  created {datetime.fromtimestamp(info['created'], timezone.utc):%Y-%m-%d %H:%M}  last used {datetime.fromtimestamp(info['last_seen'], timezone.utc):%Y-%m-%d %H:%M}  {'has data' if reg.has_data(wid) else 'empty'}")
    elif a.cmd == "rotate":
        credential = reg.rotate(a.id)
        if credential is None:
            print("no such workspace, or it was revoked", file=sys.stderr)
            return 1
        print(f"credential {credential}\n(shown once; the old one no longer works)")
    elif a.cmd == "revoke":
        print("revoked" if reg.revoke(a.id) else "no such workspace")
    elif a.cmd == "delete":
        if a.confirm != a.id:
            print("--confirm must repeat the workspace id", file=sys.stderr)
            return 2
        print("deleted" if reg.delete(a.id) else "no such workspace")
    elif a.cmd == "sweep":
        removed = reg.sweep_idle_empty(a.idle_days * 86400)
        print(f"removed {len(removed)} empty idle workspace(s)")
    elif a.cmd == "import-legacy":
        from eventmate.service import EventMate

        if not reg.directory.exists(a.id):
            print("no such workspace", file=sys.stderr)
            return 1
        legacy = EventMate(plan_dir=Path(a.plan_dir), log_dir=Path(a.plan_dir) / ".legacy-logs-unused")
        try:
            report = import_archive(export_workspace(legacy), reg._build(a.id).em)
        except TransferError as exc:
            print(f"not imported: {exc}", file=sys.stderr)
            return 1
        print(f"imported {report['imported']['plans']} plan(s) into {a.id}; remapped ids: {report['remapped'] or 'none'}. The old files were copied, not moved.")
    return 0


def _backup_command(a) -> int:
    from eventmate import backup

    try:
        if a.cmd == "backup":
            data_dir = os.environ.get("EVENTMATE_DATA_DIR", "")
            if not data_dir:
                raise SystemExit("set EVENTMATE_DATA_DIR first")
            out = backup.make_backup(Path(data_dir), Path(a.to))
            print(f"backup written to {out} ({len(backup.verify_backup(out))} problems)")
        elif a.cmd == "verify-backup":
            problems = backup.verify_backup(Path(a.path))
            print("ok" if not problems else "\n".join(problems))
            return 0 if not problems else 1
        else:
            report = backup.restore_backup(Path(a.path), Path(a.into), Path(a.deletion_log) if a.deletion_log else None)
            print(f"restored {report['restored_files']} file(s); {report['tombstones_applied']} deletion(s) re-applied")
    except backup.BackupError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    return 0


def _ids(reg: WorkspaceRegistry):
    import sqlite3

    conn = sqlite3.connect(reg.directory.path)
    try:
        return conn.execute("SELECT id FROM workspaces ORDER BY created").fetchall()
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
