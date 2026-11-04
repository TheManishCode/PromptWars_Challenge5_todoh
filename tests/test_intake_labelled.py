"""The labelled intake cases (tools/intake_eval) as a regression gate.

The cases are generated, with labels that come from how each case is built (see tools/intake_eval/generate.py). Four splits are fixed files in tests/fixtures/intake_labelled:
``dev``, ``heldout`` and ``stress`` were used to fix the reader, so they are regression suites and say nothing about skill on new typing; ``heldout2`` and ``stress2`` were run once
before anything was changed in response, and are the only measures of unseen typing (their numbers are in the report). Everything below is a gate, not a goal: the first group must
stay at zero on every split, the second pins the silent-miss counts measured on the stress splits so that they cannot grow.
"""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("intake_eval_run", ROOT / "tools" / "intake_eval" / "run.py")
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)

SPLITS = ["dev", "heldout", "heldout2", "stress", "stress2"]
# silent allergy misses on a split with two typos per word and wrong first letters, as measured (stress2 before any change in response: 8 of 103). Pinned so they cannot grow.
MISS_CEILING = {"dev": 0, "heldout": 0, "heldout2": 0, "stress": 12, "stress2": 8}
DIET_MISS_CEILING = {"dev": 0, "heldout": 0, "heldout2": 0, "stress": 0, "stress2": 1}


@pytest.fixture(scope="module")
def scored():
    player = run.Player(ROOT, reload=False)
    out = {}
    for split in SPLITS:
        cases = json.loads((run.FIXTURES / f"{split}.json").read_text())
        results = [run.score_case(player, c) for c in cases]
        out[split] = (results, run.summarize(results))
    return out


@pytest.mark.parametrize("split", SPLITS)
def test_no_split_ever_sets_a_wrong_value_invents_an_allergy_or_offers_build_with_a_question_open(scored, split):
    results, s = scored[split]
    bad = [r["id"] for r in results if r["outcome"] == "wrong_value"]
    assert bad == [], bad
    assert s["ready_with_open_question"] == 0
    assert s["safety"]["allergy_invented"] == 0 and s["safety"]["invented_from_a_negation"] == 0 and s["safety"]["allergy_invented_from_lookalike"] == 0
    assert s["safety"]["false_alarm_on_lookalike"] == 0, "an ordinary word (a gallery, a pancake) must not raise an allergy question"
    assert s["false_asks_on_cases_that_should_just_be_read"] == 0 and s["exact_pass"] == s["exact_cases"], "a clear brief is read exactly and without a question"


@pytest.mark.parametrize("split", SPLITS)
def test_a_stated_allergy_or_diet_is_never_missed_silently_beyond_the_pinned_ceiling(scored, split):
    _, s = scored[split]
    assert s["safety"]["allergy_silently_missed"] <= MISS_CEILING[split], s["safety"]
    assert s["safety"]["diet_silently_missed"] <= DIET_MISS_CEILING[split], s["safety"]
    assert s["safety"]["unnamed_silently_missed"] == 0, "a serious reaction or 'no peanuts' with no food named is always asked about"


@pytest.mark.parametrize("split", ["dev", "heldout", "heldout2"])
def test_the_splits_with_ordinary_typing_have_no_silent_misses_and_every_question_can_be_answered(scored, split):
    results, s = scored[split]
    assert s["dropped_silently"] == 0 and s["ask_cases_that_asked"] == s["ask_cases"]
    assert s["safety"]["removal_outcomes"] == {"asked": 4}, "asking to remove a stated allergy is a question; it is neither ignored nor done silently"


def test_the_recorded_baseline_of_the_old_reader_shows_the_same_gates_failing():
    """The scorer can fail. The reader as it was before this work (commit 7d5ec4b) is not importable here, so the table recorded from a real run of it (tools/intake_eval/BASELINE.md) is checked."""
    rows = [line.split("|")[1:-1] for line in (ROOT / "tools" / "intake_eval" / "BASELINE.md").read_text().splitlines() if line.startswith("| ") and "split" not in line]
    assert [r[0].strip() for r in rows] == SPLITS
    for r in rows:
        wrong, dropped, missed = int(r[3]), int(r[4]), int(r[5].split("/")[0])
        assert wrong > 0 and dropped > 0 and missed > 0, r
