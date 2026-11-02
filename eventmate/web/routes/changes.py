"""Changes in the user's words: formalize a message into a delta to confirm, then apply the delta the user confirmed. Nothing changes until the second call."""

from __future__ import annotations

from eventmate.web.base import ChangeApplyBody, ChangeBody


def register(app, k):
    em = k.em
    lock = k.lock
    out = k.out
    pid = k.pid

    @app.post("/api/plans/<plan_id>/changes")
    def change_turn(plan_id):
        """Read a message about a change into a delta and the questions still open. A model may help; its reply is allow-listed and grounded. Nothing is changed."""
        plan_id, b = pid(plan_id), k.body(ChangeBody)
        with lock:
            turn = em.change_turn(plan_id, b.message, k.today_fn())
        return out(turn)

    @app.post("/api/plans/<plan_id>/changes/apply")
    def change_apply(plan_id):
        """Apply a delta the user confirmed, as one new plan version, with the finished and under-way work the user reported held where it is."""
        plan_id, b = pid(plan_id), k.body(ChangeApplyBody)
        with lock:
            res = em.apply_change(plan_id, b.delta, b.done, b.in_flight, k.today_fn())
        return out(res, 200 if res.status == "replanned" else (409 if res.status == "needs_decision" else 422))
