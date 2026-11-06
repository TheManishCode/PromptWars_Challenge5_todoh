"""to;doh-the event planner web app: ``create_app`` builds the Flask app (workspace resolution, guards, limits, observability) and registers the route groups in
``eventmate/web/routes``. Module-level pieces are in ``eventmate/web/base.py``; the security model is described there."""

from __future__ import annotations

from types import SimpleNamespace

import hashlib
import hmac
import json
import re
import os
import secrets
import sys
import threading
import time
from datetime import date, datetime, UTC
from pathlib import Path
from collections.abc import Callable
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
from eventmate.web.commit_queue import CommitQueue
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
    VOICE_STREAMS_MAX,
    WORKSPACE_EXEMPT,
    _PLAN_ID,
)
__all__ = ["AUDIO_UPLOAD_LIMIT", "GUEST_UPLOAD_LIMIT", "LOOPBACK", "STATIC_FILES", "IntakeConfirmBody", "create_app"]  # what other modules and the tests import from here
from eventmate.web.routes import assistant_plans, changes, errors, evidence, ideas_sourcing, ledger_external, missions, outreach_jobs, pages, plan_extras, plan_versions, staging, voice_guests, watches, workspace_ops


def create_app(
    em: EventMate | None = None,
    api_token: str | None = None,
    rate_limit_per_minute: int = 60,
    today_fn: Callable[[], date] = date.today,
    allowed_hosts: set | None = None,
    assistant: Assistant | None = None,
    principal: Principal = LOCAL_ORGANIZER,
    poll_seconds: float | None = None,
    voice: VoiceService | None = None,
    transcriber: Transcriber | None = None,
    registry: WorkspaceRegistry | None = None,
    wall_store: WallStore | None = None,
    wall_limits: WallLimits | None = None,
    commit_inline: bool = False,
) -> Flask:
    """Local mode (no ``registry``): one workspace, as before. Shared mode: ``registry`` gives every visitor a workspace of their own, found from a cookie."""

    app = Flask(__name__, static_folder=None)

    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024

    shared = registry is not None

    local: Workspace | None = None

    if not shared:
        if em is None:
            load_env()  # keys first, so the model client below can see them
            em = EventMate(run_id=f"web-{date.today():%Y%m%d}", llm=client_from_env())
        assistant = assistant or Assistant.from_env(em)
        local = Workspace("local", em, assistant, voice or VoiceService(assistant, em), threading.RLock(), principal, secrets.token_urlsafe(32), date.today().isoformat())

    if registry is not None:
        data_root = registry.root
    elif em is not None:
        data_root = em.store.root
    else:
        raise RuntimeError("a web app needs a workspace registry or a single EventMate")
    transcriber = transcriber or Transcriber(engine_from_env(os.environ), data_root / "voice" / "tmp")

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

    poll_state: dict[str, object] = {"last": None, "failures": 0}  # for the owner status route: when the last background round finished, and how many failed

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

    wall: Wall | None = None

    if os.environ.get("EVENTMATE_WALL", "on").lower() != "off":
        wall_db = Path(os.environ.get("EVENTMATE_WALL_DB") or (registry.root if registry is not None else data_root.parent) / "wall_data" / "wall.sqlite3")
        bearer_on_wall = bool(api_token) and os.environ.get("EVENTMATE_WALL_BEARER") == "1"  # off by default: a public wall is exempt from the bearer token
        wall = Wall(app, wall_store or WallStore(wall_db, WallConfig.from_env(os.environ)), wall_limits,
                    bearer_check=(lambda req: hmac.compare_digest(req.headers.get("Authorization", ""), f"Bearer {api_token}")) if bearer_on_wall else None,
                    client_ip=caller, secure_cookies=shared)  # the wall's limits count the real caller (trusted proxy header, IPv6 as a /64), and in shared mode its cookie is always Secure

    csrf_gets = {"/api/workspace/export"}  # spends no quota, but hands over everything in the workspace, so a page on another site must not be able to fetch it

    quota_gets = {"/api/deals", "/api/explore", "/api/travel/flights", "/api/travel/cheapest-dates", "/api/travel/multi-city", "/api/travel/trip", "/api/discover/venues", "/api/weather", "/api/places"}

    def spends_quota(path: str) -> bool:
        return path in quota_gets or re.fullmatch(r"/api/places/[A-Za-z0-9_-]{8,200}", path) is not None or (path.startswith("/api/plans/") and path.endswith(("/weather", "/weather-advice")))

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
            return wall.guard(request)  # the wall authenticates on its own: exact allow-list, no private token or CSRF, no prefix exemption
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

    def version_arg(name: str = "version") -> int | None:
        raw = request.args.get(name)
        if raw is None:
            return None
        if not raw.isdigit() or len(raw) > 4:
            raise ValueError(f"{name} must be a positive integer")
        return int(raw)

    def out(model, code: int = 200):
        return Response(model.model_dump_json(), code, mimetype="application/json")

    def asset_version() -> str:
        """A short hash of every served file's name, size and time. The page names each script, style, image and video with it (``?v=``), so a changed file gets a new address:
        a phone that cached last week's script under a one-hour rule would run it against this week's markup."""
        h = hashlib.sha256()
        for name in sorted(STATIC_FILES):
            path = STATIC_DIR / name
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
    k = SimpleNamespace(P=P, _err=_err, caller=caller, asset_version=asset_version, assistant=assistant, audio_upload=audio_upload, body=body, em=em, heavy=heavy, local=local, lock=lock, metrics=metrics, no_body_or_empty_object=no_body_or_empty_object, out=out, pid=pid, plan_response=plan_response, poll_state=poll_state, registry=registry, shared=shared, started_at=started_at, stream_slots=stream_slots, today_fn=today_fn, transcriber=transcriber, version_arg=version_arg, voice_svc=voice_svc, workspace_limit=workspace_limit, commit_queue=CommitQueue(inline=commit_inline))
    for module in (errors, pages, workspace_ops, assistant_plans, staging, plan_extras, outreach_jobs, ideas_sourcing, voice_guests, ledger_external, plan_versions, missions, evidence, changes, watches):
        module.register(app, k)


    if registry is not None:
        idle_registry: WorkspaceRegistry = registry
        poll_seconds = 0.0  # visitors have no mailbox or gateway; there is nothing to poll or send

        def sweep_round() -> int:
            """Empty workspaces nobody has used for a few hours go, so a flood of throwaway workspaces cannot hold every slot. One that holds any data is never removed here."""
            hours = max(1, int(os.environ.get("EVENTMATE_WS_EMPTY_IDLE_HOURS", "6") or 6))
            return len(idle_registry.sweep_idle_empty(hours * 3600))

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
    elif local is not None and poll_seconds is None:
        raw = os.environ.get("EVENTMATE_POLL_SECONDS", "").strip()
        ready = any(c.status().can_send or c.status().can_receive for c in local.em.conversations.channels.values())
        poll_seconds = float(raw) if raw.isdigit() else (60.0 if ready else 0.0)

    if poll_seconds > 0:
        stop = threading.Event()

        def poll_round():
            try:
                with local.lock:
                    local.em.background_round()
                poll_state["last"] = datetime.now(UTC).isoformat(timespec="seconds")
            except Exception as exc:  # one failed round must not end the agent; the next round retries
                poll_state["failures"] += 1
                print(f"conversation poll failed: {type(exc).__name__}", file=sys.stderr)

        def loop():
            while not stop.wait(max(5.0, poll_seconds)):
                poll_round()

        threading.Thread(target=loop, name="eventmate-conversations", daemon=True).start()
        app.config["POLL_STOP"], app.config["POLL_ROUND"] = stop, poll_round

    return app
