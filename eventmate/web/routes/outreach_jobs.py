"""Vendor messages, inbox, standing permission and follow-through jobs."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, UTC
from flask import g, jsonify
from eventmate.followthrough.worker import digest
from eventmate.web.base import (
    ApproveBody,
    AutoreplyBody,
    ConfirmClaimBody,
    DeletePlanBody,
    EmptyBody,
    FollowupBody,
    JobBody,
    OutreachBody,
    RequestAllBody,
    ResolveItemBody,
)


def register(app, k):
    assistant = k.assistant
    body = k.body
    em = k.em
    lock = k.lock
    out = k.out
    pid = k.pid
    registry = k.registry
    shared = k.shared

    @app.get("/api/plans/<plan_id>/outreach")
    def outreach_get(plan_id):
        plan_id = pid(plan_id)
        with lock:
            items = em.outreach(plan_id)
        return jsonify([json.loads(x.model_dump_json()) for x in items])

    @app.post("/api/plans/<plan_id>/outreach")
    def outreach_add(plan_id):
        """Write a quote request and a link to send it from your own mail or WhatsApp. Nothing is sent from here."""
        plan_id, b = pid(plan_id), body(OutreachBody)
        with lock:
            draft = em.draft_outreach(plan_id, **b.model_dump())
        return out(draft, 201)

    @app.post("/api/plans/<plan_id>/outreach/request-all")
    def outreach_request_all(plan_id):
        """One proposal per vendor with a saved contact. Nothing is sent until each one is approved."""
        plan_id, b = pid(plan_id), body(RequestAllBody)
        with lock:
            made, skipped = em.request_all_quotes(plan_id, b.sender_name)
        return jsonify({"drafts": [json.loads(d.model_dump_json()) for d in made], "skipped": skipped}), 201

    @app.post("/api/plans/<plan_id>/outreach/<draft_id>/approve")
    def outreach_approve(plan_id, draft_id):
        """Approve exactly the text the user saw (the content hash). The agent sends it through the configured channel."""
        plan_id, draft_id, b = pid(plan_id), pid(draft_id), body(ApproveBody)
        with lock:
            draft = em.approve_outreach(plan_id, draft_id, b.content_hash)
        return out(draft)

    @app.post("/api/plans/<plan_id>/outreach/<draft_id>/deny")
    def outreach_deny(plan_id, draft_id):
        plan_id, draft_id = pid(plan_id), pid(draft_id)
        body(EmptyBody)
        with lock:
            draft = em.deny_outreach(plan_id, draft_id)
        return out(draft)

    @app.post("/api/plans/<plan_id>/outreach/<request_id>/followup")
    def outreach_followup(plan_id, request_id):
        """A follow-up in the same thread, as a new proposal. Fixed suggestions or the user's own words; never sent without approval."""
        plan_id, request_id, b = pid(plan_id), pid(request_id), body(FollowupBody)
        with lock:
            draft = em.followup_outreach(plan_id, request_id, b.text, b.sender_name)
        return out(draft, 201)

    @app.get("/api/plans/<plan_id>/outreach/<request_id>/suggestions")
    def outreach_suggestions(plan_id, request_id):
        plan_id, request_id = pid(plan_id), pid(request_id)
        with lock:
            options = em.reply_suggestions(plan_id, request_id)
        return jsonify({"suggestions": options})

    @app.get("/api/plans/<plan_id>/inbox")
    def inbox_get(plan_id):
        plan_id = pid(plan_id)
        with lock:
            box = em.inbox(plan_id)
        return out(box)

    @app.post("/api/plans/<plan_id>/inbox/<message_id>/read")
    def inbox_read(plan_id, message_id):
        plan_id, message_id = pid(plan_id), pid(message_id)
        body(EmptyBody)
        with lock:
            msg = em.mark_reply_read(plan_id, message_id)
        return out(msg)

    @app.post("/api/inbox/poll")
    def inbox_poll():
        """Send what is approved and read replies now, instead of waiting for the background loop."""
        body(EmptyBody)
        with lock:
            res = em.poll_conversations()
        return out(res)

    @app.get("/api/plans/<plan_id>/autoreply")
    def autoreply_get(plan_id):
        plan_id = pid(plan_id)
        with lock:
            st = em.autoreply_status(plan_id)
        return out(st)

    @app.delete("/api/plans/<plan_id>")
    def delete_plan(plan_id):
        """Forget a plan and everything it owns. The body must repeat the plan id: a stray call cannot delete anything."""
        plan_id, b = pid(plan_id), body(DeletePlanBody)
        if b.confirm != plan_id:
            raise ValueError("confirm must repeat the plan id")
        with lock:
            removed = em.delete_plan(plan_id)
            assistant.forget_plan(plan_id)
            if shared:
                registry.record_plan_deletion(g.ws.id, plan_id)
        return jsonify({"deleted": plan_id, "removed": removed})

    @app.post("/api/plans/<plan_id>/autoreply")
    def autoreply_grant(plan_id):
        """Standing permission for fixed follow-up questions, bounded in time, count and hours. Revocable at any moment."""
        plan_id, b = pid(plan_id), body(AutoreplyBody)
        with lock:
            em.grant_autoreply(plan_id, b.sender_name, b.hours, b.max_per_thread, b.max_total)
            st = em.autoreply_status(plan_id)
        return out(st, 201)

    @app.post("/api/plans/<plan_id>/autoreply/revoke")
    def autoreply_revoke(plan_id):
        plan_id = pid(plan_id)
        body(EmptyBody)
        with lock:
            st = em.revoke_autoreply(plan_id)
        return out(st)

    @app.post("/api/autoreply/kill")
    def autoreply_kill():
        """Stops every automatic message in every plan at once, including ones already queued."""
        body(EmptyBody)
        with lock:
            em.kill_all_autoreplies()
        return jsonify({"killed": True})

    @app.post("/api/autoreply/resume")
    def autoreply_resume():
        body(EmptyBody)
        with lock:
            em.resume_autoreplies()
        return jsonify({"killed": False})

    @app.get("/api/plans/<plan_id>/jobs")
    def jobs_get(plan_id):
        plan_id = pid(plan_id)
        with lock:
            em.store.latest(plan_id)
            jobs = em.jobs.store.list(plan_id) if (em.store.root / "jobs.db").exists() else []
            names = em._vendor_names()
        return jsonify([{**json.loads(j.model_dump_json()), "digest": digest(j), "names": {v: names.get(v, v) for v in j.vendors}} for j in jobs])

    @app.post("/api/plans/<plan_id>/jobs")
    def jobs_start(plan_id):
        """A goal such as "3 comparable caterer quotes by the deadline". Drafts one request per vendor; nothing is sent until the user approves them."""
        plan_id, b = pid(plan_id), body(JobBody)
        with lock:
            em.store.latest(plan_id)
            job = em.jobs.start_job(plan_id, b.category, b.vendor_ids, b.goal, datetime.now(UTC) + timedelta(days=b.deadline_days), b.sender_name,
                                    b.min_quotes, b.max_amount_inr, b.max_nudges_per_vendor, b.reply_window_hours)
        return out(job, 201)

    @app.post("/api/jobs/<job_id>/approve-requests")
    def job_approve(job_id):
        job_id = pid(job_id)
        body(EmptyBody)
        with lock:
            job = em.jobs.approve_requests(job_id)
        return out(job)

    @app.post("/api/jobs/<job_id>/claims/<claim_id>/confirm")
    def job_confirm(job_id, claim_id):
        """The only place a vendor's words become a recorded quote. The user chooses how long the price is treated as valid."""
        job_id, claim_id, b = pid(job_id), pid(claim_id), body(ConfirmClaimBody)
        with lock:
            job = em.jobs.confirm_claim(job_id, claim_id, b.valid_days)
        return out(job)

    @app.post("/api/jobs/<job_id>/claims/<claim_id>/reject")
    def job_reject(job_id, claim_id):
        job_id, claim_id = pid(job_id), pid(claim_id)
        body(EmptyBody)
        with lock:
            job = em.jobs.reject_claim(job_id, claim_id)
        return out(job)

    @app.post("/api/jobs/<job_id>/items/<item_id>/resolve")
    def job_resolve(job_id, item_id):
        job_id, item_id, b = pid(job_id), pid(item_id), body(ResolveItemBody)
        with lock:
            job = em.jobs.resolve_item(job_id, item_id, b.note)
        return out(job)

    @app.post("/api/jobs/<job_id>/items/<item_id>/approve-nudge")
    def job_nudge(job_id, item_id):
        job_id, item_id = pid(job_id), pid(item_id)
        body(EmptyBody)
        with lock:
            job = em.jobs.approve_nudge(job_id, item_id)
        return out(job)

    @app.post("/api/jobs/<job_id>/cancel")
    def job_cancel(job_id):
        job_id = pid(job_id)
        body(EmptyBody)
        with lock:
            job = em.jobs.cancel(job_id)
        return out(job)

    @app.post("/api/jobs/tick")
    def jobs_tick():
        """Run one round now instead of waiting for the background loop."""
        body(EmptyBody)
        with lock:
            report = em.background_round()
        return jsonify(report if isinstance(report, dict) else {"ran": [], "errors": [], "poll": json.loads(report.model_dump_json())})
