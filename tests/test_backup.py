"""Backup and restore: a consistent copy while the server runs, verification, restore only into an empty directory, and deletions that stay deleted."""

import contextlib
import json
import sqlite3
from pathlib import Path

import pytest

from eventmate import backup
from eventmate.workspaces import WorkspaceRegistry
from eventmate.workspaces_admin import main
from tests.workspace_helpers import SECRET, Visitor, shared_app


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("EVENTMATE_ENABLE_LLM", "0")


def world(tmp_path):
    app, registry = shared_app(tmp_path)
    a, b = Visitor(app, "198.51.100.1"), Visitor(app, "198.51.100.2")
    a.start(), b.start()
    return app, registry, a, b


def snapshot(root: Path):
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_a_backup_verifies_and_contains_the_databases_the_workspaces_and_the_deletion_log(tmp_path):
    app, registry, a, b = world(tmp_path)
    plan = a.make_plan()
    out = backup.make_backup(tmp_path / "data", tmp_path / "backups")
    assert backup.verify_backup(out) == [] and out.parent == (tmp_path / "backups").resolve()
    names = {str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()}
    assert "workspaces.db" in names and "MANIFEST.json" in names and any(n.endswith(f"plan-{plan}-v0001.json") for n in names)
    with contextlib.closing(sqlite3.connect(out / "workspaces.db")) as db:
        assert db.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0] == 2
    assert oct(out.stat().st_mode & 0o777) == "0o700" and oct((out / "workspaces.db").stat().st_mode & 0o777) == "0o600"


def test_a_backup_cannot_be_written_inside_the_data_directory(tmp_path):
    world(tmp_path)
    with pytest.raises(backup.BackupError):
        backup.make_backup(tmp_path / "data", tmp_path / "data" / "inside")
    with pytest.raises(backup.BackupError):
        backup.make_backup(tmp_path / "data", tmp_path / "data")


def test_a_changed_missing_or_extra_file_fails_verification(tmp_path):
    world(tmp_path)[2].make_plan()
    out = backup.make_backup(tmp_path / "data", tmp_path / "backups")
    plan_file = next(out.rglob("plan-*-v0001.json"))
    plan_file.chmod(0o600)
    plan_file.write_text(plan_file.read_text().replace("Pune", "Goa"))
    assert any(p.startswith("changed:") for p in backup.verify_backup(out))
    (out / "stray.txt").write_text("x")
    assert any(p.startswith("not in the manifest:") for p in backup.verify_backup(out))
    (out / "workspaces.db").unlink()
    assert any(p.startswith("missing:") for p in backup.verify_backup(out))
    assert backup.verify_backup(tmp_path / "nowhere") == ["the manifest is missing or unreadable"]


def test_restore_reproduces_the_data_and_refuses_a_directory_that_is_not_empty(tmp_path):
    app, registry, a, b = world(tmp_path)
    plan = a.make_plan()
    out = backup.make_backup(tmp_path / "data", tmp_path / "backups")
    report = backup.restore_backup(out, tmp_path / "restored")
    assert report["verified"] is True and report["tombstones_applied"] == 0
    restored = WorkspaceRegistry(tmp_path / "restored", SECRET)
    ws = restored.open(a.credential())  # the same credential still opens the same workspace
    assert ws.id == a.workspace_id and ws.em.store.load(plan).plan_id == plan
    with pytest.raises(backup.BackupError, match="empty"):
        backup.restore_backup(out, tmp_path / "restored")
    (tmp_path / "occupied").mkdir()
    (tmp_path / "occupied" / "x").write_text("x")
    with pytest.raises(backup.BackupError, match="empty"):
        backup.restore_backup(out, tmp_path / "occupied")


def test_a_restore_does_not_resurrect_a_plan_or_a_workspace_deleted_after_the_backup(tmp_path):
    app, registry, a, b = world(tmp_path)
    keep, gone = a.make_plan(), a.make_plan()
    b.make_plan()
    out = backup.make_backup(tmp_path / "data", tmp_path / "backups")  # taken while both plans and both workspaces exist
    assert a.delete(f"/api/plans/{gone}", {"confirm": gone}).status_code == 200
    assert b.delete("/api/workspace", {"confirm": "delete"}).status_code == 200
    log = tmp_path / "data" / "tombstones.jsonl"
    kinds = {(json.loads(x)["kind"], json.loads(x)["id"]) for x in log.read_text().splitlines()}
    assert ("plan", gone) in kinds and ("workspace", b.workspace_id) in kinds
    report = backup.restore_backup(out, tmp_path / "restored", deletion_log=log)
    assert report["tombstones_applied"] == 2
    restored = WorkspaceRegistry(tmp_path / "restored", SECRET)
    ws = restored.open(a.credential())
    assert ws.em.store.list_plans() == [keep] and not restored.directory.exists(b.workspace_id) and not restored.home(b.workspace_id).exists()
    leaks = [n for n, data in snapshot(tmp_path / "restored").items() if gone.encode() in data and not n.endswith("tombstones.jsonl")]
    assert leaks == [], leaks
    assert restored.open(b.credential()) is None  # the deleted workspace's credential opens nothing
    assert (tmp_path / "restored" / "tombstones.jsonl").read_text().count(gone) == 1  # remembered once for the next restore


def test_restoring_the_same_log_twice_does_not_duplicate_it(tmp_path):
    app, registry, a, b = world(tmp_path)
    gone = a.make_plan()
    out = backup.make_backup(tmp_path / "data", tmp_path / "backups")
    a.delete(f"/api/plans/{gone}", {"confirm": gone})
    log = tmp_path / "data" / "tombstones.jsonl"
    backup.restore_backup(out, tmp_path / "r1", deletion_log=log)
    again = backup.make_backup(tmp_path / "r1", tmp_path / "backups2")
    backup.restore_backup(again, tmp_path / "r2", deletion_log=log)
    assert (tmp_path / "r2" / "tombstones.jsonl").read_text().count(gone) == 1


def test_a_symlink_in_the_data_directory_is_not_followed_into_the_backup(tmp_path):
    app, registry, a, b = world(tmp_path)
    a.make_plan()
    secret = tmp_path / "outside-secret.txt"
    secret.write_text("TOP-SECRET-OUTSIDE")
    (registry.home(a.workspace_id) / "plans" / "link.txt").symlink_to(secret)
    out = backup.make_backup(tmp_path / "data", tmp_path / "backups")
    assert not any("TOP-SECRET-OUTSIDE" in p.read_text(errors="ignore") for p in out.rglob("*") if p.is_file())


def test_the_admin_commands_backup_verify_and_restore(tmp_path, monkeypatch, capsys):
    app, registry, a, b = world(tmp_path)
    a.make_plan()
    monkeypatch.setenv("EVENTMATE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("EVENTMATE_SECRET", SECRET)
    assert main(["backup", "--to", str(tmp_path / "bk")]) == 0
    path = capsys.readouterr().out.split("backup written to ")[1].split(" ")[0]
    assert main(["verify-backup", path]) == 0 and capsys.readouterr().out.strip() == "ok"
    assert main(["restore", path, "--into", str(tmp_path / "back")]) == 0 and "restored" in capsys.readouterr().out
    assert main(["restore", path, "--into", str(tmp_path / "back")]) == 1  # not empty any more
    assert main(["backup", "--to", str(tmp_path / "data" / "inside")]) == 1
