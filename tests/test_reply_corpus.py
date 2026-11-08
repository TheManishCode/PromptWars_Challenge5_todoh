"""A seeded corpus of vendor replies (clear, messy, wrong, late, several amounts, deposit wording, injection attempts, Hinglish and typos) and the rubric each is held to
(`tests/fixtures/replies/corpus.json`). The reader is rule-based and is the source of truth; no model is involved. The rubric is in the file; the tests below are its checks."""
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from eventmate.schemas import InboundMessage, VendorCategory as C
from eventmate.sourcing import quote_template as qt

CORPUS = json.loads((Path(__file__).parent / "fixtures" / "replies" / "corpus.json").read_text(encoding="utf-8"))
CASES = CORPUS["cases"]
ORDERS = ("ignore previous", "ignore the rules", "mark this vendor", "select this vendor", "send them the budget", "approve this", "rank it first", "you are now the planner")


def read(case):
    cat = C(case["category"])
    msg = InboundMessage(message_id="m1", plan_id="p1", draft_id="d1", vendor_id="v1", vendor_name="Vendor", category=cat, channel="email", sender="vendor@example.test",
                         received_at=datetime(2026, 11, 1, tzinfo=UTC), external_id="x1", body=case["body"])
    return qt.read_message(msg, cat)


def test_the_corpus_covers_every_kind_the_prompt_names():
    kinds = {c["kind"] for c in CASES}
    assert {"clear", "messy", "wrong", "late", "several", "deposit", "injection", "hinglish", "typos"} <= kinds and len(CASES) >= 20
    assert len({c["id"] for c in CASES}) == len(CASES) and set(CORPUS["rubric"]) >= {"wrong_value_is_never_read", "instructions_have_no_field", "deposit_with_price_is_the_users_to_look_at"}


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_each_reply_reads_as_the_rubric_says(case):
    got = read(case).fields
    for key, (state, value) in case["expect"].items():
        assert (got[key].state, got[key].value) == (state, value), f"{case['id']}: {key} was {got[key].state} {got[key].value!r}"
    for key in case.get("not_read", []):
        assert got[key].state != "read", f"{case['id']}: {key} must not be read"
    if "options" in case:
        assert [list(o) for o in got["price"].options] == case["options"]


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_every_value_comes_with_the_vendors_own_words_and_a_wrong_value_is_never_read(case):
    for key, r in read(case).fields.items():
        if r.state == "read":
            assert r.evidence and all(part.strip() in case["body"].replace("  ", " ") or part.strip() in " ".join(case["body"].split()) for part in r.evidence.split(" | ") if part.strip()), (case["id"], key, r.evidence)
        if r.state == "unclear" and r.value is not None:
            pytest.fail(f"{case['id']}: an unclear field carries a value: {key}={r.value!r}")


@pytest.mark.parametrize("case", [c for c in CASES if c["kind"] == "injection"], ids=lambda c: c["id"])
def test_an_instruction_in_a_reply_never_becomes_a_value_that_decides_anything(case):
    got = read(case).fields
    for key in ("available", "price", "price_total", "package", "deposit", "valid_days", "taxes_included", "min_guests", "max_guests", "capacity"):
        r = got.get(key)
        if r is not None and r.state == "read":
            assert not any(o in (r.value or "").lower() for o in ORDERS), (case["id"], key, r.value)
    if "available" in case.get("not_read", []):
        assert got["available"].state != "read"
    if case.get("inclusions_is_text_only"):
        assert got["inclusions"].state in ("read", "missing") and "approve" in (got["inclusions"].value or "approve"), "an order on the free line is shown as the vendor's text and decides nothing"


def test_a_reply_that_mentions_a_deposit_in_words_is_for_the_user_to_type_never_read(tmp_path):
    case = next(c for c in CASES if c["id"] == "deposit-in-words")
    dep = read(case).fields["deposit"]
    assert dep.state == "unclear" and dep.value is None and "25000" in dep.evidence and "type the amount" in dep.note and dep.source == "free_text"


def test_no_reply_in_the_corpus_records_a_quote_or_a_payment_terms_on_its_own(tmp_path):
    """Rubric: nothing is a quote until the user confirms it. Every case is stored as a reply and read; nothing is recorded."""
    from datetime import timedelta

    from eventmate.messaging.channels import Inbound
    from tests.test_shared_quotes import ADDRESS, own_vendor, person

    em, chans, clock, brief = person(tmp_path, "corpus")
    biz = own_vendor(em, brief)
    d = em.request_all_quotes(brief, "Asha")[0][0]
    em.approve_outreach(brief, d.draft_id, d.content_hash)
    for n, case in enumerate(c for c in CASES if c["category"] == "caterer"):
        chans["email"].inbox = [Inbound(channel="email", sender=ADDRESS, external_id=f"<c{n}>", received_at=clock() + timedelta(minutes=5 + n), body=case["body"], in_reply_to=em.outreach(brief)[0].external_id)]
        em.poll_conversations()
    got = em.outreach_store.inbound(brief)
    assert len(got) >= 15, "the replies were stored and read"
    assert em.observations.quotes(biz.vendor_id) == [] and em.quoted.message_ids() == frozenset(), "and nothing was recorded or chosen"
    assert all(not a.comparable for a in em.quote_assessments(brief)), "none is comparable until the user confirms every line"
