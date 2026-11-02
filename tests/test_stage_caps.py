"""Per-stage model caps (architecture L10): each stage of the product has its own hourly allowance per workspace, under the workspace's own caps."""

import re
from pathlib import Path

import pytest

from eventmate import consumption
from eventmate.consumption import ConsumptionError, WorkspaceBudgets, stage


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def b(monkeypatch):
    for name in list(__import__("os").environ):
        if name.startswith("EVENTMATE_STAGE_") or name.startswith("EVENTMATE_WS_"):
            monkeypatch.delenv(name)
    c = Clock()
    return WorkspaceBudgets(clock=c), c


def call(b, ws, name, tokens=10):
    with stage(name), b.slot(ws, tokens):
        pass


def test_a_stage_stops_at_its_cap_and_the_other_stages_keep_working(b, monkeypatch):
    budgets, _ = b
    monkeypatch.setenv("EVENTMATE_STAGE_DELTA_CALLS_PER_HOUR", "3")
    for _ in range(3):
        call(budgets, "ws1", "delta")
    with pytest.raises(ConsumptionError) as e:
        call(budgets, "ws1", "delta")
    assert e.value.code == "stage_budget" and "delta" in str(e.value)
    call(budgets, "ws1", "intake")  # a different stage of the same workspace is untouched
    call(budgets, "ws2", "delta")  # and another workspace has its own count


def test_a_refused_call_costs_the_workspace_nothing(b, monkeypatch):
    budgets, _ = b
    monkeypatch.setenv("EVENTMATE_STAGE_DELTA_CALLS_PER_HOUR", "1")
    monkeypatch.setenv("EVENTMATE_WS_LLM_CALLS_PER_HOUR", "3")
    call(budgets, "ws", "delta")
    for _ in range(5):
        with pytest.raises(ConsumptionError):
            call(budgets, "ws", "delta")
    call(budgets, "ws", "intake")
    call(budgets, "ws", "intake")  # three counted calls in all: the five refusals were never counted against the hourly total
    with pytest.raises(ConsumptionError) as e:
        call(budgets, "ws", "intake")
    assert e.value.code == "workspace_budget"


def test_the_allowance_comes_back_after_the_hour(b, monkeypatch):
    budgets, clock = b
    monkeypatch.setenv("EVENTMATE_STAGE_DELTA_CALLS_PER_HOUR", "1")
    call(budgets, "ws", "delta")
    with pytest.raises(ConsumptionError):
        call(budgets, "ws", "delta")
    clock.t += consumption.WINDOW_S + 1
    call(budgets, "ws", "delta")


def test_the_browser_stage_is_the_tightest_and_unlabelled_calls_have_no_stage_cap(b):
    budgets, _ = b
    assert budgets.stage_limit("browser") == min(consumption.STAGE_CALLS.values()) and budgets.stage_limit("other") == 0
    for _ in range(150):
        call(budgets, "ws", "other")  # no stage cap: only the workspace's own


def test_a_stage_cap_can_be_tuned_or_set_for_a_new_stage_from_the_environment(b, monkeypatch):
    budgets, _ = b
    monkeypatch.setenv("EVENTMATE_STAGE_NEWTHING_CALLS_PER_HOUR", "2")
    call(budgets, "w", "newthing")
    call(budgets, "w", "newthing")
    with pytest.raises(ConsumptionError):
        call(budgets, "w", "newthing")


@pytest.mark.parametrize("bad", ["", "Delta", "a b", "../x", "x" * 21, "1a"])
def test_a_stage_name_is_plain(bad):
    with pytest.raises(ValueError):
        with stage(bad):
            pass


def test_the_owner_can_see_calls_per_stage_without_workspace_ids(b):
    budgets, _ = b
    call(budgets, "w1", "intake")
    call(budgets, "w2", "intake")
    call(budgets, "w1", "delta")
    assert budgets.stage_status() == {"intake": 2, "delta": 1}
    budgets.reset()
    assert budgets.stage_status() == {}


def test_every_model_call_in_the_product_is_inside_a_labelled_stage():
    """A new model call that forgets its stage would dodge its cap; this fails when one is added without it."""
    root = Path(consumption.__file__).parent
    bare = []
    for path in root.rglob("*.py"):
        if path.name in ("llm.py", "smoke.py"):  # the client itself, and the key smoke check
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if re.search(r"\b(?:llm|client|self\.llm)\.complete\(", line) and "consumption.stage(" not in lines[i - 1]:
                bare.append(f"{path.name}:{i + 1}")
    assert bare == []


def test_the_delta_and_intake_calls_carry_their_stage_label(tmp_path):
    from datetime import date

    from eventmate.logs import RunLogs
    from eventmate.planning import intake
    from tests.conftest import EXAMPLE, TODAY
    from eventmate.service import EventMate

    seen = []

    class Spy:
        name = "spy"

        def complete(self, system, user, max_tokens):
            seen.append(consumption.current_stage())
            return "{}"

    intake.propose(["a party in Pune"], None, date(2026, 11, 3), ("Pune",), RunLogs.create(log_dir=tmp_path, run_id="s"), Spy())
    em = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="s2", llm=Spy())
    plan = em.plan(EXAMPLE, today=TODAY).plan
    em.change_turn(plan.plan_id, "now 80 guests", TODAY)
    assert seen[0] == "intake" and "delta" in seen and consumption.current_stage() == "other"  # and the label does not leak out of the block
