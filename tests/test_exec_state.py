"""Checkpoint 2: state-aware repair (architecture L5). Finished work is not re-solved, work under way blocks what depends on it, nothing starts in the past,
and every repaired schedule passes an independent checker."""

import random

import pytest
from pydantic import ValidationError

from eventmate.cpsat import solve_schedule
from eventmate.planning.exec_state import ExecState, to_repair_spec
from eventmate.schemas import ScheduleDependency as Dep, ScheduleSpec, ScheduleTask as T
from tests.oracles.schedule_validity import violations

H = 20_000


def spec(*tasks, deps=(), resources=None, anchor=None):
    return ScheduleSpec(tasks=tuple(tasks), dependencies=tuple(deps), resources=resources or {}, horizon=H, anchor_task=anchor)


def solve(rs):
    sol = solve_schedule(rs.spec)
    assert sol.status in ("optimal", "feasible"), sol
    assert violations(rs.spec, sol) == []  # the independent checker agrees
    return {e.task_id: (e.start, e.end) for e in sol.entries}


def chain():
    return spec(T(id="book_venue", name="Book venue", duration=60), T(id="book_caterer", name="Book caterer", duration=60), T(id="menu", name="Menu tasting", duration=120),
                deps=[Dep(before="book_venue", after="book_caterer", min_lag=30), Dep(before="book_caterer", after="menu")])


def test_finished_work_is_not_solved_again_and_its_lag_is_still_owed():
    rs = to_repair_spec(chain(), ExecState(now=100, done={"book_venue": 90}))
    assert rs.dropped == ("book_venue",) and {t.id for t in rs.spec.tasks} == {"book_caterer", "menu"}
    at = solve(rs)
    assert at["book_caterer"][0] >= 90 + 30 and at["book_caterer"][0] >= 100 and at["menu"][0] >= at["book_caterer"][1]


def test_nothing_that_has_not_started_is_placed_in_the_past():
    rs = to_repair_spec(chain(), ExecState(now=500))
    assert all(s >= 500 for s, _ in solve(rs).values())


def test_work_under_way_holds_its_place_and_everything_after_it_waits():
    rs = to_repair_spec(chain(), ExecState(now=100, done={"book_venue": 60}, in_flight={"book_caterer": 45}))
    assert rs.pinned == ("book_caterer",)
    at = solve(rs)
    assert at["book_caterer"] == (100, 145) and at["menu"][0] >= 145
    assert any("under way" in n for n in rs.notes)


def test_a_shared_resource_is_still_respected_around_work_under_way():
    s = spec(T(id="a", name="A", duration=60, resource="crew"), T(id="b", name="B", duration=60, resource="crew"), resources={"crew": 1})
    at = solve(to_repair_spec(s, ExecState(now=10, in_flight={"a": 30})))
    assert at["a"] == (10, 40) and at["b"][0] >= 40


def test_a_task_fixed_in_the_past_and_not_done_is_reported_not_moved():
    s = spec(T(id="venue", name="Venue", duration=60, fixed_start=50))
    rs = to_repair_spec(s, ExecState(now=100))
    sol = solve_schedule(rs.spec)
    assert sol.status == "infeasible" and "fixed:venue" in sol.infeasibility_core  # named, so the user can be told which booking is now impossible


def test_a_confirmed_future_booking_never_moves():
    s = spec(T(id="venue", name="Venue", duration=60, fixed_start=5_000), T(id="deco", name="Decor", duration=30, prefer="early"), deps=[Dep(before="deco", after="venue")])
    at = solve(to_repair_spec(s, ExecState(now=100)))
    assert at["venue"] == (5_000, 5_060) and at["deco"][0] >= 100


def test_a_late_preference_is_dropped_when_the_anchor_is_done():
    s = spec(T(id="event", name="Event", duration=60, fixed_start=3_000), T(id="deco", name="Decor", duration=30, prefer="late"), anchor="event")
    rs = to_repair_spec(s, ExecState(now=3_100, done={"event": 3_060}))
    assert rs.spec.anchor_task is None and rs.spec.tasks[0].prefer == "none"


@pytest.mark.parametrize("state,message", [
    (ExecState(now=1, done={"ghost": 0}), "unknown task"),
    (ExecState(now=H + 1), "past the end"),
    (ExecState(now=10, done={"book_venue": 5, "book_caterer": 6, "menu": 7}), "nothing is left"),
])
def test_bad_state_is_refused_with_a_reason(state, message):
    with pytest.raises(ValueError, match=message):
        to_repair_spec(chain(), state)


def test_a_task_cannot_be_under_way_before_what_it_waits_for_is_done():
    with pytest.raises(ValueError, match="cannot be under way while book_venue"):
        to_repair_spec(chain(), ExecState(now=100, in_flight={"book_caterer": 20}))


def test_inconsistent_states_cannot_be_built():
    for kw in ({"done": {"a": 1}, "in_flight": {"a": 5}}, {"done": {"a": 99}}, {"in_flight": {"a": 0}}):
        with pytest.raises(ValidationError):
            ExecState(now=10, **kw)


@pytest.mark.parametrize("seed", range(25))
def test_random_schedules_repair_to_something_the_independent_checker_accepts(seed):
    rnd = random.Random(seed)
    n = rnd.randint(4, 9)
    tasks = [T(id=f"t{i}", name=f"Task {i}", duration=rnd.randint(10, 120), resource=rnd.choice([None, "crew", "van"])) for i in range(n)]
    deps = [Dep(before=f"t{i}", after=f"t{j}", min_lag=rnd.choice([0, 0, 15, 40])) for i in range(n) for j in range(i + 1, n) if rnd.random() < 0.3]
    s = spec(*tasks, deps=deps, resources={"crew": 1, "van": 2})
    first = {e.task_id: (e.start, e.end) for e in solve_schedule(s).entries}
    now = rnd.randint(0, 200)
    done = {tid: end for tid, (_, end) in first.items() if end <= now}
    running = {tid: min(end, now + 60) - now or 1 for tid, (st, end) in first.items() if st < now < end and tid not in done and (end - now) >= 1}
    rs = to_repair_spec(s, ExecState(now=now, done=done, in_flight=running))
    at = solve(rs)
    assert all(st >= now for st, _ in at.values())
