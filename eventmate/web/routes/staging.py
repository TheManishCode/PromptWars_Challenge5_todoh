"""A brief between the reader and the planner: read it back, correct it, commit it, follow the build, and read the finished plan as a graph.

The reader's draft waits on the server in the pending-validation state (``planning/staging.py``). Nothing here starts the planner except ``commit``, and commit hands the approved draft to the
build queue and returns at once; the page follows the build by polling ``status``. Reading and polling never take the workspace lock, so they answer while a build runs.
"""

from __future__ import annotations

import json

from flask import g, jsonify
from eventmate.arcade_pulse import pulse as _arcade_pulse
from eventmate.planning import plan_graph
from eventmate.web.base import IntakeCommitBody, IntakeEditBody
from eventmate.web.commit_queue import CommitQueue


def register(app, k):
    em, body, lock, out, pid, today_fn, registry, queue = k.em, k.body, k.lock, k.out, k.pid, k.today_fn, k.registry, k.commit_queue

    def status_of(row) -> dict:
        return {"staged_id": row.staged_id, "state": row.state, "version": row.version, "draft_hash": row.draft_hash, "result": row.result, "error": row.error, "expires_at": row.expires_at.isoformat()}

    @app.get("/api/intake/<staged_id>")
    def staged_get(staged_id):
        """The brief as it is stored, shaped as a review card turn: a reloaded page or another device picks it up here."""
        return out(em.staged_turn(staged_id, today_fn()))

    @app.patch("/api/intake/<staged_id>/fields")
    def staged_edit(staged_id):
        b = body(IntakeEditBody)
        with lock:
            turn = em.edit_staged(staged_id, b.fields, b.version, today_fn())
        return out(turn)

    @app.post("/api/intake/<staged_id>/commit")
    def staged_commit(staged_id):
        """The user's "done". Refused unless the brief is ready and is the one they reviewed. Pending becomes processing once; the build runs on the queue."""
        b = body(IntakeCommitBody)
        if not queue.has_room():
            return k._err(429, "plans are being built for other people right now; try again in a moment", code="busy")
        row, started = em.commit_staged(staged_id, b.version, b.draft_hash, today_fn())
        if started:
            ws, today, em_ = g.ws, today_fn(), em._get_current_object() if hasattr(em, "_get_current_object") else em
            if registry is not None:
                registry.acquire(ws)  # a workspace is never dropped from memory while its build runs

            def job() -> None:
                _arcade_pulse.begin_run("intake commit: full plan run")
                done = None
                try:
                    with ws.lock:
                        done = em_.build_staged(staged_id, today, b.guest_list_id)
                finally:
                    _arcade_pulse.finish(bool(done and done.state == "done"), (done.error if done else "the build did not finish"))
                    if registry is not None:
                        registry.release(ws)

            if not queue.submit(job):  # the places were taken between the check and now
                if registry is not None:
                    registry.release(ws)
                em_.staging.finish(staged_id, False, error="Plans are being built for other people right now. Press Done again in a moment.")
                row = em_.staging.require(staged_id)
        return jsonify(status_of(row)), (202 if row.state == "processing" else 200)

    @app.get("/api/intake/<staged_id>/status")
    def staged_status(staged_id):
        return jsonify(status_of(em.staging.require(staged_id)))

    @app.post("/api/intake/<staged_id>/reopen")
    def staged_reopen(staged_id):
        """A build that failed goes back to pending, so the brief can be corrected and sent again."""
        k.no_body_or_empty_object()
        return out(em.reopen_staged(staged_id, today_fn()))

    @app.get("/api/plans/<plan_id>/graph")
    def plan_graph_get(plan_id):
        """The stored plan as nodes and edges: the event, its tasks, the vendors chosen and passed over, and the hard checks."""
        with lock:
            plan = em.store.latest(pid(plan_id))
        return jsonify(plan_graph.build(plan))
