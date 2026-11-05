"""Deleting a plan and expiring logs: exact paths, nothing readable left behind, other plans and safety lists untouched."""

import json
import os
import time
from datetime import timedelta
from pathlib import Path

import pytest

from eventmate.assistant.agent import Assistant
from eventmate.messaging.channels import Inbound
from eventmate.retention import PER_PLAN_JSONL, PlanBusy, delete_plan_files, purge_old_logs
from eventmate.schemas import VendorCategory as C
from eventmate.service import EventMate
from eventmate.web.app import create_app
from tests.conftest import EXAMPLE, TODAY
from tests.test_conversations import Clock, FakeChannel

SECRET_CONTACT = "sales@annapurna.in"


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def chans():
    return {"email": FakeChannel("email"), "whatsapp": FakeChannel("whatsapp")}


@pytest.fixture
def em(tmp_path, chans, clock):
    e = EventMate(log_dir=tmp_path / "logs", plan_dir=tmp_path / "plans", run_id="ret", channels=chans)
    e.outreach_store.now = e.conversations.now = e.jobs_clock = clock
    return e


def busy_plan(em, chans, clock):
    """A plan that has touched every per-plan store the product can reach without a model or the network."""
    pid = em.plan(EXAMPLE, today=TODAY).plan.plan_id
    em.ledger_add(pid, "payment", C.VENUE, 1000, "deposit")
    d = em.draft_outreach(pid, C.CATERER, "email", SECRET_CONTACT, "Asha")
    d = em.approve_outreach(pid, d.draft_id, d.content_hash)
    em.grant_autoreply(pid, sender_name="Asha")
    chans["email"].inbox = [Inbound(channel="email", sender=d.recipient, external_id="<v1>", received_at=clock() + timedelta(minutes=10), body="Yes, free that day. Rs 650 per plate.")]
    em.poll_conversations()
    em.jobs.start_job(pid, C.CATERER, {"cat-pune-annapurna": ("email", SECRET_CONTACT)}, "get a quote", clock() + timedelta(days=365), "Asha", min_quotes=1)
    em.ideas.generate(em.store.latest(pid), ())
    em.save_lead(pid, kind="caterer", url="https://caterer.example/menu", domain="caterer.example", label="thali", unit="per_plate", low_inr=500, high_inr=700)
    em.shortlist.add_entry(pid, C.CATERER, "ext_" + "a" * 24)
    return pid


def plan_root_files(em):
    return [p for p in em.store.root.rglob("*") if p.is_file() and not p.name.startswith(".")]


def mentions(em, needle):
    """Files that name or contain ``needle``, apart from the tombstone, which is the one place the id of a deleted plan is allowed to remain."""
    return [str(p.relative_to(em.store.root)) for p in plan_root_files(em) if p.name != "tombstones.jsonl" and needle.encode() in p.name.encode() + p.read_bytes()]


def test_deleting_a_plan_leaves_nothing_readable_and_does_not_touch_another_plan(em, chans, clock):
    a = busy_plan(em, chans, clock)
    b = em.plan(EXAMPLE, today=TODAY).plan.plan_id
    em.ledger_add(b, "payment", C.VENUE, 5, "b's own entry")
    assert mentions(em, a) and (em.store.root / "jobs.db").exists()
    removed = em.delete_plan(a)
    assert removed["plan_versions"] == 1 and removed["jobs"] == 1 and removed["message_and_ledger_files"] >= 6
    assert mentions(em, a) == [], "a file still names or contains the deleted plan"
    assert (em.store.root / "jobs.db").read_bytes().count(SECRET_CONTACT.encode()) == 0  # deleted SQLite rows are overwritten, not just unlinked
    with pytest.raises(FileNotFoundError):
        em.store.latest(a)
    assert em.store.latest(b).plan_id == b and em.ledger_entries(b) and a not in em.store.list_plans()


def test_deleting_keeps_the_do_not_contact_list(em, chans, clock):
    pid = busy_plan(em, chans, clock)
    em.suppressions.add("email", "stop@vendor.example", "stop_request", "ref", "test")
    em.delete_plan(pid)
    assert em.suppressions.active_for("email", "stop@vendor.example") is not None  # forgetting a plan must never let a refused vendor be messaged again


def test_a_failed_delete_can_simply_be_run_again(em, chans, clock, monkeypatch):
    pid = busy_plan(em, chans, clock)
    real = em.store.delete
    monkeypatch.setattr(em.store, "delete", lambda p: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        em.delete_plan(pid)
    assert pid in em.store.list_plans()  # the plan still lists: the versions go last
    monkeypatch.setattr(em.store, "delete", real)
    assert em.delete_plan(pid)["plan_versions"] == 1 and mentions(em, pid) == []


def test_a_plan_with_a_message_being_sent_is_refused_and_nothing_is_removed(em, chans, clock, monkeypatch):
    pid = busy_plan(em, chans, clock)
    real = em.outreach_store.list
    monkeypatch.setattr(em.outreach_store, "list", lambda p: [type("D", (), {"status": "sending"})()])
    with pytest.raises(PlanBusy):
        em.delete_plan(pid)
    monkeypatch.setattr(em.outreach_store, "list", real)
    assert em.store.latest(pid) and mentions(em, pid)


def test_unknown_and_hostile_ids_fail_before_anything_is_touched(em):
    for bad in ("nope", "../x", "a/b", "", "x" * 65):
        with pytest.raises((FileNotFoundError, ValueError)):
            em.delete_plan(bad)


def test_files_are_addressed_by_exact_path_so_confusable_ids_never_collide(tmp_path):
    for sub, channel in PER_PLAN_JSONL:
        (tmp_path / sub).mkdir(exist_ok=True)
        for pid in ("b", "a-b", "b-c"):
            (tmp_path / sub / f"{channel}-{pid}.jsonl").write_text("x")
    assert delete_plan_files(tmp_path, "b") == len(PER_PLAN_JSONL)
    left = sorted(p.name for p in tmp_path.rglob("*.jsonl"))
    assert all("-b.jsonl" not in n or "a-b" in n for n in left) and len(left) == 2 * len(PER_PLAN_JSONL)
    assert delete_plan_files(tmp_path, "b") == 0  # idempotent
    with pytest.raises(ValueError):
        delete_plan_files(tmp_path, "../b")


def test_a_symlink_in_the_way_is_removed_not_followed(tmp_path):
    target = tmp_path / "precious.txt"
    target.write_text("keep me")
    (tmp_path / "ledger").mkdir()
    os.symlink(target, tmp_path / "ledger" / "ledger-p1.jsonl")
    delete_plan_files(tmp_path, "p1")
    assert target.read_text() == "keep me" and not (tmp_path / "ledger" / "ledger-p1.jsonl").exists()


# ------------------------------------------------------------------ logs


def make_log(d: Path, name: str, age_days: float):
    p = d / name
    p.write_text("{}\n")
    t = time.time() - age_days * 86400
    os.utime(p, (t, t))
    return p


def test_old_logs_are_purged_and_recent_or_foreign_files_are_not(tmp_path):
    old = make_log(tmp_path, "decisions-run1.jsonl", 100)
    old2 = make_log(tmp_path, "assistant_traces-run2.jsonl", 91)
    edge = make_log(tmp_path, "tool_calls-run3.jsonl", 89)
    fresh = make_log(tmp_path, "llm_traces-run4.jsonl", 1)
    other = make_log(tmp_path, "notes.txt", 400)
    outside = tmp_path.parent / "outside-target.jsonl"
    outside.write_text("x")
    os.symlink(outside, tmp_path / "decisions-link.jsonl")
    assert sorted(purge_old_logs(tmp_path, 90)) == ["assistant_traces-run2.jsonl", "decisions-run1.jsonl"]
    assert not old.exists() and not old2.exists() and edge.exists() and fresh.exists() and other.exists() and outside.exists()
    with pytest.raises(ValueError):
        purge_old_logs(tmp_path, 0)


def test_the_retention_period_comes_from_the_environment_with_a_90_day_default(tmp_path, monkeypatch):
    make_log(tmp_path, "decisions-a.jsonl", 40)
    monkeypatch.delenv("EVENTMATE_LOG_RETENTION_DAYS", raising=False)
    assert purge_old_logs(tmp_path) == []
    monkeypatch.setenv("EVENTMATE_LOG_RETENTION_DAYS", "30")
    assert purge_old_logs(tmp_path) == ["decisions-a.jsonl"]


# ------------------------------------------------------------------ API, CLI, assistant


@pytest.fixture
def app_client(em):
    assistant = Assistant(em, {})
    return create_app(em, today_fn=lambda: TODAY, assistant=assistant).test_client(), assistant


def delete(client, url, body, token=True):
    h = {"Content-Type": "application/json"}
    if token:
        h["X-EventMate-CSRF"] = client.get("/api/session").get_json()["csrf"]
    return client.delete(url, data=json.dumps(body), headers=h)


def test_the_api_needs_csrf_and_the_plan_id_repeated_then_forgets_the_assistant_too(app_client, em):
    client, assistant = app_client
    pid = em.plan(EXAMPLE, today=TODAY).plan.plan_id
    assistant.sessions.record(pid, "local", "how many guests?", "120 guests", "answered")
    assistant.cache._store["k"] = (time.monotonic(), "answer")
    assert delete(client, f"/api/plans/{pid}", {"confirm": pid}, token=False).status_code == 403
    assert delete(client, f"/api/plans/{pid}", {}).status_code == 422
    assert delete(client, f"/api/plans/{pid}", {"confirm": "other"}).status_code == 400
    assert delete(client, f"/api/plans/{pid}", {"confirm": pid, "extra": 1}).status_code == 422
    assert em.store.latest(pid)  # every refusal left the plan alone
    r = delete(client, f"/api/plans/{pid}", {"confirm": pid})
    assert r.status_code == 200 and r.get_json()["deleted"] == pid and r.get_json()["removed"]["plan_versions"] == 1
    assert client.get(f"/api/plans/{pid}").status_code == 404 and delete(client, f"/api/plans/{pid}", {"confirm": pid}).status_code == 404
    assert assistant.sessions.turns(pid, "local") == 0 and assistant.cache._store == {}


def test_a_busy_plan_is_a_409(app_client, em, monkeypatch):
    client, _ = app_client
    pid = em.plan(EXAMPLE, today=TODAY).plan.plan_id
    monkeypatch.setattr(em.outreach_store, "list", lambda p: [type("D", (), {"status": "sending"})()])
    assert delete(client, f"/api/plans/{pid}", {"confirm": pid}).status_code == 409
    assert em.store.latest(pid)


def test_the_cli_deletes_with_confirm_and_purges_logs(tmp_path, capsys):
    from eventmate.__main__ import main

    base = ["--today", "2026-10-30", "--log-dir", str(tmp_path / "logs"), "--plan-dir", str(tmp_path / "plans")]
    assert main(base + ["plan", EXAMPLE]) == 0
    pid = capsys.readouterr().err.split("plan id: ")[1].split(" ")[0]
    assert main(base + ["delete-plan", pid, "--confirm", "wrong"]) == 2
    capsys.readouterr()
    assert main(base + ["delete-plan", pid, "--confirm", pid]) == 0 and json.loads(capsys.readouterr().out)["plan_versions"] == 1
    make_log(tmp_path / "logs", "decisions-ancient.jsonl", 200)
    assert main(base + ["purge-logs", "--days", "90"]) == 0 and "removed 1" in capsys.readouterr().out


def test_the_deletion_is_recorded_without_the_plan_contents(em):
    pid = em.plan(EXAMPLE, today=TODAY).plan.plan_id
    em.delete_plan(pid)
    last = [e for e in em.logs.decisions.read() if e.request_id == "retention"][-1]
    assert pid not in last.summary + last.justification and "plan version" in last.justification and EXAMPLE[:20] not in last.summary + last.justification  # the id lives only in the tombstone
    assert '"kind": "plan"' in (em.store.root / "tombstones.jsonl").read_text() and pid in (em.store.root / "tombstones.jsonl").read_text()


def test_the_tombstone_holds_only_the_kind_the_id_and_the_time(em):
    import json as _json

    pid = em.plan(EXAMPLE, today=TODAY).plan.plan_id
    em.ledger_add(pid, "payment", C.VENUE, 4321, "SECRET-NOTE-QQ")
    em.delete_plan(pid)
    lines = [_json.loads(x) for x in (em.store.root / "tombstones.jsonl").read_text().splitlines()]
    assert len(lines) == 1 and set(lines[0]) == {"kind", "id", "deleted_at"} and lines[0]["kind"] == "plan" and lines[0]["id"] == pid
    text = (em.store.root / "tombstones.jsonl").read_text()
    assert "SECRET-NOTE-QQ" not in text and "4321" not in text and EXAMPLE[:20] not in text


def test_deleting_a_plan_also_removes_its_lines_from_the_logs_and_keeps_the_others(em):
    a = em.plan(EXAMPLE, today=TODAY).plan.plan_id
    b = em.plan(EXAMPLE, today=TODAY).plan.plan_id
    log_dir = em.logs.decisions.log_dir
    assert any(a.encode() in p.read_bytes() for p in log_dir.glob("*.jsonl"))
    em.delete_plan(a)
    assert not any(a.encode() in p.read_bytes() for p in log_dir.glob("*.jsonl"))
    assert any(b.encode() in p.read_bytes() for p in log_dir.glob("*.jsonl"))  # the other plan's history is intact
    assert all(oct(p.stat().st_mode & 0o777) == "0o600" for p in log_dir.glob("*.jsonl"))
