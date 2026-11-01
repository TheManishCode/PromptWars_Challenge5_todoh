"""Workspace delete, export and import; liveness, readiness, metrics and the owner status route."""

from __future__ import annotations

import hmac
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Literal
from flask import Response, g, jsonify, request
from eventmate import consumption
from eventmate import migrations, transfer
from eventmate.integrity import StoreDamaged
from eventmate.web import observe
from eventmate.workspaces import COOKIE, WorkspaceError
from eventmate.llm import METER
from eventmate.web.base import (
    _Strict,
    log,
)


def register(app, k):
    _err = k._err
    body = k.body
    em = k.em
    heavy = k.heavy
    local = k.local
    lock = k.lock
    metrics = k.metrics
    poll_state = k.poll_state
    registry = k.registry
    shared = k.shared
    started_at = k.started_at
    workspace_limit = k.workspace_limit

    class WorkspaceDeleteBody(_Strict):
        confirm: Literal["delete"]

    @app.errorhandler(StoreDamaged)
    def _store_damaged(exc):
        return _err(503, str(exc)[:300], code="store_damaged")

    @app.errorhandler(migrations.StoreCorrupt)
    def _store_corrupt(exc):
        log.error("a stored record cannot be read (request %s): %s", g.get("rid", "-"), exc)  # which file and line: server log only
        return _err(503, "a stored file is damaged or from a newer version of the application; nothing was changed. The owner can run `python -m eventmate verify`", code="store_damaged", request_id=g.get("rid"))

    @app.errorhandler(transfer.TransferError)
    def _transfer_error(exc):
        return _err(exc.status, str(exc)[:300], code=exc.code)

    @app.get("/api/workspace/export")
    def workspace_export():
        """Everything in this workspace as one zip. Needs the CSRF header; spends no quota."""
        with lock:
            data = transfer.export_workspace(em)
        return Response(data, mimetype="application/zip", headers={"Content-Disposition": 'attachment; filename="todoh-export.zip"'})

    @app.post("/api/workspace/import")
    def workspace_import():
        """Put an export archive into this workspace. The archive is hostile input; imported outreach and jobs are history only and can never send."""
        f = request.files.get("archive") if request.mimetype == "multipart/form-data" else None
        if f is None or len(request.files) != 1:
            raise ValueError("send exactly one file, in the field named archive")
        data = f.stream.read(transfer.MAX_ARCHIVE + 1)
        if shared:
            try:
                registry.check_write(g.ws.id)
            except WorkspaceError as exc:
                return _err(exc.status, str(exc), code=exc.code)
        try:
            with lock:
                report = transfer.import_archive(data, em, check_space=(lambda n: registry.check_write(g.ws.id, incoming=n)) if shared else None)
        except WorkspaceError as exc:
            return _err(exc.status, str(exc), code=exc.code)
        return jsonify(report), 201

    @app.delete("/api/workspace")
    def workspace_delete():
        """Delete this whole workspace (shared mode): the credential, every plan and everything under it. The cookie is cleared. Needs ``{"confirm": "delete"}``."""
        if not shared:
            return _err(404, "not found", code="not_found")  # on a local install the data is the person's own files; deleting them is not an API call
        body(WorkspaceDeleteBody)
        workspace_id = g.ws.id
        with lock:
            registry.delete(workspace_id)
        resp = jsonify({"deleted": True})
        resp.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="Strict")
        return resp

    @app.get("/healthz")
    def healthz():
        return jsonify({"ok": True})  # nothing else: no counts, no names, no version

    @app.get("/readyz")
    def readyz():
        """Ready to take traffic: the data directory is writable, the workspace database opens, and some disk is free. ``/healthz`` only says the process is up."""
        problems = []
        root = Path(registry.root if shared else local.em.store.root)
        try:
            with tempfile.NamedTemporaryFile(dir=root, prefix=".ready-") as probe:
                probe.write(b"x")
                probe.flush()
                os.fsync(probe.fileno())
            if shutil.disk_usage(root).free < 50 * 1024 * 1024:
                problems.append("less than 50 MB of disk is free")
        except OSError:
            problems.append("storage is not writable")
        if shared:
            try:
                registry.directory.count()
            except Exception:  # sqlite3.Error and anything else: the cause is in the server log, not in this answer
                problems.append("the workspace database cannot be opened")
        return jsonify({"ready": not problems, **({"problems": problems} if problems else {})}), (200 if not problems else 503)

    @app.get("/metrics")
    def prometheus_metrics():
        """Prometheus scrape target. Same credential as the owner status route (the ``X-EventMate-Owner`` header, or ``Authorization: Bearer`` for scrapers that can only
        send that); anything else is the same 404 as an unknown route. Counters and gauges only: no content, ids or addresses."""
        token = os.environ.get("EVENTMATE_OWNER_TOKEN", "")
        supplied = request.headers.get("X-EventMate-Owner", "") or request.headers.get("Authorization", "").removeprefix("Bearer ")
        if len(token) < 16 or not hmac.compare_digest(supplied, token):
            return _err(404, "not found", code="not_found")
        root = Path(registry.root if shared else local.em.store.root)
        used, total = heavy.status()
        gauges = {"uptime_seconds": int(time.time() - started_at), "workspaces": registry.directory.count() if shared else 1, "workspaces_active_last_minute": workspace_limit.tracked(),
                  "searches_in_flight": used, "searches_capacity": total, "poll_failures": poll_state["failures"], "disk_free_bytes": shutil.disk_usage(root).free}
        return Response(observe.prometheus(metrics, gauges, {k: v for k, v in METER.status().items() if isinstance(v, int)}), mimetype="text/plain; version=0.0.4")

    @app.get("/api/owner/status")
    def owner_status():
        """Counts only, for the person who runs the server. Needs ``EVENTMATE_OWNER_TOKEN`` (at least 16 characters) in the ``X-EventMate-Owner`` header; a
        workspace cookie or the API token is not enough. With no token set, or a wrong one, the answer is the same 404 as any unknown route."""
        token = os.environ.get("EVENTMATE_OWNER_TOKEN", "")
        if len(token) < 16 or not hmac.compare_digest(request.headers.get("X-EventMate-Owner", ""), token):
            return _err(404, "not found", code="not_found")
        pending = None  # a count across every workspace would mean opening each one's job database: not done on a status route
        if not shared:
            pending = sum(1 for j in (local.em.jobs.store.list() if (local.em.store.root / "jobs.db").exists() else []) if j.status not in ("done", "expired", "cancelled"))
        used, total = heavy.status()
        return jsonify({
            "uptime_s": int(time.time() - started_at), "workspaces_total": registry.directory.count() if shared else 1, "workspaces_active_last_minute": workspace_limit.tracked(), "pending_jobs": pending,
            "searches_in_flight": used, "searches_capacity": total, "last_poll": poll_state["last"], "poll_failures": poll_state["failures"],
            "model_calls": {**METER.status(), **consumption.BUDGETS.status()}, "requests": metrics.snapshot(),
        })
