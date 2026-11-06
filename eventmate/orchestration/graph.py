"""The mission graph (ADR-059): parse, discover, enrich, review contacts, draft, approve, send, track.

Every node calls the existing ``EventMate`` methods, so each rule they enforce still holds (confirmed addresses, hash-bound approval, do-not-contact).
The two ``interrupt`` points are the only places a person decides; a node's side effects run after its interrupt, never before it,
because a resumed node restarts from its first line. State holds plain JSON only, so any checkpointer can store it.
"""

from __future__ import annotations

import operator
import time
from datetime import date
from typing import Annotated, Any, TypedDict
from collections.abc import Callable

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from eventmate.agents.manager import RequestParseError
from eventmate.orchestration.models import ApprovalReview, ContactReview
from eventmate.schemas import VendorCategory

LIVE_DRAFT = ("prepared", "approved", "sending", "sent")
NOTE_CHARS = 240


class MissionState(TypedDict, total=False):
    brief: str
    today: str
    sender_name: str
    categories: list[str]
    source: str
    limit: int
    plan_id: str
    stage: str
    failure: str
    draft_ids: list[str]
    approvals: list[dict[str, Any]]
    trail: Annotated[list[str], operator.add]


def _note(text: str) -> str:
    return " ".join(str(text).split())[:NOTE_CHARS]


def build_graph(em, checkpointer=None):
    """Compile the mission graph over ``em`` (an ``EventMate`` or anything with the same methods)."""

    def audited(name: str, fn: Callable[[MissionState], dict]) -> Callable[[MissionState, RunnableConfig], dict]:
        def run(state: MissionState, config: RunnableConfig) -> dict:
            started, request_id = time.perf_counter(), str(config.get("configurable", {}).get("thread_id", "mission"))
            ok, result = True, ""
            try:
                out = fn(state)
                result = str(out.get("stage", ""))
                return out
            except BaseException as exc:  # a pause for the user is control flow, not a failure
                ok = type(exc).__name__ == "GraphInterrupt"
                result = type(exc).__name__ + ": " + _note(exc)
                raise
            finally:
                em.logs.tool_calls.log(request_id=request_id, tool="mission." + name, args={"plan_id": state.get("plan_id") or ""}, ok=ok, result=result[:2000],
                                       duration_ms=round((time.perf_counter() - started) * 1000, 2))
        return run

    def items(state: MissionState):
        return [i for i in em.shortlist_view(state["plan_id"]) if i.category.value in state["categories"]]

    def parse_requirements(state: MissionState) -> dict:
        try:
            out = em.plan(state["brief"], today=date.fromisoformat(state["today"]))
        except (RequestParseError, ValueError) as exc:
            return {"stage": "failed", "failure": "The brief could not be planned: " + _note(exc)}
        if out.plan is None:
            why = "; ".join(v.message if hasattr(v, "message") else str(v) for v in out.violations)
            return {"stage": "failed", "failure": "No plan: hard constraints cannot be met. " + _note(why), "trail": ["planning stopped at the validator"]}
        return {"plan_id": out.plan.plan_id, "stage": "planned", "trail": [f"plan {out.plan.plan_id} version {out.plan.version} passed validation"]}

    def discover_vendors(state: MissionState) -> dict:
        notes = []
        for cat in state["categories"]:
            try:
                res = em.search_vendors(state["plan_id"], VendorCategory(cat), state.get("source"), state.get("limit", 10))
                notes.extend([f"{cat}: {res.added} new of {res.total} from {res.source}"] + [f"{cat}: {_note(w)}" for w in res.warnings])
            except ValueError as exc:  # a source that does not cover a category says so; the others carry on
                notes.append(f"{cat}: {_note(exc)}")
        found = items(state)
        return {"stage": "discovered" if found else "no_vendors", "trail": notes + [f"{len(found)} shortlisted businesses (discovered, not vendor records)"]}

    def enrich_vendor(state: MissionState) -> dict:
        notes = []
        for it in items(state):
            biz = it.business
            if it.contact is not None or it.candidates or biz.is_test or not (biz.website or biz.source == "gplaces"):  # a Google listing's website is fetched when its contacts are searched
                continue
            try:
                res = em.find_business_contacts(biz.vendor_id)
                notes.append(f"{_note(biz.name)}: {len(res.added)} public address(es) found on its own site")
            except (ValueError, OSError) as exc:
                notes.append(f"{_note(biz.name)}: {_note(exc)}")
        return {"stage": "enriched", "trail": notes}

    def pending(state: MissionState):
        return [i for i in items(state) if i.contact is None and any(c.status == "found" for c in i.candidates)]

    def review_contacts(state: MissionState) -> dict:
        asked = [{
            "vendor_id": i.business.vendor_id, "name": i.business.name, "website": i.business.website, "place_id": i.business.external_id.split(":", 1)[1] if i.business.source == "gplaces" else None,
            "name_confirmed": i.business.name_source != "placeholder",  # a Google listing's name is not stored: the page shows it from its place id, and the user confirms the name they want kept
            "candidates": [{"candidate_id": c.candidate_id, "value": c.value, "source_url": c.source_url, "source_kind": c.source_kind, "flags": list(c.flags)}
                           for c in i.candidates if c.status == "found"],
        } for i in pending(state)]
        answer = interrupt({"kind": "contact_review", "plan_id": state["plan_id"], "businesses": asked,
                            "note": "Addresses were found on public pages. Confirm the ones that really belong to the business (you may correct one), or reject them."})
        review, notes = ContactReview.model_validate(answer), []
        for d in review.decisions:
            try:
                if d.action == "confirm":
                    em.confirm_contact(d.vendor_id, d.candidate_id, d.by, d.edited_value, business_name=d.business_name)
                else:
                    em.reject_contact(d.vendor_id, d.candidate_id)
                notes.append(f"{d.vendor_id}: address {d.action}ed by you" if d.action == "reject" else f"{d.vendor_id}: address confirmed by {_note(d.by)}")
            except ValueError as exc:
                notes.append(f"{d.vendor_id}: {_note(exc)}")
        return {"stage": "contacts_reviewed", "trail": notes}

    def draft_outreach(state: MissionState) -> dict:
        plan_id, notes, ids = state["plan_id"], [], []
        mine = {d.vendor_id: d for d in em.outreach(plan_id) if d.kind == "request" and d.status in LIVE_DRAFT}
        for it in items(state):
            c = it.contact
            if c is None or not c.email:
                continue
            vid = it.business.vendor_id
            if vid in mine:  # a request to this business already exists in this plan: never write a second one
                if mine[vid].status == "prepared":
                    ids.append(mine[vid].draft_id)
                continue
            try:
                ids.append(em.draft_outreach(plan_id, it.category, "email", c.email, state["sender_name"], vendor_id=vid).draft_id)
            except ValueError as exc:
                notes.append(f"{_note(it.business.name)}: {_note(exc)}")
        return {"draft_ids": ids, "stage": "drafted" if ids else "nothing_to_send", "trail": notes + [f"{len(ids)} draft(s) wait for your approval; nothing has been sent"]}

    def request_user_approval(state: MissionState) -> dict:
        drafts = [d for d in (em.outreach_store.get(state["plan_id"], i) for i in state["draft_ids"]) if d is not None and d.status == "prepared"]
        answer = interrupt({"kind": "draft_approval", "plan_id": state["plan_id"], "drafts": [
            {"draft_id": d.draft_id, "vendor_name": d.vendor_name, "channel": d.channel, "recipient": d.recipient, "subject": d.subject, "body": d.body,
             "content_hash": d.content_hash} for d in drafts],
            "note": "Approving sends exactly this text. Approve with the content hash shown, or deny."})
        return {"approvals": [d.model_dump() for d in ApprovalReview.model_validate(answer).decisions], "stage": "approval_recorded"}

    def send_inquiries(state: MissionState) -> dict:
        plan_id, mine, notes, sent = state["plan_id"], set(state["draft_ids"]), [], 0
        for d in state.get("approvals", []):
            if d["draft_id"] not in mine:
                notes.append(f"{d['draft_id']}: not a draft of this mission, ignored")
                continue
            try:
                if d["action"] == "approve":
                    em.approve_outreach(plan_id, d["draft_id"], d["content_hash"])
                    sent += 1
                else:
                    em.deny_outreach(plan_id, d["draft_id"])
            except ValueError as exc:
                notes.append(f"{d['draft_id']}: {_note(exc)}")
        return {"stage": "approved", "approvals": [], "trail": notes + [f"{sent} draft(s) approved by you"]}

    def track_responses(state: MissionState) -> dict:
        res, box = em.poll_conversations(), em.inbox(state["plan_id"])
        waiting = "; ".join(_note(w) for w in res.waiting)
        notes = [f"{res.sent} sent, {res.new_messages} new repl{'y' if res.new_messages == 1 else 'ies'}, {box.unread_total} unread"
                 + ("; held: " + waiting if waiting else "") + ". Replies are the vendor's words; nothing becomes a quote until you confirm it."] + [_note(e) for e in res.errors]
        return {"stage": "awaiting_replies", "trail": notes}

    def after_parse(state: MissionState) -> str:
        return END if state.get("failure") else "discover_vendors"

    def after_discover(state: MissionState) -> str:
        return "enrich_vendor" if state["stage"] == "discovered" else END

    def after_enrich(state: MissionState) -> str:
        if pending(state):
            return "review_contacts"
        return "draft_outreach" if any(i.contact is not None for i in items(state)) else END

    def after_draft(state: MissionState) -> str:
        return "request_user_approval" if state["stage"] == "drafted" else END

    g = StateGraph(MissionState)
    for name, fn in [("parse_requirements", parse_requirements), ("discover_vendors", discover_vendors), ("enrich_vendor", enrich_vendor), ("review_contacts", review_contacts),
                     ("draft_outreach", draft_outreach), ("request_user_approval", request_user_approval), ("send_inquiries", send_inquiries), ("track_responses", track_responses)]:
        g.add_node(name, audited(name, fn))
    g.add_edge(START, "parse_requirements")
    g.add_conditional_edges("parse_requirements", after_parse, ["discover_vendors", END])
    g.add_conditional_edges("discover_vendors", after_discover, ["enrich_vendor", END])
    g.add_conditional_edges("enrich_vendor", after_enrich, ["review_contacts", "draft_outreach", END])
    g.add_edge("review_contacts", "draft_outreach")
    g.add_conditional_edges("draft_outreach", after_draft, ["request_user_approval", END])
    g.add_edge("request_user_approval", "send_inquiries")
    g.add_edge("send_inquiries", "track_responses")
    g.add_edge("track_responses", END)
    return g.compile(checkpointer=checkpointer)
