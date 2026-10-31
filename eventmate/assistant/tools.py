"""Typed tools the model may ask for.

Every call passes through ``execute``: the tool must exist, the *asker* must hold its permission, the arguments must pass
a strict schema, and the plan in scope is fixed by the session (the model cannot name another plan). Read tools return
facts. Mutating tools never act: they file a proposal for a human to approve.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Type

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from eventmate.assistant.approvals import ApprovalError, ApprovalQueue, Proposal
from eventmate.assistant.identity import Perm, Principal
from eventmate.assistant.knowledge import KnowledgeBase
from eventmate.pricing.reality import reality_check
from eventmate.schemas import Disruption, LedgerKind, VendorCategory
from eventmate.service import EventMate
from eventmate.state import build_state


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NoArgs(_Args):
    pass


class SearchArgs(_Args):
    query: str = Field(min_length=1, max_length=200)


class CategoryArgs(_Args):
    category: VendorCategory


class LedgerArgs(_Args):
    kind: LedgerKind
    category: VendorCategory
    amount: int = Field(gt=0, le=10_000_000_000)
    note: str = Field(default="", max_length=200)

    @field_validator("note")
    @classmethod
    def _plain(cls, v: str) -> str:
        if "<" in v or ">" in v or "\n" in v:
            raise ValueError("note must be plain text")
        return v


@dataclass
class ToolContext:
    em: EventMate
    principal: Principal
    plan_id: str  # fixed by the session; never taken from the model
    kb: KnowledgeBase
    queue: Optional[ApprovalQueue] = None


@dataclass
class Tool:
    name: str
    description: str
    perm: Perm
    args: Type[BaseModel]
    run: Callable[[ToolContext, Any], Dict[str, Any]]
    mutating: bool = False
    apply: Optional[Callable[[ToolContext, Dict[str, Any]], str]] = None  # runs only after a human approves
    summarize: Optional[Callable[[Any], str]] = None


@dataclass
class ToolResult:
    ok: bool
    data: Dict[str, Any]
    error: str = ""
    proposal: Optional[Proposal] = None

    def text(self) -> str:
        return json.dumps(self.data if self.ok else {"error": self.error}, sort_keys=True, default=str)


# ------------------------------------------------------------------ read tools


def _search(ctx: ToolContext, a: SearchArgs):
    hits = ctx.kb.search(a.query, ctx.principal)
    return {"passages": [{"id": h.chunk.id, "title": h.chunk.title, "text": h.chunk.text, "source": h.chunk.source} for h in hits]}


def _public(ctx: ToolContext, _):
    p = ctx.em.store.latest(ctx.plan_id)
    venue = next((s for s in p.selections if s.category == VendorCategory.VENUE), None)
    return {"event_type": p.request.event_type.value, "city": p.request.city, "event_date": p.request.event_date.isoformat(), "venue": venue.name if venue else None}


def _summary(ctx: ToolContext, _):
    p = ctx.em.store.latest(ctx.plan_id)
    r = p.request
    return {
        "plan_version": p.version,
        "event_type": r.event_type.value,
        "city": r.city,
        "event_date": r.event_date.isoformat(),
        "guests": r.guest_count,
        "dietary": [d.value for d in r.dietary],
        "vendors": [{"category": s.category.value, "name": s.name, "booking_status": s.booking_status.value} for s in p.selections],
        "warnings": list(p.warnings),
        "verification": ctx.em.verification(ctx.plan_id),  # the assistant must not call a plan fully verified when this says it needs verification
    }


def _event_state(ctx: ToolContext, _):
    """A bounded slice of EventState: counts, vendor names with statuses, dates. No guest names, contact details, prices or message text."""
    s = build_state(ctx.em, ctx.plan_id)
    kinds: Dict[str, int] = {}
    for i in s.open_items:
        kinds[i.kind] = kinds.get(i.kind, 0) + 1
    oldest = max((i.age_hours or 0 for i in s.open_items), default=0)
    return {
        "plan_version": s.plan_version,
        "as_of": s.as_of.isoformat(timespec="minutes"),
        "event_date": next(f.value for f in s.facts if f.key == "event_date"),
        "guests_yes": next(f.value for f in s.facts if f.key == "rsvp_yes"),
        "guests_no": next(f.value for f in s.facts if f.key == "rsvp_no"),
        "guests_pending": next(f.value for f in s.facts if f.key == "rsvp_pending"),
        "vendors": [{"category": v.category.value, "name": v.name, "booking_status": v.booking_status, "conversation": v.conversation,
                     "quote": None if v.quote is None else v.quote.status} for v in s.vendors],
        "open_items": {"total": len(s.open_items), "by_kind": kinds, "oldest_age_hours": oldest},
        "changes_in_last_50": len(s.changes),
        "note": "A vendor price with status quoted_unverified is only what the vendor wrote; it is not a recorded quote.",
    }


def _guest_counts(ctx: ToolContext, _):
    """Counts and diet totals from the imported guest list, beside the stated headcount. Never a name, a contact detail or a raw cell."""
    plan = ctx.em.store.latest(ctx.plan_id)
    saved = ctx.em.guest_summary(ctx.plan_id)
    if saved is None:
        return {"guest_list": False, "planning_headcount": plan.request.guest_count, "note": "No guest list has been imported for this plan."}
    t, c = saved["totals"], saved["check"]
    return {
        "guest_list": True, "planning_headcount": c["planning_headcount"], "people_on_list": t["headcount"], "yes": t["yes"], "no": t["no"], "pending": t["pending"],
        "dietary_counts": t["dietary_counts"], "list_vs_planned": c["flag"] or "the list and the planned headcount agree",
        "note": "Diet counts are people on the list who have not declined. The plan itself still uses the stated headcount.",
    }


def _alternatives(ctx: ToolContext, a: CategoryArgs):
    p = ctx.em.store.latest(ctx.plan_id)
    s = p.selection_for(a.category)
    if s is None:
        return {"category": a.category.value, "chosen": None, "alternatives": []}
    return {"category": a.category.value, "chosen": s.name,
            "alternatives": [{"option": x.option, "outcome": x.outcome, "reason_not_chosen": x.reason_not_chosen} for x in s.alternatives],
            "no_alternative_reason": s.no_alternative_reason}


def _budget(ctx: ToolContext, _):
    p = ctx.em.store.latest(ctx.plan_id)
    s = ctx.em.ledger_summary(ctx.plan_id)
    pct = round(100 * s.planned_total / s.budget_total) if s.budget_total else 0
    remaining = s.budget_total - s.planned_total
    return {"budget_total": s.budget_total, "planned_total": s.planned_total, "planned_percent_of_budget": pct, "remaining_after_planned": remaining,
            "derivation": f"planned {s.planned_total} / budget {s.budget_total} = {pct} percent; budget {s.budget_total} - planned {s.planned_total} = {remaining} (computed by to;doh-the event planner, not by the model)",
            "quoted_total": s.quoted_total, "committed_total": s.committed_total,
            "paid_total": s.paid_total, "pending_total": s.pending_total, "headroom": s.headroom, "plan_version": p.version,
            "vendors": [{"category": x.category.value, "name": x.name, "cost": x.cost} for x in p.selections]}


def _reality(ctx: ToolContext, _):
    p = ctx.em.store.latest(ctx.plan_id)
    rc = reality_check(p.request, ctx.em.repo, ctx.em.manager.budget.policy)
    return {"verdict": rc.verdict, "message": rc.message, "basis": rc.basis, "floor": rc.floor, "suggested_minimum_budget": rc.suggested_minimum_budget, "minimum_budget_default_split": rc.minimum_budget_default_split}


# ------------------------------------------------------------ mutating tools


def _propose(tool: "Tool"):
    def run(ctx: ToolContext, a: Any) -> Dict[str, Any]:
        if ctx.queue is None:
            raise ApprovalError("proposals are not available here")
        plan = ctx.em.store.latest(ctx.plan_id)
        summary = tool.summarize(a) if tool.summarize else tool.name
        p = ctx.queue.propose(plan_id=ctx.plan_id, plan_version=plan.version, proposer=ctx.principal.user_id, tool=tool.name, args=a.model_dump(mode="json", exclude_none=True), summary=summary)
        return {"proposal_id": p.id, "status": "waiting for the organiser to approve", "summary": p.summary, "note": "You cannot apply this yourself."}

    return run


def _apply_ledger(ctx: ToolContext, args: Dict[str, Any]) -> str:
    a = LedgerArgs.model_validate(args)
    e = ctx.em.ledger_add(ctx.plan_id, a.kind.value, a.category, a.amount, a.note)
    return f"recorded {a.kind.value} of {a.amount} for {a.category.value} (entry {e.entry_id[:8]})"


def _apply_confirm(ctx: ToolContext, args: Dict[str, Any]) -> str:
    out = ctx.em.confirm(ctx.plan_id, CategoryArgs.model_validate(args).category)
    if out.status != "replanned":
        raise ValueError("; ".join(v.detail for v in out.violations)[:300])
    return f"booking confirmed by you; plan is now version {out.plan.version}"


def _apply_replan(ctx: ToolContext, args: Dict[str, Any]) -> str:
    out = ctx.em.replan(ctx.plan_id, Disruption.model_validate(args))
    if out.status != "replanned":
        raise ValueError(f"{out.status}: " + "; ".join(v.detail for v in out.violations)[:300])
    return f"replanned; plan is now version {out.plan.version}"


def build_tools() -> Dict[str, Tool]:
    t: List[Tool] = [
        Tool("search_knowledge", "Search to;doh-the event planner's own policy and help documents. Use for how to;doh-the event planner works, budget rules and dietary checklists.", Perm.KB_READ, SearchArgs, _search),
        Tool("get_public_event_info", "Event type, city, date and venue name. Safe to share with guests.", Perm.PLAN_PUBLIC, NoArgs, _public),
        Tool("get_plan_summary", "The current plan without money: guests, dietary needs, chosen vendors and their booking status.", Perm.PLAN_READ, NoArgs, _summary),
        Tool("get_event_state", "Where the plan stands right now: RSVP counts, each vendor's booking status and conversation status, and how many things are still open and for how long. No names of guests, no contact details, no prices.", Perm.PLAN_READ, NoArgs, _event_state),
        Tool("get_guest_counts", "How many guests said yes, no or have not answered, and how many need each diet (vegetarian, vegan, jain, halal and so on), from the imported guest list, beside the planned headcount. Counts only: no names.", Perm.PLAN_READ, NoArgs, _guest_counts),
        Tool("list_alternatives", "Alternatives considered for one vendor category and why they were not chosen.", Perm.PLAN_READ, CategoryArgs, _alternatives),
        Tool("get_budget_status", "Budget, planned cost, quotes, payments the organiser recorded and headroom. Money: organiser only.", Perm.LEDGER_READ, NoArgs, _budget),
        Tool("check_budget_reality", "Whether the budget is realistic, an estimate from vendor records. Organiser only.", Perm.LEDGER_READ, NoArgs, _reality),
    ]
    m = [
        Tool("propose_ledger_entry", "Propose recording a quote, payment or refund the organiser reports. Nothing is paid. Needs organiser approval.", Perm.LEDGER_WRITE, LedgerArgs, None, True, _apply_ledger,
             lambda a: f"Record a {a.kind.value} of INR {a.amount:,} for {a.category.value}" + (f" ({a.note})" if a.note else "")),
        Tool("propose_confirm_booking", "Propose marking a vendor booking as confirmed by the organiser. Needs organiser approval.", Perm.BOOKING_CONFIRM, CategoryArgs, None, True, _apply_confirm,
             lambda a: f"Mark the {a.category.value} booking as confirmed by you"),
        Tool("propose_replan", "Propose a replan after a change (vendor cancelled, budget, guest count or date). Needs organiser approval.", Perm.PLAN_REPLAN, Disruption, None, True, _apply_replan,
             lambda a: f"Replan for: {a.kind.value.replace('_', ' ')}"),
    ]
    for tool in m:
        tool.run = _propose(tool)
    return {x.name: x for x in t + m}


def visible_tools(tools: Dict[str, Tool], principal: Principal) -> List[Tool]:
    """Tools the asker may use; the model is never even told about the others."""
    return [x for x in tools.values() if principal.can(x.perm)]


def execute(ctx: ToolContext, tools: Dict[str, Tool], name: Any, raw_args: Any) -> ToolResult:
    tool = tools.get(name) if isinstance(name, str) else None
    if tool is None or not ctx.principal.can(tool.perm):
        return ToolResult(False, {}, "that tool is not available")  # same answer for unknown and forbidden: no probing
    try:
        args = tool.args.model_validate(raw_args if raw_args is not None else {})
    except ValidationError as exc:
        return ToolResult(False, {}, "invalid arguments: " + "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())[:300])
    try:
        data = tool.run(ctx, args)
    except ApprovalError as exc:
        return ToolResult(False, {}, str(exc))
    except Exception as exc:  # a failing tool must not take the conversation down
        return ToolResult(False, {}, f"{type(exc).__name__}: {exc}"[:300])
    prop = ctx.queue.get(data["proposal_id"]) if tool.mutating and ctx.queue else None
    return ToolResult(True, data, proposal=prop)
