"""The evidence ledger (architecture L6): who may create which state, forward-only trust, expiry, and what a hard constraint may rest on."""

from datetime import datetime, timedelta, UTC

import pytest
from pydantic import ValidationError

from eventmate.evidence.ledger import EvidenceLedger, FactState as S

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        self.t += timedelta(seconds=1)
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def led(tmp_path, clock):
    return EvidenceLedger(tmp_path, now=clock)


def test_a_model_can_only_suggest_and_a_person_alone_confirms_or_books(led):
    led.record("v1", "price", 450, S.SUGGESTED, "llm:gpt")
    for state, source in [(S.USER_CONFIRMED, "llm:gpt"), (S.VERIFIED, "llm:gpt"), (S.BOOKED, "web:abc"), (S.USER_CONFIRMED, "tool:osm"), (S.VERIFIED, "user_input"),
                          (S.BOOKED, "doc:m1"), (S.EXTRACTED, "user_input"), (S.DISCOVERED, "code:x")]:
        with pytest.raises(ValidationError):
            led.record("v1", "price", 450, state, source)
    led.record("v1", "price", 450, S.USER_CONFIRMED, "user_input")
    led.record("v1", "price", 450, S.EXTRACTED, "doc:m1")


def test_an_unknown_value_is_allowed_until_it_would_be_trusted(led):
    led.record("v1", "price", None, S.DISCOVERED, "tool:osm")
    with pytest.raises(ValidationError):
        led.record("v1", "price", None, S.BOOKED, "user_input")


def test_lower_evidence_never_displaces_higher_and_among_equals_the_latest_wins(led):
    led.record("v1", "price", 450, S.USER_CONFIRMED, "user_input")
    led.record("v1", "price", 999, S.EXTRACTED, "doc:m2")
    assert led.best("v1", "price").value == 450
    led.record("v1", "price", 500, S.USER_CONFIRMED, "user_input")
    best = led.best("v1", "price")
    assert best.value == 500 and best.supersedes  # the change of mind names what it replaced
    assert len(led.records("v1", "price")) == 3  # nothing was edited or deleted


def test_only_code_verifies_and_only_after_the_check_passes(led):
    led.record("v1", "price", 450, S.EXTRACTED, "doc:m1")
    with pytest.raises(ValueError, match="user has confirmed"):
        led.verify("v1", "price", lambda r: True, "amount_in_text")
    led.record("v1", "price", 450, S.USER_CONFIRMED, "user_input")
    with pytest.raises(ValueError, match="failed"):
        led.verify("v1", "price", lambda r: False, "amount_in_text")
    assert led.best("v1", "price").state is S.USER_CONFIRMED
    v = led.verify("v1", "price", lambda r: r.value == 450, "amount_in_text")
    assert v.state is S.VERIFIED and v.source == "code:amount_in_text"


def test_a_hard_constraint_rests_on_verified_or_booked_only(led):
    led.record("v1", "price", 450, S.DISCOVERED, "tool:places")
    assert led.usable_for_hard_constraint("v1", "price") is None
    led.record("v1", "price", 450, S.USER_CONFIRMED, "user_input")
    assert led.usable_for_hard_constraint("v1", "price") is None  # "you told us" is shown, not trusted
    led.verify("v1", "price", lambda r: True, "ok")
    assert led.usable_for_hard_constraint("v1", "price").state is S.VERIFIED
    led.record("v2", "capacity", 200, S.VERIFIED, "seed:vendors")
    assert led.usable_for_hard_constraint("v2", "capacity")


def test_an_expired_quote_stops_counting_and_the_older_fact_shows_again(led, clock):
    led.record("v1", "price", 400, S.EXTRACTED, "doc:m1")
    led.record("v1", "price", 450, S.USER_CONFIRMED, "user_input", expires_at=clock.t + timedelta(seconds=5))
    assert led.best("v1", "price").value == 450
    clock.t += timedelta(days=1)
    assert led.best("v1", "price").value == 400


def test_the_evidence_tab_groups_current_records_by_state(led):
    led.record("v1", "email", "a@x.example", S.DISCOVERED, "web:p1", url="https://x.example/contact")
    led.record("v1", "price", 450, S.USER_CONFIRMED, "user_input")
    led.record("v1", "price", 440, S.EXTRACTED, "doc:m1")
    g = led.by_state("v1")
    assert [r.field for r in g["discovered"]] == ["email"] and [r.value for r in g["user_confirmed"]] == [450] and g["extracted"] == []


def test_the_file_is_append_only_owner_only_and_survives_a_reopen(led, tmp_path, clock):
    led.record("v1", "price", 450, S.EXTRACTED, "doc:m1")
    again = EvidenceLedger(tmp_path, now=clock)
    assert again.best("v1", "price").value == 450
    assert (led._file.path.stat().st_mode & 0o777) == 0o600


@pytest.mark.parametrize("subject", ["../x", "a b", "", "x" * 65])
def test_hostile_ids_are_refused(led, subject):
    with pytest.raises(ValidationError):
        led.record(subject, "price", 1, S.EXTRACTED, "doc:m1")


def test_a_ledger_that_never_records_creates_nothing_on_disk(tmp_path, clock):
    led = EvidenceLedger(tmp_path, now=clock)
    assert led.records() == [] and led.best("v1", "price") is None and led.by_state()["booked"] == []
    assert list(tmp_path.iterdir()) == []  # an empty workspace stays empty (it is swept as idle; its size is measured)
