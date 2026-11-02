"""The docs and the UI must not claim more than the matching can show: stop requests and delivery notices are matched on forgeable mail headers."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def read(*parts):
    path = ROOT.joinpath(*parts)
    if parts[0] == "docs" and not path.is_file():
        pytest.skip("docs/ is not in this checkout (it is git-ignored for now)")  # the claims in the docs can only be checked where the docs are
    return path.read_text(encoding="utf-8")


def test_the_readme_does_not_call_stop_or_bounce_matching_authenticated():
    readme = read("README.md")
    assert "not authenticated" in readme and "can be forged" in readme
    for claim in ("verified stop", "authenticated stop", "authenticated bounce", "verified bounce", "confirmed by the mail server"):
        assert claim not in readme.lower()


def test_the_pages_never_call_stop_or_bounce_matching_authenticated():
    """Neither the landing page nor the planner has a do-not-contact list, so the claim that must hold is the negative one."""
    js = "".join(read("eventmate", "web", "static", n).lower() for n in ("app.js", "app.html", "landing.html"))
    for claim in ("verified stop", "authenticated stop", "authenticated bounce", "verified bounce", "confirmed by the mail server"):
        assert claim not in js


def test_the_demo_states_that_a_discovered_quote_does_not_update_the_plan():
    demo = read("docs", "DEMO.md")
    assert "does not update the plan" in demo and "Re-price this plan" in demo and "not one of the plan's vendors" in demo and "promotion step that does not exist yet" in demo
    assert "TEST VENDOR" in demo and "synthetic" in demo  # which vendors are which is also stated
