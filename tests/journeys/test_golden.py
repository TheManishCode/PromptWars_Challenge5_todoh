"""Golden journeys (architecture L11): recorded briefs, plans and change requests with the outcome each must have, on the rule paths (no model, no network).

The cases are data (``golden.json``) so a new one is a line, not a test. They are written the way people write: lakh and crore, Indian number grouping, city spellings (Bangalore,
BLR, Poona, Bombay), "pax", a budget with a currency sign, and a date without a year. A case that fails is either a real gap in the parser (fix it) or a known one: those are
listed in KNOWN_GAPS with the reason, as strict xfails, so a fix makes the test fail until the entry is removed."""

import json
from datetime import date
from pathlib import Path

import pytest

from eventmate.logs import RunLogs
from eventmate.planning import intake
from eventmate.planning.delta import formalize_delta
from eventmate.service import EventMate

TODAY = date(2026, 10, 30)
CASES = json.loads((Path(__file__).parent / "golden.json").read_text(encoding="utf-8"))
KNOWN_GAPS: dict = {}  # case id -> why it is not met yet


def ids(kind):
    return [pytest.param(c, id=c["id"], marks=[pytest.mark.xfail(reason=KNOWN_GAPS[c["id"]], strict=True)] if c["id"] in KNOWN_GAPS else []) for c in CASES if c["kind"] == kind]


@pytest.fixture(scope="module")
def em(tmp_path_factory):
    root = tmp_path_factory.mktemp("golden")
    return EventMate(log_dir=root / "l", plan_dir=root / "p", run_id="golden")


@pytest.fixture(scope="module")
def base_plan(em):
    text = next(c["text"] for c in CASES if c["id"] == "p01")
    out = em.plan(text, today=TODAY)
    assert out.plan is not None, "needs the solvers: set EVENTMATE_FD and EVENTMATE_VAL"
    return out.plan


def test_the_cases_are_unique_and_cover_every_kind():
    assert len({c["id"] for c in CASES}) == len(CASES) >= 35 and {c["kind"] for c in CASES} == {"brief", "plan", "change"}


@pytest.mark.parametrize("case", ids("brief"))
def test_a_brief_is_read_the_way_it_was_written(case, em, tmp_path):
    turn = intake.propose([case["text"]], None, TODAY, em.repo.cities(), RunLogs.create(log_dir=tmp_path, run_id="g"))
    d, want = turn.draft, case["expect"]
    for field in ("guest_count", "city", "budget_total"):
        if field in want:
            assert getattr(d, field) == want[field], (field, d)
    if "event_date" in want:
        assert d.event_date is not None and d.event_date.isoformat() == want["event_date"], d
    for diet in want.get("dietary", []):
        assert diet in [x.value for x in d.dietary], d
    if want.get("ready") is not None:
        assert turn.ready is want["ready"], (turn.ready, turn.missing, turn.question)
    for field in want.get("missing_has", []):
        assert field in d.missing(), (field, d.missing())


@pytest.mark.parametrize("case", ids("plan"))
def test_a_brief_that_fits_becomes_a_validated_plan_and_one_that_cannot_is_refused(case, em):
    out = em.plan(case["text"], today=TODAY)
    assert (out.plan is not None) is (case["expect"]["status"] == "planned"), out.violations if out.plan is None else out.plan.change_reason
    if out.plan is None:
        assert out.violations and all(v.repair for v in out.violations)  # a refusal always says how to fix it


@pytest.mark.parametrize("case", ids("change"))
def test_a_change_request_becomes_the_delta_the_words_say(case, base_plan):
    t = formalize_delta(case["text"], base_plan, TODAY)
    d, want = t.delta, case["expect"]
    if want.get("empty"):
        assert d.empty() and t.questions, t
        return
    for field in ("set_guest_count", "set_budget_inr"):
        if field in want:
            assert getattr(d, field) == want[field], (field, t)
    if "set_date" in want:
        assert d.set_date is not None and d.set_date.isoformat() == want["set_date"], t
    if "cancel_vendor_categories" in want:
        cats = {s.category.value for s in base_plan.selections if s.vendor_id in d.cancel_vendor}
        assert cats == set(want["cancel_vendor_categories"]), (cats, t)
    assert not (set(want) - {"set_guest_count", "set_budget_inr", "set_date", "cancel_vendor_categories"})  # an expectation this harness does not check would pass silently
