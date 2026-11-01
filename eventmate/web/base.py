"""Module-level pieces of the web app (imports, constants, request-body models), split out of app.py so that create_app and the route modules stay small.\n\nThe description of the web layer that follows is the original one.\n\nto;doh-the event planner web UI + JSON API (Flask).

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
import json
import logging
import os
import re
from datetime import date
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from eventmate.planning import intake
from eventmate.voice import audio as voice_audio
from eventmate import transfer
from eventmate.schemas import MAX_BUDGET_INR, MAX_MULTI_CITY_LEGS, HiddenCost, LedgerKind, ObservationSource, TradeoffOption, VendorCategory
from eventmate.integrations.travel import CABINS

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = {
    "favicon.svg": "image/svg+xml",
    "avatar.glb": "model/gltf-binary",  # the attendant in the room (docs/ATTRIBUTIONS.md)
    # the /arcade page: a 3D cabinet scene (docs/ARCADE_UI.md)
    "cabinet.glb": "model/gltf-binary", "arcade.css": "text/css; charset=utf-8", "arcade.js": "text/javascript; charset=utf-8",
    "arcade3d.js": "text/javascript; charset=utf-8", "three.min.js": "text/javascript; charset=utf-8", "CopyShader.js": "text/javascript; charset=utf-8",
    "LuminosityHighPassShader.js": "text/javascript; charset=utf-8", "EffectComposer.js": "text/javascript; charset=utf-8", "RenderPass.js": "text/javascript; charset=utf-8",
    "ShaderPass.js": "text/javascript; charset=utf-8", "MaskPass.js": "text/javascript; charset=utf-8", "UnrealBloomPass.js": "text/javascript; charset=utf-8",
    "GLTFLoader.js": "text/javascript; charset=utf-8", "meshopt_decoder.js": "text/javascript; charset=utf-8",
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
VENDOR_FILES = frozenset({"three.min.js", "CopyShader.js", "LuminosityHighPassShader.js", "EffectComposer.js", "RenderPass.js", "ShaderPass.js", "MaskPass.js", "UnrealBloomPass.js", "GLTFLoader.js", "meshopt_decoder.js"})
GZIP_MIN = 1024  # below this the header overhead outweighs the saving
GZIP_MAX = 6 * 1024 * 1024  # above this a file is sent as it is: the 55 MB cat machine would stall the request and sit in memory. Measured: the 3.6 MB mural shrinks 44% (3.6 -> 2.0 MB), the Hall table 50%; compression is done once per file and cached
_gzip_cache: dict[tuple, bytes] = {}  # (name, mtime_ns) -> compressed bytes; the file set is fixed and small
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


def asset_base() -> str:
    """The origin that holds the room's full-quality models (``EVENTMATE_ASSET_BASE``, a plain https origin or origin plus path, no query). Empty when unset or malformed:
    the room then runs on the models in the repository (the light set), which is what a fresh checkout does."""
    raw = os.environ.get("EVENTMATE_ASSET_BASE", "").strip().rstrip("/")
    return raw if re.fullmatch(r"https://[A-Za-z0-9.-]+(?::\d{1,5})?(?:/[A-Za-z0-9._~/-]*)?", raw) else ""


def csp_for(base: str) -> str:
    """The page's policy; the one addition for a model host is permission to fetch from that single origin (``connect-src``), never to run script from it."""
    if not base:
        return CSP
    origin = "/".join(base.split("/", 3)[:3])
    return CSP.replace("connect-src 'self' blob:", f"connect-src 'self' blob: {origin}")


# model name -> content-addressed file name on the model host (``tools/assets/publish_models.sh`` writes this); a changed model is a new name, so the host can cache it for good
CDN_MODELS: dict[str, str] = {k: v["file"] for k, v in json.loads((Path(__file__).parent / "cdn_models.json").read_text(encoding="utf-8")).items()}
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
    guest_list_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")


class IntakeBody(_Strict):
    messages: list[str] = Field(min_length=1, max_length=intake.MAX_MESSAGES)
    draft: intake.IntakeDraft | None = None


class IntakeConfirmBody(_Strict):
    draft: intake.IntakeDraft
    raw_text: str = Field(default="", max_length=2000)
    source: str = Field(default="rule:parser", max_length=80, pattern=r"^[A-Za-z0-9:._-]+$")
    guest_list_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")


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
    guest_count: int | None = Field(default=None, ge=1)
    inclusions: list[str] = Field(default_factory=list, max_length=30)
    exclusions: list[str] = Field(default_factory=list, max_length=30)
    hidden_costs: list[HiddenCost] = Field(default_factory=list, max_length=20)
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
    email: str | None = Field(default=None, max_length=254)
    whatsapp: str | None = Field(default=None, max_length=30)
    preferred: Literal["email", "whatsapp"] = "whatsapp"


class AutoreplyBody(_Strict):
    sender_name: str = Field(min_length=1, max_length=80)
    hours: int = Field(default=72, ge=1, le=168)
    max_per_thread: int = Field(default=3, ge=1, le=5)
    max_total: int = Field(default=10, ge=1, le=30)


class JobBody(_Strict):
    category: VendorCategory
    vendor_ids: list[str] = Field(min_length=1, max_length=10)
    goal: str = Field(min_length=1, max_length=300)
    sender_name: str = Field(min_length=1, max_length=80)
    deadline_days: int = Field(default=7, ge=1, le=60)
    min_quotes: int = Field(default=3, ge=1, le=10)
    max_amount_inr: int | None = Field(default=None, gt=0)
    max_nudges_per_vendor: int = Field(default=2, ge=0, le=5)
    reply_window_hours: int = Field(default=24, ge=1, le=168)


class ConfirmClaimBody(_Strict):
    valid_days: int = Field(ge=1, le=90)


class ResolveItemBody(_Strict):
    note: str = Field(default="", max_length=200)


class IdeasBody(_Strict):
    preferences: list[str] = Field(default_factory=list, max_length=5)


class DoneBody(_Strict):
    done: bool


class CommunityItemBody(_Strict):
    status: Literal["not_verified", "confirmed_by_user", "not_applicable_by_user"]
    by: str = Field(min_length=1, max_length=60)
    on: date
    note: str = Field(default="", max_length=200)


class AccessBody(_Strict):
    features: list[Literal["step_free_access", "accessible_toilet", "reserved_seating", "hearing_support", "accessible_parking"]] = Field(default_factory=list, max_length=5)
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
    source: Literal["osm", "places"] | None = None
    limit: int = Field(default=10, ge=1, le=20)


class WebsiteBody(_Strict):
    url: str = Field(min_length=1, max_length=2048)


class ConfirmContactBody(_Strict):
    by: str = Field(min_length=1, max_length=60)
    edited_value: str | None = Field(default=None, max_length=254)
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
    city: str | None = Field(default=None, max_length=80)


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
    confidence: float | None = None
    language: str | None = Field(default=None, max_length=12)


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
    answer: str | None = Field(default=None, min_length=1, max_length=1500)


class PlanRefBody(_Strict):
    plan_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")


class ApplyBody(_Strict):
    option: TradeoffOption
    guest_list_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")


class LedgerBody(_Strict):
    kind: LedgerKind
    category: VendorCategory
    amount: int = Field(gt=0, le=MAX_BUDGET_INR)
    note: str = Field(default="", max_length=200)


_IATA_FIELD = {"pattern": r"^[A-Z]{3}$"}


class FlightBody(_Strict):
    origin: str = Field(**_IATA_FIELD)
    destination: str = Field(**_IATA_FIELD)
    date: date
    adults: int = Field(default=1, ge=1, le=9)
    cabin: Literal[CABINS] = "economy"
    max_stops: int | None = Field(default=None, ge=0, le=2)
    limit: int = Field(default=10, ge=1, le=30)


class LegBody(_Strict):
    origin: str = Field(**_IATA_FIELD)
    destination: str = Field(**_IATA_FIELD)
    date: date


class MultiCityBody(_Strict):
    legs: list[LegBody] = Field(min_length=2, max_length=MAX_MULTI_CITY_LEGS)
    adults: int = Field(default=1, ge=1, le=9)
    cabin: Literal[CABINS] = "economy"
    max_stops: int | None = Field(default=None, ge=0, le=2)
    limit: int = Field(default=5, ge=1, le=10)


class ConfirmBody(_Strict):
    category: VendorCategory
