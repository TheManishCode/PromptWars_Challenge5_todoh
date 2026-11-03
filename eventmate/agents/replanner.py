"""Replanner Agent: handles disruptions and writes a new plan version plus a diff.

Disruptions: vendor cancellation, budget change, guest-count change, date change.
Rules enforced here (and re-checked independently by the Validator):

- the previous version is never modified; a replan writes version N+1;
- user-confirmed bookings are preserved (pinned in CP-SAT, "already done" in
  PDDL) unless the user lists their vendor id in ``override_confirmed``, or the
  vendor itself cancelled;
- if the change makes a confirmed booking invalid (e.g. new date, too many
  guests) nothing is saved: the outcome is ``needs_decision`` naming the
  conflict and the exact override that would resolve it;
- every replan carries a diff: added / removed / changed / preserved.
"""

from __future__ import annotations

import contextlib
import re
from datetime import date

from pydantic import ValidationError

from eventmate.agents.budget import BudgetAgent
from eventmate.agents.guest import GuestAgent
from eventmate.agents.manager import MAX_EVENT_AHEAD_DAYS
from eventmate.agents.planner import BOOKING_TASK, PlannerAgent
from eventmate.agents.scheduler import DAY
from eventmate.agents.validator import ValidatorAgent
from eventmate.agents.vendor import VendorAgent, VendorRepository, evaluate
from eventmate.logs import RunLogs
from eventmate.plan_store import PlanStore, assemble_plan, draft_from_plan
from eventmate.schemas import (
    AgentName,
    Alternative,
    BookingStatus,
    ConstraintCheck,
    ConstraintViolation,
    ExtraTask,
    DecisionType,
    DiffItem,
    Disruption,
    DisruptionKind,
    EventRequest,
    EventType,
    PlanDiff,
    PlanVersion,
    ReplanOutcome,
    VendorCategory,
    VendorSelection,
)

_NO_CAP = 10**12  # confirmed bookings are held to the total budget, not a category cap


def diff_plans(old: PlanVersion, new: PlanVersion) -> PlanDiff:
    added: list[DiffItem] = []
    removed: list[DiffItem] = []
    changed: list[DiffItem] = []
    preserved: list[DiffItem] = []

    def req(o, n):
        return (
            ("guest_count", str(o.guest_count), str(n.guest_count)),
            ("event_date", o.event_date.isoformat(), n.event_date.isoformat()),
            ("budget_total", f"INR {o.budget_total:,}", f"INR {n.budget_total:,}"),
            ("city", o.city, n.city),
            ("dietary", ", ".join(d.value for d in o.dietary) or "none", ", ".join(d.value for d in n.dietary) or "none"),
            ("venue_setting", o.venue_setting.value, n.venue_setting.value),
        )

    for key, a, b in req(old.request, new.request):
        (preserved if a == b else changed).append(DiffItem(kind="request", key=key, before=a, after=b))
    if old.request.community is not None or new.request.community is not None:
        from eventmate.planning.community import summary

        a, b = summary(old.request.community), summary(new.request.community)
        (preserved if a == b else changed).append(DiffItem(kind="request", key="community_details", before=a, after=b))

    def label(s: VendorSelection) -> str:
        return f"{s.name} (INR {s.cost:,}, {s.booking_status.value})"

    for cat in VendorCategory:
        o, n = old.selection_for(cat), new.selection_for(cat)
        if o and not n:
            removed.append(DiffItem(kind="vendor", key=cat.value, before=label(o)))
        elif n and not o:
            added.append(DiffItem(kind="vendor", key=cat.value, after=label(n)))
        elif o and n:
            if o.vendor_id != n.vendor_id:
                removed.append(DiffItem(kind="vendor", key=cat.value, before=label(o)))
                added.append(DiffItem(kind="vendor", key=cat.value, after=label(n)))
            elif (o.cost, o.booking_status) == (n.cost, n.booking_status):
                note = "confirmed booking kept" if n.booking_status == BookingStatus.CONFIRMED else ""
                preserved.append(DiffItem(kind="vendor", key=cat.value, before=label(o), after=label(n), note=note))
            else:
                changed.append(DiffItem(kind="vendor", key=cat.value, before=label(o), after=label(n)))

    oi, ni = {i.task_id: i for i in old.items}, {i.task_id: i for i in new.items}
    span = lambda i: f"{i.start.strftime('%Y-%m-%d %H:%M')} to {i.end.strftime('%H:%M')}"  # noqa: E731
    for tid in sorted(set(oi) | set(ni)):
        if tid not in ni:
            removed.append(DiffItem(kind="task", key=tid, before=span(oi[tid])))
        elif tid not in oi:
            added.append(DiffItem(kind="task", key=tid, after=span(ni[tid])))
        elif (oi[tid].start, oi[tid].end) == (ni[tid].start, ni[tid].end):
            preserved.append(DiffItem(kind="task", key=tid, before=span(oi[tid]), after=span(ni[tid])))
        else:
            changed.append(DiffItem(kind="task", key=tid, before=span(oi[tid]), after=span(ni[tid])))

    for key, a, b in (
        ("vendor_total", old.total_cost, new.total_cost),
        ("contingency", old.allocation.contingency, new.allocation.contingency),
    ):
        item = DiffItem(kind="budget", key=key, before=f"INR {a:,}", after=f"INR {b:,}")
        (preserved if a == b else changed).append(item)

    return PlanDiff(
        parent_version=old.version,
        new_version=new.version,
        added=tuple(added),
        removed=tuple(removed),
        changed=tuple(changed),
        preserved=tuple(preserved),
    )


class _Rejected(Exception):
    def __init__(self, violation: ConstraintViolation):
        self.violation = violation


class ReplannerAgent:
    agent_name = AgentName.REPLANNER

    def __init__(
        self,
        logs: RunLogs,
        repo: VendorRepository,
        budget: BudgetAgent,
        vendor: VendorAgent,
        guest: GuestAgent,
        planner: PlannerAgent,
        validator: ValidatorAgent,
        store: PlanStore,
    ):
        self.logs, self.repo = logs, repo
        self.budget, self.vendor, self.guest = budget, vendor, guest
        self.planner, self.validator, self.store = planner, validator, store

    # ------------------------------------------------------------ confirm

    def confirm(self, plan_id: str, category: VendorCategory) -> ReplanOutcome:
        """Record that the *user* confirmed a booking (a user-provided fact) as a new version."""
        parent = self.store.latest(plan_id)
        start = len(self.logs.decisions.entry_ids)
        sel = parent.selection_for(category)
        if sel is None:
            raise ValueError(f"plan has no {category.value} to confirm")
        if sel.booking_status == BookingStatus.CONFIRMED:
            raise ValueError(f"{sel.name} is already confirmed")
        updated = sel.model_copy(update={"booking_status": BookingStatus.CONFIRMED, "justification": sel.justification + " Confirmed by the user."})
        selections = tuple(updated if s.category == category else s for s in parent.selections)
        draft = draft_from_plan(parent, selections=selections, already_done=tuple(sorted(BOOKING_TASK[s.category] for s in selections if s.booking_status == BookingStatus.CONFIRMED)))
        report = self.validator.validate(draft, parent, newly_confirmed_vendor_ids={sel.vendor_id})
        if not report.passed:
            return ReplanOutcome(status="failed", violations=report.violations)
        self.logs.decisions.log(
            request_id=parent.request.request_id,
            agent=self.agent_name,
            decision_type=DecisionType.ASSESSMENT,
            summary=f"Recorded user confirmation of {sel.name} ({category.value})",
            constraints_checked=(ConstraintCheck(name="confirmation_provenance", passed=True, detail="confirmation was supplied by the user, not inferred"),),
            justification="Booking status comes only from the user. Later replans will keep this booking unless explicitly overridden.",
            confidence=1.0,
            source="user_input",
        )
        new = assemble_plan(draft, report, parent.version + 1, parent.version, f"user confirmed booking: {sel.name}", tuple(self.logs.decisions.entry_ids[start:]), newly_confirmed_vendor_ids=(sel.vendor_id,))
        lost = self._save(new)
        if lost is not None:
            return lost
        return ReplanOutcome(status="replanned", plan=new, diff=diff_plans(parent, new), decision_entry_ids=new.decision_entry_ids)

    # ------------------------------------------------------------- replan

    def replan(self, plan_id: str, disruption: Disruption, today: date | None = None) -> ReplanOutcome:
        return self._replan(plan_id, [disruption], today, {})

    def repair(self, plan_id: str, delta, state, today: date | None = None) -> ReplanOutcome:
        """Apply a confirmed change as ONE new version, with finished and under-way work held where it really is (architecture L5).

        The same guards as ``replan`` apply (confirmed bookings, the validator, the hash chain): this is the same path with several changes folded together and
        with the execution state turned into fixed starts. A task that is done or under way is pinned at the start it had in the parent version, so what follows it
        waits for it and nothing is scheduled again that has already happened. A booking whose vendor is let go is void, so its pin is dropped. New tasks are
        refused for now rather than silently left out."""
        from eventmate.planning.delta import to_disruptions

        parent = self.store.latest(plan_id)
        rid = parent.request.request_id
        start = len(self.logs.decisions.entry_ids)
        try:
            extras = self._extra_tasks(parent, delta)
        except _Rejected as rej:
            self._fail(rid, "Change refused: new tasks", [rej.violation])
            return ReplanOutcome(status="failed", violations=(rej.violation,), decision_entry_ids=tuple(self.logs.decisions.entry_ids[start:]))
        disruptions = to_disruptions(delta)
        if not disruptions and not extras:
            v = ConstraintViolation(constraint="empty_change", detail="the change has nothing to apply", repair="say what should change")
            self._fail(rid, "Change refused: empty", [v])
            return ReplanOutcome(status="failed", violations=(v,), decision_entry_ids=tuple(self.logs.decisions.entry_ids[start:]))
        try:
            pins = self._pins(parent, state, disruptions)
        except _Rejected as rej:
            self._fail(rid, "Change refused: execution state", [rej.violation])
            return ReplanOutcome(status="failed", violations=(rej.violation,), decision_entry_ids=tuple(self.logs.decisions.entry_ids[start:]))
        return self._replan(plan_id, disruptions, today, pins, extras)

    def now_offset(self, parent: PlanVersion, today: date) -> int:
        """Minutes from the plan's origin to the start of the working day today: nothing that has not happened can be scheduled before it."""
        return max(0, (today - parent.origin.date()).days) * DAY + self.planner.scheduler.policy.work_start_hour * 60

    @staticmethod
    def _extra_tasks(parent: PlanVersion, delta) -> tuple[ExtraTask, ...]:
        """The delta's new tasks as the request's extra tasks. Every one needs a length the user said, and may follow only tasks that are already in the plan."""
        have = {t.id for t in parent.spec.tasks}
        taken = {x.task_id for x in parent.request.extra_tasks}
        out: list[ExtraTask] = []
        for t in delta.add_tasks:
            if t.duration_minutes is None:
                raise _Rejected(ConstraintViolation(constraint="new_task_needs_a_length", detail=f"how long '{t.name}' takes was not said", repair="say how long it takes, then apply the change"))
            unknown = [a for a in t.after if a not in have]
            if unknown:
                raise _Rejected(ConstraintViolation(constraint="unknown_task", detail=f"'{t.name}' comes after {', '.join(unknown)}, which is not in this plan", repair="name tasks from the current plan"))
            slug = re.sub(r"[^a-z0-9]+", "_", t.name.casefold()).strip("_")[:30] or "task"
            tid, n = f"extra_{slug}", 1
            while tid in taken:
                n += 1
                tid = f"extra_{slug}_{n}"
            taken.add(tid)
            out.append(ExtraTask(task_id=tid, name=t.name, duration_minutes=t.duration_minutes, after=t.after, days_before_event=t.days_before_event))
        return tuple(out)

    def _pins(self, parent: PlanVersion, state, disruptions: list[Disruption]) -> dict[str, int]:
        have = {i.task_id: i for i in parent.items}
        unknown = sorted((set(state.done) | set(state.in_flight)) - set(have))
        if unknown:
            raise _Rejected(ConstraintViolation(constraint="unknown_task", detail=f"not tasks of this plan: {', '.join(unknown)}", repair="use task ids from the current plan"))
        moving = any(d.kind == DisruptionKind.DATE_CHANGE for d in disruptions)
        released = {d.vendor_id for d in disruptions if d.kind == DisruptionKind.VENDOR_CANCELLATION}
        void = {BOOKING_TASK[s.category] for s in parent.selections if s.vendor_id in released and s.category in BOOKING_TASK}  # a cancelled vendor's booking is no longer done
        pins: dict[str, int] = {}
        for tid in sorted((set(state.done) | set(state.in_flight)) - void):
            if moving and have[tid].group == "event_day":
                raise _Rejected(ConstraintViolation(constraint="happened_cannot_move", detail=f"{tid} has already started or finished, so the event date cannot move", repair="keep the date, or say that the event-day work has not happened"))
            pins[tid] = self._parent_start(parent, tid)
        return pins

    def _replan(self, plan_id: str, disruptions: list[Disruption], today: date | None, pins: dict[str, int], extras: tuple[ExtraTask, ...] = ()) -> ReplanOutcome:
        today = today or date.today()
        parent = self.store.latest(plan_id)
        start = len(self.logs.decisions.entry_ids)
        rid = parent.request.request_id
        try:
            try:
                request, excluded, release, reason = parent.request, tuple(parent.excluded_vendor_ids), set(), []
                for d in disruptions:  # each change builds on the one before; the reasons are joined
                    request, excluded, release, why = self._apply(parent, d, today, request, excluded, release)
                    reason.append(why)
                if extras:
                    request = request.with_changes(extra_tasks=tuple(request.extra_tasks) + extras)
                    reason.append("added: " + ", ".join(f"{x.name} ({x.duration_minutes} min" + (f", {x.days_before_event} day(s) before)" if x.days_before_event else ", event day)") for x in extras))
                reason = "; ".join(reason)
            except ValidationError as exc:  # e.g. a guest count outside a community event's attendance range
                raise _Rejected(ConstraintViolation(
                    constraint="request_invalid", detail=str(exc.errors()[0]["msg"])[:300],
                    repair="change the other related details together (for a community event: the attendance range) so the request is consistent",
                )) from None
        except _Rejected as rej:
            self._fail(rid, "Disruption rejected", [rej.violation])
            return ReplanOutcome(status="failed", violations=(rej.violation,), decision_entry_ids=tuple(self.logs.decisions.entry_ids[start:]))

        if request.event_date < today:
            v = ConstraintViolation(
                constraint="event_in_past",
                detail=f"the event date {request.event_date.isoformat()} is before today ({today.isoformat()})",
                repair="report a date change to a future date; a finished event cannot be replanned",
            )
            self._fail(rid, "Event date has already passed", [v])
            return ReplanOutcome(status="failed", violations=(v,), decision_entry_ids=tuple(self.logs.decisions.entry_ids[start:]))

        guests = self.guest.summarize(request, parent.guests.guest_list_id)
        diets = tuple(dict.fromkeys(list(request.dietary) + list(guests.required_diets)))
        request = request.with_changes(dietary=diets)
        bad_guest = [ConstraintViolation(constraint=c.name, detail=c.detail, repair="raise the guest count or prune the guest list") for c in guests.checks if not c.passed]
        if bad_guest:
            self._fail(rid, "Guest list conflicts with the new headcount", bad_guest)
            return ReplanOutcome(status="failed", violations=tuple(bad_guest), decision_entry_ids=tuple(self.logs.decisions.entry_ids[start:]))

        forced, conflicts = self._preserve(parent, request, release)
        if conflicts:
            self._log_decision(rid, DecisionType.FAILURE, f"Confirmed booking conflicts with: {reason}", conflicts, "needs a user decision before anything changes")
            return ReplanOutcome(status="needs_decision", violations=tuple(conflicts), decision_entry_ids=tuple(self.logs.decisions.entry_ids[start:]))

        allocation = self.budget.allocate(request)
        shortlists = tuple(
            self.vendor.shortlist_category(request, c, allocation.cap_for(c), exclude_ids=excluded)
            for c in request.services
            if c not in forced
        )
        origin = parent.origin
        now_offset = self.now_offset(parent, today)
        fixed = {BOOKING_TASK[c]: self._parent_start(parent, BOOKING_TASK[c]) for c in forced}
        fixed.update(pins)  # work that has happened or is under way stays where it was
        planned = self.planner.plan(request, guests, allocation, shortlists, origin, plan_id, forced=forced, fixed_starts=fixed, now_offset=now_offset, exclude_ids=excluded)
        if planned.draft is None:
            return self._unplannable(parent, forced, list(planned.violations), start, reason)

        report = self.validator.validate(planned.draft, parent, released_vendor_ids=release)
        if not report.passed:
            return self._unplannable(parent, forced, list(report.violations), start, reason)

        kept = [s.name for s in planned.draft.selections if s.booking_status == BookingStatus.CONFIRMED]
        self._log_decision(
            rid,
            DecisionType.RECOMMENDATION,
            f"Replanned after {reason}: version {parent.version + 1}",
            [],
            f"Confirmed bookings kept: {', '.join(kept) or 'none'}. Released: {', '.join(sorted(release)) or 'none'}.",
            alternatives=(
                Alternative(option="keep the previous plan unchanged", outcome=f"version {parent.version} stays on file", reason_not_chosen=f"it no longer satisfies the request after: {reason}"),
            ),
        )
        new = assemble_plan(planned.draft, report, parent.version + 1, parent.version, reason, tuple(self.logs.decisions.entry_ids[start:]), released_vendor_ids=tuple(release))
        lost = self._save(new)
        if lost is not None:
            return lost
        return ReplanOutcome(status="replanned", plan=new, diff=diff_plans(parent, new), decision_entry_ids=new.decision_entry_ids)

    # ------------------------------------------------------------ helpers

    def _save(self, plan: PlanVersion) -> ReplanOutcome | None:
        """Write the new version; if another writer got there first, say so instead of crashing."""
        try:
            self.store.save(plan)
        except FileExistsError:
            v = ConstraintViolation(
                constraint="concurrent_modification",
                detail=f"version {plan.version} of plan {plan.plan_id} was written by someone else while this change was being planned",
                repair="reload the plan and retry the change on its newest version",
            )
            self._fail(plan.request.request_id, "Plan changed while replanning", [v])
            return ReplanOutcome(status="failed", violations=(v,))
        except OSError:
            # the decision log already says "version N chosen" (the plan embeds those entry ids, so it must come first): say what happened to it
            with contextlib.suppress(Exception):
                self._log_decision(plan.request.request_id, DecisionType.FAILURE, f"Version {plan.version} of plan {plan.plan_id} could not be written",
                                   [], f"the storage failed; version {plan.version - 1} stays current and nothing was changed")
            raise
        return None

    def _apply(self, parent: PlanVersion, d: Disruption, today: date, req: EventRequest | None = None, excluded: tuple[str, ...] | None = None,
               release: set[str] | None = None) -> tuple[EventRequest, tuple[str, ...], set[str], str]:
        confirmed = {s.vendor_id for s in parent.selections if s.booking_status == BookingStatus.CONFIRMED}
        bogus = sorted(set(d.override_confirmed) - confirmed)
        if bogus:
            raise _Rejected(ConstraintViolation(constraint="invalid_override", detail=f"not confirmed bookings of this plan: {bogus}", repair="list only vendor ids that are currently confirmed"))
        excluded = tuple(parent.excluded_vendor_ids) if excluded is None else excluded
        release = set(release or ()) | set(d.override_confirmed)
        req = parent.request if req is None else req
        if d.kind == DisruptionKind.VENDOR_CANCELLATION:
            vendor_id = d.vendor_id
            if vendor_id is None:
                raise _Rejected(ConstraintViolation(constraint="missing_vendor", detail="a vendor cancellation names no vendor", repair="give the id of the vendor that cancelled"))
            known = any(self.repo.get(c, vendor_id) for c in VendorCategory)
            if not known:
                raise _Rejected(ConstraintViolation(constraint="unknown_vendor", detail=f"{d.vendor_id} is not in the database", repair="use a vendor id from the plan"))
            excluded = tuple(sorted(set(excluded) | {vendor_id}))
            release.add(vendor_id)  # the vendor cancelled: that booking is void, no override needed
            name = next((s.name for s in parent.selections if s.vendor_id == d.vendor_id), d.vendor_id)
            return req, excluded, release, f"vendor cancellation: {name}"
        if d.kind == DisruptionKind.BUDGET_CHANGE:
            return req.with_changes(budget_total=d.new_budget), excluded, release, f"budget change: INR {req.budget_total:,} to INR {d.new_budget:,}"
        if d.kind == DisruptionKind.COMMUNITY_UPDATE:
            if req.event_type != EventType.COMMUNITY:
                raise _Rejected(ConstraintViolation(constraint="not_a_community_event", detail=f"this plan is a {req.event_type.value} event", repair="community details only apply to a community event"))
            return req.with_changes(community=d.new_community), excluded, release, "community details updated"
        if d.kind == DisruptionKind.QUOTE_UPDATE:
            return req, excluded, release, "re-priced from the quotes you confirmed"
        if d.kind == DisruptionKind.GUEST_COUNT_CHANGE:
            return req.with_changes(guest_count=d.new_guest_count), excluded, release, f"guest count change: {req.guest_count} to {d.new_guest_count}"
        new_date = d.new_date
        if new_date is None:  # the disruption's own validation requires it; this keeps a bad caller honest
            raise _Rejected(ConstraintViolation(constraint="missing_date", detail="no new date was given", repair="give the new event date"))
        if (new_date - today).days > MAX_EVENT_AHEAD_DAYS:
            raise _Rejected(ConstraintViolation(constraint="event_date_too_far", detail=f"{new_date.isoformat()} is more than 3 years away", repair="choose a date within three years"))
        if new_date < today:
            raise _Rejected(ConstraintViolation(constraint="event_date_in_past", detail=f"{new_date.isoformat()} is before today ({today.isoformat()})", repair="choose a date today or later"))
        return req.with_changes(event_date=new_date), excluded, release, f"date change: {req.event_date.isoformat()} to {new_date.isoformat()}"

    def _preserve(self, parent: PlanVersion, request: EventRequest, release: set[str]):
        forced: dict[VendorCategory, VendorSelection] = {}
        conflicts: list[ConstraintViolation] = []
        for s in parent.selections:
            if s.booking_status != BookingStatus.CONFIRMED or s.vendor_id in release:
                continue
            rec = self.repo.get(s.category, s.vendor_id)
            failed = [c for c in (evaluate(rec, request, s.category, _NO_CAP, price=self.repo.price(rec, request.guest_count), quote_expired=self.repo.quote_expired(rec)) if rec else []) if not c.passed]
            if rec is None or failed:
                why = "; ".join(f"{c.name}: {c.detail}" for c in failed) or "vendor no longer in the database"
                conflicts.append(
                    ConstraintViolation(
                        constraint="confirmed_booking_conflict",
                        detail=f"confirmed {s.category.value} {s.name} no longer fits: {why}",
                        repair=f"override the confirmed booking (override_confirmed=['{s.vendor_id}']) to let the planner replace {s.name}, or undo the change",
                    )
                )
                continue
            forced[s.category] = s.model_copy(update={"cost": self.repo.price(rec, request.guest_count)})
        return forced, conflicts

    @staticmethod
    def _parent_start(parent: PlanVersion, task_id: str) -> int:
        item = next(i for i in parent.items if i.task_id == task_id)
        return int((item.start - parent.origin).total_seconds() // 60)

    def _unplannable(self, parent, forced, violations, start, reason) -> ReplanOutcome:
        rid = parent.request.request_id
        if forced:
            ids = ", ".join(f"'{s.vendor_id}'" for s in forced.values())
            violations.append(
                ConstraintViolation(
                    constraint="confirmed_booking_conflict",
                    detail="the preserved confirmed bookings limit what the planner can do",
                    repair=f"if you accept losing them, retry with override_confirmed=[{ids}]",
                )
            )
        self._log_decision(rid, DecisionType.FAILURE, f"No valid plan after {reason}", violations, "the previous version stays valid; nothing was saved")
        return ReplanOutcome(
            status="needs_decision" if forced else "failed",
            violations=tuple(violations),
            decision_entry_ids=tuple(self.logs.decisions.entry_ids[start:]),
        )

    def _fail(self, rid: str, summary: str, violations: list[ConstraintViolation]) -> None:
        self._log_decision(rid, DecisionType.FAILURE, summary, violations, "nothing was changed")

    def _log_decision(self, rid, kind, summary, violations, note, alternatives=()) -> None:
        self.logs.decisions.log(
            request_id=rid,
            agent=self.agent_name,
            decision_type=kind,
            summary=summary[:500],
            alternatives=alternatives,
            no_alternative_reason=None if alternatives or kind != DecisionType.RECOMMENDATION else "single option",
            constraints_checked=tuple(ConstraintCheck(name=v.constraint, passed=False, detail=v.detail[:900]) for v in violations[:10]),
            justification=(note + ". " + "; ".join(f"{v.constraint}: {v.detail}. Repair: {v.repair}" for v in violations[:6])).strip(),
            confidence=1.0,
            source="rule:replanner",
        )
