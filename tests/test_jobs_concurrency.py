"""Job leases (architecture L9, ported from openmuse's worker). One worker holds a job at a time; a crashed holder's lease runs out and another takes over.

Found first, by a test that failed: two workers running one job at the same moment each queued a follow-up for every vendor, so three vendors had six drafts. The
duplicate *send* was already prevented by the outreach claim; the extra drafts were orphans nobody would approve. The version check refuses the second save but
cannot undo what the second worker did before it."""

import sqlite3
import threading
import time
from datetime import timedelta

from eventmate.followthrough.jobs import JobStore
from eventmate.followthrough.worker import JobWorker
from tests import test_jobs_worker as jw

C, VENDORS, nudges, start = jw.C, jw.VENDORS, jw.nudges, jw.start
chan, clock, em, pid = jw.chan, jw.clock, jw.em, jw.pid  # the fixtures of the jobs suite, reused as they are


def store(tmp_path):
    return JobStore(tmp_path / "p")


# ------------------------------------------------------------------ the race


def test_two_workers_ticking_at_once_do_not_queue_a_nudge_twice(em, tmp_path, clock, chan, pid):
    em.grant_autoreply(pid, "Asha", hours=168)
    w = JobWorker(em, store(tmp_path), clock)
    job = start(w, pid, clock)
    w.approve_requests(job.job_id)
    w.tick()
    clock.advance(hours=25)  # the first nudge is due for each of the three vendors
    a, b = JobWorker(em, store(tmp_path), clock), JobWorker(em, store(tmp_path), clock)
    gate, reports = threading.Barrier(2), {}
    for worker in (a, b):
        real = worker.store.save

        def save(*args, _real=real, **kw):
            try:
                gate.wait(timeout=1.5)  # lets both get as far as the save when they can; the one that was refused the job never arrives
            except threading.BrokenBarrierError:
                pass
            return _real(*args, **kw)

        worker.store.save = save
    ts = [threading.Thread(target=lambda n=n, x=x: reports.__setitem__(n, x.tick())) for n, x in (("a", a), ("b", b))]
    [t.start() for t in ts]
    [t.join(60) for t in ts]
    assert sorted(len(r["ran"]) for r in reports.values()) == [0, 1], reports  # exactly one of them worked the job
    em.conversations.dispatch()
    assert len([d for d in em.outreach(pid) if d.kind == "followup"]) == 3  # one per vendor, no orphans
    assert len(nudges(chan)) == 3


# ------------------------------------------------------------------ the store


def test_only_one_claimant_gets_a_job_and_a_claim_is_refused_while_the_lease_runs(em, tmp_path, clock, pid):
    w = JobWorker(em, store(tmp_path), clock)
    job = start(w, pid, clock)
    s = w.store
    first = s.claim(job.job_id, clock(), 60)
    assert first and s.claim(job.job_id, clock(), 60) is None
    clock.advance(seconds=59)
    assert s.claim(job.job_id, clock(), 60) is None  # one second left
    clock.advance(seconds=2)
    second = s.claim(job.job_id, clock(), 60)
    assert second and second != first  # expired: taken over


def test_only_the_holder_can_renew_or_release_and_a_finished_job_cannot_be_claimed(em, tmp_path, clock, pid):
    w = JobWorker(em, store(tmp_path), clock)
    job = start(w, pid, clock)
    s = w.store
    mine = s.claim(job.job_id, clock(), 60)
    assert not s.renew(job.job_id, "someone-else", clock(), 60) and not s.release(job.job_id, "someone-else")
    assert s.renew(job.job_id, mine, clock(), 120) and s.lease_of(job.job_id)[0] == mine
    assert s.release(job.job_id, mine) and s.lease_of(job.job_id) == (None, None)
    w.cancel(job.job_id)
    assert s.get(job.job_id).status == "cancelled" and s.claim(job.job_id, clock(), 60) is None  # nothing to work on


def test_a_lease_does_not_stop_the_user_changing_the_job(em, tmp_path, clock, pid):
    w = JobWorker(em, store(tmp_path), clock)
    job = start(w, pid, clock)
    w.store.claim(job.job_id, clock(), 60)
    assert w.approve_requests(job.job_id)  # the user's approval is guarded by the version, not by the worker's lease


def test_a_database_made_before_leases_gains_the_columns_and_keeps_its_jobs(em, tmp_path, clock, pid):
    w = JobWorker(em, store(tmp_path), clock)
    job = start(w, pid, clock)
    db = sqlite3.connect(tmp_path / "p" / "jobs.db")
    db.executescript("ALTER TABLE jobs RENAME TO new_jobs; CREATE TABLE jobs (job_id TEXT PRIMARY KEY, status TEXT NOT NULL, wake_at TEXT, version INTEGER NOT NULL, data TEXT NOT NULL);"
                     "INSERT INTO jobs SELECT job_id, status, wake_at, version, data FROM new_jobs; DROP TABLE new_jobs;")
    db.commit()
    db.close()
    s = store(tmp_path)  # opens the old shape
    assert s.get(job.job_id).job_id == job.job_id and s.claim(job.job_id, clock(), 30)


# ------------------------------------------------------------------ crashes and long runs


def test_a_crashed_worker_is_taken_over_after_its_lease_and_not_before(em, tmp_path, clock, chan, pid):
    em.grant_autoreply(pid, "Asha", hours=168)
    w = JobWorker(em, store(tmp_path), clock, lease_s=120)
    job = start(w, pid, clock)
    w.approve_requests(job.job_id)
    w.tick()
    clock.advance(hours=25)
    dead = w.store.claim(job.job_id, clock(), 120)  # a worker took the job and died: it never saves and never releases
    assert dead
    survivor = JobWorker(em, store(tmp_path), clock, lease_s=120)
    assert survivor.tick()["ran"] == [] and nudges(chan) == []  # still held: it waits
    clock.advance(seconds=121)
    report = survivor.tick()
    assert report["ran"] == [job.job_id] and len(nudges(chan)) == 3  # the lease ran out: it does the work, once
    assert survivor.store.lease_of(job.job_id) == (None, None)


def test_a_long_run_keeps_its_lease_so_another_worker_cannot_start(em, tmp_path, pid):
    real = JobWorker(em, store(tmp_path))  # the real clock: the renewal runs on a side thread
    from eventmate.followthrough.worker import _utcnow

    job = real.start_job(pid, C.CATERER, VENDORS, "g", _utcnow() + timedelta(days=2), "Asha")
    real.store.save(real.store.get(job.job_id).model_copy(update={"wake_at": _utcnow() - timedelta(seconds=1), "status": "active"}), "test", "due", _utcnow())
    slow, rival, inside, seen = JobWorker(em, store(tmp_path), lease_s=0.3), JobWorker(em, store(tmp_path), lease_s=0.3), threading.Event(), []
    release = threading.Event()
    slow._run = lambda held, now: (inside.set(), release.wait(5), held)[-1]
    t = threading.Thread(target=slow.tick)
    t.start()
    assert inside.wait(5)
    for _ in range(4):  # well past the 0.3 s lease: only the renewals keep the rival out
        time.sleep(0.25)
        seen.append(rival.store.claim(job.job_id, _utcnow(), 0.3))
    release.set()
    t.join(10)
    assert seen == [None] * 4
    assert rival.store.claim(job.job_id, _utcnow(), 0.3)  # released when the run ended
