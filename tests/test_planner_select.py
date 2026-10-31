"""The vendor-selection step on its own: budget boundaries, ranking ties, honest alternatives and the decision log."""

from types import SimpleNamespace as NS

import pytest

from eventmate.agents import planner as P
from eventmate.agents.guest import GuestAgent, GuestRepository
from eventmate.agents.scheduler import SchedulingAgent
from eventmate.logs import RunLogs
from eventmate.symbolic import SymbolicChecker
from eventmate.agents.planner import PlannerAgent, _Opt, _affordable, _options_for, _rank, _total
from eventmate.schemas import BookingStatus, VendorCategory as C
from tests.test_planner import ORIGIN, run

AV = BookingStatus.AVAILABLE


@pytest.fixture
def logs(tmp_path):
    return RunLogs.create(log_dir=tmp_path, run_id="select")


@pytest.fixture
def planner(logs):
    return PlannerAgent(logs, SchedulingAgent(logs.decisions), SymbolicChecker())


@pytest.fixture
def guests(logs, intake):
    return GuestAgent(GuestRepository(), logs.decisions).summarize(intake.request)


def opt(vid, cost, score, cat=C.VENUE, status=AV, cand=object()):
    return _Opt(cat, vid, vid.upper(), cost, score, "database:test", status, cand)


def req(budget):
    return NS(budget_total=budget, city="Pune", request_id="r")


ALLOC = NS(contingency=10)


def test_budget_boundaries_are_inclusive_at_each_tier():
    combos = [(opt("a", 90, 1),), (opt("b", 95, 1),)]
    pool, warn, viol = _affordable(req(100), ALLOC, combos)  # 90 == 100 - 10 fits with the reserve
    assert pool == [combos[0]] and not warn and viol is None
    pool, warn, viol = _affordable(req(95), ALLOC, combos)  # 95 == total fits only without the reserve
    assert pool == combos[:2] and len(warn) == 1 and "INR 10" in warn[0] and "INR 90" in warn[0] and "INR 95" in warn[0]
    pool, warn, viol = _affordable(req(89), ALLOC, combos)  # one rupee short of the cheapest
    assert pool is None and viol[0].constraint == "total_budget" and "INR 90" in viol[0].repair


def test_ranking_prefers_score_then_cost_then_ids():
    hi, cheap_hi, lo = (opt("x", 50, 2.0),), (opt("y", 40, 2.0),), (opt("z", 10, 1.0),)
    assert sorted([lo, hi, cheap_hi], key=_rank) == [cheap_hi, hi, lo]
    assert sorted([(opt("b", 5, 1),), (opt("a", 5, 1),)], key=_rank)[0][0].vendor_id == "a"
    assert _total((opt("a", 3, 1), opt("b", 4, 1))) == 7
    assert _rank((opt("a", 1, 0.1234567),))[0] == -0.123457  # rounded to 6 places so float noise cannot reorder ties


def test_a_forced_booking_is_the_only_option_and_keeps_its_status():
    f = NS(vendor_id="v1", name="Venue One", cost=70, score=0.5, source="user", booking_status=BookingStatus.CONFIRMED)
    [o] = _options_for(C.VENUE, {}, {C.VENUE: f})
    assert (o.vendor_id, o.cost, o.status, o.candidate) == ("v1", 70, BookingStatus.CONFIRMED, None)
    sl = NS(candidates=[NS(vendor_id="a", name="A", estimated_cost=5, score=1.0, source="s"), NS(vendor_id="b", name="B", estimated_cost=6, score=2.0, source="s")])
    got = _options_for(C.CATERER, {C.CATERER: sl}, {})
    assert [o.vendor_id for o in got] == ["a", "b"] and all(o.status == AV and o.candidate is not None for o in got)


def rejected(name, *failed):
    return NS(name=name, vendor_id=name, failed=[NS(name=n, detail=f"{n} failed") for n in failed])


def test_alternatives_are_capped_ordered_and_say_why_they_lost():
    opts = [opt("w", 10, 9), opt("a", 20, 8), opt("b", 30, 7), opt("c", 40, 6), opt("d", 50, 5)]
    best = (opts[0],)
    ranked = [(o,) for o in opts[:2]]  # only w and a appear in an affordable combination
    shortlist = NS(rejected=[rejected("far", "city", "capacity"), rejected("near", "budget_cap"), rejected("mid", "dietary"), rejected("far2", "booking_status")])
    s = PlannerAgent._selection(req(100), C.VENUE, best, best, opts, 0, ranked, shortlist)
    names = [a.option for a in s.alternatives]
    assert names == ["A", "B", "C", "near", "mid"]  # three other candidates, then the two nearest misses
    assert s.alternatives[0].reason_not_chosen == "the best affordable combination is higher-scoring overall"
    assert s.alternatives[1].reason_not_chosen == "its combinations exceed the budget"
    assert s.alternatives[3].outcome == "rejected" and s.alternatives[3].reason_not_chosen == "budget_cap: budget_cap failed"
    assert s.no_alternative_reason is None and s.justification.startswith("W (INR 10, score 9)")


def test_justification_mentions_the_cheaper_combination_only_when_it_differs():
    w, c = opt("w", 30, 9), opt("c", 10, 1)
    shortlist = NS(rejected=[])
    s = PlannerAgent._selection(req(100), C.VENUE, (w,), (c,), [w, c], 0, [(w,)], shortlist)
    assert "The cheapest combination used C (INR 10) but scored lower." in s.justification and "INR 30 total" in s.justification
    same = PlannerAgent._selection(req(100), C.VENUE, (w,), (w,), [w, c], 0, [(w,)], shortlist)
    assert "cheapest combination" not in same.justification


def test_a_confirmed_booking_is_kept_and_gets_no_rejected_vendors_or_cheaper_note():
    conf = opt("k", 99, 0.1, status=BookingStatus.CONFIRMED, cand=None)
    cheap = opt("c", 1, 5)
    shortlist = NS(rejected=[rejected("near", "budget_cap")])
    s = PlannerAgent._selection(req(200), C.VENUE, (conf,), (cheap,), [conf], 0, [(conf,)], shortlist)
    assert s.justification == "K is a user-confirmed booking and is kept."
    assert s.alternatives == () and s.no_alternative_reason == "user-confirmed booking: the user already committed to this vendor"


def test_a_lone_available_vendor_says_so():
    only = opt("o", 5, 1)
    s = PlannerAgent._selection(req(200), C.VENUE, (only,), (only,), [only], 0, [(only,)], NS(rejected=[]))
    assert s.alternatives == () and s.no_alternative_reason == "only O exists in the database for Pune"


def decisions(logs, kind):
    return [e for e in logs.decisions.read() if e.agent.value == "planner" and e.decision_type.value == kind]


def test_the_recommendation_log_names_runners_up_and_confidence(planner, logs, intake, guests):
    run(planner, intake, guests)
    [rec] = decisions(logs, "recommendation")
    assert 1 <= len(rec.alternatives) <= 3 and all("lower combined score" in a.reason_not_chosen or "cheapest" in a.reason_not_chosen for a in rec.alternatives)
    assert rec.confidence == 0.9 and rec.no_alternative_reason is None
    assert {c.name for c in rec.constraints_checked} == {"total_budget", "one_vendor_per_category"} and all(c.passed for c in rec.constraints_checked)
    assert "Exhaustive search over" in rec.justification and rec.source == "rule:vendor_selection"


def test_a_single_affordable_combination_is_logged_with_low_confidence(planner, logs, intake, guests):
    one = tuple(sl.model_copy(update={"candidates": sl.candidates[:1]}) for sl in intake.vendor_shortlists)
    res = planner.plan(intake.request, guests, intake.budget_allocation, one, ORIGIN, plan_id="p1")
    assert res.draft is not None
    [rec] = decisions(logs, "recommendation")
    assert rec.confidence == 0.6 and rec.no_alternative_reason == "only one affordable combination exists" and rec.alternatives == ()


def test_failures_are_logged_with_the_repair(planner, logs, intake, guests):
    from eventmate.schemas import VendorShortlist

    empty = VendorShortlist(category=C.VENUE, budget_cap=1, candidates=(), rejected=())
    planner.plan(intake.request, guests, intake.budget_allocation, (empty,) + intake.vendor_shortlists[1:], ORIGIN, plan_id="f1")
    planner.plan(intake.request.with_changes(budget_total=100_000), guests, intake.budget_allocation, intake.vendor_shortlists, ORIGIN, plan_id="f2")
    fails = decisions(logs, "failure")
    assert len(fails) == 2 and fails[0].summary.startswith("No eligible vendor for venue") and fails[1].summary == "No affordable vendor combination"
    assert all(not c.passed for f in fails for c in f.constraints_checked) and "Repair:" in fails[1].justification


def test_every_stage_is_lit_before_it_is_finished_and_in_pipeline_order(planner, intake, guests, monkeypatch):
    calls = []
    monkeypatch.setattr(P, "_arcade_pulse", NS(stage_started=lambda n: calls.append(("start", n)), stage_done=lambda n: calls.append(("done", n))))
    run(planner, intake, guests)
    order = [n for k, n in calls if k == "start"]
    assert order == ["select", "schedule", "symbolic"]
    for n in order:
        assert calls.index(("start", n)) < calls.index(("done", n))
