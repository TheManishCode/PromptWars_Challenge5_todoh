"""Proposals waiting for a click survive a restart; a decision is recorded before the action runs; an action is applied at most once."""

import multiprocessing
import stat
from datetime import datetime, timedelta, timezone

import pytest

from eventmate.assistant.approvals import APPLY_LEASE, MAX_PENDING_PER_PLAN, PROPOSAL_TTL, UNKNOWN, ApprovalError, ApprovalQueue
from eventmate.assistant.identity import LOCAL_ORGANIZER, Perm, Principal, Role
from eventmate.assistant.trace import ProposalLog

T0 = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
ARGS = {"kind": "payment", "category": "venue", "amount": 5000}


class Crash(BaseException):
    """The process dying mid-apply: not an Exception, so nothing in the queue's own error handling catches it."""


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def paths(tmp_path):
    return tmp_path / "approvals.db", tmp_path


def open_queue(paths, clock, run="r1"):
    db, root = paths
    return ApprovalQueue(ProposalLog(root, run_id=run), db=db, now=clock)


def propose(q, plan="p1", version=1, amount=5000, summary="record a payment"):
    return q.propose(plan_id=plan, plan_version=version, proposer="u", tool="propose_ledger_entry", args={**ARGS, "amount": amount}, summary=summary)


def decide(q, pid, approve=True, apply=None, latest=1, who=LOCAL_ORGANIZER):
    return q.decide(pid, who, approve, lambda plan: latest, apply or (lambda p: "done"), Perm.LEDGER_WRITE)


def test_a_pending_proposal_is_still_pending_after_a_restart(paths, clock):
    p = propose(open_queue(paths, clock))
    again = open_queue(paths, clock)  # the app was thrown away and started again on the same directory
    assert [x.id for x in again.pending("p1")] == [p.id]
    got = again.get(p.id)
    assert (got.plan_version, got.tool, got.args, got.summary, got.content_sha256, got.status) == (1, "propose_ledger_entry", ARGS, "record a payment", p.content_sha256, "pending")
    assert decide(again, p.id).status == "approved"


def test_settled_states_and_their_results_survive_a_restart(paths, clock):
    q = open_queue(paths, clock)
    ok, no, stale = propose(q, amount=1), propose(q, amount=2), propose(q, amount=3)
    assert decide(q, ok.id, apply=lambda p: "recorded INR 1").result == "recorded INR 1"
    assert decide(q, no.id, approve=False).status == "rejected"
    assert decide(q, stale.id, latest=2).status == "stale"
    again = open_queue(paths, clock)
    assert [(again.get(x.id).status, again.get(x.id).decided_by) for x in (ok, no, stale)] == [("approved", LOCAL_ORGANIZER.user_id), ("rejected", LOCAL_ORGANIZER.user_id), ("stale", LOCAL_ORGANIZER.user_id)]
    assert again.get(ok.id).status == "approved" and again.get(ok.id).result == "recorded INR 1" and again.get(no.id).status == "rejected" and again.get(stale.id).status == "stale"
    for x in (ok, no, stale):
        with pytest.raises(ApprovalError, match="already"):
            decide(again, x.id)
    assert again.pending("p1") == []


def test_the_decision_is_written_before_the_action_runs(paths, clock):
    q = open_queue(paths, clock)
    p = propose(q)
    seen = {}

    def apply(prop):
        other = open_queue(paths, clock, "r2")  # another process looking at the file while the action is running
        seen["state"] = other.get(p.id).status
        seen["decided_by"] = other.get(p.id).decided_by
        return "ok"

    decide(q, p.id, apply=apply)
    assert seen == {"state": "approving", "decided_by": LOCAL_ORGANIZER.user_id}


def test_a_crash_between_the_decision_and_the_result_is_never_applied_again(paths, clock):
    q = open_queue(paths, clock)
    p = propose(q)
    applied = []

    def apply(prop):
        applied.append(prop.id)
        raise Crash()  # the process dies after starting the action, before its result is written

    with pytest.raises(Crash):
        decide(q, p.id, apply=apply)
    restarted = open_queue(paths, clock)
    got = restarted.get(p.id)
    assert got.status == "approving" and got.decided_by == LOCAL_ORGANIZER.user_id  # the decision was not lost, and it is not yet known how the action ended
    with pytest.raises(ApprovalError, match="already approving"):
        decide(restarted, p.id, apply=applied.append)  # nobody can decide it a second time meanwhile
    clock.t += APPLY_LEASE + timedelta(seconds=1)
    later = open_queue(paths, clock)
    got = later.get(p.id)
    assert got.status == "outcome_unknown" and got.result == UNKNOWN and got.decided_by == LOCAL_ORGANIZER.user_id
    with pytest.raises(ApprovalError, match="already outcome_unknown"):
        decide(later, p.id, apply=lambda x: applied.append("again"))
    assert applied == [p.id]  # applied once, never twice
    events = [e.event for e in later.log.read() if e.request_id == p.id]
    assert events == ["proposed", "outcome_unknown"]


def test_the_lease_is_exact_and_an_ordinary_failure_is_a_failure_not_an_unknown(paths, clock):
    q = open_queue(paths, clock)
    p = propose(q)
    with pytest.raises(Crash):
        decide(q, p.id, apply=lambda x: (_ for _ in ()).throw(Crash()))
    clock.t = T0 + APPLY_LEASE
    assert q.get(p.id).status == "approving"  # exactly at the lease: still its own time
    clock.t = T0 + APPLY_LEASE + timedelta(microseconds=1)
    assert q.get(p.id).status == "outcome_unknown"
    f = propose(q, amount=7)
    out = decide(q, f.id, apply=lambda x: (_ for _ in ()).throw(ValueError("a hard constraint refused it")))
    assert out.status == "failed" and "hard constraint" in out.result  # the action ran and said no: that is known
    assert open_queue(paths, clock).get(f.id).status == "failed"


def _race(args):
    db, root, pid, who_id, out = args
    q = ApprovalQueue(ProposalLog(root, run_id=f"race-{who_id}"), db=db)

    def apply(p):
        with open(out, "a") as fh:
            fh.write(f"{who_id}\n")
        return "applied"

    try:
        q.decide(pid, LOCAL_ORGANIZER, True, lambda plan: 1, apply, Perm.LEDGER_WRITE)
        return "won"
    except ApprovalError as exc:
        return str(exc)


def test_two_processes_deciding_the_same_proposal_one_wins_and_it_is_applied_once(paths, clock, tmp_path):
    p = propose(open_queue(paths, clock))
    out = tmp_path / "applied.txt"
    ctx = multiprocessing.get_context("fork")
    with ctx.Pool(4) as pool:
        results = pool.map(_race, [(paths[0], paths[1], p.id, i, str(out)) for i in range(4)])
    assert results.count("won") == 1 and [r for r in results if r != "won"] and all("already" in r for r in results if r != "won")
    assert len(out.read_text().split()) == 1  # the action ran exactly once
    assert open_queue(paths, clock).get(p.id).status == "approved"


def test_a_proposal_made_for_an_older_plan_version_is_refused_after_a_restart(paths, clock):
    p = propose(open_queue(paths, clock), version=1)
    again = open_queue(paths, clock)
    ran = []
    out = decide(again, p.id, apply=ran.append, latest=2)  # the plan moved on while the app was off
    assert out.status == "stale" and "plan changed" in out.result and ran == []


def test_the_hash_binding_is_checked_against_what_was_stored(paths, clock):
    q = open_queue(paths, clock)
    p = propose(q)
    with q._db() as c:
        c.execute("UPDATE proposals SET args = ? WHERE id = ?", ('{"amount": 999999, "category": "venue", "kind": "payment"}', p.id))
    with pytest.raises(ApprovalError, match="hash"):
        decide(open_queue(paths, clock), p.id)
    assert open_queue(paths, clock).get(p.id).status == "pending"


def test_a_proposal_nobody_decided_expires_and_cannot_be_approved(paths, clock):
    q = open_queue(paths, clock)
    p = propose(q)
    clock.t = T0 + PROPOSAL_TTL - timedelta(seconds=1)
    assert [x.id for x in open_queue(paths, clock).pending("p1")] == [p.id]
    clock.t = T0 + PROPOSAL_TTL
    again = open_queue(paths, clock)
    assert again.pending("p1") == [] and again.get(p.id).status == "expired"
    with pytest.raises(ApprovalError, match="already expired"):
        decide(again, p.id)
    assert [e.event for e in again.log.read() if e.request_id == p.id] == ["proposed", "expired"]
    assert propose(again).id != p.id  # the same request can be made again


def test_the_same_request_is_one_proposal_even_across_a_restart_and_the_pending_cap_survives_too(paths, clock):
    q = open_queue(paths, clock)
    first = propose(q)
    assert propose(open_queue(paths, clock)).id == first.id
    for i in range(MAX_PENDING_PER_PLAN - 1):
        propose(q, amount=100 + i)
    with pytest.raises(ApprovalError, match="too many"):
        propose(open_queue(paths, clock), amount=999)
    assert propose(open_queue(paths, clock), plan="p2").plan_id == "p2"  # the cap is per plan


def test_only_someone_allowed_to_decide_can_decide(paths, clock):
    q = open_queue(paths, clock)
    p = propose(q)
    for who in (Principal(user_id="g", roles=(Role.GUEST,)), Principal(user_id="h", roles=(Role.HELPER,))):
        with pytest.raises(ApprovalError, match="not allowed"):
            decide(q, p.id, who=who)
    assert q.get(p.id).status == "pending"


def test_deleting_a_plan_removes_its_proposals_from_the_file(paths, clock):
    q = open_queue(paths, clock)
    p = propose(q, plan="gone", amount=424242)
    keep = propose(q, plan="kept", amount=1)
    assert b"424242" in paths[0].read_bytes() or b"424242" in (paths[0].parent / "approvals.db-wal").read_bytes()
    assert q.forget_plan("gone") == 1 and q.forget_plan("gone") == 0
    blob = b"".join(f.read_bytes() for f in paths[1].glob("approvals.db*"))
    assert b"424242" not in blob
    again = open_queue(paths, clock)
    assert again.pending("gone") == [] and [x.id for x in again.pending("kept")] == [keep.id]
    with pytest.raises(ApprovalError, match="unknown"):
        again.get(p.id)


def test_the_file_is_private_and_a_staging_queue_has_no_file(paths, clock, tmp_path):
    open_queue(paths, clock)
    assert stat.S_IMODE(paths[0].stat().st_mode) == 0o600
    staging = ApprovalQueue(ProposalLog(tmp_path / "x", run_id="s"))
    p = propose(staging)
    assert [x.id for x in staging.pending("p1")] == [p.id] and not list((tmp_path / "x").glob("*.db"))
    assert ApprovalQueue(ProposalLog(tmp_path / "y", run_id="s")).pending("p1") == []  # in memory only: another queue never sees it


def test_the_assistant_uses_a_durable_queue_and_forgets_a_deleted_plan(tmp_path):
    from eventmate.assistant.agent import Assistant
    from eventmate.service import EventMate
    from tests.conftest import EXAMPLE, TODAY
    from tests.test_assistant import Scripted, answer, tool

    def build():
        em = EventMate(log_dir=tmp_path / "logs", plan_dir=tmp_path / "plans", run_id="durable")
        model = Scripted(tool("propose_ledger_entry", kind="payment", category="venue", amount=1000), answer("filed", "propose_ledger_entry"))
        return em, Assistant(em, {"large": model, "small": model})

    em, a = build()
    pid = em.plan(EXAMPLE, today=TODAY).plan.plan_id
    [prop] = a.ask("record a payment of 1000 for the venue", pid, LOCAL_ORGANIZER).proposals
    em2, a2 = build()  # restart
    assert [x.id for x in a2.queue.pending(pid)] == [prop.id]
    assert a2.decide(prop.id, LOCAL_ORGANIZER, True).status == "approved" and len(em2.ledger_entries(pid)) == 1
    a2.forget_plan(pid)
    with pytest.raises(ApprovalError):
        a2.queue.get(prop.id)
