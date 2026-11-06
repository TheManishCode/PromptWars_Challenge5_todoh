"""Ratings, review counts, price level and (optionally) reviews from the Google Places API (New).

The key is sent in a header and never appears in a result, an error or a log. Reviews are requested only on demand because they bill at a
higher tier. Places are ranked by a confidence-weighted rating so a few perfect reviews do not beat hundreds of good ones.
Field names follow Google's published Places API (New) reference and have not yet been run against a live key.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, UTC
from collections.abc import Callable

from eventmate.integrations.discovery import clean_city
from eventmate.planning import cities as city_names
from eventmate.netutil import NETWORK_ERRORS, loads, read_capped
from eventmate.schemas import PlaceInfo, PlaceReview, PlaceSearch

URL = "https://places.googleapis.com/v1/places:searchText"
DETAILS_URL = "https://places.googleapis.com/v1/places/"
DETAIL_FIELDS = "id,displayName,formattedAddress,rating,userRatingCount,priceLevel,googleMapsUri,websiteUri"
PHONE_FIELD = "internationalPhoneNumber"  # billed at a higher tier: requested only when the user asks to see or use the number
PLACE_ID = re.compile(r"[A-Za-z0-9_-]{8,200}")
BASE_FIELDS = "places.id,places.displayName,places.formattedAddress,places.rating,places.userRatingCount,places.priceLevel,places.googleMapsUri"
WEBSITE_FIELD = "places.websiteUri"  # billed at a higher tier than the base fields, so it is requested only when asked for
MAX_REPLY_BYTES = 2_000_000
TIMEOUT_S = 20
CACHE_TTL_S = 0  # Google's policy: Places content is not cached or stored beyond the place id. A caller that has its own licence for caching may pass ``cache_ttl``.
CACHE_MAX = 64
PRIOR_REVIEWS = 20  # weight of the prior: a place needs about this many reviews before its own rating counts for half
PRIOR_MEAN = 4.0
_KEY_OK = re.compile(r"[A-Za-z0-9_-]{20,80}")
_PRICE = {"PRICE_LEVEL_INEXPENSIVE": 1, "PRICE_LEVEL_MODERATE": 2, "PRICE_LEVEL_EXPENSIVE": 3, "PRICE_LEVEL_VERY_EXPENSIVE": 4}
_TAGS = re.compile(r"<[^>]+>")
QUERIES = {"caterer": "caterers", "venue": "banquet halls and event venues", "photographer": "event photographers", "hotel": "hotels"}


def _city_level_names() -> dict[str, str]:
    """Every city-level spelling the intake knows, with its one canonical city. Localities are left out: "MG Road" or "New Town" is in many cities."""
    localities = {p for places in city_names._LOCALITIES.values() for p in places}
    return {a: c for a, c in city_names.ALIASES.items() if a not in localities}


# FUTURE(places-city): a listing whose address names no known city is kept, and could be somewhere else. Bias or restrict the search to the city's coordinates (Places "locationRestriction") once the city table carries them.
def in_other_city(address: str | None, city: str) -> bool:
    """True when the address names another known city and not the one asked for. Places matches a name before a location ("Udupi Caterers" in Bengaluru, or any result for a city it
    cannot find), so a listing is kept only when it is not plainly somewhere else. An address that names no known city is kept: it may simply say a locality."""
    if not address:
        return False
    text = address.casefold()
    names = _city_level_names()
    wanted = city_names.canonical(city)
    found = {c for a, c in names.items() if re.search(rf"(?<!\w){re.escape(a)}(?!\w)", text)}
    return bool(found) and wanted not in found and not re.search(rf"(?<!\w){re.escape(city.casefold())}(?!\w)", text)


class PlacesError(Exception):
    """The places lookup failed. The message is safe to show and never contains the key."""


class PlacesNotConfigured(PlacesError):
    pass


def _post(url: str, headers: dict, data: bytes | None, timeout: float) -> bytes:
    """A POST with a body, or a GET when ``data`` is None (a details request)."""
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=timeout) as resp:  # nosec B310 - fixed https URL
        return read_capped(resp, MAX_REPLY_BYTES + 1)


def _text(v, cap: int) -> str:
    v = _TAGS.sub("", v if isinstance(v, str) else "")
    return " ".join("".join(c for c in v if c.isprintable() or c == " ").split())[:cap]


def weighted(rating: float | None, count: int | None) -> float | None:
    if rating is None or not count:
        return None
    return round((count / (count + PRIOR_REVIEWS)) * rating + (PRIOR_REVIEWS / (count + PRIOR_REVIEWS)) * PRIOR_MEAN, 2)


class PlacesSource:
    def __init__(self, api_key: str | None = None, transport: Callable[[str, dict, bytes | None, float], bytes] = _post, clock: Callable[[], float] = time.monotonic, cache_ttl: float = CACHE_TTL_S):
        self._key, self._transport, self._clock, self._ttl = api_key, transport, clock, cache_ttl
        self._cache: dict[tuple[str, str, bool, bool], tuple[float, PlaceSearch]] = {}
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return "PlacesSource(key=<hidden>)"

    def configured(self) -> bool:
        return bool((self._key if self._key is not None else os.environ.get("GOOGLE_PLACES_API_KEY", "")).strip())

    def _api_key(self) -> str:
        key = (self._key if self._key is not None else os.environ.get("GOOGLE_PLACES_API_KEY", "")).strip()
        if not key:
            raise PlacesNotConfigured("places are not configured: add GOOGLE_PLACES_API_KEY to the .env file")
        if not _KEY_OK.fullmatch(key):
            raise PlacesError("GOOGLE_PLACES_API_KEY has an unexpected format")
        return key

    def search(self, kind: str, city: str, reviews: bool = False, limit: int = 10, websites: bool = False) -> PlaceSearch:
        if kind not in QUERIES:
            raise ValueError(f"kind must be one of {', '.join(QUERIES)}")
        city = clean_city(city)
        if not 1 <= limit <= 20:
            raise ValueError("limit must be 1-20")
        key = self._api_key()
        ck = (kind, city.casefold(), reviews, websites)
        with self._lock:
            hit = self._cache.get(ck)
        if self._ttl > 0 and hit and self._clock() - hit[0] < self._ttl:
            return hit[1]
        fields = BASE_FIELDS + (",places.reviews" if reviews else "") + ("," + WEBSITE_FIELD if websites else "")
        body = {"textQuery": f"{QUERIES[kind]} in {city}", "maxResultCount": limit, "languageCode": "en", "regionCode": "IN"}
        headers = {"X-Goog-Api-Key": key, "X-Goog-FieldMask": fields, "Content-Type": "application/json"}
        try:
            raw = self._transport(URL, headers, json.dumps(body).encode(), TIMEOUT_S)
        except urllib.error.HTTPError as exc:
            msg = {
                400: "Google rejected the request or the API key; check GOOGLE_PLACES_API_KEY",
                403: "Google refused the request; enable the Places API (New) for this key and check its restrictions",
                429: "the Google Places quota is used up; try again later",
            }.get(exc.code, f"the Places request failed (HTTP {exc.code})")
            raise PlacesError(msg) from None
        except NETWORK_ERRORS:
            raise PlacesError("Google Places could not be reached; try again shortly") from None
        if len(raw) > MAX_REPLY_BYTES:
            raise PlacesError("the Places reply was too large and was discarded")
        try:
            items = loads(raw)
            items = items.get("places", []) if isinstance(items, dict) else None
        except ValueError:
            items = None
        if not isinstance(items, list):
            raise PlacesError("Google Places returned something unreadable")
        places: list[PlaceInfo] = []
        skipped = 0
        for it in items:
            try:
                places.append(self._place(it, reviews))
            except (ValueError, TypeError, AttributeError, KeyError):
                skipped += 1
        elsewhere = [p for p in places if in_other_city(p.address, city)]
        places = [p for p in places if p not in elsewhere]
        places.sort(key=lambda p: (p.weighted_rating is None, -(p.weighted_rating or 0), p.name))
        result = PlaceSearch(
            kind=kind, city=city, places=tuple(places), fetched_at=datetime.now(UTC),
            warnings=((f"{skipped} place(s) could not be read and were skipped",) if skipped else ()) + ((f"{len(elsewhere)} listing(s) in other cities were left out",) if elsewhere else ()),
        )
        if self._ttl > 0:
            with self._lock:
                if len(self._cache) >= CACHE_MAX:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[ck] = (self._clock(), result)
        return result

    def details(self, place_id: str, phone: bool = False) -> PlaceInfo:
        """One place by its id, fetched now: what to show on a card. Nothing is kept. ``phone`` also asks for the number, which costs more, so only on the user's request."""
        if not isinstance(place_id, str) or not PLACE_ID.fullmatch(place_id):
            raise ValueError("not a place id")
        key = self._api_key()
        headers = {"X-Goog-Api-Key": key, "X-Goog-FieldMask": DETAIL_FIELDS + ("," + PHONE_FIELD if phone else "")}
        try:
            raw = self._transport(DETAILS_URL + place_id, headers, None, TIMEOUT_S)
        except urllib.error.HTTPError as exc:
            msg = {
                404: "Google no longer lists this business under that id. It may have closed, or its id may have changed.",
                400: "Google rejected the request or the API key; check GOOGLE_PLACES_API_KEY",
                403: "Google refused the request; enable the Places API (New) for this key and check its restrictions",
                429: "the Google Places quota is used up; try again later",
            }.get(exc.code, f"the Places request failed (HTTP {exc.code})")
            raise PlacesError(msg) from None
        except NETWORK_ERRORS:
            raise PlacesError("Google Places could not be reached; try again shortly") from None
        if len(raw) > MAX_REPLY_BYTES:
            raise PlacesError("the Places reply was too large and was discarded")
        try:
            it = loads(raw)
            if not isinstance(it, dict):
                raise ValueError
            info = self._place(it, False)
        except (ValueError, TypeError, AttributeError, KeyError):
            raise PlacesError("Google Places returned something unreadable") from None
        number = it.get(PHONE_FIELD)
        if phone and isinstance(number, str) and re.fullmatch(r"\+?[0-9 ()-]{6,24}", number):
            info = info.model_copy(update={"phone": number})
        return info

    @staticmethod
    def _place(it: dict, reviews: bool) -> PlaceInfo:
        name = _text(it["displayName"]["text"], 120)
        if not name:
            raise ValueError("no name")
        rating = it.get("rating") if isinstance(it.get("rating"), (int, float)) and not isinstance(it.get("rating"), bool) and 0 <= it["rating"] <= 5 else None
        count = it.get("userRatingCount") if isinstance(it.get("userRatingCount"), int) and it["userRatingCount"] >= 0 else None
        uri = it.get("googleMapsUri")
        revs = []
        for r in (it.get("reviews") or [])[:5] if reviews else []:
            text = _text(((r.get("text") or r.get("originalText") or {}).get("text")), 300)
            stars = r.get("rating")
            if text:
                revs.append(PlaceReview(rating=stars if isinstance(stars, int) and 1 <= stars <= 5 else None, text=text, when=_text(r.get("relativePublishTimeDescription"), 60) or None))
        fields = {
            "name": name, "address": _text(it.get("formattedAddress"), 200) or None, "rating": rating, "rating_count": count, "weighted_rating": weighted(rating, count),
            "price_level": _PRICE.get(it.get("priceLevel")), "reviews": tuple(revs),
        }
        pid, site = it.get("id"), it.get("websiteUri")
        fields["place_id"] = pid if isinstance(pid, str) and re.fullmatch(r"[A-Za-z0-9_-]{8,200}", pid) else None
        fields["website"] = site if isinstance(site, str) and re.fullmatch(r"https?://[^\s\x00-\x1f\x7f<>\"']{1,2040}", site) else None  # anything else is dropped, the place is kept
        try:
            return PlaceInfo(maps_uri=uri if isinstance(uri, str) else None, **fields)
        except ValueError:
            return PlaceInfo(**fields)  # a link that is not Google Maps is dropped, the place is kept
