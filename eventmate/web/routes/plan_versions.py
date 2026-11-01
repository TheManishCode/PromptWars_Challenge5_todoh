"""Plan versions, diffs, explanations and replanning."""

from __future__ import annotations

import json
from flask import Response, jsonify
from eventmate.schemas import Disruption
from eventmate.web.base import (
    ConfirmBody,
)


def register(app, k):
    body = k.body
    em = k.em
    lock = k.lock
    out = k.out
    pid = k.pid
    today_fn = k.today_fn
    version_arg = k.version_arg

    @app.get("/api/plans/<plan_id>")
    def get_plan(plan_id):
        plan_id, version = pid(plan_id), version_arg()
        with lock:
            doc = json.loads(em.store.load(plan_id, version).model_dump_json())
            doc["current_verification"] = em.verification(plan_id, version)  # not stored with the plan: it reflects today's data
        return jsonify(doc)

    @app.get("/api/plans/<plan_id>/versions")
    def versions(plan_id):
        with lock:
            vs = em.store.versions(pid(plan_id))
        return jsonify(
            [
                {
                    "version": p.version,
                    "parent_version": p.parent_version,
                    "created_at": p.created_at.isoformat(),
                    "change_reason": p.change_reason,
                    "total_cost": p.total_cost,
                    "event_date": p.request.event_date.isoformat(),
                    "guest_count": p.request.guest_count,
                    "budget_total": p.request.budget_total,
                }
                for p in vs
            ]
        )

    @app.get("/api/plans/<plan_id>/diff")
    def diff(plan_id):
        old, new = version_arg("old"), version_arg("new")
        if old is None or new is None:
            raise ValueError("old and new are required")
        with lock:
            return out(em.diff(pid(plan_id), old, new))

    @app.get("/api/plans/<plan_id>/explanation")
    def explanation(plan_id):
        with lock:
            ex = em.explain(pid(plan_id), version_arg())
        return jsonify({"markdown": ex.markdown, "used_llm": ex.used_llm})

    @app.get("/api/plans/<plan_id>/decisions")
    def decisions(plan_id):
        with lock:
            plan = em.store.load(pid(plan_id), version_arg())
            entries = em.decisions_for(plan)
        return Response("[" + ",".join(e.model_dump_json() for e in entries) + "]", mimetype="application/json")

    @app.get("/api/plans/<plan_id>/validate")
    def validate(plan_id):
        with lock:
            report = em.revalidate(pid(plan_id), version_arg())
        return out(report)

    @app.post("/api/plans/<plan_id>/confirm")
    def confirm(plan_id):
        b = body(ConfirmBody)
        with lock:
            res = em.confirm(pid(plan_id), b.category)
        return out(res, 200 if res.status == "replanned" else 409)

    @app.post("/api/plans/<plan_id>/replan")
    def replan(plan_id):
        d = body(Disruption)
        with lock:
            res = em.replan(pid(plan_id), d, today_fn())
        return out(res, 200 if res.status == "replanned" else (409 if res.status == "needs_decision" else 422))
