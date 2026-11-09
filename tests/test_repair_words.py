"""What a person can do about a failed check is said in plain words (F1, F4): a missing planning tool is a deployer's job and says so, a budget that is too small says how much would work, and no repair
names an internal constraint ("relax the budget_cap constraint") or a tool's own term."""
import re

import pytest

from eventmate.agents.manager import ManagerAgent
from eventmate.agents.validator import REPAIRS, SOLVERS_MISSING, _repair_for
from eventmate.agents.vendor import VendorAgent
from eventmate.schemas import ConstraintCheck, ConstraintViolation, VendorCategory as C
from eventmate.service import EventMate
from tests.conftest import TODAY


def test_a_timeline_check_that_could_not_run_tells_a_deployer_what_to_install():
    got = _repair_for(ConstraintCheck(name="symbolic_feasibility", passed=False, detail="unavailable: solver binaries not found: Fast Downward, VAL"))
    assert got == SOLVERS_MISSING and "Fast Downward and VAL" in got and "docs/CONFIGURATION.md" in got and "task dependencies" not in got


def test_a_timeline_check_that_ran_and_failed_is_about_the_schedule_not_the_tools():
    got = _repair_for(ConstraintCheck(name="symbolic_feasibility", passed=False, detail="invalid: no plan"))
    assert got == REPAIRS["symbolic_feasibility"] and "PDDL" not in got and "Fast Downward" not in got


def test_an_unknown_failed_check_is_named_in_words_not_by_its_code_name():
    got = _repair_for(ConstraintCheck(name="some_new_check", passed=False, detail="x"))
    assert "some new check" in got and "_" not in got and "constraint" not in got


def test_no_repair_in_the_table_uses_a_code_name_or_a_tools_term():
    for name, text in REPAIRS.items():
        assert not re.search(r"\b\w+_\w+\b", text), f"{name}: {text}"
        assert not re.search(r"PDDL|\bconstraint\b", text), f"{name}: {text}"


@pytest.mark.parametrize("constraint", ["budget_cap", "something_internal"])
def test_the_vendor_repair_fallback_is_plain(constraint):
    text = VendorAgent._repair_text(constraint, None, C.VENUE, [])
    assert "_" not in text and "constraint" not in text and "relax" not in text


def test_a_budget_that_is_too_small_says_the_total_that_would_work(tmp_path):
    em = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="r")
    out = em.plan("Wedding for 120 guests in Pune on 12 December 2026, budget Rs 500", today=TODAY)
    budget = [v for v in out.violations if v.constraint in ("budget_cap", "total_budget")]
    assert budget and all("A total of about INR" in v.repair and "not a quote" in v.repair for v in budget)
    assert not any("relax" in v.repair for v in out.violations)


def test_a_violation_that_is_not_about_money_gets_no_minimum():
    class Reality:
        suggested_minimum_budget, minimum_budget_default_split = 55_555, None

    v = ConstraintViolation(constraint="city", detail="d", repair="choose a different city")
    assert ManagerAgent._with_minimum(v, Reality()) == v
    money = ConstraintViolation(constraint="budget_cap", detail="d", repair="raise the budget")
    assert "about INR 55,555" in ManagerAgent._with_minimum(money, Reality()).repair
