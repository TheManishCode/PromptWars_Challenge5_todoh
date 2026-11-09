"""SQLite keeps what was deleted: a deleted row's bytes stay in a free page until something overwrites them, and in the write-ahead log until it is checkpointed. Whether they do depends on the
SQLite build (the same deletion left a workspace id behind on 3.53.1 and not on 3.54.0, F6). Every connection to a store that holds personal data goes through ``secure``, so deleted content is
overwritten with zeros as it is deleted, whatever the build."""

from __future__ import annotations

import sqlite3


def secure(conn: sqlite3.Connection) -> sqlite3.Connection:
    """``conn`` with ``secure_delete`` on: freed pages are zeroed when a row is deleted or updated."""
    conn.execute("PRAGMA secure_delete=ON")
    return conn


def scrub(conn: sqlite3.Connection) -> None:
    """After a deletion: truncate the write-ahead log (its frames hold the old bytes) and, in a store without one, rewrite the file so no free page remains. Never raises: a busy file is retried
    by the next deletion, and the zeroing above has already happened."""
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        if str(mode).lower() == "wal":
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        elif not conn.in_transaction:
            conn.execute("VACUUM")
    except sqlite3.Error:
        pass
