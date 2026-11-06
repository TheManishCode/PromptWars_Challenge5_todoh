"""A plan as a graph: nodes (the event, its tasks, the vendors chosen, the ones passed over, the hard checks) and the edges between them.

Everything here is read from the stored plan; nothing is computed again or guessed. The page can draw it, and each edge says why it is there ("not chosen: ..." carries the planner's own reason).
"""

from __future__ import annotations

from eventmate.schemas import PlanVersion

# FUTURE(graph-ui): the page does not draw this graph yet. A "why this plan" view: event in the middle, vendors chosen and passed over around it, a failed or undecided check in red.
# FUTURE(graph-candidates): only the alternatives the planner kept (``Alternative``) are nodes. Every vendor it considered, with the constraint (city, capacity, diet, price, date) that removed it as a labelled edge, needs the vendor agent's per-vendor ``ConstraintCheck`` results carried on the plan.
# FUTURE(graph-books): ``BOOKS`` repeats the page's BOOK table (static/app.js). Carry the task-to-category link on the ScheduleTask so it has one source.
BOOKS = {  # the task that books or sets up each vendor category
    "book_venue": "venue", "venue_setup": "venue", "book_caterer": "caterer", "catering_setup": "caterer", "book_photographer": "photographer", "photography_prep": "photographer",
}


def build(plan: PlanVersion) -> dict:
    """``{"nodes": [...], "edges": [...]}``. A node has ``id``, ``kind`` and ``label`` plus facts; an edge has ``source``, ``target``, ``kind`` and ``label``."""
    nodes: list[dict] = []
    edges: list[dict] = []
    r = plan.request
    nodes.append({"id": "event", "kind": "event", "label": f"{r.event_type.value} for {r.guest_count} guests", "city": r.city, "date": r.event_date.isoformat(), "budget": r.budget_total, "total_cost": plan.total_cost})
    ids = {i.task_id for i in plan.items}
    for item in plan.items:
        nodes.append({"id": f"task:{item.task_id}", "kind": "task", "label": item.name, "group": item.group, "start": item.start.isoformat(), "end": item.end.isoformat(), "resource": item.resource})
        edges.append({"source": "event", "target": f"task:{item.task_id}", "kind": "includes", "label": item.group.replace("_", " ")})
    for d in plan.spec.dependencies:
        if d.before in ids and d.after in ids:
            edges.append({"source": f"task:{d.before}", "target": f"task:{d.after}", "kind": "precedes", "label": f"at least {d.min_lag} min before" if d.min_lag else "before"})
    chosen: dict[str, str] = {}
    for s in plan.selections:
        vid = f"vendor:{s.vendor_id}"
        chosen[s.category.value] = vid
        nodes.append({"id": vid, "kind": "vendor", "label": s.name, "category": s.category.value, "cost": s.cost, "booking_status": s.booking_status.value, "source": s.source, "why": s.justification})
        edges.append({"source": vid, "target": "event", "kind": "serves", "label": f"{s.category.value}, {s.cost}"})
        for n, a in enumerate(s.alternatives):
            aid = f"alt:{s.category.value}:{n}"
            nodes.append({"id": aid, "kind": "alternative", "label": a.option, "category": s.category.value, "outcome": a.outcome})
            edges.append({"source": aid, "target": vid, "kind": "not_chosen_over", "label": a.reason_not_chosen})
    for task_id, category in BOOKS.items():
        if task_id in ids and category in chosen:
            edges.append({"source": chosen[category], "target": f"task:{task_id}", "kind": "books", "label": task_id.replace("_", " ")})
    for n, c in enumerate(plan.validation.checks):
        cid = f"check:{n}"
        nodes.append({"id": cid, "kind": "check", "label": c.name, "passed": c.passed, "detail": c.detail})
        edges.append({"source": cid, "target": "event", "kind": "verifies", "label": "passed" if c.passed else "failed"})
    for u in plan.validation.unverified:
        uid = f"unverified:{u.name}"
        nodes.append({"id": uid, "kind": "unverified", "label": u.label or u.name, "detail": u.detail, "repair": u.repair, "blocks_verified": u.blocks_verified})
        edges.append({"source": uid, "target": "event", "kind": "undecided", "label": "not yet verified"})
    return {"nodes": nodes, "edges": edges}
