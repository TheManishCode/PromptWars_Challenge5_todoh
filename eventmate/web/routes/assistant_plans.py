"""Assistant, intake and plan creation routes."""

from __future__ import annotations

import json
import time
from flask import Response, abort, jsonify, request
from eventmate.arcade_pulse import pulse as _arcade_pulse
from eventmate.assistant.agent import AssistantError
from eventmate.assistant.approvals import ApprovalError
from eventmate.assistant.identity import Perm
from eventmate.web.base import (
    ApproveAnswerBody,
    AskBody,
    EmptyBody,
    FeedbackBody,
    IntakeBody,
    IntakeConfirmBody,
    PlanBody,
    PlanRefBody,
    SessionClearBody,
    _PLAN_ID,
)


PULSE_STREAM_MAX_S = 60.0  # a plan run takes seconds; a stream that follows one for a minute is abandoned
PULSE_START_WAIT_S = 5.0  # a page opens the stream just before it starts a run; with none under way and none started, an idle stream lets go of its connection
PULSE_POLL_S = 0.1


def register(app, k):
    P = k.P
    _err = k._err
    assistant = k.assistant
    body = k.body
    em = k.em
    lock = k.lock
    out = k.out
    pid = k.pid
    plan_response = k.plan_response
    today_fn = k.today_fn

    @app.get("/api/plans")
    def list_plans():
        with lock:
            rows = []
            for plan_id in em.store.list_plans():
                p = em.store.latest(plan_id)
                rows.append(
                    {
                        "plan_id": plan_id,
                        "version": p.version,
                        "event_type": p.request.event_type.value,
                        "city": p.request.city,
                        "event_date": p.request.event_date.isoformat(),
                        "guest_count": p.request.guest_count,
                        "total_cost": p.total_cost,
                        "budget_total": p.request.budget_total,
                    }
                )
        return jsonify(sorted(rows, key=lambda r: r["event_date"]))

    @app.errorhandler(ApprovalError)
    def _approval(exc):
        return _err(403 if str(exc).startswith("you are not allowed") else 409, str(exc))

    @app.errorhandler(AssistantError)
    def _assistant(exc):
        return _err(400, str(exc))

    @app.post("/api/assistant/ask")
    def assistant_ask():
        b = body(AskBody)
        return out(assistant.ask(b.question, b.plan_id, P()))  # no global lock: a slow model must not stall the app

    @app.get("/api/assistant/session")
    def assistant_session():
        """How much conversation the assistant is holding for you on this plan (your own only)."""
        plan_id = pid(request.args.get("plan_id", ""))
        return jsonify({"plan_id": plan_id, "turns": assistant.sessions.turns(plan_id, P().user_id)})

    @app.post("/api/assistant/session/clear")
    def assistant_session_clear():
        b = body(SessionClearBody)
        assistant.sessions.clear(b.plan_id, P().user_id)
        return jsonify({"plan_id": b.plan_id, "turns": 0})

    @app.get("/api/assistant/approvals")
    def assistant_approvals():
        plan_id = pid(request.args.get("plan_id", ""))
        em.store.latest(plan_id)
        return jsonify([json.loads(p.model_dump_json()) for p in assistant.queue.pending(plan_id)] if P().can(Perm.APPROVE) else [])

    @app.post("/api/assistant/approvals/<proposal_id>/<verdict>")
    def assistant_decide(proposal_id, verdict):
        if verdict not in ("approve", "reject") or not _PLAN_ID.fullmatch(proposal_id):
            abort(404)
        body(EmptyBody)
        with lock:
            p = assistant.decide(proposal_id, P(), verdict == "approve")
        return out(p)

    @app.post("/api/assistant/traces/<trace_id>/feedback")
    def assistant_feedback(trace_id):
        b = body(FeedbackBody)
        assistant.give_feedback(pid(trace_id), P(), b.value, b.comment)
        return jsonify({"ok": True})

    @app.get("/api/assistant/annotations")
    def assistant_annotations():
        plan_id = pid(request.args.get("plan_id", ""))
        if not P().can(Perm.APPROVE):
            return jsonify([])
        plan = em.store.latest(plan_id)
        state = f"{plan.version}:{len(em.ledger_entries(plan_id))}"
        return jsonify([{**json.loads(a.model_dump_json()), "stale": a.state != state} for a in assistant.annotations.list(plan_id)])

    @app.post("/api/assistant/annotations")
    def assistant_annotate():
        b = body(ApproveAnswerBody)
        a = assistant.approve_answer(b.trace_id, P(), b.answer)
        return Response(a.model_dump_json(), 201, mimetype="application/json")

    @app.post("/api/assistant/annotations/<annotation_id>/delete")
    def assistant_unannotate(annotation_id):
        b = body(PlanRefBody)
        if not P().can(Perm.APPROVE):
            return _err(403, "you are not allowed to remove approved answers")
        if not assistant.annotations.remove(pid(annotation_id), b.plan_id):
            abort(404)
        return jsonify({"ok": True})

    @app.get("/api/assistant/traces/<trace_id>")
    def assistant_trace(trace_id):
        if not P().can(Perm.TRACE_READ):
            return _err(403, "you are not allowed to read traces")
        t = assistant.traces.find(pid(trace_id))
        if t is None:
            abort(404)
        return Response(t.model_dump_json(), mimetype="application/json")

    @app.post("/api/intake")
    def intake_turn():
        b = body(IntakeBody)
        with lock:
            turn = em.intake_turn(b.messages, b.draft, today=today_fn(), expecting=b.expecting, answer=b.answer)
        return out(turn)

    @app.post("/api/intake/confirm")
    def intake_confirm():
        b = body(IntakeConfirmBody)
        _arcade_pulse.begin_run("intake confirm: full plan run")
        try:
            with lock:
                outcome = em.confirm_intake(b.draft, b.raw_text, today=today_fn(), guest_list_id=b.guest_list_id, source=b.source)
        except Exception as exc:
            _arcade_pulse.finish(False, str(exc))
            raise
        _arcade_pulse.finish(outcome.plan is not None, "; ".join(v.detail for v in outcome.violations))
        return plan_response(outcome)

    @app.post("/api/plans")
    def create_plan():
        b = body(PlanBody)
        _arcade_pulse.begin_run("plan: " + b.request[:100])
        try:
            with lock:
                outcome = em.plan(b.request, today=today_fn(), guest_list_id=b.guest_list_id)
        except Exception as exc:
            _arcade_pulse.finish(False, str(exc))
            raise
        _arcade_pulse.finish(outcome.plan is not None, "; ".join(v.detail for v in outcome.violations))
        return plan_response(outcome)

    @app.get("/api/pulse/stream")
    def pulse_stream():
        """Server-sent events: the run's stage snapshot each time it changes, so a page shows the real pipeline moving while a plan is made. It ends when a run that
        began after the stream opened (or was already running) finishes, or after ``PULSE_STREAM_MAX_S``. It reads this workspace's pulse only and takes no lock."""
        if not k.stream_slots.acquire(blocking=False):
            return _err(429, "too many streams are open; try again in a moment")
        source = _arcade_pulse.current()

        def stream():
            try:
                first, last, deadline, beat = None, None, time.monotonic() + PULSE_STREAM_MAX_S, time.monotonic()
                opened = beat
                while time.monotonic() < deadline:
                    snap = source.snapshot()
                    if first is None:
                        first = (snap["run_id"], snap["state"] == "running")
                    key = (snap["run_id"], snap["state"], tuple(s["state"] for s in snap["stages"]))
                    if key != last:
                        last = key
                        yield f"event: progress\ndata: {json.dumps(snap)}\n\n"
                    if snap["state"] in ("done", "failed") and (snap["run_id"] > first[0] or first[1]):
                        return
                    if snap["run_id"] == first[0] and not first[1] and time.monotonic() - opened > PULSE_START_WAIT_S:  # no run under way and none has started
                        yield "event: timeout\ndata: {}\n\n"
                        return
                    if time.monotonic() - beat > 15:
                        beat = time.monotonic()
                        yield ": keep-alive\n\n"
                    time.sleep(PULSE_POLL_S)
                yield "event: timeout\ndata: {}\n\n"
            finally:
                k.stream_slots.release()

        return Response(stream(), mimetype="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/api/arcade/pulse")
    def arcade_pulse():
        # Read-only observability of the in-flight run; takes no to;doh-the event planner lock
        # so the page can watch while the (lock-held) plan call is running.
        return jsonify(_arcade_pulse.snapshot())
