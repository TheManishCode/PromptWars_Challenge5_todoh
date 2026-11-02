"""Execution state and the repair spec: what has happened, what is under way, and the schedule that is left to solve.

State-aware repair follows jiayizx/async_planning (MIT, ``_augment_with_ongoing_constraints``, ``_remaining_seconds``, ``_repair_state_dependencies``): work that is
finished is removed from what is solved, work that is under way keeps its place for the time it has left so everything after it waits, and a dependency on finished
work still honours the lag it had. Differences: times are absolute minutes from the plan origin (no re-based clock), an in-flight task stays in the spec with a fixed
start instead of a second hidden copy (so its dependencies and resource use need no rewiring), and a finished task's lag becomes an earliest start on its successors.

Nothing is guessed here: every id must exist in the spec, and a task cannot be both done and in flight. A task nobody has started that was fixed in the past is left
fixed, so the solver reports it as a named conflict instead of this module moving it.
"""

from __future__ import annotations


from pydantic import Field, model_validator

from eventmate.schemas import ScheduleSpec, _Model


class ExecState(_Model):
    now: int = Field(ge=0)  # minutes from the plan origin
    done: dict[str, int] = {}  # task id -> the minute it finished (a confirmed booking that has happened, a completed task)
    in_flight: dict[str, int] = {}  # task id -> minutes still to run (a quote request awaiting a reply, a held date)

    @model_validator(mode="after")
    def _consistent(self) -> ExecState:
        if set(self.done) & set(self.in_flight):
            raise ValueError("a task cannot be both done and in flight")
        if any(end > self.now for end in self.done.values()):
            raise ValueError("a done task cannot finish after now")
        if any(left < 1 for left in self.in_flight.values()):
            raise ValueError("an in-flight task has at least one minute left; a task with none is done")
        return self


class RepairSpec(_Model):
    spec: ScheduleSpec
    dropped: tuple[str, ...] = ()  # finished tasks, removed from what is solved
    pinned: tuple[str, ...] = ()  # in-flight tasks, held where they are for the time they have left
    notes: tuple[str, ...] = ()  # what changed, in plain words


def to_repair_spec(spec: ScheduleSpec, state: ExecState) -> RepairSpec:
    known = {t.id for t in spec.tasks}
    unknown = (set(state.done) | set(state.in_flight)) - known
    if unknown:
        raise ValueError(f"unknown task(s) in the execution state: {', '.join(sorted(unknown))}")
    if state.now > spec.horizon:
        raise ValueError("now is past the end of the schedule horizon")

    for d in spec.dependencies:
        if d.after in state.in_flight and d.before not in state.done:
            raise ValueError(f"{d.after} cannot be under way while {d.before}, which it waits for, is not done")
    earliest: dict[str, int] = {}
    for d in spec.dependencies:
        if d.before in state.done and d.after not in state.done:
            earliest[d.after] = max(earliest.get(d.after, 0), state.done[d.before] + d.min_lag)  # the lag still owed after finished work

    notes, tasks = [], []
    for t in spec.tasks:
        if t.id in state.done:
            continue
        if t.id in state.in_flight:
            tasks.append(t.model_copy(update={"earliest_start": 0, "fixed_start": state.now, "duration": state.in_flight[t.id], "deadline_end": t.deadline_end}))
            notes.append(f"{t.name}: under way, {state.in_flight[t.id]} min left; what depends on it waits")
            continue
        floor = max(state.now, t.earliest_start, earliest.get(t.id, 0))
        tasks.append(t.model_copy(update={"earliest_start": floor}))  # nothing that has not started can start in the past
    for tid in sorted(state.done):
        notes.append(f"{next(t.name for t in spec.tasks if t.id == tid)}: already done, not scheduled again")

    kept = {t.id for t in tasks}
    deps = tuple(d for d in spec.dependencies if d.before in kept and d.after in kept)
    anchor = spec.anchor_task if spec.anchor_task in kept else None
    if anchor is None and spec.anchor_task is not None:
        tasks = [t.model_copy(update={"prefer": "none"}) if t.prefer == "late" else t for t in tasks]  # a "late" preference is measured against a task that is gone
        notes.append("the task other work was timed against is done, so 'as late as possible' no longer applies")
    if not tasks:
        raise ValueError("nothing is left to schedule")
    out = ScheduleSpec(tasks=tuple(tasks), dependencies=deps, resources=spec.resources, horizon=spec.horizon, anchor_task=anchor)
    return RepairSpec(spec=out, dropped=tuple(sorted(state.done)), pinned=tuple(sorted(state.in_flight)), notes=tuple(notes))
