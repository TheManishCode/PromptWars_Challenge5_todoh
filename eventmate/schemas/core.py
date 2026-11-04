"""Typed data contracts for to;doh-the event planner.

Every structure that crosses an agent boundary, is persisted, or is logged is
a frozen Pydantic model with ``extra="forbid"``: unknown fields are rejected
rather than silently dropped, and nothing is mutated after validation.

Money is whole INR (``int``) to avoid float drift; budget sums are exact.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, UTC
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_GUESTS = 100_000
MAX_BUDGET_INR = 10_000_000_000
MAX_ORGANIZERS = 10

# Decision sources are restricted so a log entry can never claim a fact came
# from somewhere unaccountable. LLM-originated content must be tagged "llm:".
SOURCE_PATTERN = re.compile(
    r"^(user_input|database:[A-Za-z0-9_./-]+|rule:[A-Za-z0-9_.-]+"
    r"|tool:[A-Za-z0-9_.-]+|llm:[A-Za-z0-9_.-]+|web:[a-z0-9.-]+)$"
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EventType(str, Enum):
    ENGAGEMENT_PARTY = "engagement_party"
    WEDDING = "wedding"
    BIRTHDAY = "birthday"
    CORPORATE = "corporate"
    CONFERENCE = "conference"
    COMMUNITY = "community"
    OTHER = "other"


class DietaryRequirement(str, Enum):
    VEGETARIAN = "vegetarian"
    NON_VEGETARIAN = "non_vegetarian"
    VEGAN = "vegan"
    JAIN = "jain"
    HALAL = "halal"
    GLUTEN_FREE = "gluten_free"


class Allergen(str, Enum):
    PEANUT = "peanut"
    TREE_NUT = "tree_nut"
    DAIRY = "dairy"
    EGG = "egg"
    SHELLFISH = "shellfish"
    FISH = "fish"
    SOY = "soy"
    SESAME = "sesame"
    GLUTEN = "gluten"
    CELERY = "celery"
    MUSTARD = "mustard"
    SULPHITES = "sulphites"
    LUPIN = "lupin"


class VenueSetting(str, Enum):
    INDOOR = "indoor"
    OUTDOOR = "outdoor"
    ANY = "any"


class VendorCategory(str, Enum):
    VENUE = "venue"
    CATERER = "caterer"
    PHOTOGRAPHER = "photographer"


class ReminderKind(str, Enum):
    INVITATION = "invitation"
    VENDOR_CONFIRMATION = "vendor_confirmation"


class BookingStatus(str, Enum):
    AVAILABLE = "available"
    HELD = "held"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class AgentName(str, Enum):
    MANAGER = "manager"
    BUDGET = "budget"
    VENDOR = "vendor"
    GUEST = "guest"
    SCHEDULER = "scheduler"
    PLANNER = "planner"
    VALIDATOR = "validator"
    EXPLAINER = "explainer"
    REPLANNER = "replanner"
    IDEAS = "ideas"


class TaskKind(str, Enum):
    ALLOCATE_BUDGET = "allocate_budget"
    TRACK_GUESTS = "track_guests"
    SOURCE_VENDOR = "source_vendor"
    BUILD_SCHEDULE = "build_schedule"
    SCHEDULE_REMINDER = "schedule_reminder"
    BUILD_PLAN_MODEL = "build_plan_model"
    VALIDATE_PLAN = "validate_plan"
    EXPLAIN_PLAN = "explain_plan"


class TaskStatus(str, Enum):
    PENDING = "pending"
    DONE = "done"


class ConstraintCheck(_Model):
    """Outcome of one named hard-constraint check; the unit of explainability."""

    name: str
    passed: bool
    detail: str


class ConstraintViolation(_Model):
    """A failed constraint with a concrete proposed repair (quality rule)."""

    constraint: str
    detail: str
    repair: str


# ---------------------------------------------------------------- request


class CapWeight(_Model):
    """A user-approved share (relative weight) for one category's budget cap."""

    category: VendorCategory
    weight: int = Field(gt=0, le=MAX_BUDGET_INR)


# ------------------------------------------------------------- community
# Community events. Three kinds of requirement are kept apart on purpose:
#   A. hard checks the validator computes from what the user entered and from the database (these live in ``CommunityDetails``);
#   B. conditional requirements, shown when the user's answers trigger them and only ever marked by the user (see ``community.py``);
#   C. suggestions (ideas), which are not constraints at all.

ACCESS_FEATURES = ("step_free_access", "accessible_toilet", "reserved_seating", "hearing_support", "accessible_parking")
AccessFeature = Literal["step_free_access", "accessible_toilet", "reserved_seating", "hearing_support", "accessible_parking"]
Phase = Literal["setup", "event", "teardown"]
YesNoUnsure = Literal["yes", "no", "unsure"]
FundingStatus = Literal["pledged", "committed", "received"]
_SLUG = r"^[a-z0-9_]{1,30}$"
MAX_FUNDING_SOURCES = 20
MAX_VOLUNTEERS = 200
MAX_SLOTS = 60
MAX_SHIFTS = 400
MAX_ACTIVITIES = 30


def _name(v: str, limit: int = 60) -> str:
    v = v.strip()
    if not 1 <= len(v) <= limit:
        raise ValueError(f"must be 1-{limit} characters")
    if any(unicodedata.category(ch) == "Cc" for ch in v):
        raise ValueError("control characters are not allowed")
    return v


class FundingSource(_Model):
    """Money for the event. ``pledged`` is a hope, ``committed`` is the user's statement that it is promised, ``received`` is money in hand."""

    label: str = Field(min_length=1, max_length=60)
    status: FundingStatus
    amount_inr: int = Field(gt=0, le=MAX_BUDGET_INR)

    @field_validator("label")
    @classmethod
    def _label_ok(cls, v: str) -> str:
        return _name(v)


class VolunteerSlot(_Model):
    """A role the user confirmed is needed, in one phase, with how many people. Times are minutes from the start of the phase."""

    slot_id: str = Field(pattern=_SLUG)
    role: str = Field(min_length=1, max_length=40)
    phase: Phase
    needed: int = Field(default=1, ge=1, le=50)
    start_min: int = Field(default=0, ge=0)
    end_min: int | None = Field(default=None, ge=1)  # None = to the end of the phase

    @field_validator("role")
    @classmethod
    def _role_ok(cls, v: str) -> str:
        return _name(v)

    @model_validator(mode="after")
    def _ordered(self) -> VolunteerSlot:
        if self.end_min is not None and self.end_min <= self.start_min:
            raise ValueError("a slot must end after it starts")
        return self


class VolunteerShift(_Model):
    volunteer: str = Field(min_length=1, max_length=60)
    role: str = Field(min_length=1, max_length=40)
    phase: Phase
    start_min: int = Field(default=0, ge=0)
    end_min: int | None = Field(default=None, ge=1)

    @field_validator("volunteer", "role")
    @classmethod
    def _text_ok(cls, v: str) -> str:
        return _name(v)

    @model_validator(mode="after")
    def _ordered(self) -> VolunteerShift:
        if self.end_min is not None and self.end_min <= self.start_min:
            raise ValueError("a shift must end after it starts")
        return self


class ProgramActivity(_Model):
    """Something on the day's programme. Activities that share a resource (a stage, a hall) cannot overlap."""

    activity_id: str = Field(pattern=_SLUG)
    title: str = Field(min_length=1, max_length=80)
    minutes: int = Field(ge=5, le=720)
    resource: str = Field(pattern=r"^[a-z0-9_]{1,20}$")
    after: str | None = Field(default=None, pattern=_SLUG)  # an activity that must finish first

    @field_validator("title")
    @classmethod
    def _title_ok(cls, v: str) -> str:
        return _name(v)


class CommunityAnswers(_Model):
    """The user's answers that decide which conditional requirements are shown. ``unsure`` shows them (fail closed); only ``no`` hides one."""

    public_event: YesNoUnsure = "unsure"
    third_party_premises: YesNoUnsure = "unsure"
    amplified_sound: YesNoUnsure = "unsure"
    sells_food: YesNoUnsure = "unsure"
    collects_money: YesNoUnsure = "unsure"
    large_crowd: YesNoUnsure = "unsure"
    temporary_structures: YesNoUnsure = "unsure"
    physical_activities: YesNoUnsure = "unsure"
    outdoor_area: YesNoUnsure = "unsure"


class CommunityDetails(_Model):
    subtype: str = Field(default="other", pattern=r"^[a-z0-9_]{1,40}$")  # checked against the profile file, which is data
    attendance_low: int | None = Field(default=None, ge=1, le=MAX_GUESTS)
    attendance_high: int | None = Field(default=None, ge=1, le=MAX_GUESTS)
    own_funds_inr: int | None = Field(default=None, ge=0, le=MAX_BUDGET_INR)
    count_committed: bool = False  # opt in: count committed (not received) money toward what the plan may spend
    funding: tuple[FundingSource, ...] = Field(default=(), max_length=MAX_FUNDING_SOURCES)
    volunteers: tuple[str, ...] = Field(default=(), max_length=MAX_VOLUNTEERS)
    slots: tuple[VolunteerSlot, ...] = Field(default=(), max_length=MAX_SLOTS)
    shifts: tuple[VolunteerShift, ...] = Field(default=(), max_length=MAX_SHIFTS)
    setup_owner: str | None = Field(default=None, max_length=60)
    cleanup_owner: str | None = Field(default=None, max_length=60)
    program: tuple[ProgramActivity, ...] = Field(default=(), max_length=MAX_ACTIVITIES)
    access_needs: tuple[AccessFeature, ...] = ()
    answers: CommunityAnswers = CommunityAnswers()

    @field_validator("volunteers", "setup_owner", "cleanup_owner")
    @classmethod
    def _names_ok(cls, v):
        if v is None:
            return v
        return tuple(_name(x) for x in v) if isinstance(v, tuple) else _name(v)

    @model_validator(mode="after")
    def _consistent(self) -> CommunityDetails:
        lo, hi = self.attendance_low, self.attendance_high
        if lo is not None and hi is not None and lo > hi:
            raise ValueError("attendance_low is above attendance_high")
        roster = set(self.volunteers)
        if len(roster) != len(self.volunteers):
            raise ValueError("volunteer names must be unique")
        for who in (self.setup_owner, self.cleanup_owner):
            if who is not None and who not in roster:
                raise ValueError(f"{who!r} is not on the volunteer list")
        unknown = sorted({s.volunteer for s in self.shifts} - roster)
        if unknown:
            raise ValueError(f"shifts name people who are not on the volunteer list: {unknown[:3]}")
        for what, ids in (("slot", [s.slot_id for s in self.slots]), ("activity", [a.activity_id for a in self.program])):
            if len(set(ids)) != len(ids):
                raise ValueError(f"{what} ids must be unique")
        known = {a.activity_id for a in self.program}
        for a in self.program:
            if a.after is not None and (a.after not in known or a.after == a.activity_id):
                raise ValueError(f"activity {a.activity_id} comes after an activity that does not exist")
        if len(set(self.access_needs)) != len(self.access_needs):
            raise ValueError("access needs repeat")
        return self


class ExtraTask(_Model):
    """A step the organiser added to a plan ("a mehendi the night before"). It is part of the request, so the scheduler places it and the validator knows it is allowed."""

    task_id: str = Field(pattern=r"^extra_[a-z0-9_]{1,50}$")
    name: str = Field(min_length=1, max_length=80, pattern=r"^[^\x00-\x1f<>]+$")
    duration_minutes: int = Field(ge=1, le=1440)
    after: tuple[str, ...] = Field(default=(), max_length=10)  # ids of tasks that must finish first
    days_before_event: int = Field(default=0, ge=0, le=60)  # 0 = on the event day, before it starts


class EventRequest(_Model):
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    raw_text: str = Field(min_length=1, max_length=2000)
    event_type: EventType
    guest_count: int = Field(ge=1, le=MAX_GUESTS)
    city: str = Field(min_length=1, max_length=80)
    event_date: date
    budget_total: int = Field(gt=0, le=MAX_BUDGET_INR)
    currency: Literal["INR"] = "INR"
    dietary: tuple[DietaryRequirement, ...] = ()
    # allergies the user stated; no vendor record can prove a kitchen is free of one, so the plan says so instead of passing (empty for every plan made before this field)
    allergens: tuple[Allergen, ...] = ()
    # the user's own words for an allergy we could not name: never read as "no allergy"; the plan says it was not understood
    allergy_unclear: tuple[Annotated[str, Field(min_length=1, max_length=60, pattern=r"^[A-Za-z' -]+$")], ...] = Field(default=(), max_length=5)
    # food requirements the vendor records cannot answer (sattvic, no onion or garlic, kosher...), as fixed labels; and the user's own words for a restriction we could not place
    diet_notes: tuple[Annotated[str, Field(min_length=1, max_length=60, pattern=r"^[A-Za-z0-9' %-]+$")], ...] = Field(default=(), max_length=5)
    diet_unclear: tuple[Annotated[str, Field(min_length=1, max_length=60, pattern=r"^[A-Za-z' -]+$")], ...] = Field(default=(), max_length=5)
    venue_setting: VenueSetting = VenueSetting.ANY
    services: tuple[VendorCategory, ...] = (VendorCategory.VENUE,)
    reminders: tuple[ReminderKind, ...] = ()
    assumptions: tuple[str, ...] = ()
    parse_confidence: float = Field(ge=0.0, le=1.0)
    # empty = the BudgetPolicy default split; otherwise exactly one weight per requested service
    cap_weights: tuple[CapWeight, ...] = ()
    # people who can make bookings in parallel; the capacity of the scheduler's organizer resource
    organizers: int = Field(default=1, ge=1, le=MAX_ORGANIZERS)
    # only for community events; everything the community hard checks compute from
    community: CommunityDetails | None = None
    # steps the organiser added after the plan existed; empty for every plan made before this field (old stored plans load as they are)
    extra_tasks: tuple[ExtraTask, ...] = Field(default=(), max_length=10)

    @property
    def planning_attendance(self) -> int:
        """The headcount a venue must hold: the expected guests, or the top of the user's attendance range when that is higher."""
        high = self.community.attendance_high if self.community else None
        return max(self.guest_count, high or 0)

    def with_changes(self, **changes) -> EventRequest:
        """Copy with changes, re-running validation (``model_copy`` would skip it)."""
        return EventRequest.model_validate({**self.model_dump(), **changes})

    @model_validator(mode="after")
    def _extra_task_ids_are_unique(self) -> EventRequest:
        ids = [x.task_id for x in self.extra_tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("extra task ids must be unique")
        return self

    @model_validator(mode="after")
    def _venue_always_required(self) -> EventRequest:
        if VendorCategory.VENUE not in self.services:
            raise ValueError("services must include a venue")
        if len(set(self.services)) != len(self.services):
            raise ValueError("services contain duplicates")
        if self.cap_weights and sorted(w.category.value for w in self.cap_weights) != sorted(c.value for c in self.services):
            raise ValueError("cap_weights must name each requested service exactly once")
        if self.community is not None:
            if self.event_type != EventType.COMMUNITY:
                raise ValueError("community details only apply to a community event")
            lo, hi = self.community.attendance_low, self.community.attendance_high
            if (lo is not None and lo > self.guest_count) or (hi is not None and hi < self.guest_count):
                raise ValueError("the expected guest count must lie inside the attendance range")
        return self


# ------------------------------------------------------------- task graph


class Task(_Model):
    id: str = Field(pattern=r"^[a-z_]+:[a-z_]+$")
    kind: TaskKind
    subject: str = Field(pattern=r"^[a-z_]+$")
    agent: AgentName
    depends_on: tuple[str, ...] = ()
    description: str
    status: TaskStatus = TaskStatus.PENDING


class TaskGraph(_Model):
    """A DAG of tasks. Structural validity is enforced at construction."""

    tasks: tuple[Task, ...]

    @model_validator(mode="after")
    def _structure(self) -> TaskGraph:
        ids = [t.id for t in self.tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate task ids")
        keys = [(t.kind, t.subject) for t in self.tasks]
        if len(set(keys)) != len(keys):
            raise ValueError("redundant tasks: duplicate (kind, subject)")
        known = set(ids)
        for t in self.tasks:
            if t.id in t.depends_on:
                raise ValueError(f"task {t.id} depends on itself")
            if len(set(t.depends_on)) != len(t.depends_on):
                raise ValueError(f"task {t.id} has duplicate dependencies")
            missing = set(t.depends_on) - known
            if missing:
                raise ValueError(f"task {t.id} depends on unknown {sorted(missing)}")
        if len(self._topological(self.tasks)) != len(self.tasks):
            raise ValueError("task graph contains a cycle")
        return self

    @staticmethod
    def _topological(tasks: tuple[Task, ...]) -> list[Task]:
        remaining = {t.id: t for t in tasks}
        done: set = set()
        ordered: list[Task] = []
        while remaining:
            ready = sorted(
                (t for t in remaining.values() if set(t.depends_on) <= done),
                key=lambda t: t.id,
            )
            if not ready:
                break
            for t in ready:
                ordered.append(t)
                done.add(t.id)
                del remaining[t.id]
        return ordered

    def topological_order(self) -> list[Task]:
        return self._topological(self.tasks)

    def get(self, task_id: str) -> Task:
        for t in self.tasks:
            if t.id == task_id:
                return t
        raise KeyError(task_id)

    def ancestors(self, task_id: str) -> set:
        seen: set = set()
        stack = list(self.get(task_id).depends_on)
        while stack:
            current = stack.pop()
            if current not in seen:
                seen.add(current)
                stack.extend(self.get(current).depends_on)
        return seen

    def with_status(self, task_id: str, status: TaskStatus) -> TaskGraph:
        self.get(task_id)
        return TaskGraph(
            tasks=tuple(
                t.model_copy(update={"status": status}) if t.id == task_id else t
                for t in self.tasks
            )
        )


# ----------------------------------------------------------------- budget


class BudgetLine(_Model):
    category: str
    amount: int = Field(ge=0)
    share: float = Field(ge=0.0, le=1.0)


class BudgetAllocation(_Model):
    total: int = Field(gt=0)
    currency: Literal["INR"] = "INR"
    lines: tuple[BudgetLine, ...]
    contingency: int = Field(ge=0)

    @model_validator(mode="after")
    def _sums_exactly(self) -> BudgetAllocation:
        spent = sum(line.amount for line in self.lines) + self.contingency
        if spent != self.total:
            raise ValueError(f"allocation sums to {spent}, expected {self.total}")
        return self

    def cap_for(self, category: VendorCategory) -> int:
        for line in self.lines:
            if line.category == category.value:
                return line.amount
        raise KeyError(category.value)


class BudgetAssessment(_Model):
    status: Literal["within_budget", "contingency_breach", "overspend", "incomplete"]
    cheapest_total: int | None = None
    headroom: int | None = None
    violations: tuple[ConstraintViolation, ...] = ()


# ----------------------------------------------------------------- vendors


class _Provider(_Model):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    name: str = Field(min_length=1, max_length=120)
    city: str = Field(min_length=1, max_length=80)
    available_dates: tuple[date, ...] = ()
    booking_status: BookingStatus = BookingStatus.AVAILABLE
    rating: float = Field(ge=0.0, le=5.0)
    source: str

    @field_validator("source")
    @classmethod
    def _source_ok(cls, v: str) -> str:
        if not SOURCE_PATTERN.fullmatch(v):
            raise ValueError("invalid source tag")
        return v


class Venue(_Provider):
    setting: Literal["indoor", "outdoor"]
    capacity: int = Field(ge=1, le=MAX_GUESTS)
    flat_fee: int = Field(ge=0, le=MAX_BUDGET_INR)
    # None = the record says nothing (unknown, not "none"); a tuple is what the venue declared
    accessibility: tuple[AccessFeature, ...] | None = None

    def cost_for(self, guest_count: int) -> int:
        return self.flat_fee


class Vendor(_Provider):
    """Caterer or photographer. Pricing fields depend on ``category``."""

    category: Literal[VendorCategory.CATERER, VendorCategory.PHOTOGRAPHER]
    price_per_plate: int | None = Field(default=None, ge=0, le=MAX_BUDGET_INR)
    min_guests: int | None = Field(default=None, ge=1, le=MAX_GUESTS)
    max_guests: int | None = Field(default=None, ge=1, le=MAX_GUESTS)
    dietary_options: tuple[DietaryRequirement, ...] = ()
    flat_fee: int | None = Field(default=None, ge=0, le=MAX_BUDGET_INR)

    @model_validator(mode="after")
    def _pricing_matches_category(self) -> Vendor:
        if self.category == VendorCategory.CATERER:
            if self.price_per_plate is None or self.min_guests is None or self.max_guests is None:
                raise ValueError("caterer needs price_per_plate, min_guests, max_guests")
            if self.flat_fee is not None:
                raise ValueError("caterer must not set flat_fee")
            if self.min_guests > self.max_guests:
                raise ValueError("min_guests > max_guests")
        else:
            if self.flat_fee is None:
                raise ValueError("photographer needs flat_fee")
            if self.price_per_plate is not None:
                raise ValueError("photographer must not set price_per_plate")
        return self

    def cost_for(self, guest_count: int) -> int:
        if self.category == VendorCategory.CATERER:
            if self.price_per_plate is None:
                raise ValueError("caterer needs price_per_plate")  # the model validator guarantees it; a bypass is a bug, not a free quote
            return self.price_per_plate * guest_count
        if self.flat_fee is None:
            raise ValueError("photographer needs flat_fee")
        return self.flat_fee


class VendorCandidate(_Model):
    vendor_id: str
    name: str
    category: VendorCategory
    estimated_cost: int = Field(ge=0)
    score: float
    checks: tuple[ConstraintCheck, ...]
    source: str


class RejectedVendor(_Model):
    vendor_id: str
    name: str
    category: VendorCategory
    estimated_cost: int = Field(ge=0)
    failed: tuple[ConstraintCheck, ...]


class VendorShortlist(_Model):
    category: VendorCategory
    budget_cap: int
    candidates: tuple[VendorCandidate, ...]
    rejected: tuple[RejectedVendor, ...]
    violations: tuple[ConstraintViolation, ...] = ()


# -------------------------------------------------------------- decisions


class Alternative(_Model):
    option: str
    outcome: str
    reason_not_chosen: str


class DecisionType(str, Enum):
    PARSE = "parse"
    DECOMPOSITION = "decomposition"
    ALLOCATION = "allocation"
    RECOMMENDATION = "recommendation"
    ASSESSMENT = "assessment"
    FAILURE = "failure"


class DecisionLogEntry(_Model):
    entry_id: str
    timestamp: datetime
    run_id: str
    request_id: str
    agent: AgentName
    decision_type: DecisionType
    summary: str = Field(min_length=1, max_length=500)
    alternatives: tuple[Alternative, ...] = ()
    no_alternative_reason: str | None = None
    constraints_checked: tuple[ConstraintCheck, ...] = ()
    justification: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    source: str

    @field_validator("source")
    @classmethod
    def _source_ok(cls, v: str) -> str:
        if not SOURCE_PATTERN.fullmatch(v):
            raise ValueError("invalid source tag")
        return v

    @field_validator("timestamp")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return v.astimezone(UTC)

    @model_validator(mode="after")
    def _recommendations_need_alternatives(self) -> DecisionLogEntry:
        if (
            self.decision_type == DecisionType.RECOMMENDATION
            and not self.alternatives
            and not self.no_alternative_reason
        ):
            raise ValueError("recommendation needs alternatives or no_alternative_reason")
        return self


# ------------------------------------------------------------ intake result


class CategoryFloor(_Model):
    category: VendorCategory
    vendor_id: str
    vendor_name: str
    cost: int = Field(ge=0)


class RealityCheck(_Model):
    """Is the budget realistic? An estimate from database records, never a quote."""

    verdict: Literal["comfortable", "tight", "contingency_breach", "unrealistic", "unknown"]
    budget: int = Field(gt=0)
    floor: int | None = Field(default=None, ge=0)
    per_category: tuple[CategoryFloor, ...] = ()
    suggested_minimum_budget: int | None = Field(default=None, gt=0)  # the least total that covers the cheapest set and the contingency reserve, if money can move between services
    minimum_budget_default_split: int | None = Field(default=None, gt=0)  # the least total at which every service's own share (its cap) covers its cheapest eligible vendor
    message: str
    basis: str
    source: str = "tool:reality_check"


class IntakeResult(_Model):
    """Output of the first milestone. ``final_plan`` is always None here."""

    request: EventRequest
    task_graph: TaskGraph
    budget_allocation: BudgetAllocation
    vendor_shortlists: tuple[VendorShortlist, ...]
    budget_assessment: BudgetAssessment
    guests: GuestSummary | None = None
    reality: RealityCheck | None = None
    status: Literal["intake_complete", "needs_repair"]
    violations: tuple[ConstraintViolation, ...] = ()
    decision_entry_ids: tuple[str, ...] = ()
    final_plan: None = None




ShortlistMap = dict[VendorCategory, VendorShortlist]

# ------------------------------------------------------------------ guests


class RSVP(str, Enum):
    YES = "yes"
    NO = "no"
    PENDING = "pending"


class Guest(_Model):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    name: str = Field(min_length=1, max_length=120)
    rsvp: RSVP = RSVP.PENDING
    party_size: int = Field(default=1, ge=1, le=20)
    dietary: tuple[DietaryRequirement, ...] = ()


class GuestSummary(_Model):
    """Headcount facts. ``planning_headcount`` is always the user-stated count."""

    planning_headcount: int = Field(ge=1)
    confirmed: int = Field(ge=0)
    declined: int = Field(ge=0)
    pending: int = Field(ge=0)
    dietary_counts: dict[str, int] = {}
    required_diets: tuple[DietaryRequirement, ...] = ()
    checks: tuple[ConstraintCheck, ...] = ()
    guest_list_id: str | None = None
    source: str


# --------------------------------------------------------------- scheduling

TaskGroup = Literal["pre_event", "event_day"]


class ScheduleTask(_Model):
    """One interval to place. Times are whole minutes from the plan origin."""

    id: str = Field(pattern=r"^[a-z0-9_]{1,64}$")
    name: str = Field(min_length=1, max_length=120)
    duration: int = Field(ge=1, le=525_600)
    resource: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,40}$")
    group: TaskGroup = "pre_event"
    earliest_start: int = Field(default=0, ge=0)
    fixed_start: int | None = Field(default=None, ge=0)
    deadline_end: int | None = Field(default=None, ge=0)
    prefer: Literal["early", "late", "none"] = "none"
    weight: int = Field(default=1, ge=1, le=1000)


class ScheduleDependency(_Model):
    before: str
    after: str
    min_lag: int = Field(default=0, ge=0)


def _has_cycle(ids: list[str], edges: list[tuple[str, str]]) -> bool:
    indegree = dict.fromkeys(ids, 0)
    for _, b in edges:
        indegree[b] += 1
    queue = [i for i, n in indegree.items() if n == 0]
    seen = 0
    while queue:
        node = queue.pop()
        seen += 1
        for a, b in edges:
            if a == node:
                indegree[b] -= 1
                if indegree[b] == 0:
                    queue.append(b)
    return seen != len(ids)


class ScheduleSpec(_Model):
    tasks: tuple[ScheduleTask, ...]
    dependencies: tuple[ScheduleDependency, ...] = ()
    resources: dict[str, int] = {}
    horizon: int = Field(gt=0, le=5_256_000)
    anchor_task: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> ScheduleSpec:
        ids = [t.id for t in self.tasks]
        if not ids or len(set(ids)) != len(ids):
            raise ValueError("schedule needs unique, non-empty task ids")
        known = set(ids)
        for d in self.dependencies:
            if d.before not in known or d.after not in known or d.before == d.after:
                raise ValueError(f"bad dependency {d.before}->{d.after}")
        if self.anchor_task is not None and self.anchor_task not in known:
            raise ValueError("anchor_task is not a task")
        if self.anchor_task is None and any(t.prefer == "late" for t in self.tasks):
            raise ValueError("tasks preferring 'late' need an anchor_task")
        if _has_cycle(ids, [(d.before, d.after) for d in self.dependencies]):
            raise ValueError("dependencies contain a cycle")
        for t in self.tasks:
            if t.resource is not None and self.resources.get(t.resource, 0) < 1:
                raise ValueError(f"task {t.id} uses undeclared resource {t.resource}")
            if t.earliest_start + t.duration > self.horizon:
                raise ValueError(f"task {t.id} cannot fit inside the horizon")
        return self


class ScheduledEntry(_Model):
    task_id: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)


class ScheduleSolution(_Model):
    status: Literal["optimal", "feasible", "infeasible", "error"]
    entries: tuple[ScheduledEntry, ...] = ()
    objective: int | None = None
    infeasibility_core: tuple[str, ...] = ()
    detail: str = ""


class SymbolicResult(_Model):
    """PDDL feasibility check. ``unavailable`` means the solver binaries are absent."""

    status: Literal["valid", "invalid", "no_plan", "unavailable", "error"]
    plan_actions: tuple[str, ...] = ()
    detail: str = ""


# -------------------------------------------------------------------- plans


class VendorSelection(_Model):
    category: VendorCategory
    vendor_id: str
    name: str
    cost: int = Field(ge=0)
    booking_status: BookingStatus = BookingStatus.AVAILABLE
    score: float = 0.0
    justification: str
    alternatives: tuple[Alternative, ...] = ()
    no_alternative_reason: str | None = None
    source: str

    @model_validator(mode="after")
    def _has_alternative_or_reason(self) -> VendorSelection:
        if not self.alternatives and not self.no_alternative_reason:
            raise ValueError("every recommendation needs an alternative or a no_alternative_reason")
        return self


class PlanItem(_Model):
    """A scheduled task with wall-clock times (timezone-naive local event time)."""

    task_id: str
    name: str
    resource: str | None = None
    group: TaskGroup
    start: datetime
    end: datetime


class UnverifiedItem(_Model):
    """Something the validator could not decide from the data it has. Not a pass and not a failure: it never blocks a plan and is never shown as satisfied."""

    name: str = Field(min_length=1, max_length=80)
    detail: str = Field(min_length=1, max_length=400)
    repair: str = Field(min_length=1, max_length=400)
    label: str = Field(default="", max_length=120)  # short words for a badge or a sentence, e.g. "required accessibility unknown"
    blocks_verified: bool = True  # True: the plan may be shown as a provisional plan but never as fully verified until this is resolved


class ValidationReport(_Model):
    passed: bool
    checks: tuple[ConstraintCheck, ...]
    violations: tuple[ConstraintViolation, ...] = ()
    warnings: tuple[str, ...] = ()
    unverified: tuple[UnverifiedItem, ...] = ()

    @model_validator(mode="after")
    def _passed_matches_checks(self) -> ValidationReport:
        if self.passed != all(c.passed for c in self.checks):
            raise ValueError("passed must equal all(check.passed)")
        if self.passed and self.violations:
            raise ValueError("a passing report cannot list violations")
        return self

    def blocking_unverified(self) -> tuple[UnverifiedItem, ...]:
        return tuple(u for u in self.unverified if u.blocks_verified)

    @property
    def fully_verified(self) -> bool:
        """Every hard check passed AND nothing that matters is still undecided. ``passed`` alone only says no check failed."""
        return self.passed and not self.blocking_unverified()

    def verification_label(self) -> str:
        """"Verified", or "Needs verification: <what is unknown>". The one wording every surface uses."""
        if not self.passed:
            return "Failed"
        blocking = self.blocking_unverified()
        return "Verified" if not blocking else "Needs verification: " + "; ".join(u.label or u.name.replace("_", " ") for u in blocking)


class PlanVersion(_Model):
    """One immutable version of an event plan. Only validated plans can exist."""

    plan_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    version: int = Field(ge=1)
    parent_version: int | None = Field(default=None, ge=1)
    created_at: datetime
    origin: datetime
    change_reason: str = Field(min_length=1, max_length=500)
    request: EventRequest
    guests: GuestSummary
    allocation: BudgetAllocation
    selections: tuple[VendorSelection, ...]
    spec: ScheduleSpec
    solution: ScheduleSolution
    items: tuple[PlanItem, ...]
    total_cost: int = Field(ge=0)
    validation: ValidationReport
    symbolic: SymbolicResult
    decision_entry_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    excluded_vendor_ids: tuple[str, ...] = ()
    # vendors the user confirmed *in this version* (the provenance of CONFIRMED status)
    newly_confirmed_vendor_ids: tuple[str, ...] = ()
    # confirmed vendors this version let go of (vendor cancelled, or the user explicitly overrode)
    released_vendor_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _only_validated_plans_exist(self) -> PlanVersion:
        if not self.validation.passed:
            raise ValueError("a plan with failed hard constraints cannot be constructed")
        if self.parent_version is not None and self.parent_version >= self.version:
            raise ValueError("parent_version must be older than version")
        if self.total_cost != sum(s.cost for s in self.selections):
            raise ValueError("total_cost must equal the sum of selection costs")
        return self

    def selection_for(self, category: VendorCategory) -> VendorSelection | None:
        return next((s for s in self.selections if s.category == category), None)


DiffKind = Literal["request", "vendor", "task", "budget"]


class DiffItem(_Model):
    kind: DiffKind
    key: str
    before: str | None = None
    after: str | None = None
    note: str = ""


class PlanDiff(_Model):
    parent_version: int
    new_version: int
    added: tuple[DiffItem, ...] = ()
    removed: tuple[DiffItem, ...] = ()
    changed: tuple[DiffItem, ...] = ()
    preserved: tuple[DiffItem, ...] = ()


class DisruptionKind(str, Enum):
    VENDOR_CANCELLATION = "vendor_cancellation"
    BUDGET_CHANGE = "budget_change"
    GUEST_COUNT_CHANGE = "guest_count_change"
    DATE_CHANGE = "date_change"
    COMMUNITY_UPDATE = "community_update"
    QUOTE_UPDATE = "quote_update"  # no payload: a quote the user confirmed changed what a vendor costs, so price the plan again


class Disruption(_Model):
    """A user- or tool-reported change. Exactly one payload field matches ``kind``."""

    kind: DisruptionKind
    vendor_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    new_budget: int | None = Field(default=None, gt=0, le=MAX_BUDGET_INR)
    new_guest_count: int | None = Field(default=None, ge=1, le=MAX_GUESTS)
    new_date: date | None = None
    new_community: CommunityDetails | None = None
    # Confirmed bookings are kept unless their vendor id is listed here.
    override_confirmed: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _payload_matches_kind(self) -> Disruption:
        payload = {
            DisruptionKind.VENDOR_CANCELLATION: ("vendor_id", self.vendor_id),
            DisruptionKind.BUDGET_CHANGE: ("new_budget", self.new_budget),
            DisruptionKind.GUEST_COUNT_CHANGE: ("new_guest_count", self.new_guest_count),
            DisruptionKind.DATE_CHANGE: ("new_date", self.new_date),
            DisruptionKind.COMMUNITY_UPDATE: ("new_community", self.new_community),
        }
        for kind, (name, value) in payload.items():
            if kind == self.kind and value is None:
                raise ValueError(f"{self.kind.value} requires {name}")
            if kind != self.kind and value is not None:
                raise ValueError(f"{name} is not valid for {self.kind.value}")
        return self


class ReplanOutcome(_Model):
    status: Literal["replanned", "needs_decision", "failed"]
    plan: PlanVersion | None = None
    diff: PlanDiff | None = None
    violations: tuple[ConstraintViolation, ...] = ()
    decision_entry_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _shape(self) -> ReplanOutcome:
        if self.status == "replanned" and (self.plan is None or self.diff is None):
            raise ValueError("replanned outcome needs plan and diff")
        if self.status != "replanned" and (self.plan is not None or self.diff is not None):
            raise ValueError("only a replanned outcome carries a plan")
        if self.status != "replanned" and not self.violations:
            raise ValueError("a non-replanned outcome must say which constraint failed")
        return self


class PlanOutcome(_Model):
    status: Literal["planned", "needs_repair"]
    intake: IntakeResult
    task_graph: TaskGraph
    plan: PlanVersion | None = None
    violations: tuple[ConstraintViolation, ...] = ()
    decision_entry_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _shape(self) -> PlanOutcome:
        if (self.status == "planned") != (self.plan is not None):
            raise ValueError("plan present iff status is planned")
        if self.status == "needs_repair" and not self.violations:
            raise ValueError("needs_repair must list violations")
        return self


# ------------------------------------------------------------------- drafts


class PlanDraft(_Model):
    """A candidate plan. It becomes a ``PlanVersion`` only after the Validator passes it."""

    plan_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    origin: datetime
    request: EventRequest
    guests: GuestSummary
    allocation: BudgetAllocation
    selections: tuple[VendorSelection, ...]
    spec: ScheduleSpec
    solution: ScheduleSolution
    symbolic: SymbolicResult
    items: tuple[PlanItem, ...]
    already_done: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    excluded_vendor_ids: tuple[str, ...] = ()


class PlanningResult(_Model):
    draft: PlanDraft | None = None
    violations: tuple[ConstraintViolation, ...] = ()

    @model_validator(mode="after")
    def _shape(self) -> PlanningResult:
        if (self.draft is None) == (not self.violations):
            raise ValueError("exactly one of draft or violations")
        return self


# --------------------------------------------------------------- trade-offs


class TradeoffKind(str, Enum):
    REBALANCE_CAPS = "rebalance_caps"
    DROP_SERVICE = "drop_service"
    SHIFT_DATE = "shift_date"
    RAISE_BUDGET = "raise_budget"


class TradeoffOption(_Model):
    """A verified way out of a failed plan. Proposing it changes nothing; the user applies it."""

    option_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    kind: TradeoffKind
    title: str = Field(min_length=1, max_length=200)
    detail: str = Field(min_length=1, max_length=1000)
    request: EventRequest
    preference_harm: float = Field(ge=0.0, le=1.0)
    extra_spend: int = Field(ge=0)
    utility: float
    estimated_total: int = Field(ge=0)
    verified: Literal[True] = True  # a full plan was built and every check that could be decided passed (dry run, nothing stored)
    needs_verification: tuple[str, ...] = ()  # what that plan would still leave undecided; empty means it would be fully verified
    source: str = "tool:tradeoff"


class TradeoffReport(_Model):
    status: Literal["not_needed", "options", "no_option"]
    request: EventRequest
    options: tuple[TradeoffOption, ...] = ()
    violations: tuple[ConstraintViolation, ...] = ()
    decision_entry_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _shape(self) -> TradeoffReport:
        if (self.status == "options") != bool(self.options):
            raise ValueError("options present iff status is options")
        if self.status == "no_option" and not self.violations:
            raise ValueError("no_option must say which constraint blocks every repair")
        return self


IntakeResult.model_rebuild()  # resolves the forward reference to GuestSummary


# ----------------------------------------------------------------- budget ledger
# Money the user reports. to;doh-the event planner never moves money; every entry is `source=user_input`.


class LedgerKind(str, Enum):
    QUOTE = "quote"  # the vendor's current price; the latest quote per category wins
    PAYMENT = "payment"
    REFUND = "refund"


class LedgerEntry(_Model):
    entry_id: str
    timestamp: datetime
    plan_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    kind: LedgerKind
    category: VendorCategory
    amount: int = Field(gt=0, le=MAX_BUDGET_INR)
    note: str = Field(default="", max_length=200)
    source: Literal["user_input"] = "user_input"


class LedgerLine(_Model):
    category: VendorCategory
    vendor_name: str
    planned: int = Field(ge=0)
    quoted: int | None = Field(default=None, ge=0)
    committed: int = Field(ge=0)  # the quote if there is one, else the planned cost
    paid: int = Field(ge=0)
    pending: int = Field(ge=0)
    overpaid: int = Field(ge=0)
    variance: int  # committed - planned; positive = costs more than planned


class LedgerSummary(_Model):
    plan_id: str
    plan_version: int = Field(ge=1)
    budget_total: int = Field(gt=0)
    lines: tuple[LedgerLine, ...]
    planned_total: int = Field(ge=0)
    quoted_total: int = Field(ge=0)
    committed_total: int = Field(ge=0)
    paid_total: int = Field(ge=0)
    pending_total: int = Field(ge=0)
    variance_total: int
    headroom: int  # budget_total - committed_total; negative = over budget
    # net paid to categories that are no longer in the latest plan (money is never silently dropped)
    orphaned_paid: dict[str, int] = {}


# ------------------------------------------------------------ observation ledger
# Real vendor data with provenance. Text is data: control characters are refused.

MAX_OBSERVATION_ITEMS = 30
MAX_HIDDEN_COSTS = 20


def _clean_text(v: str) -> str:
    if any(unicodedata.category(ch) == "Cc" for ch in v):
        raise ValueError("control characters are not allowed")
    return v


class ObservationSource(str, Enum):
    USER_INPUT = "user_input"
    VENDOR_QUOTE = "vendor_quote"
    VENDOR_CONFIRMATION = "vendor_confirmation"
    PUBLIC_LISTING = "public_listing"


class HiddenCost(_Model):
    label: str = Field(min_length=1, max_length=80)
    amount_inr: int | None = Field(default=None, gt=0, le=MAX_BUDGET_INR)  # None = the vendor named it but not the price

    _label_ok = field_validator("label")(lambda cls, v: _clean_text(v))


class QuoteObservation(_Model):
    observation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    vendor_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    category: VendorCategory
    source_type: ObservationSource
    entered_by: str = Field(min_length=1, max_length=80)
    observed_at: datetime
    expires_at: date
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ref: str = Field(default="", max_length=200)
    amount_inr: int = Field(gt=0, le=MAX_BUDGET_INR)
    basis: Literal["total", "per_plate"]
    guest_count: int | None = Field(default=None, ge=1, le=MAX_GUESTS)
    inclusions: tuple[str, ...] = Field(default=(), max_length=MAX_OBSERVATION_ITEMS)
    exclusions: tuple[str, ...] = Field(default=(), max_length=MAX_OBSERVATION_ITEMS)
    hidden_costs: tuple[HiddenCost, ...] = Field(default=(), max_length=MAX_HIDDEN_COSTS)
    note: str = Field(default="", max_length=200)

    @field_validator("entered_by", "evidence_ref", "note")
    @classmethod
    def _text_ok(cls, v: str) -> str:
        return _clean_text(v)

    @field_validator("inclusions", "exclusions")
    @classmethod
    def _items_ok(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        for item in v:
            if not 1 <= len(item) <= 80:
                raise ValueError("each item must be 1-80 characters")
            _clean_text(item)
        return v

    @model_validator(mode="after")
    def _consistent(self) -> QuoteObservation:
        if self.source_type in (ObservationSource.VENDOR_QUOTE, ObservationSource.VENDOR_CONFIRMATION) and not self.evidence_ref.strip():
            raise ValueError("evidence_ref is required when the vendor is the source")
        if self.basis == "per_plate" and self.guest_count is None:
            raise ValueError("guest_count is required for a per_plate quote")
        if self.basis == "total" and self.guest_count is not None:
            raise ValueError("guest_count only applies to a per_plate quote")
        if self.expires_at < self.observed_at.date():
            raise ValueError("expires_at is before observed_at")
        return self


class QuoteReport(_Model):
    vendor_id: str
    category: VendorCategory
    observation_id: str
    source_type: ObservationSource
    confidence: float
    expires_at: date
    status: Literal["valid", "expiring", "expired"]
    quoted_total: int = Field(ge=0)
    known_hidden_total: int = Field(ge=0)
    effective_total: int = Field(ge=0)
    unquantified_hidden: tuple[str, ...] = ()
    missing_items: tuple[str, ...] = ()
    excluded_items: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()


class QuoteComparison(_Model):
    category: VendorCategory
    ranked: tuple[QuoteReport, ...] = ()
    expired: tuple[QuoteReport, ...] = ()


class ScoreComponent(_Model):
    name: Literal["price", "completeness", "evidence"]
    weight: float = Field(ge=0.0, le=1.0)
    score: int | None = Field(default=None, ge=0, le=100)  # None = not enough data; the component is left out, never guessed
    detail: str = Field(min_length=1, max_length=300)


class VendorScore(_Model):
    vendor_id: str
    category: VendorCategory
    observation_id: str
    status: Literal["valid", "expiring"]
    overall: int | None = Field(default=None, ge=0, le=100)
    coverage: float = Field(ge=0.0, le=1.0)  # share of the formula's weight that had data
    components: tuple[ScoreComponent, ...]
    not_scored: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


class UnscoredVendor(_Model):
    vendor_id: str
    reason: str


class VendorScoreboard(_Model):
    category: VendorCategory
    scores: tuple[VendorScore, ...] = ()
    unscored: tuple[UnscoredVendor, ...] = ()


class SelectionQuoteStatus(_Model):
    """Per selection in a plan: what the plan paid, what the vendor's price is now, and who confirmed the booking."""

    category: VendorCategory
    vendor_id: str
    vendor_name: str
    booking_status: BookingStatus
    plan_cost: int = Field(ge=0)
    current_price: int = Field(ge=0)
    price_stale: bool  # a later quote (or expiry) means the plan was priced differently; replan to re-price
    confirmed_by: Literal["you", "vendor"] | None = None
    report: QuoteReport | None = None
