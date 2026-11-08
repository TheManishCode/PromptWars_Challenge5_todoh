"""The outside services say how they are doing (WP5): used up, failing, not set up or fine, in words a person can act on, without a key, a query or the provider's own text. And the live
behavioural evals stop at a cap of model calls and never count a cut-short run as a pass."""
import json

import pytest

from eventmate.assistant import evals
from eventmate.assistant.agent import Assistant
from eventmate.integrations.health import HEALTH, QUOTA_FOR_S, ServiceHealth, Watched, classify
from eventmate.integrations.places import PlacesError, PlacesNotConfigured
from eventmate.llm import FallbackClient, LLMError
from eventmate.service import EventMate
from eventmate.web.app import create_app
from tests.conftest import TODAY
from tests.test_assistant import Scripted, answer


@pytest.fixture(autouse=True)
def fresh_health():
    HEALTH._services.clear()
    yield
    HEALTH._services.clear()


def state_of(name, configured=None):
    return next(s for s in HEALTH.snapshot(configured) if s["name"] == name)


# ---- classifying a failure

@pytest.mark.parametrize("exc,kind", [
    (PlacesNotConfigured("GOOGLE_PLACES_API_KEY is not set"), "not_configured"),
    (PlacesError("the Google Places quota is used up; try again later"), "quota"),
    (RuntimeError("HTTP 429 too many requests"), "quota"),
    (TimeoutError("timed out"), "unreachable"),
    (OSError("connection refused"), "unreachable"),
    (ValueError("the answer was not JSON"), "error"),
])
def test_a_failure_is_one_of_four_kinds(exc, kind):
    assert classify(exc) == kind


# ---- the states

def test_a_service_is_unknown_then_ok_then_degraded_and_quota_used_lapses_back_to_degraded():
    now = [1000.0]
    h = ServiceHealth(clock=lambda: now[0])
    snap = lambda: {s["name"]: s for s in h.snapshot()}  # noqa: E731
    assert snap()["weather"]["state"] == "unknown"
    h.ok("weather")
    assert snap()["weather"]["state"] == "ok" and snap()["weather"]["last_ok_seconds_ago"] == 0
    now[0] += 5
    h.failed("weather", "unreachable")
    assert snap()["weather"]["state"] == "degraded" and "could not be reached" in snap()["weather"]["note"]
    h.failed("places", "quota")
    assert snap()["places"]["state"] == "quota_used" and "missing, not absent" in snap()["places"]["note"]
    now[0] += QUOTA_FOR_S + 1
    assert snap()["places"]["state"] == "degraded", "after an hour it is simply asked again"
    h.ok("places")
    assert snap()["places"]["state"] == "ok" and snap()["places"]["failures"] == 1
    assert all("free_tier" in s and s["free_tier"] for s in h.snapshot())


def test_an_unconfigured_service_is_switched_off_not_broken():
    assert state_of("places", {"places": False})["state"] == "not_configured"
    HEALTH.failed("places", "not_configured")
    assert state_of("places")["state"] == "not_configured"


# ---- the wrapper

class Svc:
    def __init__(self):
        self.calls = 0

    def search(self, q):
        self.calls += 1
        if q == "boom":
            raise PlacesError("the Google Places quota is used up; key=SECRET123 q=boom")
        return [q]

    def other(self):
        return "plain"


def test_a_watched_call_records_the_outcome_and_changes_nothing_else():
    s = Watched(Svc(), "places", ("search",))
    assert s.search("a") == ["a"] and state_of("places")["state"] == "ok"
    with pytest.raises(PlacesError, match="SECRET123"):  # the exception is the service's own, raised untouched
        s.search("boom")
    assert state_of("places")["state"] == "quota_used"
    assert s.other() == "plain" and isinstance(s, Svc) and s.calls == 2
    assert "SECRET123" not in json.dumps(HEALTH.snapshot()), "what is kept is the kind, never the words"


def test_the_event_planner_reports_its_services_through_the_route_with_no_key_or_query_in_it(tmp_path):
    class Places:
        configured = True

        def search(self, kind, city, reviews=False, limit=10, websites=False):
            raise PlacesError("the Google Places quota is used up; try again later (key=AIzaSECRET, query=caterers in Mysuru)")

        def details(self, place_id, phone=False):
            return None

    em = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="svc", places=Places(), require_symbolic=False)
    with pytest.raises(PlacesError):
        em.places.search("caterer", "Mysuru")
    got = create_app(em, today_fn=lambda: TODAY, poll_seconds=0).test_client().get("/api/services")
    rows = {r["name"]: r for r in got.get_json()}
    assert got.status_code == 200 and rows["places"]["state"] == "quota_used" and rows["weather"]["state"] in ("unknown", "not_configured")
    text = got.get_data(as_text=True)
    assert "AIzaSECRET" not in text and "Mysuru" not in text


def test_the_model_router_records_a_limit_and_a_recovery():
    class Limited:
        name, model = "openrouter", "free-model"

        def complete(self, system, user, max_tokens):
            raise LLMError("rate limit exceeded (429) for key sk-SECRET")

    router = FallbackClient([Limited()])
    with pytest.raises(LLMError):
        router.complete("s", "u", 10)
    assert state_of("model")["state"] == "quota_used" and "sk-SECRET" not in json.dumps(HEALTH.snapshot())
    ok = FallbackClient([Scripted("fine")])
    ok.clients[0].name, ok.clients[0].model = "x", "y"
    assert ok.complete("s", "u", 10) == "fine" and state_of("model")["state"] == "ok"


# ---- the evals stop at a cap and a cut-short run is never a pass

def test_the_behavioral_evals_stop_between_cases_at_the_cap_and_are_not_a_pass(tmp_path):
    em = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="evalcap")
    model = Scripted(answer("It is in Pune.", "x"))
    cases = [{"id": f"c{i}", "role": "organizer", "question": "Which company is best overall, compare them?"} for i in range(6)]  # a question that needs the model
    report = evals.run_behavioral(Assistant(em, {"large": model, "small": model}), cases, max_calls=2)
    assert report["capped"] is True and report["cases"] < 6 and report["total"] == 6 and report["calls"] >= 2 and report["passed"] is False
    assert "cap of 2 model calls" in report["failures"][-1]
    with pytest.raises(SystemExit):
        evals.update_lock(report, "model")  # a cut-short run can never update the lock


def test_an_uncapped_enough_run_runs_every_case():
    assert evals.DEFAULT_MAX_CALLS >= 20, "the shipped cases (20) fit inside the default cap with room for tool steps"
