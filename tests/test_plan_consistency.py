"""A step that fails around saving a plan must not leave state that disagrees with what the user was told, or an audit trail that claims something that did not happen."""

import pytest

from eventmate.schemas import Disruption, DisruptionKind, VendorCategory as C
from eventmate.service import EventMate
from eventmate.state import build_state
from tests.conftest import EXAMPLE, TODAY


@pytest.fixture
def em(tmp_path):
    return EventMate(log_dir=tmp_path / "logs", plan_dir=tmp_path / "plans", run_id="consistency")


def failures(em):
    return [e for e in em.logs.decisions.read() if e.decision_type.value == "failure"]


def broke(*a, **k):
    raise OSError("disk full")


def test_a_replan_whose_save_fails_leaves_the_previous_version_current_and_the_trail_truthful(em, monkeypatch):
    pid = em.plan(EXAMPLE, today=TODAY).plan.plan_id
    em.ledger_add(pid, "payment", C.VENUE, 1000, "deposit")
    d = em.draft_outreach(pid, C.CATERER, "email", "sales@annapurna.in", "Asha")
    before = (em.store.latest(pid), em.ledger_entries(pid), em.outreach(pid))
    real = em.store.save
    monkeypatch.setattr(em.store, "save", broke)
    with pytest.raises(OSError):
        em.replan(pid, Disruption(kind=DisruptionKind.BUDGET_CHANGE, new_budget=300_000), today=TODAY)
    assert (em.store.latest(pid), em.ledger_entries(pid), em.outreach(pid)) == before and em.store.latest(pid).version == 1
    [fail] = failures(em)
    assert "Version 2" in fail.summary and "could not be written" in fail.summary and "version 1 stays current" in fail.justification
    assert build_state(em, pid).plan_version == 1 and em.ledger_summary(pid).plan_version == 1  # every view still agrees on one version
    monkeypatch.setattr(em.store, "save", real)
    out = em.replan(pid, Disruption(kind=DisruptionKind.BUDGET_CHANGE, new_budget=300_000), today=TODAY)
    assert out.status == "replanned" and out.plan.version == 2 and out.plan.parent_version == 1  # a retry leaves no gap and no duplicate
    assert [e.entry_id for e in em.ledger_entries(pid)] == [e.entry_id for e in before[1]] and em.outreach(pid)[0].draft_id == d.draft_id


def test_a_first_plan_whose_save_fails_leaves_no_orphan_plan_and_says_so(em, monkeypatch):
    monkeypatch.setattr(em.store, "save", broke)
    with pytest.raises(OSError):
        em.plan(EXAMPLE, today=TODAY)
    assert em.store.list_plans() == []
    [fail] = failures(em)
    assert "could not be written" in fail.summary and "no plan exists" in fail.justification


def test_an_explanation_that_cannot_be_written_does_not_turn_a_saved_plan_into_an_error(em, monkeypatch):
    monkeypatch.setattr(em.explainer, "explain", lambda *a, **k: (_ for _ in ()).throw(OSError("log disk full")))
    out = em.plan(EXAMPLE, today=TODAY)
    assert out.plan is not None and em.store.latest(out.plan.plan_id) == out.plan  # the user gets the plan that was saved
    [fail] = failures(em)
    assert "explanation" in fail.summary and "OSError" in fail.summary and "plan is saved and valid" in fail.justification
    monkeypatch.undo()
    assert em.explain(out.plan.plan_id).markdown  # and the explanation is there when asked for


def test_a_failing_log_during_the_failure_report_never_hides_the_original_error(em, monkeypatch):
    pid = em.plan(EXAMPLE, today=TODAY).plan.plan_id

    def save_then_break_the_log(plan):
        monkeypatch.setattr(em.logs.decisions, "log", lambda **k: (_ for _ in ()).throw(RuntimeError("the log is broken too")))
        raise OSError("disk full")

    monkeypatch.setattr(em.store, "save", save_then_break_the_log)
    with pytest.raises(OSError, match="disk full"):  # the storage error, not the logging one, is what the caller sees
        em.replan(pid, Disruption(kind=DisruptionKind.BUDGET_CHANGE, new_budget=300_000), today=TODAY)
    assert em.store.latest(pid).version == 1


def test_a_concurrent_writer_is_still_reported_as_a_plain_failure_not_an_error(em, monkeypatch):
    pid = em.plan(EXAMPLE, today=TODAY).plan.plan_id
    monkeypatch.setattr(em.store, "save", lambda plan: (_ for _ in ()).throw(FileExistsError("taken")))
    out = em.replan(pid, Disruption(kind=DisruptionKind.BUDGET_CHANGE, new_budget=300_000), today=TODAY)
    assert out.status == "failed" and out.violations[0].constraint == "concurrent_modification"


def test_every_view_of_a_plan_names_the_same_latest_version_after_a_real_replan(em):
    pid = em.plan(EXAMPLE, today=TODAY).plan.plan_id
    em.ledger_add(pid, "quote", C.CATERER, 70_000, "q")
    out = em.replan(pid, Disruption(kind=DisruptionKind.BUDGET_CHANGE, new_budget=300_000), today=TODAY)
    assert out.plan.version == 2
    assert em.store.latest(pid).version == build_state(em, pid).plan_version == em.ledger_summary(pid).plan_version == em.explain(pid).version == 2
    assert em.verification(pid)["state"] in ("verified", "needs_verification")  # computed from the latest version
