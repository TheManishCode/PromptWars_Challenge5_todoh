"""Phase 2.6: the owner's commands, including bringing a single-user install's plans into a named workspace (never automatically)."""

import json
import re

import pytest

from eventmate.service import EventMate
from eventmate.workspaces import WorkspaceRegistry
from eventmate.workspaces_admin import main
from tests.conftest import EXAMPLE, TODAY
from tests.workspace_helpers import SECRET


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENTMATE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("EVENTMATE_SECRET", SECRET)
    monkeypatch.setenv("EVENTMATE_ENABLE_LLM", "0")
    return tmp_path


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_new_shows_the_credential_once_and_it_opens_that_workspace_only(env, capsys):
    code, out, _ = run(capsys, "new")
    wid, credential = re.search(r"workspace (ws-\w+)", out).group(1), re.search(r"credential (\S+)", out).group(1)
    reg = WorkspaceRegistry(env / "data", SECRET)
    assert code == 0 and reg.open(credential).id == wid
    assert credential not in run(capsys, "list")[1]  # listing never shows a credential
    for p in (env / "data").rglob("*"):
        assert not p.is_file() or credential.encode() not in p.read_bytes()


def test_rotate_revoke_sweep_and_delete(env, capsys):
    wid = re.search(r"workspace (ws-\w+)", run(capsys, "new")[1]).group(1)
    reg = WorkspaceRegistry(env / "data", SECRET)
    code, out, _ = run(capsys, "rotate", wid)
    new_credential = re.search(r"credential (\S+)", out).group(1)
    assert code == 0 and reg.open(new_credential).id == wid
    assert run(capsys, "revoke", wid)[1].strip() == "revoked" and reg.open(new_credential) is None
    assert run(capsys, "rotate", wid)[0] == 1  # a revoked workspace cannot be rotated back to life
    assert run(capsys, "delete", wid, "--confirm", "wrong")[0] == 2 and reg.directory.exists(wid)
    assert run(capsys, "delete", wid, "--confirm", wid)[1].strip() == "deleted" and not reg.directory.exists(wid)
    assert "removed 0" in run(capsys, "sweep", "--idle-days", "30")[1]
    assert run(capsys, "rotate", "not-an-id")[0] == 2


def test_import_legacy_copies_a_single_user_installs_plans_into_the_named_workspace_and_leaves_the_originals(env, capsys):
    legacy = tmp_legacy = env / "legacy-plans"
    em = EventMate(plan_dir=legacy, log_dir=env / "legacy-logs", run_id="legacy")
    pid = em.plan(EXAMPLE, today=TODAY).plan.plan_id
    before = json.loads((legacy / f"plan-{pid}-v0001.json").read_text())
    wid = re.search(r"workspace (ws-\w+)", run(capsys, "new")[1]).group(1)
    other = re.search(r"workspace (ws-\w+)", run(capsys, "new")[1]).group(1)
    code, out, _ = run(capsys, "import-legacy", wid, "--plan-dir", str(legacy))
    assert code == 0 and "imported 1 plan(s)" in out
    reg = WorkspaceRegistry(env / "data", SECRET)
    assert reg._build(wid).em.store.load(pid).plan_id == pid and reg._build(other).em.store.list_plans() == []  # only the workspace the owner named
    assert json.loads((legacy / f"plan-{pid}-v0001.json").read_text()) == before and tmp_legacy.exists()  # copied, never moved
    assert run(capsys, "import-legacy", "ws-" + "0" * 16, "--plan-dir", str(legacy))[0] == 1  # an unknown workspace is refused


def test_the_commands_refuse_to_run_without_the_two_settings(tmp_path, monkeypatch):
    monkeypatch.delenv("EVENTMATE_DATA_DIR", raising=False)
    with pytest.raises(SystemExit):
        main(["list"])
