"""The docs and the UI must not claim more than the matching can show: stop requests and delivery notices are matched on forgeable mail headers."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(*parts):
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def test_the_readme_does_not_call_stop_or_bounce_matching_authenticated():
    readme = read("README.md")
    assert "not authenticated" in readme and "can be forged" in readme
    for claim in ("verified stop", "authenticated stop", "authenticated bounce", "verified bounce", "confirmed by the mail server"):
        assert claim not in readme.lower()


def test_the_room_never_calls_stop_or_bounce_matching_authenticated():
    """The classic page (removed) said next to the do-not-contact list that neither is authenticated. The room has no such list, so the claim that must hold is the negative one."""
    js = read("eventmate", "web", "static", "arcade.js").lower()
    for claim in ("verified stop", "authenticated stop", "authenticated bounce", "verified bounce", "confirmed by the mail server"):
        assert claim not in js


def test_the_demo_states_that_a_discovered_quote_does_not_update_the_plan():
    demo = read("docs", "DEMO.md")
    assert "does not update the plan" in demo and "Re-price this plan" in demo and "not one of the plan's vendors" in demo and "promotion step that does not exist yet" in demo
    assert "TEST VENDOR" in demo and "synthetic" in demo  # which vendors are which is also stated
