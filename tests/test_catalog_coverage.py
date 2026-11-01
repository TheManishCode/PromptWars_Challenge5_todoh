"""Problem alignment: weddings, birthdays and community events in the cities the product claims, planned end to end against the full demonstration catalogue.

Before this the only event that planned was a Pune engagement party; a Jaipur wedding or a Bengaluru birthday came back with no vendors at all."""

import os
from datetime import date

import pytest

from eventmate.agents.vendor import VendorRepository
from eventmate.service import EventMate

TODAY = date(2026, 11, 1)
CASES = [
    ("wedding", "Plan a wedding in Jaipur for 250 guests on December 18 under INR 1,500,000. Vegetarian catering, indoor venue, photographer."),
    ("wedding", "Plan a wedding in Hyderabad for 180 guests on February 12 under INR 1,200,000. Non-vegetarian catering, outdoor venue, photographer."),
    ("birthday", "Plan a birthday party in Bengaluru for 30 guests on March 13 under INR 160,000. Vegetarian catering, indoor venue, photographer."),
    ("birthday", "Plan a birthday party in Chennai for 60 guests on January 16 under INR 150,000. Vegetarian catering, indoor venue, photographer."),
    ("community", "Plan a community event, a Diwali mela, in Pune for 100 guests on December 12 under INR 400,000. Vegetarian catering, outdoor venue, photographer."),
    ("community", "Plan a cultural festival in Kolkata for 120 guests on January 22 under INR 300,000. Vegetarian catering, indoor venue, photographer."),
    ("engagement_party", "Plan a 120-person engagement party in Delhi on January 9 under INR 450,000. Vegetarian catering, indoor venue, photographer."),
    ("wedding", "Plan a wedding in Ahmedabad for 200 guests on November 20 under INR 1,000,000. Vegetarian catering, indoor venue, photographer."),
]


@pytest.fixture
def full(monkeypatch):
    monkeypatch.setenv("EVENTMATE_CATALOG", "full")


def test_the_full_catalogue_covers_nine_cities_with_every_category_and_keeps_the_seed(full):
    repo = VendorRepository()
    assert len(repo.cities()) >= 9
    from eventmate.schemas import VendorCategory as C

    for city in repo.cities():
        for cat in (C.VENUE, C.CATERER, C.PHOTOGRAPHER):
            assert [p for p in repo.providers(cat) if p.city == city], (city, cat)
    assert repo.get(C.CATERER, "cat-pune-annapurna") is not None  # the original records are still there


def test_the_unit_test_catalogue_is_still_the_small_fixed_one():
    assert os.environ["EVENTMATE_CATALOG"] == "seed" and len(VendorRepository().cities()) == 2


@pytest.mark.parametrize("kind,text", CASES)
def test_each_event_type_in_each_city_plans_with_real_vendors_and_a_budget_that_holds(full, tmp_path, kind, text):
    em = EventMate(plan_dir=tmp_path / "p", log_dir=tmp_path / "l", run_id="cov")
    out = em.plan(text, today=TODAY)
    assert out.status in ("planned", "replanned"), (out.status, getattr(out, "violations", None))
    plan = out.plan
    assert plan.request.event_type.value == kind
    assert plan.total_cost <= plan.request.budget_total
    assert {s.category.value for s in plan.selections} == {"venue", "caterer", "photographer"}
    assert all(s.vendor_id for s in plan.selections)


def test_the_catalogue_says_it_is_synthetic():
    import json
    from pathlib import Path

    import eventmate

    root = Path(eventmate.__file__).parent / "data"
    for name in ("catalog_vendors.json", "catalog_venues.json"):
        assert "ynthetic" in json.loads((root / name).read_text())["note"]
