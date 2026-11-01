"""EventState: one read-only view of where a plan stands, built on demand from the existing stores.

It reads the plan version, ledger, vendor messages, jobs and guest counts and calls nothing else: no model, no network, no new database. Every fact
says where it came from (``source``), when (``as_of``) and how far it can be trusted (``status``). A price a vendor wrote is ``quoted_unverified``
and never reprices the plan. The view holds no guest names and no contact details, and it authorises nothing: seeing that something is
waiting does not approve, send or spend anything.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, UTC
from typing import Literal, overload

from eventmate.guestlist import check_against, totals_of
from eventmate.schemas import LedgerKind, VendorCategory, _Model

FactSource = Literal["user", "vendor_reply", "tool", "model_idea", "seed_data"]
FactStatus = Literal["user_stated", "quoted_unverified", "confirmed", "pending"]
Conversation = Literal["none", "awaiting_approval", "asked", "replied", "unavailable", "stopped", "problem"]
OpenKind = Literal["pending_rsvps", "approval_waiting", "unanswered_vendor", "unconfirmed_quote", "follow_up_due", "job_item"]
ChangeKind = Literal["plan_version", "ledger", "message", "reply", "job"]

MAX_CHANGES = 50
# What closes each kind of open item; shown to the user so "nothing left hanging" is checkable.
CLOSES_WHEN: dict[str, str] = {
    "pending_rsvps": "the guest list is updated and nobody is left pending",
    "approval_waiting": "you approve or deny the message, or it expires",
    "unanswered_vendor": "the vendor replies",
    "unconfirmed_quote": "you record the price in the ledger, or mark the reply read",
    "follow_up_due": "a follow-up is sent or approved, or the vendor replies",
    "job_item": "you resolve the item in its quote job",
}
JOB_ITEM_LABELS = {
    "confirm_claim": "A quoted price needs your confirmation", "vendor_question": "A vendor asked you a question", "approve_requests": "Quote requests wait for your approval",
    "approve_nudge": "A follow-up waits for your approval", "check_unknown_send": "A send has an unknown outcome: check the vendor chat", "unclear_price": "A reply needs reading",
    "vendor_unavailable": "A vendor says it is not available", "different_sender": "A reply came from an unexpected address", "do_not_contact": "A request was blocked: do not contact",
    "no_quotes_possible": "Too few vendors can still give a quote", "request_stale": "A request is out of date because the plan changed: draft it again",
}


class Fact(_Model):
    key: str
    label: str
    value: str | int | bool | None
    source: FactSource
    as_of: datetime
    status: FactStatus


class VendorState(_Model):
    category: VendorCategory
    vendor_id: str
    name: str
    booking_status: str
    plan_cost: int
    price_stale: bool = False  # a later quote or an expiry means the plan was priced differently; replan to re-price
    conversation: Conversation = "none"
    last_activity_at: datetime | None = None
    quote: Fact | None = None


class OpenItem(_Model):
    kind: OpenKind
    ref: str
    label: str
    since: datetime | None = None
    age_hours: int | None = None
    closes_when: str


class Change(_Model):
    at: datetime
    kind: ChangeKind
    summary: str


class EventState(_Model):
    plan_id: str
    plan_version: int
    as_of: datetime
    facts: tuple[Fact, ...]
    vendors: tuple[VendorState, ...]
    open_items: tuple[OpenItem, ...]
    changes: tuple[Change, ...]
    notice: str = "A read-only view assembled from this plan's records. It approves, sends and spends nothing, and a vendor's quote stays unverified until you record it."


@overload
def _aware(t: datetime) -> datetime: ...
@overload
def _aware(t: None) -> None: ...
def _aware(t: datetime | None) -> datetime | None:
    return t if t is None or t.tzinfo else t.replace(tzinfo=UTC)


def _age_hours(since: datetime | None, now: datetime) -> int | None:
    return None if since is None else max(0, int((now - since).total_seconds() // 3600))


def _item(kind: str, ref: str, label: str, since: datetime | None, now: datetime) -> OpenItem:
    since = _aware(since)
    return OpenItem(kind=kind, ref=ref, label=label, since=since, age_hours=_age_hours(since, now), closes_when=CLOSES_WHEN[kind])


def _conversation(drafts: list, inbound: list) -> tuple[Conversation, datetime | None]:
    """Where the latest request to one vendor stands, from the vendor's own side of the thread."""
    requests = [d for d in drafts if d.kind == "request"]
    if not requests:
        return "none", None
    latest = max(requests, key=lambda d: _aware(d.created_at))
    replies = [m for m in inbound if m.draft_id == latest.draft_id]
    last_at = max([_aware(latest.sent_at or latest.created_at)] + [_aware(m.received_at) for m in replies])
    if any(m.analysis.asks_to_stop for m in replies):
        return "stopped", last_at
    if replies:
        return ("unavailable" if any(m.analysis.says_unavailable for m in replies) else "replied"), last_at
    if latest.status in ("sent", "marked_sent"):
        return "asked", last_at
    if latest.status == "prepared":
        return "awaiting_approval", last_at
    return "problem", last_at  # approved or sending is in flight; failed, denied, expired, blocked or unknown outcome need a look


def _quote_fact(vendor_id: str, category: VendorCategory, inbound: list, ledger: list, confirmed_claims: set, now: datetime) -> Fact | None:
    recorded = [e for e in ledger if e.kind == LedgerKind.QUOTE and e.category == category]
    if recorded:  # the user recorded a price: that is the one that counts
        e = max(recorded, key=lambda x: x.timestamp)
        return Fact(key=f"quote:{vendor_id}", label=f"{category.value} price you recorded (INR)", value=e.amount, source="user", as_of=_aware(e.timestamp), status="user_stated")
    priced = [m for m in inbound if m.vendor_id == vendor_id and m.analysis.amounts and not m.sender_differs]
    if not priced:
        return None
    m = max(priced, key=lambda x: x.received_at)
    a = m.analysis.amounts[0]
    status: FactStatus = "confirmed" if m.message_id in confirmed_claims else "quoted_unverified"
    return Fact(key=f"quote:{vendor_id}", label=f"{category.value} price the vendor wrote (INR, {a.unit}, as they wrote it)", value=a.low_inr, source="vendor_reply", as_of=_aware(m.received_at), status=status)


def _facts(plan, as_of: datetime, listed=None) -> list[Fact]:
    """``listed`` is the totals of an imported guest list, if the user saved one: then the RSVP counts are the list's (people, by party size)."""
    r, g = plan.request, plan.guests
    at = _aware(plan.created_at)
    guest_source: FactSource = "seed_data" if "seed" in (g.source or "") else "user"
    if listed is not None:
        guest_source = "user"
        g = g.model_copy(update={"confirmed": listed.yes, "declined": listed.no, "pending": listed.pending})
    facts = [
        Fact(key="event_type", label="Event type", value=r.event_type.value, source="user", as_of=at, status="user_stated"),
        Fact(key="city", label="City", value=r.city, source="user", as_of=at, status="user_stated"),
        Fact(key="event_date", label="Event date", value=r.event_date.isoformat(), source="user", as_of=at, status="user_stated"),
        Fact(key="budget_total_inr", label="Budget (INR)", value=r.budget_total, source="user", as_of=at, status="user_stated"),
        Fact(key="guest_count", label="Guests you planned for", value=r.guest_count, source="user", as_of=at, status="user_stated"),
        Fact(key="rsvp_yes", label="Guests who said yes", value=g.confirmed, source=guest_source, as_of=at, status="user_stated"),
        Fact(key="rsvp_no", label="Guests who declined", value=g.declined, source=guest_source, as_of=at, status="user_stated"),
        Fact(key="rsvp_pending", label="Guests who have not answered", value=g.pending, source=guest_source, as_of=at, status="pending"),
        Fact(key="plan_cost_inr", label="Planned vendor cost (INR)", value=plan.total_cost, source="tool", as_of=at, status="pending" if any(s.booking_status.value != "confirmed" for s in plan.selections) else "confirmed"),
    ]
    if listed is not None:
        flag = check_against(listed, r.guest_count).flag
        if flag:
            facts.append(Fact(key="guest_list_check", label="Guest list against your planned headcount", value=flag, source="tool", as_of=as_of, status="pending"))
    return facts


def _vendor_states(plan, drafts: list, inbound: list, ledger: list, confirmed_claims: set, stale: dict[str, bool], now: datetime) -> list[VendorState]:
    """One row per selected vendor: booking status, conversation, and the latest quote with its provenance."""
    vendors: list[VendorState] = []
    for s in plan.selections:
        mine_d = [d for d in drafts if d.vendor_id == s.vendor_id]
        mine_i = [m for m in inbound if m.vendor_id == s.vendor_id]
        conv, last = _conversation(mine_d, mine_i)
        vendors.append(VendorState(
            category=s.category, vendor_id=s.vendor_id, name=s.name, booking_status=s.booking_status.value, plan_cost=s.cost, price_stale=stale.get(s.vendor_id, False),
            conversation=conv, last_activity_at=last, quote=_quote_fact(s.vendor_id, s.category, inbound, ledger, confirmed_claims, now),
        ))
    return vendors


def _rsvp_items(em, plan, plan_id: str, listed, now: datetime) -> list[OpenItem]:
    pending = plan.guests.pending if listed is None else listed.pending
    if pending <= 0:
        return []
    first = em.store.load(plan_id, 1).created_at if plan.version > 1 else plan.created_at
    if listed is not None:
        label = f"{pending} guest(s) on your list have not answered"
    else:
        no_list = "" if plan.guests.guest_list_id is not None else " (no guest list is loaded, so replies are not being tracked)"
        label = f"{pending} guest(s) have not answered{no_list}"
    return [_item("pending_rsvps", "guests", label, first, now)]


def _message_items(drafts: list, inbound: list, confirmed_claims: set, now: datetime) -> list[OpenItem]:
    items: list[OpenItem] = []
    replied_drafts = {m.draft_id for m in inbound}
    for d in drafts:
        if d.status == "prepared" and (d.expires_at is None or _aware(d.expires_at) > now):
            items.append(_item("approval_waiting", d.draft_id, f"A message to {d.vendor_name} waits for your approval", d.created_at, now))
        elif d.kind == "request" and d.status in ("sent", "marked_sent") and d.draft_id not in replied_drafts:
            items.append(_item("unanswered_vendor", d.draft_id, f"{d.vendor_name} has not replied", d.sent_at or d.marked_at or d.created_at, now))
    for m in inbound:
        if m.analysis.amounts and not m.read and m.message_id not in confirmed_claims:
            items.append(_item("unconfirmed_quote", m.message_id, f"{m.vendor_name} mentioned a price that is not recorded", m.received_at, now))
    return items


def _job_items(jobs: list, now: datetime) -> list[OpenItem]:
    items: list[OpenItem] = []
    for j in jobs:
        if j.status in ("done", "expired", "cancelled"):
            continue
        window = timedelta(hours=j.reply_window_hours)
        for vid, p in j.vendors.items():
            if p.state == "asked" and p.asked_at is not None and now >= _aware(p.asked_at) + window and p.nudges < j.max_nudges_per_vendor and not p.pending_nudge_draft_id:
                items.append(_item("follow_up_due", f"{j.job_id}:{vid}", f"A follow-up to {vid} is due in the quote job", _aware(p.asked_at) + window, now))
        for it in j.items:
            if not it.resolved:
                items.append(_item("job_item", f"{j.job_id}:{it.item_id}", JOB_ITEM_LABELS.get(it.kind, "A quote job item needs you"), j.created_at, now))
    return items


def build_state(em, plan_id: str, now: datetime | None = None, changes_since: datetime | None = None, today=None) -> EventState:
    """Assemble the state of one plan. Raises FileNotFoundError for an unknown plan. Reads only; never calls a model or the network."""
    now = _aware(now) or datetime.now(UTC)
    plan = em.store.latest(plan_id)
    drafts, inbound, ledger = em.outreach(plan_id), em.outreach_store.inbound(plan_id), em.ledger_entries(plan_id)
    jobs = em._jobs_of(plan_id)
    saved = em.guest_lists.load(plan_id)
    listed = None if saved is None else totals_of(saved)
    confirmed_claims = {c.message_id for j in jobs for c in j.claims if c.status == "confirmed"}
    stale: dict[str, bool] = {}
    with contextlib.suppress(Exception):  # a price check that cannot run must not hide the rest of the state
        stale = {q.vendor_id: q.price_stale for q in em.quote_status(plan_id, today)}

    vendors = _vendor_states(plan, drafts, inbound, ledger, confirmed_claims, stale, now)
    items = _rsvp_items(em, plan, plan_id, listed, now) + _message_items(drafts, inbound, confirmed_claims, now) + _job_items(jobs, now)
    items.sort(key=lambda i: (i.since or now, i.kind, i.ref))

    return EventState(
        plan_id=plan_id, plan_version=plan.version, as_of=now, facts=tuple(_facts(plan, now, listed)), vendors=tuple(vendors), open_items=tuple(items),
        changes=tuple(_changes(em, plan, plan_id, drafts, inbound, ledger, jobs, changes_since)),
    )


def _changes(em, plan, plan_id: str, drafts: list, inbound: list, ledger: list, jobs: list, since: datetime | None) -> list[Change]:
    """What happened, newest first, from the stores' own timestamps (the decision logs are per run, not per plan, so they are not the source)."""
    out: list[Change] = []
    for v in range(1, plan.version + 1):
        pv = plan if v == plan.version else em.store.load(plan_id, v)
        out.append(Change(at=_aware(pv.created_at), kind="plan_version", summary=f"Plan version {pv.version} saved: {pv.change_reason[:160]}"))
    for e in ledger:
        out.append(Change(at=_aware(e.timestamp), kind="ledger", summary=f"You recorded a {e.kind.value} of INR {e.amount:,} for the {e.category.value}"))
    for d in drafts:
        out.append(Change(at=_aware(d.created_at), kind="message", summary=f"A {d.kind} to {d.vendor_name} was drafted"))
        if d.sent_at:
            out.append(Change(at=_aware(d.sent_at), kind="message", summary=f"A {d.kind} to {d.vendor_name} was sent"))
        elif d.status in ("denied", "expired") and d.decided_at:
            out.append(Change(at=_aware(d.decided_at), kind="message", summary=f"A {d.kind} to {d.vendor_name} was {d.status}"))
    for m in inbound:
        note = " and mentioned a price (not recorded)" if m.analysis.amounts else ""
        out.append(Change(at=_aware(m.received_at), kind="reply", summary=f"{m.vendor_name} replied{note}"))
    for j in jobs:
        for ts, actor, event, _ in em.jobs.store.history(j.job_id):
            with contextlib.suppress(ValueError):
                out.append(Change(at=_aware(datetime.fromisoformat(ts)), kind="job", summary=f"Quote job: {event.replace('_', ' ')} ({actor})"))
    if since is not None:
        since = _aware(since)
        out = [c for c in out if c.at > since]
    out.sort(key=lambda c: (c.at, c.summary), reverse=True)
    return out[:MAX_CHANGES]
