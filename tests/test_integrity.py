"""v6 1.3 and 1.4: verify, quarantine-style repair, the block on sending while a money or send-state file is damaged, and a plan history that cannot lose a version unnoticed."""

import hashlib
import json
import os
import shutil
import stat
import threading
from pathlib import Path

import pytest

from eventmate import integrity
from eventmate.__main__ import main
from eventmate.integrity import Guard, StoreDamaged
from eventmate.plan_store import PlanStore
from eventmate.schemas import VendorCategory as C
from eventmate.service import EventMate
from tests.conftest import EXAMPLE, TODAY
from tests.test_conversations import Clock, FakeChannel

FIXTURES = Path(__file__).parent / "fixtures" / "v1"
PID = (FIXTURES / "PLAN_ID.txt").read_text().strip()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("EVENTMATE_ENABLE_LLM", "0")


@pytest.fixture
def chans():
    return {"email": FakeChannel("email"), "whatsapp": FakeChannel("whatsapp")}


@pytest.fixture
def em(tmp_path, chans):
    clock = Clock()
    e = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="int", channels=chans)
    e.outreach_store.now = clock
    e.conversations.now = clock
    e.clock = clock
    return e


@pytest.fixture
def pid(em):
    plan = em.plan(EXAMPLE, today=TODAY).plan.plan_id
    em.ledger_add(plan, "payment", C.CATERER, 5000, "advance")
    em.ledger_add(plan, "quote", C.VENUE, 60000, "hall quote")  # a second line, so cutting the last one short leaves an earlier one to keep
    return plan


def ledger_file(em, pid):
    return em.store.root / "ledger" / f"ledger-{pid}.jsonl"


def tear(path: Path, keep_of_last: int = 20):
    """Cut the last line short, as a crash in the middle of an append would."""
    data = path.read_bytes()
    body, _, last = data.rstrip(b"\n").rpartition(b"\n")
    path.write_bytes((body + b"\n" if body else b"") + last[:keep_of_last])


def verify_code(em, capsys, *extra):
    code = main(["--plan-dir", str(em.store.root), "--log-dir", str(em.logs.decisions.log_dir), "verify", *extra])
    out = capsys.readouterr()
    return code, out.out, out.err


# ---- verify


def test_verify_is_clean_on_a_healthy_workspace_and_changes_nothing(em, pid, capsys):
    before = {p: p.read_bytes() for p in em.store.root.rglob("*") if p.is_file()}
    code, out, _ = verify_code(em, capsys)
    assert code == 0 and out.startswith("ok:")
    assert {p: p.read_bytes() for p in em.store.root.rglob("*") if p.is_file()} == before


def test_a_torn_last_line_is_found_by_file_and_line_without_printing_its_content(em, pid, capsys):
    tear(ledger_file(em, pid))
    code, out, err = verify_code(em, capsys)
    assert code == 1 and f"ledger/ledger-{pid}.jsonl line 2" in out and "torn last line" in out
    assert "advance" not in out and "caterer" not in out  # the line's content is never in a report
    assert "Sending and approving are stopped" in err


def test_a_damaged_middle_line_a_future_schema_and_a_wrong_shape_are_each_named(em, pid, capsys):
    path = ledger_file(em, pid)
    lines = path.read_text().splitlines()
    path.write_text("\n".join([lines[0], "not json at all", json.dumps({**json.loads(lines[0]), "schema_version": 9}), json.dumps({"amount": -4}), lines[0]]) + "\n")
    _, out, _ = verify_code(em, capsys, "--json")
    found = {d["line"]: d["reason"] for d in json.loads(out)["damaged"]}
    assert set(found) == {2, 3, 4} and "torn or damaged" in found[2]
    assert "newer version" in found[3] and "record format" in found[4]


def test_a_missing_or_damaged_job_database_and_an_orphan_record_are_reported(em, pid, capsys):
    plan = em.store.latest(pid)
    stray = em.store.root / "ledger" / "ledger-ghostplan123.jsonl"
    shutil.copy(ledger_file(em, pid), stray)
    _, out, _ = verify_code(em, capsys)
    assert "orphan" in out and "ghostplan123" in out and plan.plan_id == pid
    (em.store.root / "jobs.db").write_bytes(b"this is not a database" * 50)
    code, out, _ = verify_code(em, capsys)
    assert code == 1 and "jobs.db" in out and "authoritative" in out


def test_a_derived_store_damage_is_reported_but_does_not_stop_sending(em, pid, capsys):
    digest = em.store.root / "digests"
    digest.mkdir(exist_ok=True)
    (digest / f"digest-{pid}.jsonl").write_text("garbage\n")
    code, out, _ = verify_code(em, capsys)
    assert code == 1 and "derived" in out
    assert Guard(em.store.root).blocker() is None  # digests are rebuilt; they hold no send history


# ---- the block while an authoritative file is damaged


def drafted_and_approved(em, pid, to="sales@annapurna.in", sent=True):
    d = em.draft_outreach(pid, C.CATERER, "email", to, "Asha")
    em.approve_outreach(pid, d.draft_id, d.content_hash)
    return d


def test_nothing_is_approved_or_sent_while_a_money_or_send_file_is_damaged(em, pid, chans):
    d = em.draft_outreach(pid, C.CATERER, "email", "sales@annapurna.in", "Asha")
    tear(em.store.root / "outreach" / f"outreach-{pid}.jsonl", 30)
    with pytest.raises(StoreDamaged, match="python -m eventmate verify"):
        em.approve_outreach(pid, d.draft_id, d.content_hash)
    assert em.conversations.dispatch() == (0, [em.integrity.blocker()]) and chans["email"].sent == []
    with pytest.raises(StoreDamaged):
        em.grant_autoreply(pid, "Asha")


def test_the_block_lifts_when_the_file_is_repaired_and_not_before(em, pid, chans):
    path = ledger_file(em, pid)
    tear(path)
    assert em.integrity.blocker() is not None
    q = integrity.quarantine(path)
    assert em.integrity.blocker() is not None  # a candidate beside the damaged file changes nothing
    integrity.apply_repair(q.candidate, len(q.dropped))
    assert em.integrity.blocker() is None


def test_a_damaged_job_database_stops_sending_too(em, pid, chans):
    d = em.draft_outreach(pid, C.CATERER, "email", "sales@annapurna.in", "Asha")
    (em.store.root / "jobs.db").write_bytes(b"x" * 4096)
    with pytest.raises(StoreDamaged):
        em.approve_outreach(pid, d.draft_id, d.content_hash)


# ---- repair never swaps silently


def test_quarantine_keeps_the_damaged_file_read_only_writes_a_candidate_and_leaves_the_live_file_alone(em, pid, capsys):
    path = ledger_file(em, pid)
    tear(path)
    live_before = path.read_bytes()
    code = main(["--plan-dir", str(em.store.root), "--log-dir", str(em.logs.decisions.log_dir), "repair", "--quarantine"])
    out = capsys.readouterr().out
    assert code == 0 and "set aside 1" in out and "dropped line 2" in out and "Nothing was swapped in" in out and "advance" not in out
    assert path.read_bytes() == live_before  # still the damaged file
    damaged = next(path.parent.glob("*.damaged-*"))
    candidate = next(p for p in path.parent.glob("*.repaired-*") if p.suffix != ".json")
    assert stat.S_IMODE(damaged.stat().st_mode) == 0o444 and damaged.read_bytes() == live_before
    assert len(candidate.read_text().splitlines()) == 1 and json.loads(candidate.read_text().splitlines()[0])["note"] == "advance"  # the earlier line survived
    meta = json.loads(Path(str(candidate) + ".json").read_text())
    assert meta["dropped"] == [{"line": 2, "reason": "torn last line (the write was cut off)"}] and meta["kept"] == 1


def test_apply_needs_the_exact_number_of_dropped_lines_and_a_live_file_that_has_not_moved(em, pid):
    path = ledger_file(em, pid)
    tear(path)
    q = integrity.quarantine(path)
    for wrong in (0, 2, -1, True):
        with pytest.raises(ValueError, match="confirm with that number"):
            integrity.apply_repair(q.candidate, wrong)
    path.write_bytes(path.read_bytes() + b"")  # untouched bytes: still fine
    path.write_bytes(path.read_bytes() + b"x")  # but a change after the quarantine is refused
    with pytest.raises(ValueError, match="changed after it was quarantined"):
        integrity.apply_repair(q.candidate, 1)


def test_apply_swaps_once_and_a_second_apply_is_refused(em, pid):
    path = ledger_file(em, pid)
    tear(path)
    q = integrity.quarantine(path)
    assert integrity.apply_repair(q.candidate, 1) == path
    assert [e.note for e in em.ledger.entries(pid)] == ["advance"] and stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(ValueError):
        integrity.apply_repair(q.candidate, 1)  # the candidate is gone


def test_a_changed_candidate_is_refused(em, pid):
    path = ledger_file(em, pid)
    tear(path)
    q = integrity.quarantine(path)
    q.candidate.write_text(q.candidate.read_text() + "\n")
    with pytest.raises(ValueError, match="candidate was changed"):
        integrity.apply_repair(q.candidate, 1)


def test_the_cli_refuses_to_apply_without_a_confirmation_off_a_terminal(em, pid, capsys, monkeypatch):
    path = ledger_file(em, pid)
    tear(path)
    q = integrity.quarantine(path)
    monkeypatch.setattr("sys.stdin", open(os.devnull))
    base = ["--plan-dir", str(em.store.root), "--log-dir", str(em.logs.decisions.log_dir), "repair"]
    assert main(base + ["--apply", str(q.candidate)]) == 2 and "--confirm N" in capsys.readouterr().err
    assert main(base + ["--apply", str(q.candidate), "--confirm", "5"]) == 2  # wrong number: refused as an error, nothing swapped
    assert main(base + ["--apply", str(q.candidate), "--confirm", "1"]) == 0 and main(base[:-1] + ["verify"]) == 0


def test_only_a_derived_store_can_be_reset_and_the_old_file_is_kept(em, pid):
    ledger = ledger_file(em, pid)
    with pytest.raises(ValueError, match="only a derived store"):
        integrity.reset_derived(ledger)
    digest = em.store.root / "digests" / f"digest-{pid}.jsonl"
    digest.parent.mkdir(exist_ok=True)
    digest.write_text("garbage\n")
    aside = integrity.reset_derived(digest)
    assert not digest.exists() and aside.read_text() == "garbage\n" and stat.S_IMODE(aside.stat().st_mode) == 0o444


# ---- sends stay blocked, and the caps still count what was sent, until the operator confirms


def test_a_send_history_file_with_a_cut_off_final_line_keeps_blocking_and_the_unknown_send_still_counts_against_the_caps(em, pid, chans):
    policy_cap = em.conversations.policy.per_vendor_per_day
    for i in range(policy_cap - 1):
        drafted_and_approved(em, pid, to="sales@annapurna.in")
    assert len(chans["email"].sent) == policy_cap - 1
    last = em.draft_outreach(pid, C.CATERER, "email", "sales@annapurna.in", "Asha")
    em.approve_outreach(pid, last.draft_id, last.content_hash)  # the cap-th send goes out now
    assert len(chans["email"].sent) == policy_cap
    path = em.store.root / "outreach" / f"outreach-{pid}.jsonl"
    tear(path, 25)  # the record of the final send is cut off (a crash while appending)
    sent_before = len(chans["email"].sent)
    try:
        em.draft_outreach(pid, C.CATERER, "email", "sales@annapurna.in", "Asha")  # writing a draft sends nothing, so it may or may not be allowed...
    except ValueError:
        pass
    assert len(chans["email"].sent) == sent_before  # ...but nothing goes out
    q = integrity.quarantine(path)  # quarantine alone changes nothing: the live file is as damaged as before
    assert em.integrity.blocker() is not None and path.read_bytes() == q.damaged_copy.read_bytes()
    assert q.damaged_copy.read_text().count("\n") >= policy_cap  # the quarantined copy still holds the history the caps are computed from
    with pytest.raises(StoreDamaged):
        em.conversations.approve(pid, last.draft_id, last.content_hash)
    integrity.apply_repair(q.candidate, len(q.dropped))  # only now, after the typed confirmation
    assert em.integrity.blocker() is None
    states = [d.status for d in em.outreach_store.list(pid)]
    assert states.count("sent") + states.count("sending") + states.count("outcome_unknown") >= policy_cap - 1
    em.clock.advance(minutes=30)  # past the lease: a send whose outcome was lost is outcome_unknown, and that counts against the caps
    em.conversations.dispatch()
    again = em.draft_outreach(pid, C.CATERER, "email", "sales@annapurna.in", "Asha")
    em.approve_outreach(pid, again.draft_id, again.content_hash)
    assert len(chans["email"].sent) <= policy_cap  # the per-vendor cap held through the damage and the repair


# ---- the web layer


def test_a_damaged_file_gives_503_with_a_code_and_never_a_path_or_content(tmp_path, monkeypatch):
    from eventmate.web.app import create_app

    em = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="web", channels={"email": FakeChannel("email"), "whatsapp": FakeChannel("whatsapp")})
    app = create_app(em, today_fn=lambda: TODAY)
    c = app.test_client()
    h = {"X-EventMate-CSRF": c.get("/api/session").get_json()["csrf"], "Content-Type": "application/json"}
    plan = c.post("/api/plans", data=json.dumps({"request": EXAMPLE}), headers=h).get_json()["plan"]["plan_id"]
    c.post(f"/api/plans/{plan}/ledger", data=json.dumps({"kind": "payment", "category": "caterer", "amount": 4242, "note": "SECRETNOTE"}), headers=h)
    tear(em.store.root / "ledger" / f"ledger-{plan}.jsonl", 15)
    r = c.get(f"/api/plans/{plan}/ledger")
    body = r.get_data(as_text=True)
    assert r.status_code == 503 and r.get_json()["code"] == "store_damaged" and r.get_json()["request_id"]
    assert "SECRETNOTE" not in body and str(tmp_path) not in body and "ledger-" not in body


# ---- appends


def test_concurrent_appends_never_interleave_a_line_and_each_is_synced(em, pid, monkeypatch):
    synced = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real(fd))[1])
    plan = em.store.latest(pid)

    def add(n):
        for _ in range(25):
            em.ledger.add(plan, "payment", C.CATERER, 1 + n, "x" * 200)

    threads = [threading.Thread(target=add, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    lines = ledger_file(em, pid).read_text().splitlines()
    assert len(lines) == 2 + 200 and all(json.loads(x) for x in lines)  # every line whole
    assert len(synced) >= 200  # one fsync per append: a record that was acknowledged is on disk


# ---- plan history (1.4)


def plan_store_with_two_versions(tmp_path):
    root = tmp_path / "plans"
    root.mkdir()
    shutil.copy(FIXTURES / "plan-v1.json", root / f"plan-{PID}-v0001.json")  # a version written before parent hashes existed
    (root / f"plan-{PID}-v0001.json").chmod(0o444)
    ps = PlanStore(root)
    first = ps.load(PID)
    ps.save(first.model_copy(update={"version": 2, "parent_version": 1}))
    ps.save(first.model_copy(update={"version": 3, "parent_version": 2}))
    return ps


def test_a_new_version_records_its_parents_hash_and_every_save_updates_the_head_record(tmp_path):
    ps = plan_store_with_two_versions(tmp_path)
    v1, v2, v3 = (json.loads((ps.root / f"plan-{PID}-v000{n}.json").read_text()) for n in (1, 2, 3))
    assert "parent_sha256" not in v1 and v2["parent_sha256"] == v1["sha256"] and v3["parent_sha256"] == v2["sha256"]
    assert ps.heads()[PID] == (3, v3["sha256"]) and ps.check_chain(PID) == []


def test_deleting_the_newest_version_is_found_as_a_rollback(tmp_path):
    ps = plan_store_with_two_versions(tmp_path)
    newest = ps.root / f"plan-{PID}-v0003.json"
    newest.chmod(0o644)
    newest.unlink()
    assert ps.latest(PID).version == 2  # the application would carry on with the older plan...
    problems = ps.check_chain(PID)
    assert len(problems) == 1 and "rolled back" in problems[0] and "version 3" in problems[0] and "version 2" in problems[0]  # ...but verify says what happened


def test_a_missing_middle_version_a_replaced_version_and_a_changed_head_hash_are_found(tmp_path):
    ps = plan_store_with_two_versions(tmp_path)
    middle = ps.root / f"plan-{PID}-v0002.json"
    keep = middle.read_bytes()
    middle.chmod(0o644)
    middle.unlink()
    assert any("missing from the middle" in p for p in ps.check_chain(PID))
    middle.write_bytes(keep)
    # replace v2 with a different, internally consistent document (its own hash recomputed): v3 no longer vouches for it
    doc = json.loads(keep)
    doc["plan"]["change_reason"] = "quietly rewritten"
    doc["sha256"] = hashlib.sha256(json.dumps(doc["plan"], sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    middle.write_text(json.dumps(doc))
    assert any("v3: does not link to the version before it" in p for p in ps.check_chain(PID))
    newest = ps.root / f"plan-{PID}-v0003.json"
    d3 = json.loads(newest.read_bytes())
    d3["sha256"] = "0" * 64
    newest.chmod(0o644)
    newest.write_text(json.dumps(d3))
    assert any("not the one recorded when it was saved" in p for p in ps.check_chain(PID))


def test_scan_reports_plan_problems_beside_store_damage(tmp_path, em, pid, capsys):
    v1 = em.store.root / f"plan-{pid}-v0001.json"
    plan = em.store.load(pid)
    em.store.save(plan.model_copy(update={"version": 2, "parent_version": 1}))
    newest = em.store.root / f"plan-{pid}-v0002.json"
    newest.chmod(0o644)
    newest.unlink()
    code, out, _ = verify_code(em, capsys)
    assert code == 1 and "rolled back" in out and v1.exists()


def test_deleting_a_plan_removes_it_from_the_head_record(tmp_path, em, pid):
    other = em.plan(EXAMPLE, today=TODAY).plan.plan_id
    assert pid in em.store.heads() and other in em.store.heads()
    em.delete_plan(pid)
    assert pid not in em.store.heads() and other in em.store.heads() and pid not in em.store.heads_path.read_text()


# ---- log rotation (1.5)


def test_an_oversized_operational_log_rotates_and_only_the_newest_files_are_kept(em, monkeypatch):
    monkeypatch.setenv("EVENTMATE_LOG_MAX_BYTES", "1500")
    monkeypatch.setenv("EVENTMATE_LOG_KEEP", "3")
    log = em.logs.decisions
    from eventmate.schemas import AgentName, DecisionType

    for i in range(80):
        log.log(request_id=f"r{i}", agent=AgentName.MANAGER, decision_type=DecisionType.ASSESSMENT, summary="s" * 100, justification="j" * 100, confidence=1.0, source="rule:test")
    rotated = sorted(p.name for p in log.log_dir.glob(log.path.name + ".*"))
    assert 1 <= len(rotated) <= 3 and log.path.exists()  # rotated at the size limit, and no more than the newest three kept
    assert all(p.stat().st_size > 0 for p in log.log_dir.glob(log.path.name + ".*"))


def test_money_and_send_state_never_rotate(em, pid, monkeypatch):
    monkeypatch.setenv("EVENTMATE_LOG_MAX_BYTES", "1000")
    plan = em.store.latest(pid)
    for i in range(30):
        em.ledger.add(plan, "payment", C.CATERER, 1 + i, "n" * 150)
    assert ledger_file(em, pid).stat().st_size > 1000 and not list(ledger_file(em, pid).parent.glob("*.jsonl.*"))
    assert len(em.ledger.entries(pid)) == 32


def test_log_cleanup_never_touches_the_plan_directory(em, pid, monkeypatch):
    monkeypatch.setenv("EVENTMATE_LOG_KEEP", "1")
    before = sorted(p.name for p in em.store.root.rglob("*") if p.is_file())
    em.purge_logs(1)  # age-based purge of old logs, with nothing old
    from eventmate.retention import LOG_FILE

    assert LOG_FILE.fullmatch("decisions-x.jsonl.20260101T000000Z-1") and not LOG_FILE.fullmatch("ledger-x.jsonl")
    assert sorted(p.name for p in em.store.root.rglob("*") if p.is_file()) == before
