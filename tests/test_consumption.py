"""Phase 4.7: one workspace cannot spend another's model allowance, and the owner's ceiling sits above all of them."""

import json
import threading

import pytest

from eventmate import consumption
from eventmate.assistant.agent import Assistant
from eventmate.consumption import ConsumptionError, WorkspaceBudgets
from eventmate.llm import BudgetExceeded, LLMError, OpenAICompatClient, UsageMeter
from eventmate.web.app import create_app
from eventmate.service import EventMate
from tests.conftest import EXAMPLE, TODAY
from tests.test_assistant import answer
from tests.test_web import post

REPLY = json.dumps({"choices": [{"message": {"content": json.dumps(answer("x"))}}]}).encode()


@pytest.fixture
def em(tmp_path):
    return EventMate(log_dir=tmp_path / "logs", plan_dir=tmp_path / "plans", run_id="p4")


@pytest.fixture
def pid(em):
    return em.plan(EXAMPLE, today=TODAY).plan.plan_id


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    consumption.BUDGETS.reset()
    for k in ("EVENTMATE_WS_LLM_CALLS_PER_HOUR", "EVENTMATE_WS_LLM_TOKENS_PER_HOUR", "EVENTMATE_WS_LLM_CONCURRENCY"):
        monkeypatch.delenv(k, raising=False)
    yield
    consumption.BUDGETS.reset()


def client(transport=None, meter=None):
    return OpenAICompatClient("n", "https://x.test/v1", "k" * 12, "m", transport=transport or (lambda *a: REPLY), meter=meter or UsageMeter(1000))


def ask_as(workspace, c):
    token = consumption.bind(workspace)
    try:
        return c.complete("s", "u", 50)
    finally:
        consumption.unbind(token)


def test_workspace_a_exhausts_its_calls_and_workspace_b_still_answers(monkeypatch):
    monkeypatch.setenv("EVENTMATE_WS_LLM_CALLS_PER_HOUR", "2")
    c = client()
    ask_as("a", c), ask_as("a", c)
    with pytest.raises(ConsumptionError) as exc:
        ask_as("a", c)
    assert exc.value.code == "workspace_budget" and exc.value.status == 429
    assert ask_as("b", c)


def test_the_hourly_token_budget_is_per_workspace(monkeypatch):
    monkeypatch.setenv("EVENTMATE_WS_LLM_TOKENS_PER_HOUR", "200")
    c = client()
    with pytest.raises(ConsumptionError, match="allowance"):
        ask_as("a", c) and c.complete("s", "u" * 4000, 50)  # the second prompt alone is over the budget
    assert ask_as("b", c)


def test_the_allowance_comes_back_when_the_hour_is_over():
    now = [0.0]
    b = WorkspaceBudgets(clock=lambda: now[0])
    for _ in range(b.limits()[0]):
        with b.slot("a", 1):
            pass
    with pytest.raises(ConsumptionError):
        with b.slot("a", 1):
            pass
    now[0] += consumption.WINDOW_S + 1
    with b.slot("a", 1):
        pass


def test_a_failed_call_still_counts_as_spent(monkeypatch):
    monkeypatch.setenv("EVENTMATE_WS_LLM_CALLS_PER_HOUR", "2")

    def boom(*a):
        raise OSError("down")

    c = client(boom)
    for _ in range(2):
        with pytest.raises(LLMError):
            ask_as("a", c)
    with pytest.raises(ConsumptionError):
        ask_as("a", c)


def test_the_owner_ceiling_stops_every_workspace():
    c = client(meter=UsageMeter(max_calls=2))
    ask_as("a", c), ask_as("b", c)
    for ws in ("a", "b", "c"):
        with pytest.raises(BudgetExceeded) as exc:
            ask_as(ws, c)
        assert exc.value.code == "owner_ceiling" and isinstance(exc.value, LLMError)


def test_a_limit_is_not_retried_and_is_not_passed_to_another_provider(monkeypatch):
    from eventmate.llm import FallbackClient

    monkeypatch.setenv("EVENTMATE_WS_LLM_CALLS_PER_HOUR", "1")
    seen = []
    fb = FallbackClient([client(lambda *a: seen.append(1) or REPLY), client(lambda *a: seen.append(2) or REPLY)])
    token = consumption.bind("a")
    try:
        fb.complete("s", "u", 50)
        with pytest.raises(ConsumptionError):
            fb.complete("s", "u", 50)
    finally:
        consumption.unbind(token)
    assert seen == [1]


def _blocking_assistant(em, gate, entered):
    def transport(*a):
        entered.set()
        gate.wait(5)
        return REPLY

    c = client(transport)
    return Assistant(em, {"large": c, "small": c})


def test_the_concurrency_cap_returns_429_with_a_code(em, pid, monkeypatch):
    monkeypatch.setenv("EVENTMATE_WS_LLM_CONCURRENCY", "1")
    gate, entered = threading.Event(), threading.Event()
    http = create_app(em, today_fn=lambda: TODAY, assistant=_blocking_assistant(em, gate, entered)).test_client()
    question = {"plan_id": pid, "question": "compare the caterer options for me"}
    first = []
    t = threading.Thread(target=lambda: first.append(post(http, "/api/assistant/ask", question)))
    t.start()
    assert entered.wait(5)
    r = post(http, "/api/assistant/ask", {**question, "question": "compare the venue options for me"})
    gate.set()
    t.join(5)
    assert r.status_code == 429 and r.get_json()["code"] == "workspace_busy" and set(r.get_json()) == {"error", "code"}


def test_a_spent_allowance_gives_a_degraded_answer_not_an_error(em, pid, monkeypatch):
    monkeypatch.setenv("EVENTMATE_WS_LLM_CALLS_PER_HOUR", "1")
    c = client()
    a = Assistant(em, {"large": c, "small": c}, policy={**__import__("eventmate.assistant.guard", fromlist=["x"]).load_policy(), "retries": 0})
    http = create_app(em, today_fn=lambda: TODAY, assistant=a).test_client()
    q = lambda text: post(http, "/api/assistant/ask", {"plan_id": pid, "question": text})  # noqa: E731
    q("compare the caterer options for me")  # uses the one call (its answer is refused: no evidence), so the next is over the limit
    r = q("compare the venue options for me")
    assert r.status_code == 200 and r.get_json()["outcome"] in ("degraded", "refused")
    assert "allowance" in (r.get_json()["degraded_reason"] or "")
