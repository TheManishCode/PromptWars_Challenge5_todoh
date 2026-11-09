"""Deleted personal data is overwritten as it is deleted, on every SQLite build (F6). The same workspace deletion left the id in a free page on SQLite 3.53.1 and not on 3.54.0; the guard is the
pragma on every connection to a store that holds personal data, checked here by asking the connection, not by hoping the build zeroes pages."""
import re
import sqlite3
from pathlib import Path

from eventmate.sqlsafe import scrub, secure
from eventmate.workspaces import WorkspaceDirectory

ROOT = Path(__file__).resolve().parents[1]
# Connections that need no pragma: read-only opens (they delete nothing), the backup copy, a private in-memory queue
READ_ONLY_OR_COPY = {"eventmate/backup.py", "eventmate/wall_admin.py", "eventmate/integrity.py"}


def test_every_connection_to_a_store_of_personal_data_is_made_secure():
    bare = []
    for path in sorted((ROOT / "eventmate").rglob("*.py")):
        rel = str(path.relative_to(ROOT))
        if rel in READ_ONLY_OR_COPY:
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for n, line in enumerate(lines, 1):
            if "sqlite3.connect(" in line and ":memory:" not in line and "secure(sqlite3.connect(" not in line and "out = sqlite3.connect" not in line and "secure_delete" not in "\n".join(lines[n: n + 3]):
                bare.append(f"{rel}:{n}: {line.strip()[:90]}")
    assert bare == [], "a connection without secure(): a deleted row could stay readable in the file\n" + "\n".join(bare)


def test_the_workspace_registry_connection_zeroes_what_it_deletes(tmp_path):
    directory = WorkspaceDirectory(tmp_path / "data")
    with directory._db() as db:
        assert db.execute("PRAGMA secure_delete").fetchone()[0] == 1


def test_a_deleted_workspace_id_is_not_left_in_the_registry_file_or_its_log(tmp_path):
    directory = WorkspaceDirectory(tmp_path / "data")
    ids = [directory.create()[0] for _ in range(40)]  # enough rows that the deleted one shares a page with others
    victim = ids[17]
    assert victim.encode() in directory.path.read_bytes()
    directory.forget(victim)
    leftovers = [p.name for p in directory.path.parent.glob("workspaces.db*") if victim.encode() in p.read_bytes()]
    assert leftovers == [] and victim not in [r[0] for r in sqlite3.connect(directory.path).execute("SELECT id FROM workspaces")]


def test_secure_zeroes_a_deleted_row_even_without_the_vacuum_and_scrub_empties_the_log(tmp_path):
    path = tmp_path / "t.db"
    db = secure(sqlite3.connect(path, isolation_level=None))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE t (v TEXT)")
    db.execute("INSERT INTO t VALUES ('SECRET-VALUE-123456')")
    db.execute("DELETE FROM t")
    scrub(db)
    db.close()
    assert not any(b"SECRET-VALUE-123456" in p.read_bytes() for p in tmp_path.glob("t.db*"))
    assert re.fullmatch(r"(?s)\x00*", (tmp_path / "t.db-wal").read_bytes().decode("latin1")) if (tmp_path / "t.db-wal").exists() else True
