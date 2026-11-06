"""A staged brief standing where a plan stands, for the steps that only need the event's facts.

Finding vendors, reading their sites for contacts, drafting quote requests and reading the replies are keyed by an id and read a handful of facts (what, where, when, how many, which
diets). None of it needs a finished plan. In a city with no vendor records a plan cannot be built until quotes are in, so these steps run against the brief: the id is ``brief_<staged id>``
and the same stores, routes and approvals are used. A request says only what the brief states, and the budget is never in it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from eventmate.planning.staging import StagedIntake
from eventmate.schemas import DietaryRequirement, EventType

BRIEF_PREFIX = "brief_"


def is_brief(plan_id: str) -> bool:
    return isinstance(plan_id, str) and plan_id.startswith(BRIEF_PREFIX)


def staged_id_of(plan_id: str) -> str:
    return plan_id[len(BRIEF_PREFIX):]


@dataclass(frozen=True)
class BriefRequest:
    event_type: EventType
    city: str
    event_date: date
    guest_count: int
    dietary: tuple[DietaryRequirement, ...]


@dataclass(frozen=True)
class BriefScope:
    plan_id: str  # brief_<staged id>: the key every sourcing store files this brief under
    version: int
    request: BriefRequest
    selections: tuple = ()  # a brief has chosen nothing
    is_brief: bool = True

    def selection_for(self, category):
        return None


def scope_of(row: StagedIntake) -> BriefScope:
    d = row.draft
    if not (d.city and d.event_date and d.guest_count):
        raise ValueError("a vendor can be asked only once the brief has a city, a date and a guest count")
    return BriefScope(plan_id=BRIEF_PREFIX + row.staged_id, version=row.version, request=BriefRequest(d.event_type or EventType.OTHER, d.city, d.event_date, d.guest_count, d.dietary))
