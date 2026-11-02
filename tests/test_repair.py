"""Checkpoint 4: ReplannerAgent.repair applies a confirmed delta over the execution state as one validated, hash-chained version (architecture L5)."""

from datetime import timedelta

import pytest

from eventmate.planning.delta import EventDelta, NewTask
from eventmate.planning.exec_state import ExecState
from eventmate.service import EventMate
from tests.conftest import EXAMPLE, TODAY


@pytest.fixture
def em(tmp_path):
    return EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="repair")


@pytest.fixture
def made(em):
    out = em.plan(EXAMPLE, today=TODAY)
    assert out.plan is not None, "needs the solvers: set EVENTMATE_FD and EVENTMATE_VAL"
    return out.plan


def start_min(plan, task_id):
    item = next(i for i in plan.items if i.task_id == task_id)
    return int((item.start - plan.origin).total_seconds() // 60)


def end_min(plan, task_id):
    item = next(i for i in plan.items if i.task_id == task_id)
    return int((item.end - plan.origin).total_seconds() // 60)


def test_one_delta_with_several_changes_is_one_new_version_and_a_diff(em, made):
    d = EventDelta(set_guest_count=80, set_budget_inr=180_000)
    out = em.repair(made.plan_id, d, ExecState(now=0), TODAY)
    assert out.status == "replanned" and out.plan.version == made.version + 1 and out.plan.parent_version == made.version
    assert out.plan.request.guest_count == 80 and out.plan.request.budget_total == 180_000 and out.diff is not None
    assert "guest count change" in out.plan.change_reason and "budget change" in out.plan.change_reason


def test_work_that_is_done_stays_exactly_where_it_was_while_the_rest_moves_to_now(em, made):
    later = TODAY + timedelta(days=10)  # ten days on: anything not yet done cannot start before then, so an unpinned venue booking would have to move
    now = 10 * 24 * 60
    state = ExecState(now=now, done={"book_venue": end_min(made, "book_venue")})
    out = em.repair(made.plan_id, EventDelta(set_guest_count=80), state, later)
    assert out.status == "replanned"
    assert start_min(out.plan, "book_venue") == start_min(made, "book_venue") < now  # it happened, so it stays in the past
    assert start_min(out.plan, "book_caterer") >= now and start_min(out.plan, "book_caterer") >= end_min(out.plan, "book_venue")


def test_a_cancelled_vendors_booking_is_void_so_it_is_booked_again_from_now(em, made):
    c = next(s for s in made.selections if s.category.value == "caterer")
    later, now = TODAY + timedelta(days=10), 10 * 24 * 60
    state = ExecState(now=now, done={"book_venue": end_min(made, "book_venue"), "book_caterer": end_min(made, "book_caterer")})
    out = em.repair(made.plan_id, EventDelta(cancel_vendor=(c.vendor_id,)), state, later)
    assert out.status == "replanned" and next(s for s in out.plan.selections if s.category.value == "caterer").vendor_id != c.vendor_id
    assert start_min(out.plan, "book_venue") == start_min(made, "book_venue")  # the venue booking that did happen is kept
    assert start_min(out.plan, "book_caterer") >= now  # the cancelled one did not happen any more: its replacement is booked from now, not at the old time


def test_an_event_that_has_happened_cannot_be_moved(em, made):
    state = ExecState(now=end_min(made, "event"), done={"event": end_min(made, "event")})
    out = em.repair(made.plan_id, EventDelta(set_date=made.request.event_date + timedelta(days=7)), state, TODAY)
    assert out.status == "failed" and out.violations[0].constraint == "happened_cannot_move"
    assert em.store.latest(made.plan_id).version == made.version  # nothing saved


def test_unknown_tasks_unfinished_new_tasks_and_empty_changes_are_refused_and_save_nothing(em, made):
    for delta, state, why in [
        (EventDelta(set_guest_count=80), ExecState(now=10, done={"book_ghost": 5}), "unknown_task"),
        (EventDelta(add_tasks=(NewTask(name="Mehendi"),), set_guest_count=80), ExecState(now=0), "new_task_needs_a_length"),
        (EventDelta(add_tasks=(NewTask(name="Mehendi", duration_minutes=60, after=("book_ghost",)),)), ExecState(now=0), "unknown_task"),
        (EventDelta(), ExecState(now=0), "empty_change"),
    ]:
        out = em.repair(made.plan_id, delta, state, TODAY)
        assert out.status == "failed" and out.violations[0].constraint == why
    assert em.store.latest(made.plan_id).version == made.version


def test_a_change_the_plan_cannot_absorb_keeps_the_old_version_valid(em, made):
    out = em.repair(made.plan_id, EventDelta(set_budget_inr=1_000), ExecState(now=0), TODAY)
    assert out.status in ("failed", "needs_decision") and out.plan is None
    assert em.store.latest(made.plan_id).version == made.version


def test_repair_with_one_change_gives_the_same_plan_as_replan(em, made, tmp_path):
    from eventmate.schemas import Disruption, DisruptionKind

    other = EventMate(log_dir=tmp_path / "l2", plan_dir=tmp_path / "p2", run_id="repair2")
    twin = other.plan(EXAMPLE, today=TODAY).plan
    a = em.repair(made.plan_id, EventDelta(set_guest_count=90), ExecState(now=0), TODAY).plan
    b = other.replan(twin.plan_id, Disruption(kind=DisruptionKind.GUEST_COUNT_CHANGE, new_guest_count=90), TODAY).plan
    assert [(s.category, s.vendor_id, s.cost) for s in a.selections] == [(s.category, s.vendor_id, s.cost) for s in b.selections] and a.total_cost == b.total_cost
    assert [(i.task_id, i.start, i.end) for i in a.items] == [(i.task_id, i.start, i.end) for i in b.items]


# ---------------------------------------------------------------- new tasks (checkpoint 5)


def mehendi(**kw):
    return NewTask(name="Mehendi", duration_minutes=180, days_before_event=1, **kw)


def test_a_task_added_the_night_before_is_placed_that_day_and_the_plan_still_validates(em, made):
    out = em.repair(made.plan_id, EventDelta(add_tasks=(mehendi(after=("book_venue",)),)), ExecState(now=0), TODAY)
    assert out.status == "replanned" and out.plan.version == made.version + 1 and "added: Mehendi" in out.plan.change_reason
    (x,) = out.plan.request.extra_tasks
    item = next(i for i in out.plan.items if i.task_id == x.task_id)
    eve = made.request.event_date - timedelta(days=1)
    assert item.start.date() == eve and item.end.date() == eve and (item.end - item.start) == timedelta(minutes=180)
    assert item.start >= next(i for i in out.plan.items if i.task_id == "book_venue").end  # it follows what it named
    assert out.plan.validation.passed and any(i.task_id == x.task_id for i in out.plan.items)
    assert any(x.task_id in str(d) or "Mehendi" in str(d) for d in out.diff.added)  # the diff says the step was added


def test_an_event_day_task_ends_before_the_event_starts(em, made):
    out = em.repair(made.plan_id, EventDelta(add_tasks=(NewTask(name="Sound check", duration_minutes=45, after=("venue_setup",)),)), ExecState(now=0), TODAY)
    assert out.status == "replanned"
    item = next(i for i in out.plan.items if i.task_id.startswith("extra_sound_check"))
    ev = next(i for i in out.plan.items if i.task_id == "event")
    assert item.end <= ev.start and item.start >= next(i for i in out.plan.items if i.task_id == "venue_setup").end


def test_adding_the_same_step_twice_gives_each_its_own_id_and_the_earlier_one_stays(em, made):
    a = em.repair(made.plan_id, EventDelta(add_tasks=(mehendi(),)), ExecState(now=0), TODAY).plan
    b = em.repair(made.plan_id, EventDelta(add_tasks=(mehendi(),)), ExecState(now=0), TODAY).plan
    ids = [x.task_id for x in b.request.extra_tasks]
    assert len(ids) == 2 == len(set(ids)) and b.version == a.version + 1 and ids[0] == a.request.extra_tasks[0].task_id


def test_a_step_that_cannot_fit_its_day_is_refused_and_the_old_version_stays(em, made):
    out = em.repair(made.plan_id, EventDelta(add_tasks=(NewTask(name="Rehearsal", duration_minutes=1440, days_before_event=1),)), ExecState(now=0), TODAY)  # a whole day inside a day plus a work-start offset
    assert out.status in ("failed", "needs_decision") and out.plan is None
    assert any("extra_rehearsal" in v.detail for v in out.violations)  # the solver names the step that cannot fit, not a vague failure
    assert em.store.latest(made.plan_id).version == made.version


def test_a_plan_made_before_extra_tasks_existed_still_loads_unchanged(made):
    from eventmate.schemas import EventRequest

    old = made.request.model_dump(mode="json")
    old.pop("extra_tasks")
    assert EventRequest.model_validate(old).extra_tasks == () and made.request.extra_tasks == ()


def test_a_step_that_names_another_added_step_waits_for_it_even_when_it_sorts_first(em, made):
    first = em.repair(made.plan_id, EventDelta(add_tasks=(mehendi(),)), ExecState(now=0), TODAY).plan
    mid = first.request.extra_tasks[0].task_id
    out = em.repair(made.plan_id, EventDelta(add_tasks=(NewTask(name="Aarti prep", duration_minutes=60, days_before_event=1, after=(mid,)),)), ExecState(now=0), TODAY)
    assert out.status == "replanned"
    a = next(i for i in out.plan.items if i.task_id == "extra_aarti_prep")
    m = next(i for i in out.plan.items if i.task_id == mid)
    assert a.start >= m.end  # "Aarti" sorts before "Mehendi" and both want the same hours with one organizer, so only the named dependency puts it second
