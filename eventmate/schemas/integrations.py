"""Contracts for the integrations and conversations: travel, weather, deals, places, outreach, ideas, digests and external businesses."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import Field, field_validator, model_validator

from eventmate.schemas.core import AccessFeature, ConstraintCheck, MAX_BUDGET_INR, MAX_GUESTS, SOURCE_PATTERN, UnverifiedItem, VendorCategory, _Model, _clean_text

# ------------------------------------------------------------------ travel
# Flight fares and schedules from an external tool. Indicative only; to;doh-the event planner never books or pays.

_IATA = r"^[A-Z]{3}$"
GOOGLE_FLIGHTS_PREFIX = "https://www.google.com/travel/flights"
MAX_FLIGHT_OPTIONS = 30
MAX_DATE_RANGE_DAYS = 61  # one Google Flights date-grid request covers about two months
MAX_MULTI_CITY_LEGS = 6


class FlightSegmentInfo(_Model):
    airline_code: str = Field(min_length=2, max_length=3, pattern=r"^[A-Z0-9]{2,3}$")
    airline_name: str = Field(min_length=1, max_length=80)
    flight_number: str = Field(min_length=1, max_length=8, pattern=r"^[A-Za-z0-9]{1,8}$")
    from_code: str = Field(pattern=_IATA)
    to_code: str = Field(pattern=_IATA)
    departs: datetime  # local time at the departure airport, as published
    arrives: datetime  # local time at the arrival airport
    duration_min: int = Field(gt=0, le=60 * 72)

    @field_validator("airline_name")
    @classmethod
    def _name_ok(cls, v: str) -> str:
        return _clean_text(v)


class FlightOption(_Model):
    price_inr: int | None = Field(default=None, ge=0, le=MAX_BUDGET_INR)  # None = no INR price was published
    duration_min: int = Field(gt=0, le=60 * 100)
    stops: int = Field(ge=0, le=5)
    segments: tuple[FlightSegmentInfo, ...] = Field(min_length=1, max_length=8)
    booking_url: str | None = Field(default=None, max_length=2000)
    source: str

    @field_validator("source")
    @classmethod
    def _source_ok(cls, v: str) -> str:
        if not SOURCE_PATTERN.fullmatch(v):
            raise ValueError("invalid source tag")
        return v

    @field_validator("booking_url")
    @classmethod
    def _url_ok(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not v.startswith(GOOGLE_FLIGHTS_PREFIX) or any(ord(c) < 33 or ord(c) > 126 for c in v):
            raise ValueError("booking_url must be a plain https://www.google.com/travel/flights link")
        return v


class FlightSearch(_Model):
    origin: str = Field(pattern=_IATA)
    destination: str = Field(pattern=_IATA)
    travel_date: date
    options: tuple[FlightOption, ...] = Field(default=(), max_length=MAX_FLIGHT_OPTIONS)
    fetched_at: datetime
    source: str
    warnings: tuple[str, ...] = ()
    error: str | None = Field(default=None, max_length=300)  # set when this leg could not be searched
    notice: str = "Indicative fare and schedule from Google Flights through fli. Fares change; to;doh-the event planner does not book or pay."


class DateFare(_Model):
    day: date
    price_inr: int = Field(ge=0)


class CheapestDates(_Model):
    """The lowest fare Google Flights shows for each day in a range, for one direction. A day with no fare is absent, not zero."""

    origin: str = Field(pattern=_IATA)
    destination: str = Field(pattern=_IATA)
    from_date: date
    to_date: date
    days: tuple[DateFare, ...] = Field(default=(), max_length=MAX_DATE_RANGE_DAYS)
    cheapest: DateFare | None = None
    fetched_at: datetime
    source: Literal["tool:fli"] = "tool:fli"
    warnings: tuple[str, ...] = ()
    notice: str = "Indicative fares from Google Flights through fli. Fares change; to;doh-the event planner does not book or pay."


class MultiCityResult(_Model):
    legs: tuple[FlightSearch, ...] = Field(min_length=2, max_length=MAX_MULTI_CITY_LEGS)
    method: Literal["separate_one_way_searches"] = "separate_one_way_searches"
    total_cheapest_inr: int | None = Field(default=None, ge=0)  # None if any leg has no priced option
    incomplete_legs: tuple[int, ...] = ()
    note: str


# ------------------------------------------------------------ venue discovery
# Candidates from OpenStreetMap. They carry no price or availability and never enter the vendor database.

OSM_ATTRIBUTION = "Data (c) OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright)."


class VenueCandidate(_Model):
    osm_ref: str = Field(pattern=r"^(node|way|relation)/[0-9]{1,15}$")
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["venue", "hotel", "restaurant"]
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    street: str | None = Field(default=None, max_length=120)
    city: str | None = Field(default=None, max_length=80)
    website: str | None = Field(default=None, max_length=300)
    capacity_tagged: int | None = Field(default=None, ge=1, le=MAX_GUESTS)  # as tagged by an OSM contributor, unverified
    email: str | None = Field(default=None, max_length=254)  # as tagged by an OSM contributor (the listing's own field); a candidate until the user confirms it
    source: Literal["tool:overpass"] = "tool:overpass"

    @field_validator("name", "street", "city")
    @classmethod
    def _text_ok(cls, v: str | None) -> str | None:
        return v if v is None else _clean_text(v)

    @field_validator("website")
    @classmethod
    def _site_ok(cls, v: str | None) -> str | None:
        if v is not None and (not re.fullmatch(r"https?://[^\s<>\"']+", v)):
            raise ValueError("website must be a plain http(s) URL")
        return v

    @field_validator("email")
    @classmethod
    def _email_ok(cls, v: str | None) -> str | None:
        if v is not None and not _EMAIL.fullmatch(v):
            raise ValueError("not a valid email address")
        return v


class DiscoveryResult(_Model):
    city: str = Field(min_length=1, max_length=60)
    kind: Literal["venue", "hotel", "restaurant"]
    candidates: tuple[VenueCandidate, ...] = ()
    fetched_at: datetime
    source: Literal["tool:overpass"] = "tool:overpass"
    attribution: str = OSM_ATTRIBUTION
    warnings: tuple[str, ...] = ()
    notice: str = "Places other people have mapped. No prices, availability or quality are implied; ask the venue before relying on anything here."


# ---------------------------------------------------------------------- weather
# Forecast from OpenWeather. Only what the service returned; nothing is estimated beyond its horizon.

RAIN_POSSIBLE_AT = 0.3


class WeatherSlot(_Model):
    local_time: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")  # local time at the place
    temp_c: float = Field(ge=-90.0, le=60.0)
    feels_like_c: float = Field(ge=-90.0, le=70.0)
    humidity_pct: int | None = Field(default=None, ge=0, le=100)
    rain_chance: float = Field(ge=0.0, le=1.0)
    wind_ms: float | None = Field(default=None, ge=0.0, le=120.0)
    description: str | None = Field(default=None, max_length=80)

    @field_validator("description")
    @classmethod
    def _desc_ok(cls, v: str | None) -> str | None:
        return v if v is None else _clean_text(v)


class WeatherDay(_Model):
    city: str = Field(min_length=1, max_length=60)
    travel_date: date
    resolved_place: str = Field(max_length=200)
    slots: tuple[WeatherSlot, ...] = ()
    temp_min: float | None = None
    temp_max: float | None = None
    max_rain_chance: float | None = Field(default=None, ge=0.0, le=1.0)
    rain_possible: bool | None = None
    conditions: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=300)
    warnings: tuple[str, ...] = ()
    fetched_at: datetime
    source: Literal["tool:openweathermap"] = "tool:openweathermap"
    attribution: str = "Weather data by OpenWeather (https://openweathermap.org)."


# ------------------------------------------------------------- multi-city trip search
# The system orders the stops, picks dates and searches each distinct leg. Prices come only from the flight tool.

MAX_TRIP_STOPS = 4
MAX_TRIP_SEARCHES = 60


class TripStop(_Model):
    code: str = Field(pattern=_IATA)
    nights: int = Field(ge=1, le=30)


class TripRequest(_Model):
    origin: str = Field(pattern=_IATA)
    stops: tuple[TripStop, ...] = Field(min_length=1, max_length=MAX_TRIP_STOPS)
    return_to: str | None = Field(default=None, pattern=_IATA)  # default: back to the origin
    earliest_start: date
    flex_days: int = Field(default=0, ge=0, le=3)  # try starting up to this many days later
    adults: int = Field(default=1, ge=1, le=9)
    cabin: Literal["economy", "premium_economy", "business", "first"] = "economy"
    max_stops: int | None = Field(default=None, ge=0, le=2)
    fixed_order: bool = False  # True = visit the stops in the order given
    max_searches: int = Field(default=30, ge=1, le=MAX_TRIP_SEARCHES)
    value_per_hour_inr: int = Field(default=300, ge=0, le=5000)  # what an hour in the air is worth to the traveller; an assumption, not data

    @model_validator(mode="after")
    def _consistent(self) -> TripRequest:
        codes = [s.code for s in self.stops]
        if len(set(codes)) != len(codes):
            raise ValueError("a stop appears twice")
        if self.origin in codes or (self.return_to and self.return_to in codes):
            raise ValueError("the origin and return airport cannot also be stops")
        return self


class TripLeg(_Model):
    origin: str = Field(pattern=_IATA)
    destination: str = Field(pattern=_IATA)
    date: date
    option: FlightOption


class TripPackage(_Model):
    label: Literal["cheapest", "fastest", "best_value", "alternative"]
    order: tuple[str, ...]
    start_date: date
    legs: tuple[TripLeg, ...] = Field(min_length=2)
    total_price_inr: int = Field(ge=0)
    total_flight_minutes: int = Field(gt=0)
    value_score_inr: int = Field(ge=0)  # price plus the traveller's time valued at value_per_hour_inr; lower is better
    below_median_inr: int  # how far under the median complete itinerary's cheapest total this is (negative = above)


class TripSearchResult(_Model):
    request: TripRequest
    packages: tuple[TripPackage, ...] = ()
    searches_planned: int = Field(ge=0)
    searches_used: int = Field(ge=0)
    orderings_considered: int = Field(ge=0)
    dropped_orders: int = Field(default=0, ge=0)
    insights: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    fetched_at: datetime
    source: Literal["tool:fli"] = "tool:fli"
    method: Literal["separate_one_way_searches"] = "separate_one_way_searches"
    note: str
    notice: str = "Indicative fares from Google Flights through fli. Fares change; to;doh-the event planner does not book or pay."


# ------------------------------------------------------------------------- deals
# Advertised prices found on public web pages. They are leads, never quotes, and never set a plan's price.

DEAL_KINDS = ("caterer", "venue", "photographer", "hotel")


class DealQuery(_Model):
    kind: Literal["caterer", "venue", "photographer", "hotel"]
    city: str = Field(min_length=1, max_length=60)
    guests: int | None = Field(default=None, ge=1, le=MAX_GUESTS)
    nights: int | None = Field(default=None, ge=1, le=60)  # hotels: turns a nightly price into a total
    budget_inr: int | None = Field(default=None, gt=0, le=MAX_BUDGET_INR)
    limit: int = Field(default=10, ge=1, le=20)

    @field_validator("city")
    @classmethod
    def _city_ok(cls, v: str) -> str:
        return _clean_text(v)


class DealOffer(_Model):
    rank: int = Field(ge=1)
    title: str = Field(max_length=200)
    label: str = Field(max_length=100)
    url: str = Field(max_length=2048, pattern=r"^https?://\S+$")
    domain: str = Field(max_length=253)
    unit: Literal["per_plate", "per_night", "per_day", "per_hour", "per_event"]
    unit_assumed: bool = False  # the page gave no unit; the service's usual one was assumed
    low_inr: int = Field(gt=0, le=MAX_BUDGET_INR)
    high_inr: int = Field(gt=0, le=MAX_BUDGET_INR)
    est_total_low_inr: int | None = Field(default=None, ge=0)
    est_total_high_inr: int | None = Field(default=None, ge=0)
    within_budget: bool | None = None
    vs_market_pct: int  # advertised low against the median low found: negative = cheaper than typical
    corroborated_by: int = Field(ge=0)  # other sites advertising a similar entry price (within 25 percent)
    flags: tuple[str, ...] = ()  # cautions computed in code, e.g. unusually low or a wide range
    snippet: str = Field(max_length=200)
    source: str

    @field_validator("source")
    @classmethod
    def _source_ok(cls, v: str) -> str:
        if not SOURCE_PATTERN.fullmatch(v):
            raise ValueError("invalid source tag")
        return v


class MarketSummary(_Model):
    unit: Literal["per_plate", "per_night", "per_day", "per_hour", "per_event"]
    median_low_inr: int = Field(ge=0)
    median_high_inr: int = Field(ge=0)
    n_offers: int = Field(ge=0)
    n_domains: int = Field(ge=0)


class SkippedPage(_Model):
    url: str = Field(max_length=2048)
    reason: str = Field(max_length=200)


class DealReport(_Model):
    query: DealQuery
    offers: tuple[DealOffer, ...] = ()
    market: MarketSummary | None = None
    pages_read: int = Field(ge=0)
    skipped: tuple[SkippedPage, ...] = ()
    queries_run: tuple[str, ...] = ()
    provider: str = Field(max_length=40)
    warnings: tuple[str, ...] = ()
    fetched_at: datetime
    notice: str = (
        "Advertised prices found on public web pages: often 'starting at' figures, not a quote, and not checked with the vendor. "
        "Ask for a written quote before relying on any of them."
    )


# ------------------------------------------------------------------- places
# Ratings and reviews from Google Places. A price level is a coarse band, never a price.

GOOGLE_MAPS_PREFIXES = ("https://maps.google.com/", "https://www.google.com/maps", "https://maps.app.goo.gl/", "https://goo.gl/maps/")


class PlaceReview(_Model):
    rating: int | None = Field(default=None, ge=1, le=5)
    text: str = Field(max_length=300)
    when: str | None = Field(default=None, max_length=60)


class PlaceInfo(_Model):
    name: str = Field(min_length=1, max_length=120)
    address: str | None = Field(default=None, max_length=200)
    rating: float | None = Field(default=None, ge=0.0, le=5.0)
    rating_count: int | None = Field(default=None, ge=0)
    weighted_rating: float | None = Field(default=None, ge=0.0, le=5.0)  # rating pulled toward the average when there are few reviews
    price_level: int | None = Field(default=None, ge=1, le=4)  # 1 inexpensive ... 4 very expensive: a band, not a price
    maps_uri: str | None = Field(default=None, max_length=500)
    place_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{8,200}$")
    website: str | None = Field(default=None, max_length=2048, pattern=r"^https?://[^\s\x00-\x1f\x7f<>\"']+$")  # the business's own site, when the listing publishes one
    reviews: tuple[PlaceReview, ...] = Field(default=(), max_length=10)

    @field_validator("name", "address")
    @classmethod
    def _text_ok(cls, v: str | None) -> str | None:
        return v if v is None else _clean_text(v)

    @field_validator("maps_uri")
    @classmethod
    def _uri_ok(cls, v: str | None) -> str | None:
        if v is not None and (not v.startswith(GOOGLE_MAPS_PREFIXES) or any(ord(c) <= 32 or ord(c) == 127 for c in v)):
            raise ValueError("maps_uri must be a Google Maps link")
        return v


class PlaceSearch(_Model):
    kind: Literal["caterer", "venue", "photographer", "hotel"]
    city: str = Field(min_length=1, max_length=60)
    places: tuple[PlaceInfo, ...] = ()
    fetched_at: datetime
    source: Literal["tool:google_places"] = "tool:google_places"
    attribution: str = "Ratings and reviews from Google Maps."
    warnings: tuple[str, ...] = ()
    notice: str = "Ratings and reviews are other people's opinions. A price level is a coarse band, not a price; ask the vendor for a quote."


# ------------------------------------------------------------------- saved leads
# A bookmark of an advertised price the user found. Never a quote: it does not price a plan or enter the ledger.


class DealLead(_Model):
    lead_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    plan_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    saved_at: datetime
    kind: Literal["caterer", "venue", "photographer", "hotel"]
    url: str = Field(max_length=2048, pattern=r"^https?://[^\s\x00-\x1f\x7f]+$")
    domain: str = Field(min_length=1, max_length=253)
    label: str = Field(max_length=100)
    unit: Literal["per_plate", "per_night", "per_day", "per_hour", "per_event"]
    low_inr: int = Field(gt=0, le=MAX_BUDGET_INR)
    high_inr: int = Field(gt=0, le=MAX_BUDGET_INR)
    note: str = Field(default="", max_length=200)
    source: Literal["user_input"] = "user_input"
    notice: str = "Saved by you from a public page: an advertised price, not a quote, and not checked with the vendor."

    @field_validator("domain", "label", "note")
    @classmethod
    def _text_ok(cls, v: str) -> str:
        return _clean_text(v)

    @model_validator(mode="after")
    def _range_ok(self) -> DealLead:
        if self.low_inr > self.high_inr:
            raise ValueError("low_inr is above high_inr")
        return self


# ----------------------------------------------------------------- weather advice
# Plain-code suggestions from a forecast and the chosen venue's setting. Advice only: it never changes a plan.


class AdviceItem(_Model):
    level: Literal["ok", "info", "warn"]
    title: str = Field(max_length=100)
    detail: str = Field(max_length=400)


class WeatherAdvice(_Model):
    plan_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    venue_setting: Literal["indoor", "outdoor"] | None = None
    items: tuple[AdviceItem, ...] = Field(min_length=1)
    forecast: WeatherDay
    source: Literal["rule:weather_advice"] = "rule:weather_advice"


# ----------------------------------------------------------------- outreach and conversations
# A quote request to;doh-the event planner drafts, the user approves, and the agent sends and follows. Content is bound to a hash
# at proposal time; approval is for that exact content. Replies are stored as untrusted text with a rule-based analysis, never as facts.

_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(\.[A-Za-z0-9-]{1,63})*\.[A-Za-z]{2,24}$")
_LINK = re.compile(r"^(mailto:[A-Za-z0-9._%+@-]+\?[^\s]*|https://wa\.me/[1-9][0-9]{7,14}\?text=[^\s]*)$")
OutreachStatus = Literal["prepared", "approved", "sending", "sent", "failed", "outcome_unknown", "denied", "expired", "marked_sent", "blocked", "stale"]  # stale: the plan changed after the text was written
OUTREACH_FINAL = frozenset({"sent", "failed", "outcome_unknown", "denied", "expired", "marked_sent", "blocked"})
VendorOrigin = Literal["seed", "discovered", "test"]  # where a business came from; shown wherever it appears


def draft_content_hash(d: dict) -> str:
    """What an approval is bound to: where it goes, what it says, and which thread it belongs to."""
    import hashlib
    import json as _json

    parts = [d.get(k) for k in ("plan_id", "vendor_id", "channel", "recipient", "subject", "body")] + [d.get("kind") or "request", d.get("parent_id") or None]
    return hashlib.sha256(_json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


class OutreachDraft(_Model):
    draft_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    plan_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    created_at: datetime
    category: VendorCategory
    vendor_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    vendor_name: str = Field(min_length=1, max_length=120)
    vendor_origin: VendorOrigin = "seed"
    channel: Literal["email", "whatsapp"]
    recipient: str = Field(max_length=254)  # an email address, or WhatsApp digits with the country code
    subject: str = Field(default="", max_length=200)  # email only
    body: str = Field(min_length=1, max_length=2000)
    link: str = Field(max_length=4000)  # manual fallback: open in your own app
    kind: Literal["request", "followup"] = "request"
    parent_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")  # the request a follow-up continues
    content_hash: str = Field(default="", max_length=64)
    expires_at: datetime | None = None  # a proposal nobody approved by then is dead
    status: OutreachStatus = "prepared"
    marked_at: datetime | None = None
    decided_at: datetime | None = None
    sent_at: datetime | None = None
    external_id: str = Field(default="", max_length=400)  # Message-ID or WhatsApp message id of what was sent
    error: str = Field(default="", max_length=300)
    action_key: str = Field(default="", max_length=200)  # who claimed this outbound action, e.g. "reply:<message_id>" or "nudge:<job>:<vendor>:<n>"; one live draft per key
    plan_version: int | None = Field(default=None, ge=1)  # the plan version the text was written against (None on drafts made before this was recorded)
    basis_sha256: str = Field(default="", max_length=64)  # a hash of the plan facts the text states: event, city, date, guests, diets for a caterer
    for_plan_pick: bool = False  # written to the vendor the plan itself chose, so a plan without that vendor makes it out of date
    approved_by: Literal["user", "mandate"] = "user"  # "mandate": sent under the user's standing permission, not a per-message approval
    source: Literal["user_input"] = "user_input"
    notice: str = "Not sent until you approve it. After that to;doh-the event planner sends it through the channel you configured and reads the vendor's reply."

    @model_validator(mode="before")
    @classmethod
    def _bind_hash(cls, data):
        if isinstance(data, dict):
            want = draft_content_hash(data)
            if not data.get("content_hash"):
                data = {**data, "content_hash": want}
            elif data["content_hash"] != want:
                raise ValueError("content_hash does not match the content: the record was changed")
        return data

    @field_validator("vendor_name", "subject", "error")
    @classmethod
    def _text_ok(cls, v: str) -> str:
        return _clean_text(v)

    @field_validator("body")
    @classmethod
    def _body_ok(cls, v: str) -> str:
        if any(unicodedata.category(ch) == "Cc" and ch != "\n" for ch in v):
            raise ValueError("control characters are not allowed")
        return v

    @model_validator(mode="after")
    def _consistent(self) -> OutreachDraft:
        if not _LINK.fullmatch(self.link):
            raise ValueError("link must be a mailto: or https://wa.me/ link")
        if self.channel == "email":
            if not _EMAIL.fullmatch(self.recipient) or not self.link.startswith("mailto:"):
                raise ValueError("an email draft needs a valid address and a mailto: link")
        elif not re.fullmatch(r"[1-9][0-9]{7,14}", self.recipient) or not self.link.startswith(f"https://wa.me/{self.recipient}?text="):
            raise ValueError("a WhatsApp draft needs digits with the country code and a wa.me link to that number")
        if (self.status == "marked_sent") != (self.marked_at is not None):
            raise ValueError("marked_at is set exactly when the status is marked_sent")
        if self.status == "sent" and self.sent_at is None:
            raise ValueError("a sent draft needs sent_at")
        if (self.kind == "followup") != (self.parent_id is not None):
            raise ValueError("a follow-up needs parent_id and a request must not have one")
        return self


class VendorContact(_Model):
    """Where to reach a vendor. For a seed vendor the user typed it. For an external business it is only ever created when the user confirmed it, and it keeps where it came from."""

    vendor_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    email: str | None = Field(default=None, max_length=254)
    whatsapp: str | None = Field(default=None, pattern=r"^[1-9][0-9]{7,14}$")
    preferred: Literal["email", "whatsapp"] = "whatsapp"
    updated_at: datetime
    origin: VendorOrigin = "seed"
    source_url: str | None = Field(default=None, max_length=2048, pattern=r"^https?://[^\s\x00-\x1f\x7f]+$")  # the public page or listing the address was read from
    confirmed_by: str | None = Field(default=None, min_length=1, max_length=60)
    confirmed_at: datetime | None = None
    edited_by_user: bool = False  # the user typed a different address than the one that was found

    @model_validator(mode="after")
    def _ok(self) -> VendorContact:
        if self.email is None and self.whatsapp is None:
            raise ValueError("give an email address or a WhatsApp number")
        if self.email is not None and not _EMAIL.fullmatch(self.email):
            raise ValueError("not a valid email address")
        if getattr(self, self.preferred) is None:
            raise ValueError(f"preferred channel {self.preferred} has no contact")
        return self


class AmountMention(_Model):
    low_inr: int = Field(gt=0, le=MAX_BUDGET_INR)
    high_inr: int = Field(gt=0, le=MAX_BUDGET_INR)
    unit: str = Field(max_length=20)  # as written by the vendor, or "unspecified"
    snippet: str = Field(max_length=200)


class ReplyAnalysis(_Model):
    """Rule-based reading of a reply. Every item is a quotation of the vendor's own words; nothing here is a recorded quote."""

    amounts: tuple[AmountMention, ...] = Field(default=(), max_length=5)
    says_unavailable: bool = False
    says_available: bool = False
    questions: tuple[str, ...] = Field(default=(), max_length=5)  # sentences the vendor asked you
    asks_to_stop: bool = False  # "stop", "do not contact me", "wrong number": nothing automatic is ever sent after this
    checklist_mentioned: tuple[str, ...] = ()
    source: Literal["rule:reply_analysis"] = "rule:reply_analysis"


class InboundMessage(_Model):
    message_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    plan_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    draft_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")  # the request this answers
    vendor_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    vendor_name: str = Field(min_length=1, max_length=120)
    vendor_origin: VendorOrigin = "seed"
    category: VendorCategory
    channel: Literal["email", "whatsapp"]
    sender: str = Field(max_length=254)
    received_at: datetime
    external_id: str = Field(min_length=1, max_length=400)
    body: str = Field(max_length=4000)
    truncated: bool = False
    attachments: int = Field(default=0, ge=0, le=100)  # counted; opened only when the user has turned on reading quote attachments, and then only in the sandbox
    quote_documents: tuple[str, ...] = Field(default=(), max_length=2)  # quote files read from this reply (their ids): the lines are unreviewed evidence until the user confirms them
    analysis: ReplyAnalysis = ReplyAnalysis()
    read: bool = False
    sender_differs: bool = False  # an email reply carrying the thread's headers from an address other than the one written to: shown, never trusted or answered automatically
    auto_handled: bool = False  # the standing permission has already looked at this reply (answered it or handed it to the user)
    notice: str = "The vendor's own words, shown as text. Nothing in a reply changes a plan or records a quote until you do."

    @field_validator("vendor_name", "sender")
    @classmethod
    def _text_ok(cls, v: str) -> str:
        return _clean_text(v)

    @field_validator("body")
    @classmethod
    def _body_ok(cls, v: str) -> str:
        return "".join(ch for ch in v if ch in "\n\t" or unicodedata.category(ch) != "Cc")


class ChannelStatus(_Model):
    channel: Literal["email", "whatsapp"]
    can_send: bool
    can_receive: bool
    detail: str = Field(max_length=300)  # what is missing; never a secret


class ConversationThread(_Model):
    """One request to one vendor, everything sent on it, and everything the vendor answered."""

    request: OutreachDraft
    drafts: tuple[OutreachDraft, ...]  # the request first, then follow-ups, oldest first
    replies: tuple[InboundMessage, ...] = ()
    unread: int = Field(default=0, ge=0)
    awaiting_approval: int = Field(default=0, ge=0)


class Inbox(_Model):
    plan_id: str
    threads: tuple[ConversationThread, ...] = ()
    unread_total: int = Field(default=0, ge=0)
    approvals_total: int = Field(default=0, ge=0)
    channels: tuple[ChannelStatus, ...] = ()


class PollResult(_Model):
    new_messages: int = Field(ge=0)
    ignored: int = Field(ge=0)  # mail from strangers or replies already stored
    sent: int = Field(default=0, ge=0)  # approved drafts dispatched in this round
    waiting: tuple[str, ...] = ()  # why approved drafts have not gone out yet
    errors: tuple[str, ...] = ()


MANDATE_MAX_HOURS = 168


class Mandate(_Model):
    """The user's standing permission for the agent to ask routine follow-up questions in a plan's vendor threads without approving each one.

    Narrow by design: fixed questions about missing information only. Bounded in time, count and hours of the day, and revocable at any moment.
    """

    plan_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    sender_name: str = Field(min_length=1, max_length=80)
    granted_at: datetime
    expires_at: datetime
    max_per_thread: int = Field(default=3, ge=1, le=5)
    max_total: int = Field(default=10, ge=1, le=30)
    window_start: int = Field(default=9, ge=0, le=23)  # hours in IST; automatic messages wait outside [start, end)
    window_end: int = Field(default=20, ge=1, le=24)
    revoked_at: datetime | None = None

    @field_validator("sender_name")
    @classmethod
    def _name_ok(cls, v: str) -> str:
        return _clean_text(v)

    @model_validator(mode="after")
    def _bounds(self) -> Mandate:
        if not self.granted_at < self.expires_at <= self.granted_at + timedelta(hours=MANDATE_MAX_HOURS):
            raise ValueError(f"the permission must end after it starts and within {MANDATE_MAX_HOURS} hours")
        if self.window_start >= self.window_end:
            raise ValueError("window_start must be before window_end")
        return self

    def active_at(self, now: datetime) -> bool:
        return self.revoked_at is None and self.granted_at <= now < self.expires_at


class AutoreplyStatus(_Model):
    plan_id: str
    active: bool  # a permission exists, is not revoked or expired, and the global kill switch is off
    killed: bool  # the global kill switch is on
    mandate: Mandate | None = None
    sent_total: int = Field(default=0, ge=0)  # automatic messages queued or sent under this plan's permission


# ----------------------------------------------------------------- ideas
# Personalized suggestions. They are never facts: no price, no availability, no capacity, no cultural claim, and they never
# change a validated plan. A model may propose and phrase them; templates work with the model off.

IdeaKind = Literal["theme", "decor", "activity", "menu_note", "checklist_item"]
IDEA_FIELDS = ("event_type", "guest_count", "budget_band", "dietary", "city", "venue_setting", "preferences", "community_subtype")
IdeaField = Literal["event_type", "guest_count", "budget_band", "dietary", "city", "venue_setting", "preferences", "community_subtype"]

_PRICE_WORDS = re.compile(
    r"₹|\b(?:rs|inr|usd|eur|gbp|rupees?|dollars?|euros?)\b|\$\s?\d|\d[\d,.]{0,14}\s?(?:k|lakhs?|lacs?|crores?|cr|thousand|million)\b|\b(?:lakhs?|lacs?|crores?)\b", re.I,
)
_NUMBER_WORDS_IN_NOTE = re.compile(r"\b(?:hundred|thousand|million)\b", re.I)


def mentions_price(text: str) -> bool:
    """A currency or an amount written out. Plain counts such as "40 guests" are not prices."""
    return bool(_PRICE_WORDS.search(text))


class Idea(_Model):
    idea_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    kind: IdeaKind
    title: str = Field(min_length=1, max_length=120)
    why: str = Field(min_length=1, max_length=300)  # which brief fields it used, in words
    est_cost_note: str = Field(default="", max_length=200)  # text only; never a number
    fields_used: tuple[IdeaField, ...] = Field(min_length=1, max_length=8)
    status: Literal["suggestion"] = "suggestion"
    source: str = Field(pattern=r"^(llm:[A-Za-z0-9_.-]{1,60}|rule:idea_templates)$")
    review_note: str = Field(default="", max_length=240)  # "Organiser to check ...": shown with the idea; the idea is kept, not dropped

    @field_validator("review_note")
    @classmethod
    def _note_ok(cls, v: str) -> str:
        return _clean_text(v)

    @field_validator("title", "why", "est_cost_note")
    @classmethod
    def _text_ok(cls, v: str) -> str:
        _clean_text(v)
        if mentions_price(v):
            raise ValueError("an idea never states a price")
        return v

    @field_validator("est_cost_note")
    @classmethod
    def _note_is_words(cls, v: str) -> str:
        if re.search(r"\d", v) or _NUMBER_WORDS_IN_NOTE.search(v):
            raise ValueError("a cost note is words only, never a number")
        return v


class DroppedIdea(_Model):
    reason: str = Field(min_length=1, max_length=160)  # "<category>: <detail>"
    title: str = Field(default="", max_length=120)


class IdeaSet(_Model):
    plan_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    generated_at: datetime
    source: str = Field(pattern=r"^(llm:[A-Za-z0-9_.-]{1,60}|rule:idea_templates)$")  # llm when any idea came from a model
    ideas: tuple[Idea, ...] = Field(default=(), max_length=12)
    dropped: tuple[DroppedIdea, ...] = Field(default=(), max_length=40)
    facts_used: tuple[IdeaField, ...] = ()
    template_version: int | None = None
    prompt_version: int | None = None
    notice: str = "Suggestions, not verified offers"


class IdeaDecision(_Model):
    plan_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    idea_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    decision: Literal["pinned", "dismissed", "restored"]
    decided_at: datetime
    note_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")


class PlanNote(_Model):
    """A pinned idea as a note or checklist item. It sits beside the plan and never changes a validated constraint."""

    note_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    plan_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    source_idea_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    kind: Literal["checklist_item", "note"]
    text: str = Field(min_length=1, max_length=200)
    done: bool = False
    created_at: datetime

    @field_validator("text")
    @classmethod
    def _text_ok(cls, v: str) -> str:
        return _clean_text(v)


class IdeaView(_Model):
    plan_id: str
    visible: tuple[Idea, ...] = ()
    dismissed: tuple[str, ...] = ()
    pinned: tuple[str, ...] = ()
    notes: tuple[PlanNote, ...] = ()
    generated_at: datetime | None = None
    source: str | None = None
    facts_used: tuple[IdeaField, ...] = ()
    notice: str = "Suggestions, not verified offers"


class AccessRecord(_Model):
    """What a venue told the user about its accessibility, recorded with evidence. An empty ``features`` means the venue said it has none of them."""

    venue_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    features: tuple[AccessFeature, ...] = ()
    evidence_ref: str = Field(min_length=1, max_length=200)
    entered_by: str = Field(min_length=1, max_length=80)
    recorded_at: datetime

    @field_validator("evidence_ref", "entered_by")
    @classmethod
    def _text_ok(cls, v: str) -> str:
        return _clean_text(v.strip())


ItemStatus = Literal["not_verified", "confirmed_by_user", "not_applicable_by_user"]
USER_ITEM_STATUSES = ("not_verified", "confirmed_by_user", "not_applicable_by_user")


class ChecklistRecord(_Model):
    """The user's word about one conditional requirement. Append-only; the latest record for an item wins."""

    plan_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    item_id: str = Field(pattern=r"^[a-z0-9_]{1,40}$")
    status: ItemStatus
    by: str = Field(min_length=1, max_length=60)
    on: date
    note: str = Field(default="", max_length=200)
    recorded_at: datetime

    @field_validator("by", "note")
    @classmethod
    def _text_ok(cls, v: str) -> str:
        return _clean_text(v.strip())


class ConditionalItem(_Model):
    """A requirement the user's answers triggered. to;doh-the event planner states who to check with and never states a rule; only the user can mark it."""

    item_id: str
    title: str
    check_with: str
    instruction: str  # "Check with <who>"
    triggered_by: tuple[str, ...]
    status: ItemStatus = "not_verified"
    recorded_by: str | None = None
    recorded_on: date | None = None
    note: str = ""
    source: Literal["rule:community_profile"] = "rule:community_profile"


class CommunityStatus(_Model):
    plan_id: str
    version: int
    subtype: str
    profile_label: str
    profile_version: int
    profile_reviewed: bool
    items: tuple[ConditionalItem, ...] = ()
    not_verified: int = Field(ge=0)
    summary: str  # "N conditional items not verified"; never says safe or compliant
    summary_detail: str
    unverified: tuple[UnverifiedItem, ...] = ()
    checks: tuple[ConstraintCheck, ...] = ()  # the community hard checks of the plan version
    suggested_roles: tuple[tuple[str, str], ...] = ()  # prompts for the user to confirm, never requirements


# ------------------------------------------------------------- digests


class DigestRecord(_Model):
    """A job update for the owner, kept in the app. It is written whenever something the owner should know about changes."""

    digest_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    job_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    plan_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    created_at: datetime
    text: str = Field(min_length=1, max_length=4000)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    read: bool = False
    emailed_at: datetime | None = None  # set when the optional owner email went out, or may have (an unknown outcome is never resent)
    email_result: str = Field(default="", max_length=20)


class NotifyScope(_Model):
    """What an owner email may contain. Status and counts are always included; each of these is opt-in."""

    include_vendor_names: bool = False
    include_amounts: bool = False
    include_raw_text: bool = False  # vendor wording, quoted


class NotifySetting(_Model):
    """One append-only record of the owner's choice. Only the latest counts, and it is active only if its status is ``active``."""

    destination: str = Field(max_length=254)
    scope: NotifyScope
    status: Literal["pending", "active", "disabled"]
    code: str = Field(pattern=r"^[0-9a-f]{64}$")  # binds a confirmation to this exact destination and scope
    proposed_at: datetime
    confirmed_at: datetime | None = None


class NotifyStatus(_Model):
    state: Literal["off", "pending", "on"]
    destination: str | None = None
    include_vendor_names: bool = False
    include_amounts: bool = False
    include_raw_text: bool = False
    sent_last_day: int = 0
    per_day: int = 0
    min_gap_minutes: int = 0


class NotifyProposal(_Model):
    destination: str
    scope: NotifyScope
    code: str
    summary: str  # the exact words shown to the owner before they confirm


class NotifyLogEntry(_Model):
    """One owner-email attempt. It records that and when, never the content or the address (only a short hash of it)."""

    at: datetime
    result: Literal["sent", "failed", "unknown"]
    destination_hash: str = Field(pattern=r"^[0-9a-f]{16}$")
    digest_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    job_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    scope: dict[str, bool] = {}


# ------------------------------------------------- external businesses
# A business found by discovery is NOT a vendor record. It has its own identity, is never merged with or renamed to a seed vendor,
# and carries where it came from. Ids never collide: external vendor ids start with "ext_", seed ids never do.

EXTERNAL_PREFIX = "ext_"
_EXT_ID = r"^ext_[0-9a-f]{24}$"
_EXTERNAL_REF = r"^(osm:(node|way|relation)/[0-9]{1,15}|gplaces:[A-Za-z0-9_-]{8,200}|test:[a-z0-9_-]{1,40})$"


def external_vendor_id(external_id: str) -> str:
    """A path-safe id derived from the source reference, e.g. osm:node/123 or gplaces:<place id>."""
    import hashlib

    return EXTERNAL_PREFIX + hashlib.sha256(external_id.encode("utf-8")).hexdigest()[:24]


class VendorRef(_Model):
    """The one explicit way to point at a vendor: a seed record, or an external business. Lookups apply the vendor database only to ``seed``."""

    kind: Literal["seed", "external"]
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")

    @model_validator(mode="after")
    def _kind_matches_id(self) -> VendorRef:
        if (self.kind == "external") != self.id.startswith(EXTERNAL_PREFIX):
            raise ValueError("external ids start with 'ext_' and seed ids never do")
        return self

    @classmethod
    def of(cls, vendor_id: str) -> VendorRef:
        return cls(kind="external" if vendor_id.startswith(EXTERNAL_PREFIX) else "seed", id=vendor_id)


class ExternalBusiness(_Model):
    vendor_id: str = Field(pattern=_EXT_ID)
    external_id: str = Field(pattern=_EXTERNAL_REF)
    name: str = Field(min_length=1, max_length=120)
    city: str = Field(min_length=1, max_length=80)
    category: VendorCategory
    source: Literal["osm", "gplaces", "test"]
    source_url: str | None = Field(default=None, max_length=2048, pattern=r"^https?://[^\s\x00-\x1f\x7f]+$")
    website: str | None = Field(default=None, max_length=2048, pattern=r"^https?://[^\s\x00-\x1f\x7f<>\"']+$")
    website_source: Literal["listing", "user_input"] = "listing"
    rating: float | None = Field(default=None, ge=0.0, le=5.0)  # only when a source published it
    is_test: bool = False
    origin: Literal["discovered", "test"] = "discovered"  # shown wherever the business appears; derived from the source, never chosen by a caller
    owner_confirmed_by: str | None = Field(default=None, min_length=1, max_length=60)  # a test vendor's address must be owned by this person
    created_at: datetime

    @field_validator("name", "city", "owner_confirmed_by")
    @classmethod
    def _text_ok(cls, v: str | None) -> str | None:
        return v if v is None else _clean_text(v.strip())

    @model_validator(mode="after")
    def _consistent(self) -> ExternalBusiness:
        if self.vendor_id != external_vendor_id(self.external_id):
            raise ValueError("vendor_id does not match external_id")
        if self.is_test != (self.source == "test") or self.is_test != self.external_id.startswith("test:") or self.origin != ("test" if self.is_test else "discovered"):
            raise ValueError("only a business created as a test vendor can be a test vendor, and a discovered one never is")
        if self.is_test and not self.owner_confirmed_by:
            raise ValueError("a test vendor needs the owner's confirmation that the address is theirs")
        return self


class ContactCandidate(_Model):
    """An address software found. It is never used until the user confirms it for that business, and it never exists without the page or listing it came from."""

    candidate_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    vendor_id: str = Field(pattern=_EXT_ID)
    channel: Literal["email", "whatsapp"]
    value: str = Field(min_length=3, max_length=254)
    source_url: str = Field(min_length=8, max_length=2048, pattern=r"^https?://[^\s\x00-\x1f\x7f]+$")
    source_kind: Literal["listing_field", "page_mailto", "page_text"]
    flags: tuple[str, ...] = Field(default=(), max_length=6)
    found_at: datetime
    status: Literal["found", "confirmed", "rejected"] = "found"
    confirmed_by: str | None = Field(default=None, min_length=1, max_length=60)
    confirmed_at: datetime | None = None
    confirmed_value: str | None = Field(default=None, max_length=254)  # what the user confirmed, if they edited the found address

    @model_validator(mode="after")
    def _ok(self) -> ContactCandidate:
        if self.channel == "email" and not _EMAIL.fullmatch(self.value):
            raise ValueError("not a valid email address")
        if self.channel == "whatsapp" and not re.fullmatch(r"[1-9][0-9]{7,14}", self.value):
            raise ValueError("not a valid WhatsApp number")
        if (self.status == "confirmed") != (self.confirmed_by is not None and self.confirmed_at is not None):
            raise ValueError("a confirmed candidate records who confirmed it and when; others record neither")
        return self


class ShortlistEntry(_Model):
    plan_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    category: VendorCategory
    vendor_id: str = Field(pattern=_EXT_ID)
    added_at: datetime


class Suppression(_Model):
    """Do not contact this address (channel and normalised value), on any plan, in any job. Only the user can lift it."""

    suppression_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    channel: Literal["email", "whatsapp"]
    value: str = Field(min_length=3, max_length=254)  # normalised
    reason: Literal["stop_request", "user", "bad_address"]
    source_ref: str = Field(default="", max_length=400)  # the matched message id, or what the user said
    detail: str = Field(default="", max_length=300)  # for a bad address: the failure status and the remote server's own words (untrusted, shown as such)
    created_at: datetime
    created_by: str = Field(min_length=1, max_length=80)
    lifted_at: datetime | None = None
    lifted_by: str | None = Field(default=None, max_length=80)
    lift_reason: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def _lift_is_recorded(self) -> Suppression:
        if (self.lifted_at is None) != (self.lifted_by is None) or (self.lifted_at is not None and not self.lift_reason.strip()):
            raise ValueError("lifting a suppression records who, when and why")
        return self

    @property
    def active(self) -> bool:
        return self.lifted_at is None


class UnmatchedMessage(_Model):
    """Mail that is not tied to anything we sent but looks like a stop request or a bounce. It changes nothing; the user decides."""

    message_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    channel: Literal["email", "whatsapp"]
    sender: str = Field(max_length=254)
    received_at: datetime
    external_id: str = Field(min_length=1, max_length=400)
    kind: Literal["stop_like", "bounce"]
    body: str = Field(max_length=1000)
    reviewed: bool = False

    @field_validator("sender", "body")
    @classmethod
    def _text_ok(cls, v: str) -> str:
        return "".join(ch for ch in v if ch in "\n\t" or unicodedata.category(ch) != "Cc")


class ShortlistItem(_Model):
    """One shortlisted business as the user sees it: where it came from, the addresses found for it, and the confirmed one if any."""

    category: VendorCategory
    business: ExternalBusiness
    candidates: tuple[ContactCandidate, ...] = ()
    contact: VendorContact | None = None  # present only after the user confirmed an address
    blocked: str | None = None  # why it cannot be contacted: "do not contact" or a bad address


class SearchOutcome(_Model):
    source: Literal["osm", "places"]
    category: VendorCategory
    city: str
    added: int = Field(ge=0)
    total: int = Field(ge=0)
    warnings: tuple[str, ...] = ()


class ContactFindResult(_Model):
    vendor_id: str
    added: tuple[ContactCandidate, ...] = ()
    pages: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

