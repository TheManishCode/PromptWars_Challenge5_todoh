"""8.9: the owner's CLI, backups and the multi-process behaviour of the wall file."""

import json
import multiprocessing as mp
import sqlite3

import pytest

from dataclasses import replace

from eventmate.wall.store import WallConfig, WallStore
from eventmate.wall_admin import main
from tests.test_wall_store import h, visitor
from tests.wall_helpers import make_wall


def run(db, *args):
    return main(["--db", str(db), *args])


def test_cli_moderation_round_trip(tmp_path, capsys):
    store, _ = make_wall(tmp_path)
    ids = [visitor(store, i, f"Name{i}x", approve=False) for i in range(1, 4)]
    db = store.path
    assert run(db, "list-pending") == 0
    out = capsys.readouterr().out
    assert all(f"Name{i}x" in out for i in (1, 2, 3))
    assert run(db, "approve", str(ids[0])) == 0 and run(db, "reject", str(ids[1]), "--reason", "spam") == 0
    capsys.readouterr()
    assert run(db, "hide", str(ids[0])) == 0 and run(db, "restore", str(ids[0])) == 0 and run(db, "remove", str(ids[2])) == 0
    run(db, "list", "--status", "visible")
    visible = capsys.readouterr().out
    assert "Name1x" in visible and "Name2x" not in visible and "Name3x" not in visible
    assert run(db, "stats") == 0 and '"visible_count": 1' in capsys.readouterr().out
    assert run(db, "accepting", "off") == 0 and store.meta()["accepting"] is False
    assert run(db, "accepting", "on") == 0 and run(db, "sweep") == 0 and run(db, "new-layout", "1") == 0


def test_cli_errors_are_clean_exit_codes(tmp_path, capsys):
    store, _ = make_wall(tmp_path)
    assert run(store.path, "approve", "999") == 1
    assert "error:" in capsys.readouterr().err
    assert run(store.path, "new-layout", "42") == 1
    assert run(store.path, "restore-backup", str(tmp_path / "missing.db")) == 1


def test_export_public_and_backup_commands(tmp_path, capsys):
    store, _ = make_wall(tmp_path)
    a = visitor(store, 1, "Public One")
    b = visitor(store, 2, "Hidden One")
    store.hide(b)
    out = tmp_path / "pub" / "wall.json"
    assert run(store.path, "export-public", "--out", str(out)) == 0
    doc = json.loads(out.read_text())
    assert doc == {"marks": [{"section_id": 0, "slot": 0, "name": "Public One"}]}
    assert "cannot be recalled" in capsys.readouterr().out
    bak = tmp_path / "bk.db"
    assert run(store.path, "backup", "--out", str(bak)) == 0 and run(store.path, "backup", "--out", str(bak)) == 1
    fresh = tmp_path / "fresh.db"
    assert run(fresh, "restore-backup", str(bak)) == 0 and WallStore(fresh).list_marks("visible")[0]["name"] == "Public One"
    assert run(fresh, "restore-backup", str(bak)) == 1  # refuses to overwrite a live file
    assert run(fresh, "restore-backup", str(bak), "--force") == 0


def test_a_restore_does_not_replay_a_stale_journal(tmp_path):
    store, _ = make_wall(tmp_path)
    visitor(store, 1, "Before")
    bak = store.backup(tmp_path / "b.db")
    visitor(store, 2, "After")
    from eventmate.wall_admin import restore_backup
    restore_backup(bak, store.path, force=True)
    assert [m["name"] for m in WallStore(store.path).list_marks("visible")] == ["Before"]


def _child(path, n, q):
    from tests.wall_helpers import Clock
    store = WallStore(path, replace(WallConfig(), section_capacity=9), clock=Clock())
    cred = h(1000 + n)
    store.create_session(cred)
    status, body, _ = store.submit(cred, f"idem-child-{n:04d}", f"Child{n}x")
    store.approve(body["mark_id"])
    q.put((n, status))


def test_many_processes_share_one_wall_file_without_losing_or_duplicating_slots(tmp_path):
    store, _ = make_wall(tmp_path, section_capacity=9)
    ctx = mp.get_context("fork")
    q = ctx.Queue()
    procs = [ctx.Process(target=_child, args=(store.path, n, q)) for n in range(24)]
    [p.start() for p in procs]
    [p.join(90) for p in procs]
    assert sorted(q.get(timeout=5)[1] for _ in procs) == [201] * 24
    with sqlite3.connect(store.path) as c:
        placed = c.execute("SELECT section_id, slot FROM marks WHERE status='visible'").fetchall()
        assert len(placed) == 24 and len(set(placed)) == 24
        assert c.execute("SELECT visible_count FROM meta").fetchone()[0] == 24
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert c.execute("SELECT COUNT(*) FROM sections").fetchone()[0] == 3


def test_state_survives_a_restart(tmp_path):
    store, clock = make_wall(tmp_path)
    visitor(store, 1, "Persistent")
    store.issue_recovery_code(h(1), "a" * 64, False)
    again = WallStore(store.path, clock=clock)
    assert again.list_marks("visible")[0]["name"] == "Persistent" and again.me(h(1))["has_recovery_code"] is True
