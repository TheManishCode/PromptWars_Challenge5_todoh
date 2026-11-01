"""Advisories, quotes and saved leads."""

from __future__ import annotations

import json
from flask import jsonify
from eventmate.web.base import (
    LeadBody,
)


def register(app, k):
    body = k.body
    em = k.em
    lock = k.lock
    out = k.out
    pid = k.pid
    today_fn = k.today_fn

    @app.get("/api/plans/<plan_id>/advisories")
    def advisories(plan_id):
        plan_id = pid(plan_id)
        with lock:
            items = em.advisories(plan_id)
        return jsonify([json.loads(a.model_dump_json()) for a in items])

    @app.get("/api/plans/<plan_id>/quotes")
    def plan_quotes(plan_id):
        plan_id = pid(plan_id)
        with lock:
            rows = em.quote_status(plan_id, today_fn())
        return jsonify([json.loads(r.model_dump_json()) for r in rows])

    @app.get("/api/plans/<plan_id>/leads")
    def leads_get(plan_id):
        plan_id = pid(plan_id)
        with lock:
            items = em.leads(plan_id)
        return jsonify([json.loads(x.model_dump_json()) for x in items])

    @app.post("/api/plans/<plan_id>/leads")
    def leads_add(plan_id):
        """Bookmark an advertised price. Not a quote; it never changes a plan price."""
        plan_id, b = pid(plan_id), body(LeadBody)
        with lock:
            lead = em.save_lead(plan_id, **b.model_dump())
        return out(lead, 201)
