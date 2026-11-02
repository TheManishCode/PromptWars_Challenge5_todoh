"""Page watches: the owner asks to be told when a public page changes, mentions something or drops a price. Local use only: a watch makes the server fetch an address the
caller chose, so a shared host (public visitors) does not offer it. What a watch finds appears in the evidence list as 'discovered'."""

from __future__ import annotations

import json
from flask import jsonify
from eventmate.web.base import EmptyBody, NotOnSharedHost, WatchBody


def register(app, k):
    em = k.em
    lock = k.lock

    def local_only():
        if k.shared:
            raise NotOnSharedHost("page watches are not available on a shared host")

    def view(w):
        d = json.loads(w.model_dump_json(exclude={"last_lines"}))  # the kept page text is the server's working copy, not an answer
        d["host"] = w.host
        return d

    @app.get("/api/watches")
    def watches_list():
        local_only()
        with lock:
            return jsonify([view(w) for w in em.watches.all()])

    @app.post("/api/watches")
    def watches_add():
        local_only()
        b = k.body(WatchBody)
        with lock:
            return jsonify(view(em.add_watch(b.subject, b.url, b.condition, b.text, b.amount_inr, b.interval_minutes))), 201

    @app.post("/api/watches/<watch_id>/resume")
    def watches_resume(watch_id):
        local_only()
        k.body(EmptyBody)
        with lock:
            return jsonify(view(em.resume_watch(watch_id)))

    @app.delete("/api/watches/<watch_id>")
    def watches_remove(watch_id):
        local_only()
        k.no_body_or_empty_object()
        with lock:
            if not em.watches.remove(watch_id):
                raise FileNotFoundError("watch")
        return jsonify({"removed": True})
