"""The Evidence tab: what the product knows, grouped by how far it can be trusted. Read-only: a fact is created by the step that learned it, never by a request here."""

from __future__ import annotations

import json
import re
from flask import jsonify, request


def register(app, k):
    em = k.em
    lock = k.lock

    @app.get("/api/evidence")
    def evidence_list():
        subject = request.args.get("subject")
        if subject is not None and not re.fullmatch(r"[A-Za-z0-9_:.-]{1,64}", subject):
            raise ValueError("invalid subject")
        with lock:
            groups = em.evidence.by_state(subject)
        return jsonify({state: [json.loads(r.model_dump_json()) for r in rows] for state, rows in groups.items()})
