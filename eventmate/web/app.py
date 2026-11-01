"""to;doh-the event planner web UI + JSON API (Flask).

Security model (local-first tool):
- binds to loopback by default; ``Host`` must be an allowed loopback name
  (blocks DNS rebinding);
- every write needs the per-process CSRF token (header) *and* JSON content type,
  and a cross-origin ``Origin`` is refused;
- optional bearer token (``EVENTMATE_WEB_TOKEN``) for the API;
- strict CSP (no inline script/style, no third-party origins), nosniff,
  no-referrer, no-store on API responses;
- 64 KB body cap, per-client write rate limit, allow-listed static files;
- unexpected errors return a generic message; details go to the server log only.
All to;doh-the event planner calls run under one lock: the planner and loggers are not designed
for concurrent mutation of the same plan.
"""

from __future__ import annotations

import errno
import gzip
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Literal, Optional

from flask import Flask, Response, abort, g, jsonify, redirect, request, send_file
from werkzeug.local import LocalProxy
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from eventmate.planning import intake
from eventmate import arcade_pulse as _pulse_mod
from eventmate.arcade_pulse import pulse as _arcade_pulse
from eventmate.voice import audio as voice_audio
from eventmate.voice.notes import NotesDisabled
from eventmate.voice.transcribe import Transcriber, TranscribeError, engine_from_env
from eventmate.voice.turns import ID as VOICE_ID, LANGUAGE as VOICE_LANGUAGE, VoiceError, VoiceService
from eventmate.state import build_state
from eventmate.guestlist import MAX_BYTES as MAX_GUEST_BYTES, GuestListTooLarge, StalePreview
from eventmate.retention import PlanBusy
from eventmate.assistant.agent import Assistant, AssistantError
from eventmate.assistant.approvals import ApprovalError
from eventmate.assistant.identity import LOCAL_ORGANIZER, Perm, Principal
from eventmate.agents.guest import GuestDataError
from eventmate.agents.manager import RequestParseError
from eventmate.agents.vendor import DataIntegrityError
from eventmate.env import load_env
from eventmate.integrations.explore import ExploreError, ExploreNotConfigured, route as explore_route
from eventmate import consumption
from eventmate import migrations, transfer
from eventmate.integrity import StoreDamaged
from eventmate.web import hosting, limits, observe
from eventmate.workspaces import COOKIE, COOKIE_MAX_AGE, Workspace, WorkspaceError, WorkspaceRegistry
from eventmate.llm import METER, LLMError, client_from_env
from eventmate.plan_store import PlanIntegrityError
from eventmate.schemas import MAX_BUDGET_INR, CommunityDetails, DealQuery, TripRequest, MAX_MULTI_CITY_LEGS, Disruption, HiddenCost, LedgerKind, ObservationSource, TradeoffOption, VendorCategory
from eventmate.integrations.discovery import DiscoveryError
from eventmate.integrations.places import PlacesError, PlacesNotConfigured
from eventmate.service import EventMate
from eventmate.integrations.weather import WeatherError, WeatherNotConfigured
from eventmate.followthrough.jobs import StaleJob
from eventmate.followthrough.worker import digest
from eventmate.sourcing.search import SourceNotSupported
from eventmate.sourcing.suppression import NeedsAcknowledgement
from eventmate.integrations.websearch import SearchError
from eventmate.integrations.travel import CABINS, TravelError, check_date
from eventmate.wall.limits import WallLimits
from eventmate.wall.routes import Wall
from eventmate.wall.store import WallConfig, WallStore

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = {
    "favicon.svg": "image/svg+xml",
    "avatar.glb": "model/gltf-binary",  # the attendant in the room (docs/ATTRIBUTIONS.md)
    # the /arcade page: a 3D cabinet scene (docs/ARCADE_UI.md)
    "cabinet.glb": "model/gltf-binary", "arcade.css": "text/css; charset=utf-8", "arcade.js": "text/javascript; charset=utf-8",
    "arcade3d.js": "text/javascript; charset=utf-8", "three.min.js": "text/javascript; charset=utf-8", "CopyShader.js": "text/javascript; charset=utf-8",
    "LuminosityHighPassShader.js": "text/javascript; charset=utf-8", "EffectComposer.js": "text/javascript; charset=utf-8", "RenderPass.js": "text/javascript; charset=utf-8",
    "ShaderPass.js": "text/javascript; charset=utf-8", "MaskPass.js": "text/javascript; charset=utf-8", "UnrealBloomPass.js": "text/javascript; charset=utf-8",
    "GLTFLoader.js": "text/javascript; charset=utf-8",
    # back-alley dressing for the /arcade room (attribution + licenses: docs/ATTRIBUTIONS.md)
    "alley_van.glb": "model/gltf-binary", "alley_dumpster.glb": "model/gltf-binary", "alley_candy.glb": "model/gltf-binary",
    "vend_nuka.glb": "model/gltf-binary", "vend_retro.glb": "model/gltf-binary",
    "vend_cat.glb": "model/gltf-binary", "wall_mural.glb": "model/gltf-binary", "wall_posters.glb": "model/gltf-binary",
    # the cabinet CRT's night road (car/road/barrier - attribution in docs/ATTRIBUTIONS.md)
    "holo_table.glb": "model/gltf-binary", "secret_dossier.glb": "model/gltf-binary", "road.glb": "model/gltf-binary", "car.glb": "model/gltf-binary", "barrier.glb": "model/gltf-binary",
    # the same models optimised for phones and small computers (textures <= 1024): tools/assets/optimize_models.sh
    "cabinet.low.glb": "model/gltf-binary", "alley_van.low.glb": "model/gltf-binary", "alley_dumpster.low.glb": "model/gltf-binary", "alley_candy.low.glb": "model/gltf-binary", "vend_nuka.low.glb": "model/gltf-binary", "vend_retro.low.glb": "model/gltf-binary", "wall_mural.low.glb": "model/gltf-binary", "wall_posters.low.glb": "model/gltf-binary", "holo_table.low.glb": "model/gltf-binary", "secret_dossier.low.glb": "model/gltf-binary",
}
# blob: (which only same-origin script can mint) is allowed for recordings and model textures instead of any external source, and wasm-unsafe-eval permits
# WebAssembly compilation only, never string eval. Both were kept when the classic page and its model viewer (and the one style hash it needed) were removed;
# neither has been shown to be unneeded by the room, so narrowing them is left until the real models have been run.
LONG_CACHE = frozenset({"avatar.glb", "cabinet.glb", "three.min.js", "alley_van.glb", "alley_dumpster.glb", "alley_candy.glb", "vend_nuka.glb", "vend_retro.glb", "vend_cat.glb", "wall_mural.glb", "wall_posters.glb", "road.glb", "car.glb", "barrier.glb", "holo_table.glb", "secret_dossier.glb", "cabinet.low.glb", "alley_van.low.glb", "alley_dumpster.low.glb", "alley_candy.low.glb", "vend_nuka.low.glb", "vend_retro.low.glb", "wall_mural.low.glb", "wall_posters.low.glb", "holo_table.low.glb", "secret_dossier.low.glb"})  # large and unchanging: not re-sent on every page load
# third-party files live in static/vendor/ but are served from the same flat /static/<name> URLs
VENDOR_FILES = frozenset({"three.min.js", "CopyShader.js", "LuminosityHighPassShader.js", "EffectComposer.js", "RenderPass.js", "ShaderPass.js", "MaskPass.js", "UnrealBloomPass.js", "GLTFLoader.js"})
GZIP_MIN = 1024  # below this the header overhead outweighs the saving
GZIP_MAX = 3 * 1024 * 1024  # above this (the scene models) a file is sent as it is: already compressed, and compressing 50 MB in the request would stall it and sit in memory
_gzip_cache: Dict[tuple, bytes] = {}  # (name, mtime_ns) -> compressed bytes; the file set is fixed and small
WORKSPACE_EXEMPT = ("/api/owner/status", "/metrics", "/api/wall/")  # /api routes with their own authentication: no workspace is looked up or made for them
# POST routes that send, or let something send later, through the owner's mailbox or gateway; refused for every guest workspace
SENDING_ROUTES = re.compile(
    r"/api/plans/[A-Za-z0-9_-]{1,64}/outreach/[A-Za-z0-9_-]{1,64}/(?:approve|followup)|/api/plans/[A-Za-z0-9_-]{1,64}/autoreply|/api/inbox/poll"
    r"|/api/jobs/tick|/api/jobs/[A-Za-z0-9_-]{1,64}/approve-requests|/api/jobs/[A-Za-z0-9_-]{1,64}/items/[A-Za-z0-9_-]{1,64}/approve-nudge|/api/notify/(?:propose|confirm)"
)
LOOPBACK = {"127.0.0.1", "localhost", "[::1]", "::1"}
CSP = (
    "default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; img-src 'self' data: blob:; "
    "media-src 'self' blob:; connect-src 'self' blob:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
HEAVY_MAX = 4  # concurrent network-bound searches
_PLAN_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
GUEST_UPLOAD_LIMIT = 640 * 1024  # a 256 KB CSV can grow when JSON-escaped; the parser enforces the real 256 KB limit
AUDIO_UPLOAD_LIMIT = voice_audio.MAX_BYTES + 256 * 1024  # a recording plus the multipart wrapper; the audio check enforces the real 5 MB
VOICE_STREAMS_MAX = 4  # concurrent server-sent event streams
# the only routes that take a body larger than 64 KB, or multipart instead of JSON: (path pattern, limit)
UPLOAD_ROUTES = (
    (re.compile(r"/api/plans/[A-Za-z0-9_-]{1,64}/guests/preview"), GUEST_UPLOAD_LIMIT),
    (re.compile(r"/api/voice/transcribe"), AUDIO_UPLOAD_LIMIT),
    (re.compile(r"/api/plans/[A-Za-z0-9_-]{1,64}/voice-notes"), AUDIO_UPLOAD_LIMIT),
    (re.compile(r"/api/workspace/import"), transfer.MAX_ARCHIVE + 256 * 1024),  # an archive plus the multipart wrapper; the importer enforces the real 20 MB
)
log = logging.getLogger("eventmate.web")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanBody(_Strict):
    request: str = Field(min_length=1, max_length=2000)
    guest_list_id: Optional[str] = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")


class IntakeBody(_Strict):
    messages: List[str] = Field(min_length=1, max_length=intake.MAX_MESSAGES)
    draft: Optional[intake.IntakeDraft] = None


class IntakeConfirmBody(_Strict):
    draft: intake.IntakeDraft
    raw_text: str = Field(default="", max_length=2000)
    source: str = Field(default="rule:parser", max_length=80, pattern=r"^[A-Za-z0-9:._-]+$")
    guest_list_id: Optional[str] = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")


class AskBody(_Strict):
    plan_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    question: str = Field(min_length=1, max_length=1000)


class QuoteBody(_Strict):
    category: VendorCategory
    source_type: ObservationSource
    entered_by: str = Field(min_length=1, max_length=80)
    expires_at: date
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ref: str = Field(default="", max_length=200)
    amount_inr: int = Field(gt=0)
    basis: Literal["total", "per_plate"]
    guest_count: Optional[int] = Field(default=None, ge=1)
    inclusions: List[str] = Field(default_factory=list, max_length=30)
    exclusions: List[str] = Field(default_factory=list, max_length=30)
    hidden_costs: List[HiddenCost] = Field(default_factory=list, max_length=20)
    note: str = Field(default="", max_length=200)


class ExploreBody(_Strict):
    text: str = Field(min_length=1, max_length=600)


class LeadBody(_Strict):
    kind: Literal["caterer", "venue", "photographer", "hotel"]
    url: str = Field(max_length=2048)
    domain: str = Field(max_length=253)
    label: str = Field(max_length=100)
    unit: Literal["per_plate", "per_night", "per_day", "per_hour", "per_event"]
    low_inr: int
    high_inr: int
    note: str = Field(default="", max_length=200)


class OutreachBody(_Strict):
    category: VendorCategory
    channel: Literal["email", "whatsapp"]
    recipient: str = Field(min_length=1, max_length=254)
    sender_name: str = Field(min_length=1, max_length=80)


class ApproveBody(_Strict):
    content_hash: str = Field(min_length=64, max_length=64)


class FollowupBody(_Strict):
    text: str = Field(min_length=1, max_length=1000)
    sender_name: str = Field(min_length=1, max_length=80)


class RequestAllBody(_Strict):
    sender_name: str = Field(min_length=1, max_length=80)


class ContactBody(_Strict):
    vendor_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    email: Optional[str] = Field(default=None, max_length=254)
    whatsapp: Optional[str] = Field(default=None, max_length=30)
    preferred: Literal["email", "whatsapp"] = "whatsapp"


class AutoreplyBody(_Strict):
    sender_name: str = Field(min_length=1, max_length=80)
    hours: int = Field(default=72, ge=1, le=168)
    max_per_thread: int = Field(default=3, ge=1, le=5)
    max_total: int = Field(default=10, ge=1, le=30)


class JobBody(_Strict):
    category: VendorCategory
    vendor_ids: List[str] = Field(min_length=1, max_length=10)
    goal: str = Field(min_length=1, max_length=300)
    sender_name: str = Field(min_length=1, max_length=80)
    deadline_days: int = Field(default=7, ge=1, le=60)
    min_quotes: int = Field(default=3, ge=1, le=10)
    max_amount_inr: Optional[int] = Field(default=None, gt=0)
    max_nudges_per_vendor: int = Field(default=2, ge=0, le=5)
    reply_window_hours: int = Field(default=24, ge=1, le=168)


class ConfirmClaimBody(_Strict):
    valid_days: int = Field(ge=1, le=90)


class ResolveItemBody(_Strict):
    note: str = Field(default="", max_length=200)


class IdeasBody(_Strict):
    preferences: List[str] = Field(default_factory=list, max_length=5)


class DoneBody(_Strict):
    done: bool


class CommunityItemBody(_Strict):
    status: Literal["not_verified", "confirmed_by_user", "not_applicable_by_user"]
    by: str = Field(min_length=1, max_length=60)
    on: date
    note: str = Field(default="", max_length=200)


class AccessBody(_Strict):
    features: List[Literal["step_free_access", "accessible_toilet", "reserved_seating", "hearing_support", "accessible_parking"]] = Field(default_factory=list, max_length=5)
    evidence_ref: str = Field(min_length=1, max_length=200)
    entered_by: str = Field(min_length=1, max_length=80)


class NotifyProposeBody(_Strict):
    destination: str = Field(min_length=1, max_length=254)
    include_vendor_names: bool = False
    include_amounts: bool = False
    include_raw_text: bool = False


class NotifyConfirmBody(_Strict):
    code: str = Field(min_length=1, max_length=128)


class ShortlistSearchBody(_Strict):
    category: VendorCategory
    source: Optional[Literal["osm", "places"]] = None
    limit: int = Field(default=10, ge=1, le=20)


class WebsiteBody(_Strict):
    url: str = Field(min_length=1, max_length=2048)


class ConfirmContactBody(_Strict):
    by: str = Field(min_length=1, max_length=60)
    edited_value: Optional[str] = Field(default=None, max_length=254)
    acknowledge_bounce: bool = False  # required to confirm an address that bounced earlier


class SuppressBody(_Strict):
    channel: Literal["email", "whatsapp"]
    value: str = Field(min_length=3, max_length=254)
    by: str = Field(min_length=1, max_length=80)
    note: str = Field(default="", max_length=200)


class LiftBody(_Strict):
    by: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=200)
    acknowledge_reason: bool = False  # required for an address that bounced: you confirm you have read why


class ByBody(_Strict):
    by: str = Field(min_length=1, max_length=80)


class TestVendorBody(_Strict):
    name: str = Field(min_length=1, max_length=120)
    category: VendorCategory
    email: str = Field(min_length=3, max_length=254)
    owns_address: bool
    by: str = Field(min_length=1, max_length=60)
    city: Optional[str] = Field(default=None, max_length=80)


class SessionClearBody(_Strict):
    plan_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")


class EmptyBody(_Strict):
    pass


class GuestCsvBody(_Strict):
    csv: str = Field(min_length=1, max_length=600_000)


class GuestCommitBody(_Strict):
    preview_token: str = Field(min_length=1, max_length=100)


class VoiceSessionBody(_Strict):
    plan_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")


class VoiceTurnBody(_Strict):
    session_id: str = Field(min_length=1, max_length=100)
    turn_id: str = Field(min_length=1, max_length=100)
    generation: StrictInt
    text: str = Field(min_length=1, max_length=4000)
    input_mode: str = Field(max_length=20)
    confidence: Optional[float] = None
    language: Optional[str] = Field(default=None, max_length=12)


class VoiceCancelBody(_Strict):
    session_id: str = Field(min_length=1, max_length=100)
    generation: StrictInt
    playback_chars: StrictInt


class DeletePlanBody(_Strict):
    confirm: str = Field(min_length=1, max_length=64)


class FeedbackBody(_Strict):
    value: int = Field(ge=-1, le=1)
    comment: str = Field(default="", max_length=200)


class ApproveAnswerBody(_Strict):
    trace_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    answer: Optional[str] = Field(default=None, min_length=1, max_length=1500)


class PlanRefBody(_Strict):
    plan_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")


class ApplyBody(_Strict):
    option: TradeoffOption
    guest_list_id: Optional[str] = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")


class LedgerBody(_Strict):
    kind: LedgerKind
    category: VendorCategory
    amount: int = Field(gt=0, le=MAX_BUDGET_INR)
    note: str = Field(default="", max_length=200)


_IATA_FIELD = dict(pattern=r"^[A-Z]{3}$")


class FlightBody(_Strict):
    origin: str = Field(**_IATA_FIELD)
    destination: str = Field(**_IATA_FIELD)
    date: date
    adults: int = Field(default=1, ge=1, le=9)
    cabin: Literal[CABINS] = "economy"
    max_stops: Optional[int] = Field(default=None, ge=0, le=2)
    limit: int = Field(default=10, ge=1, le=30)


class LegBody(_Strict):
    origin: str = Field(**_IATA_FIELD)
    destination: str = Field(**_IATA_FIELD)
    date: date


class MultiCityBody(_Strict):
    legs: List[LegBody] = Field(min_length=2, max_length=MAX_MULTI_CITY_LEGS)
    adults: int = Field(default=1, ge=1, le=9)
    cabin: Literal[CABINS] = "economy"
    max_stops: Optional[int] = Field(default=None, ge=0, le=2)
    limit: int = Field(default=5, ge=1, le=10)


class ConfirmBody(_Strict):
    category: VendorCategory


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
    # What the routes below call ``em``, ``assistant``, ``voice_svc`` and ``lock`` is whatever belongs to the workspace of the request being served.
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

    # The wall: its own SQLite file next to (not inside) the plan directory, so deleting a plan can never touch it. EVENTMATE_WALL=off removes the routes.
    wall: Optional[Wall] = None
    if os.environ.get("EVENTMATE_WALL", "on").lower() != "off":
        wall_db = Path(os.environ.get("EVENTMATE_WALL_DB") or (registry.root if shared else local.em.store.root.parent) / "wall_data" / "wall.sqlite3")
        bearer_on_wall = bool(api_token) and os.environ.get("EVENTMATE_WALL_BEARER") == "1"  # off by default: a public wall is exempt from the bearer token
        wall = Wall(app, wall_store or WallStore(wall_db, WallConfig.from_env(os.environ)), wall_limits,
                    bearer_check=(lambda req: hmac.compare_digest(req.headers.get("Authorization", ""), f"Bearer {api_token}")) if bearer_on_wall else None,
                    client_ip=caller, secure_cookies=shared)  # the wall's limits count the real caller (trusted proxy header, IPv6 as a /64), and in shared mode its cookie is always Secure

    # ------------------------------------------------------------ guards

    # GET routes that spend a paid or rate-limited quota also need the CSRF header: a page on another site can fire a plain GET at a local
    # server (an image tag is enough) but cannot add a custom header without a CORS preflight that this server never grants.
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

    # Routes that wait on the network are limited to a few at a time, so a burst cannot pin every worker thread (ADR-037), and one workspace holds only part of them.
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

    @app.errorhandler(VoiceError)
    def _voice_error(exc):
        return _err(exc.status, str(exc)[:300])

    @app.errorhandler(TranscribeError)
    def _transcribe_error(exc):
        return _err(exc.status, str(exc)[:300], code=exc.code)

    @app.errorhandler(voice_audio.AudioRejected)
    def _audio_rejected(exc):
        return _err(exc.status, str(exc)[:300], code={413: "audio_too_large", 415: "bad_audio_type"}.get(exc.status, "bad_audio"))

    @app.errorhandler(NotesDisabled)
    def _notes_disabled(exc):
        return _err(503, "keeping voice notes is not turned on")

    @app.errorhandler(GuestListTooLarge)
    def _guests_too_large(exc):
        return _err(413, str(exc)[:200], code="too_large", limit_bytes=MAX_GUEST_BYTES, scope="payload")

    @app.errorhandler(StalePreview)
    def _stale_preview(exc):
        return _err(409, str(exc)[:200])

    @app.errorhandler(PlanBusy)
    def _plan_busy(exc):
        return _err(409, str(exc)[:300])

    @app.errorhandler(RequestParseError)
    def _parse(exc):
        return _err(422, "request could not be parsed", problems=list(exc.problems))

    @app.errorhandler(ValidationError)
    def _validation(exc):
        return _err(422, "invalid input", details=[{"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in exc.errors()][:10])

    @app.errorhandler(ExploreNotConfigured)
    def _explore_off(exc):
        return _err(503, str(exc)[:300])

    @app.errorhandler(ExploreError)
    def _explore(exc):
        return _err(422, str(exc)[:400])

    @app.errorhandler(consumption.ConsumptionError)
    def _consumption(exc):
        return _err(429, str(exc)[:300], code=exc.code)

    @app.errorhandler(LLMError)
    def _llm(exc):
        return _err(502, str(exc)[:300])

    @app.errorhandler(PlacesNotConfigured)
    def _places_off(exc):
        return _err(503, str(exc)[:300])

    @app.errorhandler(PlacesError)
    def _places(exc):
        return _err(502, str(exc)[:300])

    @app.errorhandler(SearchError)
    def _search(exc):
        return _err(502, str(exc)[:300])

    @app.errorhandler(WeatherNotConfigured)
    def _weather_off(exc):
        return _err(503, str(exc)[:300])

    @app.errorhandler(WeatherError)
    def _weather(exc):
        return _err(502, str(exc)[:300])

    @app.errorhandler(DiscoveryError)
    def _discovery(exc):
        return _err(502, str(exc)[:300])

    @app.errorhandler(TravelError)
    def _travel(exc):
        return _err(502, str(exc)[:300])

    @app.errorhandler(GuestDataError)
    def _guests(exc):
        return _err(422, "unknown or invalid guest list")

    @app.errorhandler(DataIntegrityError)
    def _data(exc):
        log.error("vendor data integrity failure: %s", exc)
        return _err(500, "vendor data failed validation")

    @app.errorhandler(FileNotFoundError)
    def _nf(exc):
        return _err(404, "not found", code="not_found")

    @app.errorhandler(PlanIntegrityError)
    def _integrity(exc):
        log.error("plan integrity failure (request %s): %s", g.get("rid", "-"), exc)  # which file: server log only
        return _err(503, "stored plan failed its integrity check; nothing was changed. The owner can run `python -m eventmate verify`", code="plan_integrity", request_id=g.get("rid"))

    @app.errorhandler(413)
    def _too_big(exc):
        limit = g.get("upload_limit") or 64 * 1024
        return _err(413, "request too large", code="too_large", limit_bytes=limit, scope="envelope")  # the whole body, as opposed to the file inside it

    @app.errorhandler(405)
    def _method(exc):
        return _err(405, "method not allowed")

    @app.errorhandler(404)
    def _404(exc):
        return _err(404, "not found", code="not_found")  # the one body every miss gets, so a hidden route cannot be told from an unknown one

    @app.errorhandler(NeedsAcknowledgement)
    def _needs_ack(exc):
        return jsonify({"error": str(exc)[:600], "needs_acknowledgement": True, "reason": exc.reason}), 409

    @app.errorhandler(StaleJob)
    def _stale(exc):
        return _err(409, "this job changed while you were looking at it; reload and try again")

    @app.errorhandler(ValueError)
    def _value(exc):
        return _err(400, str(exc)[:300])

    @app.errorhandler(RecursionError)
    def _too_deep(exc):
        return _err(400, "the request is nested too deeply", code="bad_request")  # a body or value nested past the parser's limit is the caller's fault, never a 500

    @app.errorhandler(Exception)
    def _boom(exc):
        if hasattr(exc, "code") and isinstance(exc.code, int):
            return _err(exc.code, getattr(exc, "name", "error"))
        rid = g.get("rid", "-")
        if isinstance(exc, OSError) and exc.errno in (errno.ENOSPC, errno.EDQUOT):
            log.error("storage full (request %s): %s", rid, type(exc).__name__)
            return _err(507, "the server has no room to save this, so the change may not have been made; try again once the owner has freed space. If you were deleting, run the delete again to finish it", code="storage_full", request_id=rid)
        log.exception("unhandled error (request %s)", rid)  # the traceback goes to the server's own log only; the answer names the request and nothing else
        return _err(500, "internal error", code="internal", request_id=rid)

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

    # ------------------------------------------------------------- pages

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

    @app.get("/")
    def index():
        """The arcade room is the only interface (the classic page was removed on the owner's instruction)."""
        version = asset_version()
        html = (STATIC_DIR / "arcade.html").read_text(encoding="utf-8")
        html = re.sub(r'((?:src|href)="/static/[A-Za-z0-9_.-]+\.(?:js|css|svg))"', lambda m: f'{m.group(1)}?v={version}"', html)
        html = html.replace("<head>", f'<head>\n<meta name="asset-version" content="{version}">', 1)
        return Response(html, mimetype="text/html", headers={"Cache-Control": "no-cache"})  # the page itself is always asked for again; what it names is cached for good

    @app.get("/arcade")
    def arcade():
        return redirect("/", 308)  # one interface: the room lives at /, this is only for old links

    @app.get("/static/<name>")
    def static_file(name: str):
        if name not in STATIC_FILES:
            abort(404)
        path = STATIC_DIR / ("vendor" if name in VENDOR_FILES else "") / name
        versioned = request.args.get("v") == asset_version()  # asked for by the address the page was given: it can never be stale, so it may be kept for a year
        cache = "public, max-age=31536000, immutable" if versioned else ("public, max-age=3600" if name in LONG_CACHE else "no-cache")
        if path.stat().st_size > GZIP_MAX:
            resp = send_file(path, mimetype=STATIC_FILES[name].split(";")[0], conditional=True, etag=True, max_age=None)  # streamed, with ETag and Range, never read whole into memory
            resp.headers["Cache-Control"] = cache if versioned else "no-cache"
            return resp
        body = path.read_bytes()
        headers = {"Cache-Control": cache, "Vary": "Accept-Encoding"}
        if len(body) >= GZIP_MIN and "gzip" in request.headers.get("Accept-Encoding", "").lower():
            key = (name, path.stat().st_mtime_ns)
            if key not in _gzip_cache:
                _gzip_cache[key] = gzip.compress(body, compresslevel=9, mtime=0)
            body, headers["Content-Encoding"] = _gzip_cache[key], "gzip"
        return Response(body, mimetype=STATIC_FILES[name].split(";")[0], headers=headers)

    @app.get("/api/session")
    def session():
        return jsonify({"csrf": g.ws.csrf, "today": today_fn().isoformat(), "roles": [r.value for r in P().roles], "guest_lists": list(em.manager.guest.repo.list_ids()),
                        "workspace": {"id": g.ws.id, "created": g.ws.created, "mode": "shared" if shared else "local"}})

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
        """Everything in this workspace as one zip (docs/WORKSPACES.md). Needs the CSRF header; spends no quota."""
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

    # --------------------------------------------------------------- api

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
            turn = em.intake_turn(b.messages, b.draft, today=today_fn())
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

    @app.get("/api/arcade/pulse")
    def arcade_pulse():
        # Read-only observability of the in-flight run; takes no to;doh-the event planner lock
        # so the page can watch while the (lock-held) plan call is running.
        return jsonify(_arcade_pulse.snapshot())

    def plan_response(outcome):
        payload = {
            "status": outcome.status,
            "plan": json.loads(outcome.plan.model_dump_json()) if outcome.plan else None,
            "violations": [v.model_dump() for v in outcome.violations],
            "tasks": [{"id": t.id, "agent": t.agent.value, "status": t.status.value, "depends_on": list(t.depends_on)} for t in outcome.task_graph.tasks],
            "reality": json.loads(outcome.intake.reality.model_dump_json()) if outcome.intake.reality else None,
        }
        return jsonify(payload), (201 if outcome.plan else 422)

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
        """Forget a plan and everything it owns (docs/RETENTION.md). The body must repeat the plan id: a stray call cannot delete anything."""
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
            job = em.jobs.start_job(plan_id, b.category, b.vendor_ids, b.goal, datetime.now(timezone.utc) + timedelta(days=b.deadline_days), b.sender_name,
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

    @app.get("/api/plans/<plan_id>/ideas")
    def ideas_get(plan_id):
        plan_id = pid(plan_id)
        with lock:
            em.store.latest(plan_id)
            v = em.ideas.view(plan_id)
        return jsonify({
            "ideas": [json.loads(i.model_dump_json()) for i in v.visible], "dismissed": list(v.dismissed), "pinned": list(v.pinned),
            "notes": [json.loads(n.model_dump_json()) for n in v.notes], "generated_at": v.generated_at.isoformat() if v.generated_at else None,
            "source": v.source, "facts_used": list(v.facts_used), "notice": v.notice,
        })

    @app.post("/api/plans/<plan_id>/ideas")
    def ideas_generate(plan_id):
        """Personalized suggestions for this plan. Labelled as suggestions: nothing here is a verified offer or changes the plan."""
        plan_id, b = pid(plan_id), body(IdeasBody)
        with lock:
            result = em.ideas.generate(em.store.latest(plan_id), b.preferences)
            for pref in b.preferences:  # a preference typed on the form is a user-stated fact, with its source
                assistant.sessions.add_fact(plan_id, P().user_id, pref, "form:ideas.preferences")
        return out(result, 201)

    @app.post("/api/plans/<plan_id>/ideas/<idea_id>/pin")
    def ideas_pin(plan_id, idea_id):
        plan_id, idea_id = pid(plan_id), pid(idea_id)
        body(EmptyBody)
        with lock:
            em.store.latest(plan_id)
            note = em.ideas.pin(plan_id, idea_id)
        return out(note, 201)

    @app.post("/api/plans/<plan_id>/ideas/<idea_id>/dismiss")
    def ideas_dismiss(plan_id, idea_id):
        plan_id, idea_id = pid(plan_id), pid(idea_id)
        body(EmptyBody)
        with lock:
            em.store.latest(plan_id)
            em.ideas.dismiss(plan_id, idea_id)
        return jsonify({"dismissed": idea_id})

    @app.post("/api/plans/<plan_id>/ideas/<idea_id>/restore")
    def ideas_restore(plan_id, idea_id):
        plan_id, idea_id = pid(plan_id), pid(idea_id)
        body(EmptyBody)
        with lock:
            em.store.latest(plan_id)
            em.ideas.restore(plan_id, idea_id)
        return jsonify({"restored": idea_id})

    @app.post("/api/plans/<plan_id>/notes/<note_id>/done")
    def notes_done(plan_id, note_id):
        plan_id, note_id, b = pid(plan_id), pid(note_id), body(DoneBody)
        with lock:
            em.store.latest(plan_id)
            note = em.ideas.set_done(plan_id, note_id, b.done)
        return out(note)

    @app.get("/api/plans/<plan_id>/digests")
    def digests_get(plan_id):
        """What changed on this plan's vendor jobs and what needs you. Always kept in the app, newest first."""
        plan_id = pid(plan_id)
        with lock:
            em.store.latest(plan_id)
            items = em.digest_store.list(plan_id)
        return jsonify([json.loads(d.model_dump_json()) for d in items])

    @app.post("/api/plans/<plan_id>/digests/<digest_id>/read")
    def digest_read(plan_id, digest_id):
        plan_id, digest_id = pid(plan_id), pid(digest_id)
        body(EmptyBody)
        with lock:
            em.store.latest(plan_id)
            rec = em.digest_store.mark_read(plan_id, digest_id)
        return out(rec)

    @app.get("/api/notify")
    def notify_get():
        with lock:
            st = em.notifier.status()
        return out(st)

    @app.post("/api/notify/propose")
    def notify_propose():
        """Step one of the optional email copy: the exact address and what it may contain. Nothing is sent until the owner confirms."""
        b = body(NotifyProposeBody)
        with lock:
            p = em.notifier.propose(b.destination, b.include_vendor_names, b.include_amounts, b.include_raw_text)
        return out(p, 201)

    @app.post("/api/notify/confirm")
    def notify_confirm():
        b = body(NotifyConfirmBody)
        with lock:
            st = em.notifier.confirm(b.code)
        return out(st)

    @app.post("/api/notify/disable")
    def notify_disable():
        body(EmptyBody)
        with lock:
            st = em.notifier.disable()
        return out(st)

    @app.post("/api/plans/<plan_id>/shortlist/search")
    def shortlist_search(plan_id):
        """Find businesses for this plan's city and category. They are shortlisted, never added to the vendor database, and nothing is contacted."""
        plan_id, b = pid(plan_id), body(ShortlistSearchBody)
        with lock:
            try:
                res = em.search_vendors(plan_id, b.category, b.source, b.limit)
            except SourceNotSupported as exc:
                return jsonify({"status": "not_supported", "error": str(exc)}), 422
        return out(res)

    @app.get("/api/plans/<plan_id>/shortlist")
    def shortlist_get(plan_id):
        plan_id = pid(plan_id)
        with lock:
            items = em.shortlist_view(plan_id)
        return jsonify([json.loads(i.model_dump_json()) for i in items])

    @app.post("/api/plans/<plan_id>/shortlist/<vendor_id>/website")
    def shortlist_website(plan_id, vendor_id):
        plan_id, vendor_id, b = pid(plan_id), pid(vendor_id), body(WebsiteBody)
        with lock:
            em.store.latest(plan_id)
            biz = em.set_business_website(vendor_id, b.url)
        return out(biz)

    @app.post("/api/plans/<plan_id>/shortlist/<vendor_id>/find-contacts")
    def shortlist_find_contacts(plan_id, vendor_id):
        """Read the business's own website for public email addresses. Found addresses are candidates; none is used until you confirm it."""
        plan_id, vendor_id = pid(plan_id), pid(vendor_id)
        body(EmptyBody)
        with lock:
            em.store.latest(plan_id)
            res = em.find_business_contacts(vendor_id)
        return out(res)

    @app.post("/api/plans/<plan_id>/shortlist/<vendor_id>/contacts/<candidate_id>/confirm")
    def shortlist_confirm(plan_id, vendor_id, candidate_id):
        plan_id, vendor_id, candidate_id, b = pid(plan_id), pid(vendor_id), pid(candidate_id), body(ConfirmContactBody)
        with lock:
            em.store.latest(plan_id)
            contact = em.confirm_contact(vendor_id, candidate_id, b.by, b.edited_value, b.acknowledge_bounce)
        return out(contact, 201)

    @app.post("/api/plans/<plan_id>/shortlist/<vendor_id>/contacts/<candidate_id>/reject")
    def shortlist_reject(plan_id, vendor_id, candidate_id):
        plan_id, vendor_id, candidate_id = pid(plan_id), pid(vendor_id), pid(candidate_id)
        body(EmptyBody)
        with lock:
            em.store.latest(plan_id)
            cand = em.reject_contact(vendor_id, candidate_id)
        return out(cand)

    @app.post("/api/plans/<plan_id>/shortlist/test-vendor")
    def shortlist_test_vendor(plan_id):
        """A vendor whose address is yours, for testing. It needs your statement that you own the address and is labelled TEST VENDOR everywhere."""
        plan_id, b = pid(plan_id), body(TestVendorBody)
        with lock:
            plan = em.store.latest(plan_id)
            biz, _ = em.add_test_vendor(b.name, b.city or plan.request.city, b.category, b.email, b.owns_address, b.by, plan_id)
            item = next(i for i in em.shortlist_view(plan_id) if i.business.vendor_id == biz.vendor_id)
        return out(item, 201)

    @app.get("/api/vendors/directory")
    def vendor_directory():
        with lock:
            return jsonify(em.vendor_directory())

    @app.get("/api/vendors/labels")
    def vendor_labels():
        with lock:
            return jsonify(em.vendor_labels())

    @app.get("/api/suppressions")
    def suppressions_get():
        """The do-not-contact list: one entry per address, covering every plan and job."""
        with lock:
            items = em.suppression_list()
        return jsonify([{**json.loads(s.model_dump_json()), "active": s.active, "note": note} for s, note in items])

    @app.post("/api/suppressions")
    def suppressions_add():
        b = body(SuppressBody)
        with lock:
            s = em.suppress_address(b.channel, b.value, b.by, b.note)
        return out(s, 201)

    @app.post("/api/suppressions/<suppression_id>/lift")
    def suppressions_lift(suppression_id):
        """Only you can lift one, and the record says who and why."""
        suppression_id, b = pid(suppression_id), body(LiftBody)
        with lock:
            s = em.lift_suppression(suppression_id, b.by, b.reason, b.acknowledge_reason)
        return out(s)

    @app.get("/api/unmatched")
    def unmatched_get():
        """Stop-like mail and delivery failures that are not tied to anything we sent. They change nothing until you decide."""
        with lock:
            items = em.unmatched_messages()
        return jsonify([json.loads(m.model_dump_json()) for m in items])

    @app.post("/api/unmatched/<message_id>/dismiss")
    def unmatched_dismiss(message_id):
        message_id = pid(message_id)
        body(EmptyBody)
        with lock:
            m = em.dismiss_unmatched(message_id)
        return out(m)

    @app.post("/api/unmatched/<message_id>/suppress")
    def unmatched_suppress(message_id):
        message_id, b = pid(message_id), body(ByBody)
        with lock:
            s = em.suppress_unmatched(message_id, b.by)
        return out(s, 201)

    @app.get("/api/community/profiles")
    def community_profiles():
        """The community subtypes (data in community_profiles.json) for the intake review card."""
        from eventmate.planning.community import load_profiles

        p = load_profiles()
        return jsonify({"default": p.default, "version": p.version, "reviewed": p.reviewed, "profiles": [{"key": k, "label": v.label} for k, v in p.profiles.items()]})

    @app.get("/api/plans/<plan_id>/community")
    def community_get(plan_id):
        plan_id = pid(plan_id)
        with lock:
            status = em.community_status(plan_id, today_fn())
            details = em.store.latest(plan_id).request.community
        return jsonify({**json.loads(status.model_dump_json()), "details": json.loads(details.model_dump_json()) if details else None,
                        "suggested_roles": [{"role": r, "note": n} for r, n in status.suggested_roles]})

    @app.post("/api/plans/<plan_id>/community")
    def community_update(plan_id):
        """Replace the community details as a validated new version, or refuse with repairs. Nothing is saved on a refusal."""
        plan_id, details = pid(plan_id), body(CommunityDetails)
        with lock:
            res = em.update_community(plan_id, details, today_fn())
        return out(res, 200 if res.status == "replanned" else (409 if res.status == "needs_decision" else 422))

    @app.post("/api/plans/<plan_id>/community/items/<item_id>")
    def community_item(plan_id, item_id):
        """The user's mark on one conditional requirement. to;doh-the event planner never marks one itself."""
        plan_id, b = pid(plan_id), body(CommunityItemBody)
        if not re.fullmatch(r"[a-z0-9_]{1,40}", item_id):
            raise ValueError("invalid item id")
        with lock:
            rec = em.set_community_item(plan_id, item_id, b.status, b.by, b.on, b.note, today_fn())
        return out(rec, 201)

    @app.post("/api/venues/<venue_id>/accessibility")
    def venue_accessibility(venue_id):
        """What a venue told the user about its access, with evidence. It turns an unverified item into a pass or a failure on the next check."""
        venue_id, b = pid(venue_id), body(AccessBody)
        with lock:
            rec = em.record_venue_accessibility(venue_id, tuple(b.features), b.evidence_ref, b.entered_by)
        return out(rec, 201)

    @app.get("/api/contacts")
    def contacts_get():
        with lock:
            items = em.vendor_contacts()
        return jsonify([json.loads(c.model_dump_json()) for c in items])

    @app.post("/api/contacts")
    def contacts_set():
        b = body(ContactBody)
        with lock:
            c = em.set_vendor_contact(b.vendor_id, b.email, b.whatsapp, b.preferred)
        return out(c, 201)

    @app.post("/api/plans/<plan_id>/outreach/<draft_id>/sent")
    def outreach_sent(plan_id, draft_id):
        """The user says they sent it. to;doh-the event planner cannot verify that."""
        plan_id, draft_id = pid(plan_id), pid(draft_id)
        body(EmptyBody)
        with lock:
            draft = em.mark_outreach_sent(plan_id, draft_id)
        return out(draft)

    # ------------------------------------------------------------- voice (docs/VOICE.md)

    def audio_upload():
        """The one audio file of a multipart request, as (bytes, declared type)."""
        f = request.files.get("audio")
        if f is None or len(request.files) != 1:
            raise ValueError("send exactly one recording, in the field named audio")
        return f.stream.read(voice_audio.MAX_BYTES + 1), f.mimetype or ""

    @app.get("/api/voice/capabilities")
    def voice_capabilities():
        return jsonify({
            "transcribe": transcriber.capabilities(),
            "sessions": {"enabled": True},
            "turns": {"enabled": True},
            "live": {"enabled": True, "reason_unavailable": None},  # the server side of live mode is always ready; whether the browser can listen is the page's to check
            "keep_original": em.voice_notes.enabled,
        })

    @app.post("/api/voice/transcribe")
    def voice_transcribe():
        """Turn one recorded note into text on this machine. Nothing is sent to the assistant by this call; the audio is deleted before it returns."""
        data, declared = audio_upload()
        language = request.form.get("language") or None
        if language is not None and not VOICE_LANGUAGE.fullmatch(language):
            raise ValueError("language must be a language code such as en or hi-IN")
        got = transcriber.transcribe(data, declared, language)
        return jsonify({"transcript": got.text, "language": got.language, "duration_s": got.duration_s})

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
            raise ValueError("date must be YYYY-MM-DD")
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
            raise ValueError("limit must be a whole number")
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
