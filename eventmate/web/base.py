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
import logging
import re
from datetime import date
from pathlib import Path
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator
from eventmate.planning import intake
from eventmate.planning.delta import EventDelta
from eventmate.voice import audio as voice_audio
from eventmate import transfer
from eventmate.schemas import MAX_BUDGET_INR, MAX_MULTI_CITY_LEGS, GuestTravel, HiddenCost, LedgerKind, ObservationSource, TradeoffOption, VendorCategory
from eventmate.integrations.travel import CABINS

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = {
    "favicon.svg": "image/svg+xml",
    "google-maps-logo.svg": "image/svg+xml",  # Google's own attribution file (dark gray, for a plain background), byte for byte as Google supplies it: it must not be modified
    # the landing page (/) and the planner (/app); the look they share is site.css
    "site.css": "text/css; charset=utf-8", "landing.css": "text/css; charset=utf-8", "landing.js": "text/javascript; charset=utf-8",
    "app.css": "text/css; charset=utf-8", "app.js": "text/javascript; charset=utf-8",
    # the planning front door (/plan): built from ui/ (npm run build). The two reference-* drawings are not in the repository (their licence is unconfirmed): the page works without them.
    "plan.css": "text/css; charset=utf-8", "plan.js": "text/javascript; charset=utf-8", "plan-instrument-serif-italic.ttf": "font/ttf",
    "plan-story-knot.webp": "image/webp", "plan-story-relaxed.webp": "image/webp", "plan-story-table-sketch.webp": "image/webp", "plan-story-char-cat.webp": "image/webp",
    "plan-story-char-duck.webp": "image/webp", "plan-story-char-frog.webp": "image/webp", "plan-story-char-music.webp": "image/webp", "plan-story-char-robot.webp": "image/webp",
    "plan-story-char-tallbird.webp": "image/webp", "plan-reference-cap.webp": "image/webp", "plan-reference-head-open.webp": "image/webp", "plan-reference-head-closed.webp": "image/webp",
    # fonts and hero media served from this origin: the CSP allows no third party. The poster and the video are not in the repository (their licence is unconfirmed): without
    # them the hero shows its plain gradient, and a file that is absent is a 404.
    "landing-poster.jpg": "image/jpeg", "landing-hero.mp4": "video/mp4",
    "landing-instrument-serif.ttf": "font/ttf", "landing-inter-400.ttf": "font/ttf", "landing-inter-500.ttf": "font/ttf",
}
LONG_CACHE = frozenset({"landing-poster.jpg", "landing-hero.mp4", "landing-instrument-serif.ttf", "landing-inter-400.ttf", "landing-inter-500.ttf", "plan-instrument-serif-italic.ttf"})  # large and unchanging: not re-sent on every page load
GZIP_MIN = 1024  # below this the header overhead outweighs the saving
GZIP_MAX = 6 * 1024 * 1024  # above this a file (the hero video) is streamed as it is, with ETag and Range, never read whole into memory; compression is done once per file and cached
_gzip_cache: dict[tuple, bytes] = {}  # (name, mtime_ns) -> compressed bytes; the file set is fixed and small
WORKSPACE_EXEMPT = ("/api/owner/status", "/metrics", "/api/wall/")  # /api routes with their own authentication: no workspace is looked up or made for them
# POST routes that send, or let something send later, through the owner's mailbox or gateway; refused for every guest workspace
SENDING_ROUTES = re.compile(
    r"/api/plans/[A-Za-z0-9_-]{1,64}/outreach/[A-Za-z0-9_-]{1,64}/(?:approve|followup)|/api/plans/[A-Za-z0-9_-]{1,64}/autoreply|/api/inbox/poll"
    r"|/api/jobs/tick|/api/jobs/[A-Za-z0-9_-]{1,64}/approve-requests|/api/jobs/[A-Za-z0-9_-]{1,64}/items/[A-Za-z0-9_-]{1,64}/approve-nudge|/api/notify/(?:propose|confirm)"
)
LOOPBACK = {"127.0.0.1", "localhost", "[::1]", "::1"}
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; font-src 'self'; connect-src 'self'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
HEAVY_MAX = 4  # concurrent network-bound searches
_PLAN_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
GUEST_UPLOAD_LIMIT = 640 * 1024  # a 256 KB CSV can grow when JSON-escaped; the parser enforces the real 256 KB limit
QUOTE_DOC_UPLOAD_LIMIT = 5 * 1024 * 1024 + 256 * 1024  # a quote file plus the multipart wrapper; the sandbox check enforces the real 5 MB
AUDIO_UPLOAD_LIMIT = voice_audio.MAX_BYTES + 256 * 1024  # a recording plus the multipart wrapper; the audio check enforces the real 5 MB
VOICE_STREAMS_MAX = 4  # concurrent server-sent event streams
# the only routes that take a body larger than 64 KB, or multipart instead of JSON: (path pattern, limit)
UPLOAD_ROUTES = (
    (re.compile(r"/api/plans/[A-Za-z0-9_-]{1,64}/guests/preview"), GUEST_UPLOAD_LIMIT),
    (re.compile(r"/api/voice/transcribe"), AUDIO_UPLOAD_LIMIT),
    (re.compile(r"/api/plans/[A-Za-z0-9_-]{1,64}/voice-notes"), AUDIO_UPLOAD_LIMIT),
    (re.compile(r"/api/vendors/[A-Za-z0-9_:.-]{1,64}/quote-document"), QUOTE_DOC_UPLOAD_LIMIT),
    (re.compile(r"/api/workspace/import"), transfer.MAX_ARCHIVE + 256 * 1024),  # an archive plus the multipart wrapper; the importer enforces the real 20 MB
)
log = logging.getLogger("eventmate.web")


class NotOnSharedHost(Exception):
    """A feature that makes the server act on an address or a mailbox the caller chose: only the owner of a local server may use it."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanBody(_Strict):
    request: str = Field(min_length=1, max_length=2000)
    guest_list_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")


class TradeoffsBody(_Strict):
    request: str = Field(default="", max_length=2000)  # the words, read from scratch (no draft)
    draft: intake.IntakeDraft | None = None  # or the draft the user confirmed: then it is used as it stands and the words are only checked against it
    messages: list[Annotated[str, Field(max_length=2000)]] | None = Field(default=None, max_length=intake.MAX_MESSAGES)
    guest_list_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    guest_travel: GuestTravel | None = None  # guests flying in: adds indicative fares to date-shift options

    @model_validator(mode="after")
    def _one_source(self):
        if self.draft is None and not self.request.strip():
            raise ValueError("send the draft or the request text")
        return self


class IntakeBody(_Strict):
    messages: list[str] = Field(min_length=1, max_length=intake.MAX_MESSAGES)  # only the newest is read; the page sends just that one
    draft: intake.IntakeDraft | None = None
    expecting: Literal["guest_count", "budget_total", "event_date", "city", "allergens"] | None = None  # the fact the last question was about
    answer: intake.IntakeAnswer | None = None  # an option of an open question the user pressed
    staged_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{24}$")  # the brief's place on the server: when it is sent, the staged draft is the one corrected, not ``draft``


class QuoteConfirmBody(_Strict):
    fields: dict[str, str | None] = Field(min_length=1, max_length=9)  # field key -> a value the user typed, or None for "as read"; the same parser as the vendor's reply decides if it is usable
    by: str = Field(min_length=1, max_length=60)
    valid_days: str | None = Field(default=None, max_length=20)  # how long the price holds, when the user is confirming the price and the reply did not say


class QuotePromoteBody(_Strict):
    by: str = Field(min_length=1, max_length=60)
    setting: str | None = Field(default=None, max_length=10)  # a venue only: "indoor" or "outdoor"


class QuoteRetractBody(_Strict):
    field: str = Field(min_length=1, max_length=30)
    by: str = Field(min_length=1, max_length=60)


class IntakeEditBody(_Strict):
    fields: dict[str, StrictInt | str | None] = Field(min_length=1, max_length=8)  # values typed onto the review card; the schema decides what is acceptable
    version: int = Field(ge=1)  # the version of the brief the user was looking at


class IntakeCommitBody(_Strict):
    version: int = Field(ge=1)
    draft_hash: str = Field(pattern=r"^[0-9a-f]{64}$")  # the draft as the user saw it: a different one is refused
    guest_list_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    from_quotes: bool = False  # plan only from vendors whose quote the user confirmed and promoted: no seed or catalogue vendor is considered


class IntakeConfirmBody(_Strict):
    draft: intake.IntakeDraft
    raw_text: str = Field(default="", max_length=2000)
    messages: list[Annotated[str, Field(max_length=2000)]] | None = Field(default=None, max_length=intake.MAX_MESSAGES)  # what the user typed, one message each: read again to check no answer was lost
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
    quoted_for_date: date | None = None
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


class QuoteLineConfirmBody(_Strict):
    valid_days: int = Field(ge=1, le=90)
    plan_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")  # the plan to say the effect on


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
    business_name: str | None = Field(default=None, max_length=120)  # the name the user confirms for a Google-listed business: its listing name is not stored


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


class PasteReplyBody(_Strict):
    vendor_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    text: str = Field(min_length=3, max_length=4000)
    by: str = Field(min_length=1, max_length=60)
    channel: Literal["email", "whatsapp"] = "email"


class UserVendorBody(_Strict):
    name: str = Field(min_length=1, max_length=120)
    category: VendorCategory
    by: str = Field(min_length=1, max_length=60)  # who is adding it
    city: str | None = Field(default=None, max_length=80)  # the brief's city when left out
    email: str | None = Field(default=None, max_length=254)
    whatsapp: str | None = Field(default=None, max_length=30)


class SessionClearBody(_Strict):
    plan_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")


class EmptyBody(_Strict):
    pass


class ResumeBody(_Strict):
    snapshot: str | None = Field(default=None, pattern=r"^[0-9a-f]{20}$")  # the id of the held queue the caller reviewed (GET /api/sending): required when anything is held


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


class CheapestDatesBody(_Strict):
    origin: str = Field(**_IATA_FIELD)
    destination: str = Field(**_IATA_FIELD)
    from_date: date
    to_date: date
    adults: int = Field(default=1, ge=1, le=9)
    cabin: Literal[CABINS] = "economy"
    max_stops: int | None = Field(default=None, ge=0, le=2)


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


class MissionStartBody(_Strict):
    brief: str = Field(min_length=1, max_length=4000)
    sender_name: str = Field(min_length=1, max_length=60)
    categories: list[str] = Field(min_length=1, max_length=10)
    source: str | None = Field(default=None, max_length=40)
    limit: int = Field(default=10, ge=1, le=25)


class ChangeBody(_Strict):
    message: str = Field(min_length=1, max_length=2000)


class ChangeApplyBody(_Strict):
    delta: EventDelta
    done: tuple[str, ...] = Field(default=(), max_length=20)  # tasks the user says are finished
    in_flight: dict[str, int] = Field(default={}, max_length=20)  # tasks under way: minutes left

    @field_validator("done")
    @classmethod
    def _done_ids(cls, v):
        if not all(re.fullmatch(r"[a-z0-9_]{1,64}", t) for t in v):
            raise ValueError("task ids are lowercase letters, digits and '_'")
        return v

    @field_validator("in_flight")
    @classmethod
    def _in_flight(cls, v):
        for t, left in v.items():
            if not re.fullmatch(r"[a-z0-9_]{1,64}", t) or isinstance(left, bool) or not 1 <= left <= 525_600:
                raise ValueError("in_flight maps a task id to 1 to 525600 minutes left")
        return v


class WatchBody(_Strict):
    subject: str = Field(pattern=r"^[A-Za-z0-9_:.-]{1,64}$")
    url: str = Field(min_length=8, max_length=2048)
    condition: Literal["change", "contains", "price_below"]
    text: str | None = Field(default=None, min_length=1, max_length=80)
    amount_inr: int | None = Field(default=None, ge=1, le=MAX_BUDGET_INR)
    interval_minutes: int = Field(default=1440, ge=60, le=10_080)


class ConfirmBody(_Strict):
    category: VendorCategory
