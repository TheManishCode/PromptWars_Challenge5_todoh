"""An independent checker for a schedule: precedence with lag, resource capacity, earliest start, fixed start and deadline.

It shares no code with ``cpsat.py`` or ``ValidatorAgent``, so a bug in the solver model cannot hide in the check. The idea follows ``check_plan_validity`` in
jiayizx/async_planning (MIT); this one is written for the typed ``ScheduleSpec``.
"""

from __future__ import annotations


from eventmate.schemas import ScheduleSolution, ScheduleSpec


def violations(spec: ScheduleSpec, sol: ScheduleSolution) -> list[str]:
    out: list[str] = []
    at = {e.task_id: (e.start, e.end) for e in sol.entries}
    for t in spec.tasks:
        if t.id not in at:
            out.append(f"{t.id}: not placed")
            continue
        s, e = at[t.id]
        if e - s != t.duration:
            out.append(f"{t.id}: length {e - s} is not its duration {t.duration}")
        if s < t.earliest_start:
            out.append(f"{t.id}: starts {s}, before its earliest {t.earliest_start}")
        if t.fixed_start is not None and s != t.fixed_start:
            out.append(f"{t.id}: starts {s}, not at its fixed {t.fixed_start}")
        if t.deadline_end is not None and e > t.deadline_end:
            out.append(f"{t.id}: ends {e}, after its deadline {t.deadline_end}")
        if e > spec.horizon:
            out.append(f"{t.id}: ends past the horizon")
    for d in spec.dependencies:
        if d.before in at and d.after in at and at[d.after][0] < at[d.before][1] + d.min_lag:
            out.append(f"{d.before}->{d.after}: starts {at[d.after][0]}, before {at[d.before][1]} + lag {d.min_lag}")
    for resource, cap in spec.resources.items():
        users = [(at[t.id][0], at[t.id][1]) for t in spec.tasks if t.resource == resource and t.id in at]
        for point in {s for s, _ in users}:
            busy = sum(1 for s, e in users if s <= point < e)
            if busy > cap:
                out.append(f"resource {resource}: {busy} at minute {point}, capacity {cap}")
    return out
