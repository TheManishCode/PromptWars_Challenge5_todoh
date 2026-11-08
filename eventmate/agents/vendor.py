"""Vendor Agent: shortlists venues, caterers and photographers.

Every fact (availability, price, capacity, dietary options, booking status)
comes from the seed database through ``VendorRepository``. Nothing here asks
an LLM for facts. All hard constraints are evaluated for every record (no
short-circuit) so a rejection lists *every* reason, not just the first.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import os
from collections import Counter
from pathlib import Path
from datetime import date
from typing import Union
from collections.abc import Callable, Collection

from pydantic import ValidationError

from eventmate.decision_logger import DecisionLogger
from eventmate.schemas import (
    AgentName,
    Alternative,
    BookingStatus,
    BudgetAllocation,
    ConstraintCheck,
    ConstraintViolation,
    ObservationSource,
    DecisionType,
    EventRequest,
    RejectedVendor,
    ShortlistMap,
    Vendor,
    VendorCandidate,
    VendorCategory,
    VendorShortlist,
    Venue,
    VenueSetting,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MAX_DATA_BYTES = 5_000_000
MAX_LOGGED_ALTERNATIVES = 10

Provider = Union[Venue, Vendor]


class DataIntegrityError(Exception):
    """Seed data is malformed. The agent fails closed rather than guessing."""


def _load_records(path: Path) -> list[dict]:
    try:
        if path.stat().st_size > MAX_DATA_BYTES:
            raise DataIntegrityError(f"{path.name} exceeds {MAX_DATA_BYTES} bytes")
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DataIntegrityError(f"cannot read {path.name}: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("schema_version") != 1:
        raise DataIntegrityError(f"{path.name}: unsupported schema_version")
    records = doc.get("records")
    if not isinstance(records, list):
        raise DataIntegrityError(f"{path.name}: 'records' must be a list")
    return records


_QUOTED_ONLY: contextvars.ContextVar[bool] = contextvars.ContextVar("quoted_only", default=False)


def quote_fits(q, provider: Provider, guests: int | None, event_date: date | None) -> bool:
    """Whether a recorded quote may price this event: it was given for the same date, and for a per-plate price the guest count is the one quoted or inside the range the
    vendor itself states (min and max guests). A flat price is for the date, not the headcount. Without the event's date nothing fits: a quote never prices an event it was not given for."""
    if event_date is None or q.quoted_for_date != event_date:
        return False
    if q.basis != "per_plate":
        return True
    if guests is None:
        return False
    lo, hi = getattr(provider, "min_guests", None), getattr(provider, "max_guests", None)
    return q.guest_count == guests or (lo is not None and hi is not None and lo <= guests <= hi)


class VendorRepository:
    """Read-only, validated view over the seed JSON files."""

    def __init__(self, venues_path: Path | None = None, vendors_path: Path | None = None):
        # The demonstration catalogue (9 cities) is the default; EVENTMATE_CATALOG=seed selects the small fixed one the unit tests are written against.
        prefix = "seed" if os.environ.get("EVENTMATE_CATALOG", "full") == "seed" else "catalog"
        venues_path = Path(venues_path) if venues_path else DATA_DIR / f"{prefix}_venues.json"
        vendors_path = Path(vendors_path) if vendors_path else DATA_DIR / f"{prefix}_vendors.json"
        try:
            venues = tuple(Venue.model_validate(r) for r in _load_records(venues_path))
            vendors = tuple(Vendor.model_validate(r) for r in _load_records(vendors_path))
        except ValidationError as exc:
            raise DataIntegrityError(f"invalid record: {exc}") from exc
        ids = [p.id for p in venues + vendors]
        dupes = sorted(i for i, n in Counter(ids).items() if n > 1)
        if dupes:
            raise DataIntegrityError(f"duplicate record ids: {dupes}")
        self._venues = venues
        self._vendors = vendors
        self.quotes = None  # ObservationStore, attached by to;doh-the event planner
        self.access = None  # AccessibilityStore, attached by to;doh-the event planner
        self.quoted: Callable[[], tuple[Provider, ...]] | None = None  # records made from fully confirmed quotes (sourcing/quote_records.py), attached by the service
        self.clock: Callable[[], date] = date.today

    def venue_access(self, venue_id: str):
        """The accessibility features a venue has declared, or None when nothing is known. What the user recorded wins over the seed record."""
        rec = self.access.latest(venue_id) if self.access is not None else None
        if rec is not None:
            return rec.features
        venue = self.get(VendorCategory.VENUE, venue_id)
        return getattr(venue, "accessibility", None)

    def _quote(self, provider: Provider, guests: int | None = None, event_date: date | None = None):
        """The newest recorded quote that prices THIS event, or None. A quote prices an event it fits (``quote_fits``); a quote given for another date or size is only history."""
        if self.quotes is None:
            return None
        fitting = [o for o in self.quotes.quotes(provider.id) if o.source_type != ObservationSource.PUBLIC_LISTING and quote_fits(o, provider, guests, event_date)]
        return fitting[-1] if fitting else None

    def quote_expired(self, provider: Provider, guests: int | None = None, event_date: date | None = None) -> bool:
        q = self._quote(provider, guests, event_date)
        return q is not None and self.clock() > q.expires_at

    def price(self, provider: Provider, guests: int, event_date: date | None = None) -> int:
        """Price for ``guests`` on ``event_date``: the newest unexpired recorded quote that fits this event (plus its known hidden costs), else the seed record.

        A quote for another date, or for a guest count outside what the vendor stated, never prices this event (A1). An expired one is not used either;
        ``evaluate`` then fails the vendor on ``quote_valid`` instead of silently using stale money.
        """
        q = self._quote(provider, guests, event_date)
        if q is None or self.clock() > q.expires_at:
            return provider.cost_for(guests)
        base = q.amount_inr * guests if q.basis == "per_plate" else q.amount_inr
        return base + sum(h.amount_inr for h in q.hidden_costs if h.amount_inr)

    @contextlib.contextmanager
    def quoted_only(self):
        """Inside this block only vendors made from a fully confirmed quote are visible: no seed or catalogue record is. A plan built here comes from what vendors answered, nothing built in."""
        token = _QUOTED_ONLY.set(True)
        try:
            yield
        finally:
            _QUOTED_ONLY.reset(token)

    def _quoted(self, category: VendorCategory) -> tuple[Provider, ...]:
        mine = self.quoted() if self.quoted is not None else ()
        return tuple(p for p in mine if (category == VendorCategory.VENUE) == isinstance(p, Venue) and (isinstance(p, Venue) or p.category == category))

    def providers(self, category: VendorCategory) -> tuple[Provider, ...]:
        if _QUOTED_ONLY.get():
            return self._quoted(category)
        if category == VendorCategory.VENUE:
            return self._venues + self._quoted(category)
        return tuple(v for v in self._vendors if v.category == category) + self._quoted(category)

    def get(self, category: VendorCategory, provider_id: str) -> Provider | None:
        return next((p for p in self.providers(category) if p.id == provider_id), None)

    def cities(self) -> tuple[str, ...]:
        extra = self.quoted() if self.quoted is not None else ()
        return tuple(sorted({p.city for p in (extra if _QUOTED_ONLY.get() else self._venues + self._vendors + extra)}))


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def evaluate(
    provider: Provider, request: EventRequest, category: VendorCategory, cap: int, excluded: bool = False,
    price: int | None = None, quote_expired: bool = False,
) -> list[ConstraintCheck]:
    """Run every hard constraint against one record."""
    guests = request.guest_count
    checks = [
        ConstraintCheck(
            name="booking_status",
            passed=provider.booking_status == BookingStatus.AVAILABLE and not excluded,
            detail="vendor was cancelled or excluded for this event" if excluded else f"status is {provider.booking_status.value}",
        ),
        ConstraintCheck(
            name="city",
            passed=_norm(provider.city) == _norm(request.city),
            detail=f"vendor city {provider.city}, requested {request.city}",
        ),
        ConstraintCheck(
            name="date_availability",
            passed=request.event_date in provider.available_dates,
            detail=f"{request.event_date.isoformat()} "
            + ("is" if request.event_date in provider.available_dates else "is not")
            + " in the vendor's available dates",
        ),
    ]
    if category == VendorCategory.VENUE:
        attendance = request.planning_attendance  # the top of a community event's attendance range; the guest count for every other event
        checks.append(
            ConstraintCheck(
                name="capacity",
                passed=attendance <= provider.capacity,
                detail=f"{attendance} guests vs capacity {provider.capacity}",
            )
        )
        wanted = request.venue_setting
        checks.append(
            ConstraintCheck(
                name="venue_setting",
                passed=wanted == VenueSetting.ANY or wanted.value == provider.setting,
                detail=f"venue is {provider.setting}, requested {wanted.value}",
            )
        )
    elif category == VendorCategory.CATERER:
        checks.append(
            ConstraintCheck(
                name="capacity",
                passed=provider.min_guests is not None and provider.max_guests is not None and provider.min_guests <= guests <= provider.max_guests,  # a missing range does not pass
                detail=f"{guests} guests vs served range {provider.min_guests}-{provider.max_guests}",
            )
        )
        missing = [d for d in request.dietary if d not in provider.dietary_options]
        checks.append(
            ConstraintCheck(
                name="dietary",
                passed=not missing,
                detail="supports all required diets"
                if not missing
                else "does not offer: " + ", ".join(d.value for d in missing),
            )
        )
    cost = provider.cost_for(guests) if price is None else price
    if quote_expired:
        checks.append(ConstraintCheck(name="quote_valid", passed=False, detail="re-quote needed: the recorded quote has expired"))
    checks.append(
        ConstraintCheck(
            name="budget_cap",
            passed=cost <= cap,
            detail=f"cost INR {cost:,} vs category cap INR {cap:,}",
        )
    )
    return checks


def _score(rating: float, cost: int, cap: int) -> float:
    price_term = 1.0 - cost / cap if cap > 0 else 0.0
    return round(0.6 * (rating / 5.0) + 0.4 * price_term, 4)


def dietary_repair(request) -> str:
    """What to do when no caterer offers every required diet. A restriction guests really have is not something to relax, and the planner books one caterer for the whole event, so
    several diets must all be offered by that one caterer; serving different groups from different caterers is not supported yet."""
    diets = ", ".join(d.value.replace("_", " ") for d in request.dietary)
    if len(request.dietary) > 1:
        what = f"The planner books one caterer for the whole event, so that caterer must offer all of: {diets}. None in {request.city} does."
    else:
        what = f"No caterer in {request.city} offers {diets}."
    some = " Only some guests need a diet, but one caterer still has to cover every group." if request.diet_groups else ""
    return f"{what}{some} Add a caterer that offers them, or ask a caterer in writing whether they can; remove a diet from the request only if nobody needs it."


class VendorAgent:
    agent_name = AgentName.VENDOR

    def __init__(self, repo: VendorRepository, logger: DecisionLogger, max_candidates: int = 3):
        if max_candidates < 1:
            raise ValueError("max_candidates must be >= 1")
        self.repo = repo
        self.logger = logger
        self.max_candidates = max_candidates

    def shortlist(self, request: EventRequest, allocation: BudgetAllocation, exclude_ids: Collection[str] = ()) -> ShortlistMap:
        return {
            category: self.shortlist_category(request, category, allocation.cap_for(category), exclude_ids)
            for category in request.services
        }

    def shortlist_category(
        self, request: EventRequest, category: VendorCategory, cap: int, exclude_ids: Collection[str] = ()
    ) -> VendorShortlist:
        passed: list[tuple[Provider, int, float, list[ConstraintCheck]]] = []
        rejected: list[tuple[Provider, list[ConstraintCheck]]] = []
        providers = self.repo.providers(category)
        for p in providers:
            checks = evaluate(p, request, category, cap, excluded=p.id in exclude_ids, price=self.repo.price(p, request.guest_count, request.event_date), quote_expired=self.repo.quote_expired(p, request.guest_count, request.event_date))
            failed = [c for c in checks if not c.passed]
            if failed:
                rejected.append((p, failed))
            else:
                cost = self.repo.price(p, request.guest_count, request.event_date)
                passed.append((p, cost, _score(p.rating, cost, cap), checks))
        passed.sort(key=lambda t: (-t[2], t[1], t[0].id))
        top, rest = passed[: self.max_candidates], passed[self.max_candidates :]

        candidates = tuple(
            VendorCandidate(
                vendor_id=p.id,
                name=p.name,
                category=category,
                estimated_cost=cost,
                score=score,
                checks=tuple(checks),
                source=p.source,
            )
            for p, cost, score, checks in top
        )
        rejected_models = tuple(
            RejectedVendor(
                vendor_id=p.id,
                name=p.name,
                category=category,
                estimated_cost=self.repo.price(p, request.guest_count, request.event_date),
                failed=tuple(f),
            )
            for p, f in sorted(rejected, key=lambda t: t[0].id)
        )
        violations = () if candidates else self._repair(request, category, cap, providers, rejected)
        shortlist = VendorShortlist(
            category=category,
            budget_cap=cap,
            candidates=candidates,
            rejected=rejected_models,
            violations=tuple(violations),
        )
        self._log(request, shortlist, rest)
        return shortlist

    # ------------------------------------------------------------ repairs

    def _repair(self, request, category, cap, providers, rejected) -> list[ConstraintViolation]:
        label = category.value
        if not providers:
            return [
                ConstraintViolation(
                    constraint="vendor_coverage",
                    detail=f"database has no {label} records",
                    repair=f"add {label} records to the database before planning",
                )
            ]
        only_budget = [(p, self.repo.price(p, request.guest_count, request.event_date)) for p, f in rejected if {c.name for c in f} == {"budget_cap"}]
        if only_budget:
            p, cost = min(only_budget, key=lambda t: (t[1], t[0].id))
            return [
                ConstraintViolation(
                    constraint="budget_cap",
                    detail=f"cheapest otherwise-eligible {label} ({p.name}) costs INR {cost:,}; cap is INR {cap:,}",
                    repair=f"raise the {label} allocation to at least INR {cost:,} or raise the total budget",
                )
            ]
        counts = Counter(c.name for _, f in rejected for c in f)
        worst, _ = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        return [
            ConstraintViolation(
                constraint=worst,
                detail=f"no {label} satisfies all constraints; most common failure is {worst} "
                f"({counts[worst]} of {len(providers)} records)",
                repair=self._repair_text(worst, request, category, rejected),
            )
        ]

    @staticmethod
    def _repair_text(constraint, request, category, rejected) -> str:
        label = category.value
        if constraint == "date_availability":
            near = sorted(
                {
                    d
                    for p, f in rejected
                    if {c.name for c in f} == {"date_availability"}
                    for d in p.available_dates
                },
                key=lambda d: (abs((d - request.event_date).days), d),
            )[:3]
            if near:
                return "move the event to one of: " + ", ".join(d.isoformat() for d in near)
            return f"no {label} is free near {request.event_date.isoformat()}; choose a different date"
        if constraint == "city":
            return f"no {label} in {request.city}; choose a different city or add vendors there"
        if constraint == "capacity":
            return f"change the guest count ({request.guest_count}) or relax capacity needs for the {label}"
        if constraint == "dietary":
            return dietary_repair(request)
        if constraint == "venue_setting":
            return "allow either indoor or outdoor venues"
        if constraint == "quote_valid":
            return f"re-quote needed: ask the {label} vendors for a fresh quote and record it"
        if constraint == "booking_status":
            return f"all matching {label}s are unavailable; add alternatives or choose another date"
        return f"relax the {constraint} constraint"

    # ------------------------------------------------------------ logging

    def _log(self, request: EventRequest, shortlist: VendorShortlist, rest) -> None:
        label = shortlist.category.value
        db = "database:seed_venues.json" if shortlist.category == VendorCategory.VENUE else "database:seed_vendors.json"
        if not shortlist.candidates:
            v = shortlist.violations[0]
            self.logger.log(
                request_id=request.request_id,
                agent=self.agent_name,
                decision_type=DecisionType.FAILURE,
                summary=f"No eligible {label} found",
                alternatives=self._alternatives(shortlist, rest),
                constraints_checked=tuple(c for r in shortlist.rejected[:3] for c in r.failed),
                justification=f"{v.detail}. Repair: {v.repair}.",
                confidence=1.0,
                source=db,
            )
            return
        top = shortlist.candidates[0]
        alts = self._alternatives(shortlist, rest)
        self.logger.log(
            request_id=request.request_id,
            agent=self.agent_name,
            decision_type=DecisionType.RECOMMENDATION,
            summary=f"Shortlisted {len(shortlist.candidates)} {label}(s); top choice {top.name}",
            alternatives=alts,
            no_alternative_reason=None if alts else f"only {top.name} exists in the database for {request.city}",
            constraints_checked=top.checks,
            justification=(
                f"{top.name} passes every hard constraint at INR {top.estimated_cost:,} "
                f"(cap INR {shortlist.budget_cap:,}) and has the best rating/price score {top.score}. "
                f"{len(shortlist.rejected)} record(s) were rejected."
            ),
            confidence=0.9 if len(shortlist.candidates) >= 2 else 0.6,
            source=top.source,
        )

    @staticmethod
    def _alternatives(shortlist: VendorShortlist, rest) -> tuple[Alternative, ...]:
        alts: list[Alternative] = []
        for c in shortlist.candidates[1:]:
            alts.append(
                Alternative(
                    option=c.name,
                    outcome=f"eligible, INR {c.estimated_cost:,}, score {c.score}",
                    reason_not_chosen="lower rating/price score than the top choice",
                )
            )
        for p, cost, score, _ in rest:
            alts.append(
                Alternative(
                    option=p.name,
                    outcome=f"eligible, INR {cost:,}, score {score}",
                    reason_not_chosen="ranked below the shortlist size limit",
                )
            )
        for r in shortlist.rejected:
            alts.append(
                Alternative(
                    option=r.name,
                    outcome="rejected",
                    reason_not_chosen="; ".join(f"{c.name}: {c.detail}" for c in r.failed),
                )
            )
        return tuple(alts[:MAX_LOGGED_ALTERNATIVES])
