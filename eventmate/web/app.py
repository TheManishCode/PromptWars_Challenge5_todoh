"""to;doh-the event planner web app: ``create_app`` builds the Flask app (workspace resolution, guards, limits, observability) and registers the route groups in
``eventmate/web/routes``. Module-level pieces are in ``eventmate/web/base.py``; the security model is described there."""

from __future__ import annotations

from types import SimpleNamespace

import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Optional
from flask import Flask, Response, abort, g, jsonify, request
from werkzeug.local import LocalProxy
from eventmate import arcade_pulse as _pulse_mod
from eventmate.voice import audio as voice_audio
from eventmate.voice.transcribe import Transcriber, engine_from_env
from eventmate.voice.turns import VoiceService
from eventmate.assistant.agent import Assistant
from eventmate.assistant.identity import LOCAL_ORGANIZER, Principal
from eventmate.env import load_env
from eventmate import consumption
from eventmate.web import hosting, limits, observe
from eventmate.workspaces import COOKIE, COOKIE_MAX_AGE, Workspace, WorkspaceError, WorkspaceRegistry
from eventmate.llm import client_from_env
from eventmate.service import EventMate
from eventmate.wall.limits import WallLimits
from eventmate.wall.routes import Wall
from eventmate.wall.store import WallConfig, WallStore
from eventmate.web.base import (
    AUDIO_UPLOAD_LIMIT,
    CSP,
    EmptyBody,
    GUEST_UPLOAD_LIMIT,
    HEAVY_MAX,
    IntakeConfirmBody,
    LOOPBACK,
    SENDING_ROUTES,
    STATIC_DIR,
    STATIC_FILES,
    UPLOAD_ROUTES,
    VENDOR_FILES,
    VOICE_STREAMS_MAX,
    WORKSPACE_EXEMPT,
    _PLAN_ID,
)
__all__ = ["create_app", "AUDIO_UPLOAD_LIMIT", "GUEST_UPLOAD_LIMIT", "IntakeConfirmBody", "STATIC_FILES", "VENDOR_FILES", "LOOPBACK"]  # what other modules and the tests import from here
from eventmate.web.routes import assistant_plans, errors, ideas_sourcing, ledger_external, outreach_jobs, pages, plan_extras, plan_versions, voice_guests, workspace_ops


def create_app(
    em: Optional[EventMate] = None,
    api_token: Optional[str] = None,
    rate_limit_per_minute: int = 60,
    today_fn: Callable[[], date] = date.today,
    allowed_hosts: Optional[set] = None,
    assistant: Optional[Assistant] = None,
    principal: Principal = LOCAL_ORGANIZER,
    poll_seconds: Optional[float] = None,
    voice: Optional[VoiceService] = None,
    transcriber: Optional[Transcriber] = None,
    registry: Optional[WorkspaceRegistry] = None,
    wall_store: Optional[WallStore] = None,
    wall_limits: Optional[WallLimits] = None,
) -> Flask:
    """Local mode (no ``registry``): one workspace, as before. Shared mode: ``registry`` gives every visitor a workspace of their own, found from a cookie."""

    app = Flask(__name__, static_folder=None)

    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024

    shared = registry is not None

    local: Optional[Workspace] = None

    if not shared:
        if em is None:
            load_env()  # keys first, so the model client below can see them
            em = EventMate(run_id=f"web-{date.today():%Y%m%d}", llm=client_from_env())
        assistant = assistant or Assistant.from_env(em)
        local = Workspace("local", em, assistant, voice or VoiceService(assistant, em), threading.RLock(), principal, secrets.token_urlsafe(32), date.today().isoformat())

    transcriber = transcriber or Transcriber(engine_from_env(os.environ), (registry.root if shared else em.store.root) / "voice" / "tmp")

    stream_slots = threading.BoundedSemaphore(VOICE_STREAMS_MAX)

    em = LocalProxy(lambda: g.ws.em)

    assistant = LocalProxy(lambda: g.ws.assistant)

    voice_svc = LocalProxy(lambda: g.ws.voice)

    lock = LocalProxy(lambda: g.ws.lock)

    def P() -> Principal:
        """The asker, as a real object (never a proxy: a voice session keeps what it is given)."""
        return g.ws.principal

    hosts = set(allowed_hosts) if allowed_hosts else set(LOOPBACK)

    proxies = limits.trusted_proxies()  # forwarded headers are read only from these (EVENTMATE_TRUSTED_PROXY); otherwise they are ignored

    address_limit = limits.WindowLimiter(rate_limit_per_minute)  # writes per minute per caller address

    workspace_limit = limits.WindowLimiter(rate_limit_per_minute * 2)  # and per workspace, so many addresses cannot add up to more than one visitor's share

    heavy = limits.FairShare(HEAVY_MAX, HEAVY_MAX if hosting.mode(os.environ) == "local" else max(1, HEAVY_MAX // 2))  # shared mode: one workspace holds at most half of the slow-search slots; the lone local user may use them all

    lookup_limit = limits.WindowLimiter(int(os.environ.get("EVENTMATE_WS_LOOKUPS_PER_HOUR", "60") or 60), 3600.0)  # outside lookups (places, search, weather, maps) per workspace per hour, shared mode only

    started_at = time.time()

    poll_state: Dict[str, object] = {"last": None, "failures": 0}  # for the owner status route: when the last background round finished, and how many failed

    metrics = observe.Metrics()

    observe.configure_access_log(os.environ.get("EVENTMATE_ACCESS_LOG", "stderr" if shared else "off").strip().lower())

    @app.before_request
    def begin_request():
        g.rid = observe.new_request_id(request.headers.get("X-Request-Id"))
        g.t0 = time.perf_counter()
        metrics.begin()

    @app.after_request
    def finish_request(resp: Response) -> Response:
        ms = (time.perf_counter() - g.get("t0", time.perf_counter())) * 1000
        rid = g.get("rid") or observe.new_request_id()
        resp.headers["X-Request-Id"] = rid
        if g.get("t0") is not None:
            metrics.end(resp.status_code, ms, request.url_rule.rule if request.url_rule else None)
            if request.path not in ("/healthz", "/readyz"):  # a probe every few seconds would drown the log
                observe.log_request(rid, request.method, request.url_rule.rule if request.url_rule else None, resp.status_code, ms, g.ws.id if g.get("ws") is not None and shared else None)
        return resp

    def caller() -> str:
        return limits.limiter_key(limits.client_address(request.remote_addr, request.headers.get("X-Forwarded-For"), proxies))  # counted per IPv4 address or per IPv6 /64

    wall: Optional[Wall] = None

    if os.environ.get("EVENTMATE_WALL", "on").lower() != "off":
        wall_db = Path(os.environ.get("EVENTMATE_WALL_DB") or (registry.root if shared else local.em.store.root.parent) / "wall_data" / "wall.sqlite3")
        bearer_on_wall = bool(api_token) and os.environ.get("EVENTMATE_WALL_BEARER") == "1"  # off by default: a public wall is exempt from the bearer token
        wall = Wall(app, wall_store or WallStore(wall_db, WallConfig.from_env(os.environ)), wall_limits,
                    bearer_check=(lambda req: hmac.compare_digest(req.headers.get("Authorization", ""), f"Bearer {api_token}")) if bearer_on_wall else None,
                    client_ip=caller, secure_cookies=shared)  # the wall's limits count the real caller (trusted proxy header, IPv6 as a /64), and in shared mode its cookie is always Secure

    csrf_gets = {"/api/workspace/export"}  # spends no quota, but hands over everything in the workspace, so a page on another site must not be able to fetch it

    quota_gets = {"/api/deals", "/api/explore", "/api/travel/flights", "/api/travel/multi-city", "/api/travel/trip", "/api/discover/venues", "/api/weather", "/api/places"}

    def spends_quota(path: str) -> bool:
        return path in quota_gets or (path.startswith("/api/plans/") and path.endswith(("/weather", "/weather-advice")))

    def resolve_workspace():
        """Find the workspace of this request. Local mode has one. Shared mode reads the cookie; a missing, malformed, unknown or revoked credential gets the
        same generic 404 as any miss and creates nothing, except on ``GET /api/session``, which makes a new empty workspace (bounded: see WorkspaceRegistry)."""
        g.ws = local
        if shared:
            g.ws = None
            if not request.path.startswith("/api/") or request.path.startswith(WORKSPACE_EXEMPT):
                return None  # the page and its files, and routes that have their own authentication
            g.ws = registry.open(request.cookies.get(COOKIE))
            if g.ws is None:
                if not (request.method == "GET" and request.path == "/api/session"):
                    return _err(404, "not found", code="not_found")
                try:
                    g.ws, g.new_credential = registry.create(caller())
                except WorkspaceError as exc:
                    return _err(exc.status, str(exc), code=exc.code)
        if g.ws is not None and shared:
            registry.acquire(g.ws)  # released in teardown: a workspace in use is never evicted from memory (F-016)
            g.ws_held = g.ws
        if g.ws is not None:
            g.workspace_token = consumption.bind(g.ws.id)  # model calls made for this request are charged to this workspace (Phase 4.7)
            g.pulse_token = _pulse_mod.pulse.bind(g.ws.pulse)  # and the room's run lights are this workspace's own (F-014)
        return None

    @app.teardown_request
    def unbind_workspace(_exc):
        token = g.pop("workspace_token", None)
        if token is not None:
            consumption.unbind(token)
        held = g.pop("ws_held", None)
        if held is not None:
            registry.release(held)
        pulse_token = g.pop("pulse_token", None)
        if pulse_token is not None:
            _pulse_mod.pulse.unbind(pulse_token)

    @app.before_request
    def guard():
        hostname = request.host.rsplit(":", 1)[0] if not request.host.startswith("[") else request.host.split("]")[0] + "]"
        if hostname not in hosts:
            abort(Response(json.dumps({"error": "host not allowed"}), 400, mimetype="application/json"))
        wall_path = wall is not None and request.path.startswith("/api/wall/")
        if wall_path and wall.owns(request):
            return wall.guard(request)  # the wall authenticates on its own (docs/WALL.md 8.2): exact allow-list, no private token or CSRF, no prefix exemption
        if request.path.startswith("/api/") and api_token:
            supplied = request.headers.get("Authorization", "")
            if not hmac.compare_digest(supplied, f"Bearer {api_token}"):
                return _err(401, "authentication required")
        if wall_path:
            return wall.unknown()  # an unlisted wall path gets no exemption: the bearer check above applied, and the answer is a plain 404
        refused = resolve_workspace()
        if refused is not None:
            return refused
        if shared and request.method == "POST" and SENDING_ROUTES.fullmatch(request.path):
            return _err(403, "sending is not enabled for guest workspaces", code="sending_disabled")  # also no channel exists in a guest workspace: two layers
        csrf_token = g.ws.csrf if g.ws is not None else None  # a route outside the workspaces (its own authentication) has no workspace CSRF value
        if csrf_token is not None and request.method in ("GET", "HEAD") and (spends_quota(request.path) or request.path in csrf_gets):
            if not hmac.compare_digest(request.headers.get("X-EventMate-CSRF", ""), csrf_token):
                return _err(403, "missing or invalid CSRF token")
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if csrf_token is not None and not hmac.compare_digest(request.headers.get("X-EventMate-CSRF", ""), csrf_token):
                return _err(403, "missing or invalid CSRF token")
            origin = request.headers.get("Origin")
            if origin and origin.split("://", 1)[-1] != request.host:
                return _err(403, "cross-origin request refused")
            upload = next((limit for rx, limit in UPLOAD_ROUTES if request.method == "POST" and rx.fullmatch(request.path)), None)
            if upload:
                request.max_content_length = upload
                g.upload_limit = upload
                if request.content_length is None:  # an upload must say how big it is, so the cap is checked before a byte is read (a chunked body is refused)
                    return _err(411, "uploads must send a Content-Length", code="length_required")
            bodyless_delete = request.method == "DELETE" and not request.content_length  # a DELETE that carries nothing has nothing to type-check
            if request.mimetype != "application/json" and not bodyless_delete and not (upload and request.mimetype == "multipart/form-data"):
                return _err(415, "JSON body required", **({"code": "unsupported_type"} if upload else {}))
            if not address_limit.allow(caller()) or not workspace_limit.allow(consumption.current_workspace()):
                return _err(429, "too many requests", code="rate_limited")
            if shared and g.ws is not None and request.method in ("POST", "PUT", "PATCH"):
                try:
                    registry.check_write(g.ws.id, creating_plan=request.method == "POST" and request.path == "/api/plans")
                except WorkspaceError as exc:
                    return _err(exc.status, str(exc), code=exc.code)
        if shared and g.ws is not None and spends_quota(request.path) and not lookup_limit.allow(g.ws.id):
            return _err(429, "this workspace has used its lookups for the hour (the outside services behind them have free limits); try again later", code="lookup_quota")  # never a silent switch to another source
        return None

    @app.before_request
    def limit_heavy():
        if spends_quota(request.path):
            workspace = consumption.current_workspace()
            why = heavy.acquire(workspace)
            if why:
                return _err(429, "the server is busy with other searches; try again in a moment" if why == "busy" else "this workspace already has its share of searches running; wait for one to finish",
                            code="busy" if why == "busy" else "workspace_share")
            g.heavy_permit = workspace
        return None

    @app.teardown_request
    def release_heavy(_exc):
        workspace = g.pop("heavy_permit", None)
        if workspace is not None:
            heavy.release(workspace)

    @app.after_request
    def headers(resp: Response) -> Response:
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Frame-Options"] = "DENY"
        if request.path.startswith("/api/") and not request.path.startswith("/api/wall/"):  # the wall sets its own: public reads are cacheable, private ones no-store
            resp.headers["Cache-Control"] = "no-store"
        credential = g.get("new_credential") or (request.cookies.get(COOKIE) if shared and g.get("ws") is not None and request.path == "/api/session" else None)
        if credential:  # a new workspace's cookie, or the renewal of the one the visitor already holds (each page load pushes the year forward)
            resp.set_cookie(COOKIE, credential, max_age=COOKIE_MAX_AGE, httponly=True, samesite="Strict", secure=True, path="/")  # no Domain: the cookie belongs to this host only
        if hosting.is_https(request.scheme, request.remote_addr, request.headers.get("X-Forwarded-Proto"), proxies):
            resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"  # only over TLS, and only believed from a trusted proxy
        return resp

    def _err(status: int, message: str, **extra):
        return jsonify({"error": message, **extra}), status

    def body(model):
        try:
            data = request.get_json(silent=True)
        except RecursionError:  # absurdly nested JSON
            data = None
        if not isinstance(data, dict):
            raise ValueError("JSON object body required")
        return model.model_validate(data)

    def no_body_or_empty_object() -> None:
        """A DELETE that has nothing to say may carry no body at all (the page sends none); a body, if present, must still be an empty JSON object."""
        if request.content_length:
            body(EmptyBody)

    def pid(plan_id: str) -> str:
        if not _PLAN_ID.fullmatch(plan_id):
            raise ValueError("invalid plan id")
        return plan_id

    def version_arg(name: str = "version") -> Optional[int]:
        raw = request.args.get(name)
        if raw is None:
            return None
        if not raw.isdigit() or len(raw) > 4:
            raise ValueError(f"{name} must be a positive integer")
        return int(raw)

    def out(model, code: int = 200):
        return Response(model.model_dump_json(), code, mimetype="application/json")

    def asset_version() -> str:
        """A short hash of every served file's name, size and time. The page names each script, style and model with it (``?v=``), so a changed file gets a new address:
        a phone that cached last week's ``cabinet.glb`` under a one-hour rule showed the old, sideways cabinet beside new code written for the new one."""
        h = hashlib.sha256()
        for name in sorted(STATIC_FILES):
            path = STATIC_DIR / ("vendor" if name in VENDOR_FILES else "") / name
            try:
                st = path.stat()
            except OSError:
                continue
            h.update(f"{name}:{st.st_size}:{st.st_mtime_ns}".encode())
        return h.hexdigest()[:12]

    def plan_response(outcome):
        payload = {
            "status": outcome.status,
            "plan": json.loads(outcome.plan.model_dump_json()) if outcome.plan else None,
            "violations": [v.model_dump() for v in outcome.violations],
            "tasks": [{"id": t.id, "agent": t.agent.value, "status": t.status.value, "depends_on": list(t.depends_on)} for t in outcome.task_graph.tasks],
            "reality": json.loads(outcome.intake.reality.model_dump_json()) if outcome.intake.reality else None,
        }
        return jsonify(payload), (201 if outcome.plan else 422)

    def audio_upload():
        """The one audio file of a multipart request, as (bytes, declared type)."""
        f = request.files.get("audio")
        if f is None or len(request.files) != 1:
            raise ValueError("send exactly one recording, in the field named audio")
        return f.stream.read(voice_audio.MAX_BYTES + 1), f.mimetype or ""

    # ---- the routes (eventmate/web/routes/*): registered once the helpers above exist
    k = SimpleNamespace(P=P, _err=_err, asset_version=asset_version, assistant=assistant, audio_upload=audio_upload, body=body, em=em, heavy=heavy, local=local, lock=lock, metrics=metrics, no_body_or_empty_object=no_body_or_empty_object, out=out, pid=pid, plan_response=plan_response, poll_state=poll_state, registry=registry, shared=shared, started_at=started_at, stream_slots=stream_slots, today_fn=today_fn, transcriber=transcriber, version_arg=version_arg, voice_svc=voice_svc, workspace_limit=workspace_limit)
    for module in (errors, pages, workspace_ops, assistant_plans, plan_extras, outreach_jobs, ideas_sourcing, voice_guests, ledger_external, plan_versions):
        module.register(app, k)


<<<<<<< HEAD
    @app.post("/api/voice/sessions")
    def voice_session_open():
        b = body(VoiceSessionBody)
        return jsonify(voice_svc.create_session(P(), b.plan_id)), 201

    @app.delete("/api/voice/sessions/<session_id>")
    def voice_session_close(session_id):
        voice_svc.end_session(P(), session_id if VOICE_ID.fullmatch(session_id) else "")  # no body: the id in the path is all it needs
        return jsonify({"ended": True})

    @app.post("/api/voice/turns")
    def voice_turn():
        """One transcript through the same assistant pipeline as typed chat. Server-sent events when asked for (Accept: text/event-stream), else one JSON body."""
        b = body(VoiceTurnBody)
        turn = voice_svc.start_turn(P(), b.session_id, b.turn_id, b.generation, b.text, b.input_mode, b.confidence, b.language)
        events = voice_svc.run_turn(P(), b.session_id, turn)
        if "text/event-stream" not in request.headers.get("Accept", ""):
            got = list(events)
            by = {e["event"]: e["data"] for e in got if e["event"] != "sentence"}
            return jsonify({"turn_id": turn.turn_id, "sentences": [e["data"] for e in got if e["event"] == "sentence"], "final": by.get("final"), "error": by.get("error")})
        if not stream_slots.acquire(blocking=False):
            events.close()
            turn.request_cancel()  # nothing was started, so nothing is left running
            return _err(429, "too many voice streams are open; try again in a moment")

        def stream():
            try:
                for e in events:
                    yield f"event: {e['event']}\ndata: {json.dumps(e['data'])}\n\n"
            finally:
                events.close()
                stream_slots.release()

        return Response(stream(), mimetype="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.post("/api/voice/turns/<turn_id>/cancel")
    def voice_turn_cancel(turn_id):
        """The user spoke over the answer or walked away. ``playback_chars`` is how much speech was played: an estimate, not proof of what was heard."""
        b = body(VoiceCancelBody)
        return jsonify(voice_svc.cancel_turn(P(), b.session_id, turn_id if VOICE_ID.fullmatch(turn_id) else "", b.generation, b.playback_chars))

    @app.post("/api/plans/<plan_id>/voice-notes")
    def voice_note_save(plan_id):
        """Keep the original recording with the plan. Off unless the owner turned it on (EVENTMATE_VOICE_KEEP_ORIGINAL=1)."""
        plan_id = pid(plan_id)
        data, declared = audio_upload()
        with lock:
            em.store.latest(plan_id)
            return jsonify(em.voice_notes.save(plan_id, data, declared)), 201

    @app.get("/api/plans/<plan_id>/voice-notes/<note_id>")
    def voice_note_get(plan_id, note_id):
        plan_id = pid(plan_id)
        with lock:
            em.store.latest(plan_id)
            got = em.voice_notes.read(plan_id, note_id)
        if got is None:
            abort(404)
        return Response(got[0], mimetype=got[1], headers={"Cache-Control": "no-store", "Content-Disposition": "attachment; filename=voice-note"})

    @app.delete("/api/plans/<plan_id>/voice-notes/<note_id>")
    def voice_note_delete(plan_id, note_id):
        plan_id = pid(plan_id)
        no_body_or_empty_object()
        with lock:
            em.store.latest(plan_id)
            return jsonify({"deleted": em.voice_notes.delete(plan_id, note_id)})

    @app.post("/api/plans/<plan_id>/guests/preview")
    def guests_preview(plan_id):
        """Parse a guest CSV (JSON {"csv": "..."} or one multipart file) without saving it. Row problems are listed by line; a clean file gets a single-use token."""
        plan_id = pid(plan_id)
        if request.mimetype == "multipart/form-data":
            f = request.files.get("file")
            if f is None or len(request.files) != 1:
                raise ValueError("send exactly one file, in the field named file")
            raw = f.stream.read(MAX_GUEST_BYTES + 1)
        else:
            raw = body(GuestCsvBody).csv.encode("utf-8")
        with lock:
            preview = em.guest_preview(plan_id, raw)
        return out(preview)

    @app.post("/api/plans/<plan_id>/guests/commit")
    def guests_commit(plan_id):
        plan_id, b = pid(plan_id), body(GuestCommitBody)
        with lock:
            saved = em.guest_commit(plan_id, b.preview_token)
        return jsonify({"saved": True, "totals": saved["totals"], "check": saved["check"]}), 201

    @app.get("/api/plans/<plan_id>/guests")
    def guests_get(plan_id):
        """Totals for the saved list beside the stated headcount. Never names."""
        plan_id = pid(plan_id)
        with lock:
            saved = em.guest_summary(plan_id)
        return jsonify({"has_list": saved is not None, **(saved or {})})

    @app.delete("/api/plans/<plan_id>/guests")
    def guests_delete(plan_id):
        plan_id = pid(plan_id)
        no_body_or_empty_object()
        with lock:
            gone = em.guest_delete(plan_id)
        return jsonify({"deleted": gone})

    @app.get("/api/plans/<plan_id>/state")
    def plan_state(plan_id):
        """Where the plan stands: facts with their source and status, vendors, what is still open, and what changed (docs/EVENT_STATE.md). Read-only."""
        plan_id = pid(plan_id)
        since = None
        if request.args.get("changes_since"):
            try:
                since = datetime.fromisoformat(request.args["changes_since"].replace("Z", "+00:00"))
            except ValueError:
                raise ValueError("changes_since must be an ISO 8601 time") from None
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
        with lock:
            state = build_state(em, plan_id, changes_since=since, today=today_fn())
        return out(state)

    @app.get("/api/plans/<plan_id>/ledger")
    def ledger_get(plan_id):
        plan_id = pid(plan_id)
        with lock:
            summary, entries = em.ledger_summary(plan_id), em.ledger_entries(plan_id)
        return jsonify({"summary": json.loads(summary.model_dump_json()), "entries": [json.loads(e.model_dump_json()) for e in entries]})

    @app.post("/api/plans/<plan_id>/ledger")
    def ledger_add(plan_id):
        """User-reported money only: a quote, a payment or a refund. to;doh-the event planner never initiates a payment."""
        plan_id, b = pid(plan_id), body(LedgerBody)
        with lock:
            em.ledger_add(plan_id, b.kind.value, b.category, b.amount, b.note)
            summary = em.ledger_summary(plan_id)
        return out(summary, 201)

    @app.post("/api/observations/<vendor_id>/quotes")
    def observation_add(vendor_id):
        """A quote the user was given. Evidence is required when the vendor is the source; nothing is sent to the vendor."""
        vendor_id, b = pid(vendor_id), body(QuoteBody)
        fields = b.model_dump(exclude={"category"})
        with lock:
            obs = em.observe_quote(vendor_id, b.category, observed_at=datetime.now(timezone.utc), **fields)
            report = em.observations.report(vendor_id, today_fn())
        return jsonify({"observation": json.loads(obs.model_dump_json()), "report": json.loads(report.model_dump_json())}), 201

    @app.get("/api/observations/<vendor_id>")
    def observation_get(vendor_id):
        vendor_id = pid(vendor_id)
        with lock:
            quotes, report = em.observations.quotes(vendor_id), em.observations.report(vendor_id, today_fn())
        return jsonify({"quotes": [json.loads(q.model_dump_json()) for q in quotes], "report": json.loads(report.model_dump_json()) if report else None})

    @app.get("/api/observations/compare/<category>")
    def observation_compare(category):
        with lock:
            cmp_ = em.observations.compare(VendorCategory(category), today_fn())
        return out(cmp_)

    @app.get("/api/observations/score/<category>")
    def observation_score(category):
        with lock:
            board = em.vendor_scores(VendorCategory(category), today_fn())
        return out(board)

    @app.get("/api/weather/status")
    def weather_status():
        """Lets the UI ask whether weather is set up without provoking an error response."""
        return jsonify({"configured": em.weather.configured()})

    @app.get("/api/weather")
    def weather_get():
        """Forecast for a city and date. Past the provider's horizon the answer says so instead of guessing."""
        raw = request.args.get("date", "")
        try:
            day = date.fromisoformat(raw)
        except ValueError:
            raise ValueError("date must be YYYY-MM-DD") from None
        check_date(day, today_fn())
        return out(em.weather_forecast(request.args.get("city", ""), day))

    @app.get("/api/plans/<plan_id>/weather-advice")
    def plan_weather_advice(plan_id):
        """The forecast plus plain-code advice (rain backup, heat) for the chosen venue. Advice only."""
        return out(em.weather_advice(pid(plan_id)))

    @app.get("/api/plans/<plan_id>/weather")
    def plan_weather(plan_id):
        return out(em.plan_weather(pid(plan_id)))

    @app.get("/api/discover/venues")
    def discover_venues():
        """Read-only: places mapped on OpenStreetMap. Candidates only; no price or availability."""
        city, kind = request.args.get("city", ""), request.args.get("kind", "venue")
        try:
            limit = int(request.args.get("limit", "20"))
        except ValueError:
            raise ValueError("limit must be a whole number") from None
        res = em.discover_venues(city, kind, limit)  # no app-wide lock: a slow public server must not block other requests
        return out(res)

    @app.post("/api/travel/flights")
    def travel_flights():
        """Indicative fares and schedules from fli. Nothing is booked or paid."""
        b = body(FlightBody)
        check_date(b.date, today_fn())
        res = em.search_flights(b.origin, b.destination, b.date, adults=b.adults, cabin=b.cabin, max_stops=b.max_stops, limit=b.limit)
        return out(res)

    @app.get("/api/places/status")
    def places_status():
        return jsonify({"configured": em.places.configured()})

    @app.get("/api/places")
    def places_get():
        """Ratings and review counts (optionally reviews, which cost more) for a service in a city. Opinions, not prices."""
        return out(em.find_places(request.args.get("kind", ""), request.args.get("city", ""), request.args.get("reviews") == "1"))

    @app.get("/api/explore/status")
    def explore_status():
        return jsonify({"configured": bool(em.router_clients())})

    @app.post("/api/explore")
    def explore():
        """Free text in, one read-only search out. A model picks the search; the same validators as the forms check it; nothing is written."""
        r = explore_route(em, body(ExploreBody).text, today_fn(), em.router_clients())
        return jsonify({"action": r.action, "params": r.params, "result": r.result, "note": r.note, "model": r.model})

    @app.post("/api/deals")
    def deals():
        """The agent searches the web, reads pages safely and ranks advertised prices. Leads only: not quotes, and no plan price changes."""
        return out(em.find_deals(body(DealQuery)))  # no app-wide lock: this waits on the network

    @app.post("/api/travel/trip")
    def travel_trip():
        """Give the stops and nights; the system tries the orders and dates, searches each leg once and ranks packages. Nothing is booked."""
        res = em.plan_trip(body(TripRequest), today_fn())  # no app-wide lock: this waits on the network
        return out(res)

    @app.post("/api/travel/multi-city")
    def travel_multi_city():
        """Built from separate one-way searches (Google does not serve multi-city to fli); the response says so."""
        b = body(MultiCityBody)
        for leg in b.legs:
            check_date(leg.date, today_fn())
        res = em.search_multi_city([(l.origin, l.destination, l.date) for l in b.legs], adults=b.adults, cabin=b.cabin, max_stops=b.max_stops, limit=b.limit)
        return out(res)

    @app.post("/api/tradeoffs")
    def tradeoffs():
        b = body(PlanBody)
        with lock:
            report = em.tradeoffs(b.request, today=today_fn(), guest_list_id=b.guest_list_id)
        return out(report)

    @app.post("/api/tradeoffs/apply")
    def apply_tradeoff():
        """Runs the chosen option through the full pipeline; only ``option.request`` is used, and it is re-validated."""
        b = body(ApplyBody)
        with lock:
            outcome = em.apply_tradeoff(b.option, today=today_fn(), guest_list_id=b.guest_list_id)
        payload = {
            "status": outcome.status,
            "plan": json.loads(outcome.plan.model_dump_json()) if outcome.plan else None,
            "violations": [v.model_dump() for v in outcome.violations],
            "tasks": [{"id": t.id, "agent": t.agent.value, "status": t.status.value, "depends_on": list(t.depends_on)} for t in outcome.task_graph.tasks],
        }
        return jsonify(payload), (201 if outcome.plan else 422)

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

    # The agent's background loop: send what is approved and read replies. Off unless a channel is set up (or EVENTMATE_POLL_SECONDS says otherwise).
=======
>>>>>>> quality/split-app
    if shared:
        poll_seconds = 0.0  # visitors have no mailbox or gateway; there is nothing to poll or send

        def sweep_round() -> int:
            """Empty workspaces nobody has used for a few hours go, so a flood of throwaway workspaces cannot hold every slot. One that holds any data is never removed here."""
            hours = max(1, int(os.environ.get("EVENTMATE_WS_EMPTY_IDLE_HOURS", "6") or 6))
            return len(registry.sweep_idle_empty(hours * 3600))

        app.config["WORKSPACE_SWEEP"] = sweep_round
        sweep_every = float(os.environ.get("EVENTMATE_WS_SWEEP_SECONDS", "600") or 600)
        if sweep_every > 0:
            def sweep_loop():
                while True:
                    time.sleep(sweep_every)
                    try:
                        sweep_round()
                    except Exception as exc:  # one failed sweep must not end the loop
                        print(f"workspace sweep failed: {type(exc).__name__}", file=sys.stderr)

            threading.Thread(target=sweep_loop, name="eventmate-workspace-sweep", daemon=True).start()
    elif poll_seconds is None:
        raw = os.environ.get("EVENTMATE_POLL_SECONDS", "").strip()
        ready = any(c.status().can_send or c.status().can_receive for c in local.em.conversations.channels.values())
        poll_seconds = float(raw) if raw.isdigit() else (60.0 if ready else 0.0)

    if poll_seconds > 0:
        stop = threading.Event()

        def poll_round():
            try:
                with local.lock:
                    local.em.background_round()
                poll_state["last"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            except Exception as exc:  # one failed round must not end the agent; the next round retries
                poll_state["failures"] += 1
                print(f"conversation poll failed: {type(exc).__name__}", file=sys.stderr)

        def loop():
            while not stop.wait(max(5.0, poll_seconds)):
                poll_round()

        threading.Thread(target=loop, name="eventmate-conversations", daemon=True).start()
        app.config["POLL_STOP"], app.config["POLL_ROUND"] = stop, poll_round

    return app
