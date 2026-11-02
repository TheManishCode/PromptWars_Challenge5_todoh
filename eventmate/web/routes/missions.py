"""Missions (ADR-059): the LangGraph controller behind the same CSRF, token, rate and body rules. The graph pauses for the two human answers; these routes only start, read and answer it.
Local use only: a shared host has visitors with no mailbox, and a mission sends mail."""

from __future__ import annotations

from flask import request
from eventmate.orchestration import MissionRunner
from eventmate.web.base import MissionStartBody, NotOnSharedHost


def register(app, k):
    em = k.em
    lock = k.lock
    out = k.out
    runner = []

    def missions():
        if k.shared:
            raise NotOnSharedHost("missions are not available on a shared host")
        if not runner:  # built on first use: a server that never runs a mission opens no database
            runner.append(MissionRunner(em, em.store.root / "missions.sqlite"))
        return runner[0]

    @app.post("/api/missions")
    def mission_start():
        b = k.body(MissionStartBody)
        with lock:
            return out(missions().start(b.brief, b.sender_name, b.categories, today=k.today_fn(), source=b.source, limit=b.limit), 201)

    @app.get("/api/missions/<mission_id>")
    def mission_show(mission_id):
        with lock:
            return out(missions().view(mission_id))

    @app.post("/api/missions/<mission_id>/resume")
    def mission_resume(mission_id):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError("JSON object body required")
        with lock:
            return out(missions().resume(mission_id, data))
