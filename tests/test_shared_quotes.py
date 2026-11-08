"""A vendor's last confirmed quote is shown to the next person who adds the same vendor, while their own request is out. It is shown, never priced from, and never says who it was for."""

import json

from datetime import timedelta

from eventmate.messaging.channels import Inbound
from eventmate.messaging.conversations import SendPolicy
from eventmate.pricing import shared_quotes
from eventmate.schemas import VendorCategory as C
from eventmate.service import EventMate
from tests.conftest import TODAY
from tests.test_brief_sourcing import MYSURU
from tests.test_conversations import Clock, FakeChannel
from tests.test_quote_readings import OK_FOR_40

ADDRESS = "kitchen@srihari.example"


def person(tmp_path, name):
    clock, chans = Clock(), {"email": FakeChannel("email"), "whatsapp": FakeChannel("whatsapp", False, False)}
    em = EventMate(log_dir=tmp_path / name / "l", plan_dir=tmp_path / name / "p", run_id=name, channels=chans, require_symbolic=False, shared_dir=tmp_path / "shared")
    em.outreach_store.now = em.conversations.now = em.quote_confirmations.now = clock
    em.conversations.policy = SendPolicy(per_vendor_per_day=20, per_day=200, whatsapp_gap_s=0)
    em._clock(TODAY)
    brief = "brief_" + em.intake_turn([MYSURU], today=TODAY).staged_id
    return em, chans, clock, brief


def own_vendor(em, brief, name="Sri Hari Caterers", email=ADDRESS):
    biz, _ = em.add_user_vendor(name, "Mysuru", C.CATERER, email, None, "Asha", plan_id=brief)
    return biz


def quoted(em, chans, clock, brief, biz):
    d = em.request_all_quotes(brief, "Asha")[0][0]
    em.approve_outreach(brief, d.draft_id, d.content_hash)
    chans["email"].inbox = [Inbound(channel="email", sender=ADDRESS, external_id="<r1>", received_at=clock() + timedelta(minutes=5), body=OK_FOR_40, in_reply_to=em.outreach(brief)[0].external_id)]
    clock.advance(minutes=10)
    em.poll_conversations()
    mid = em.outreach_store.inbound(brief)[-1].message_id
    em.confirm_quote_fields(brief, mid, dict.fromkeys(("available", "taxes_included", "min_guests", "max_guests", "diets")), "Asha")
    em.confirm_quote_fields(brief, mid, {"price": None}, "Asha", valid_days="10")


def facts(em, brief, biz, field):
    (card,) = [c for c in em.vendor_cards(brief, TODAY) if c.vendor_id == biz.vendor_id]
    return [f for f in card.facts if f.field == field]


def test_the_next_person_to_add_the_same_vendor_sees_the_last_quote_and_not_who_it_was_for(tmp_path):
    first = person(tmp_path, "first")
    quoted(*first, own_vendor(first[0], first[3]))
    em, _, _, brief = person(tmp_path, "second")
    biz = own_vendor(em, brief, name="Sri Hari Caterers (Mysuru)", email=ADDRESS.upper())  # the same vendor, written another way: the address is what matches
    (e,) = facts(em, brief, biz, "earlier_quote")
    assert e.value == "INR 650 per plate for 40 guests" and e.source_type == "earlier_quote" and e.source_ref is None
    assert "orientation only" in e.note and "Asha" not in e.model_dump_json()
    assert not facts(em, brief, biz, "quote"), "it is not this person's quote and is not priced from"
    assert em.observations.quotes(biz.vendor_id) == []


def test_a_quote_for_another_date_is_said_to_be_old_for_this_event(tmp_path):
    first = person(tmp_path, "first")
    quoted(*first, own_vendor(first[0], first[3]))
    em, _, _, brief = person(tmp_path, "second")
    (e,) = facts(em, brief, own_vendor(em, brief), "earlier_quote")
    assert e.status == "valid"  # same guests and date as the brief it was quoted for
    em2, _, _, brief2 = person(tmp_path, "third")
    scope = em2._scope(brief2)
    shared = em2.shared_earlier(own_vendor(em2, brief2).vendor_id, TODAY + timedelta(days=60), 40, scope.request.event_date)
    assert shared and any("days old" in r or "expired" in r for r in shared[0]["old"])


def test_a_different_vendor_or_a_test_vendor_is_never_matched(tmp_path):
    first = person(tmp_path, "first")
    quoted(*first, own_vendor(first[0], first[3]))
    em, _, _, brief = person(tmp_path, "second")
    other = own_vendor(em, brief, name="Another Kitchen", email="other@kitchen.example")
    assert not facts(em, brief, other, "earlier_quote")
    test_biz, _ = em.add_test_vendor("Test Caterer", "Mysuru", C.CATERER, ADDRESS, True, "Asha", plan_id=brief)
    assert not facts(em, brief, test_biz, "earlier_quote") and em.shared_earlier(test_biz.vendor_id, TODAY) == []


def test_what_is_shared_holds_no_name_no_contact_and_no_words(tmp_path):
    first = person(tmp_path, "first")
    quoted(*first, own_vendor(first[0], first[3]))
    text = "".join(p.read_text() for p in (tmp_path / "shared").rglob("*.jsonl"))
    assert json.loads(text.splitlines()[0])["amount_inr"] == 650
    for private in ("Asha", "Sri Hari", ADDRESS, "kitchen", "owner", "Mysuru brief"):
        assert private not in text
    assert all(k.startswith("email:") for k in json.loads(text.splitlines()[0])["keys"])


def test_turned_off_nothing_is_written_or_shown(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENTMATE_SHARED_QUOTES", "0")
    assert not shared_quotes.enabled()
    first = person(tmp_path, "first")
    quoted(*first, own_vendor(first[0], first[3]))
    assert first[0].shared_quotes is None and not (tmp_path / "shared").exists()
    em, _, _, brief = person(tmp_path, "second")
    assert not facts(em, brief, own_vendor(em, brief), "earlier_quote")


def test_the_same_confirmation_twice_is_one_record(tmp_path):
    first = person(tmp_path, "first")
    em, chans, clock, brief = first
    biz = own_vendor(em, brief)
    quoted(*first, biz)
    mid = em.outreach_store.inbound(brief)[-1].message_id
    em.confirm_quote_fields(brief, mid, {"price": None}, "Asha", valid_days="10")
    assert len(em.shared_quotes.earlier(em._shared_keys(biz.vendor_id), C.CATERER)) == 1


def test_two_real_workspaces_share_the_quote_store_and_nothing_else(tmp_path):
    from datetime import UTC, datetime, date
    from tests.workspace_helpers import shared_app

    _, registry = shared_app(tmp_path)
    (a, _), (b, _) = registry.create("198.51.100.1"), registry.create("198.51.100.2")
    assert a.em.shared_quotes.root == b.em.shared_quotes.root == tmp_path / "data" / "shared" / "shared_quotes"
    assert a.em.store.root != b.em.store.root
    brief_a = "brief_" + a.em.intake_turn([MYSURU], today=TODAY).staged_id
    biz_a = own_vendor(a.em, brief_a)
    a.em.shared_quotes.add(a.em._shared_keys(biz_a.vendor_id), C.CATERER, 700, "per_plate", 40, date(2026, 11, 14), datetime(2026, 11, 1, tzinfo=UTC), date(2026, 11, 20), True)
    b.em._clock(TODAY)
    brief_b = "brief_" + b.em.intake_turn([MYSURU], today=TODAY).staged_id
    biz_b = own_vendor(b.em, brief_b)
    assert [e["amount_inr"] for e in b.em.shared_earlier(biz_b.vendor_id, TODAY, 40, date(2026, 11, 14))] == [700]
    assert not list(b.em.store.root.rglob("*srihari*")) and b.em.shared_earlier(own_vendor(b.em, brief_b, name="Other", email="o@o.example").vendor_id, TODAY) == []


def test_two_different_businesses_with_the_same_name_and_city_are_not_the_same_vendor(tmp_path):
    first = person(tmp_path, "first")
    quoted(*first, own_vendor(first[0], first[3]))
    em, _, _, brief = person(tmp_path, "second")
    other = own_vendor(em, brief, name="Sri Hari Caterers", email="someone-else@other.example")
    assert not facts(em, brief, other, "earlier_quote"), "a name is typed by the user and is not unique: only the address identifies a vendor"


def test_a_backup_carries_the_shared_quotes_and_a_restore_brings_them_back(tmp_path):
    from eventmate.backup import make_backup, restore_backup, verify_backup
    data = tmp_path / "data"
    first = person(data, "first")  # its shared store is data/shared, next to where the workspaces live
    quoted(*first, own_vendor(first[0], first[3]))
    (data / "workspaces").mkdir(exist_ok=True)
    out = make_backup(data, tmp_path / "bk")
    assert verify_backup(out) == [] and (out / "shared" / "shared_quotes" / "shared_quote-all.jsonl").is_file()
    restore_backup(out, tmp_path / "back")
    assert (tmp_path / "back" / "shared" / "shared_quotes" / "shared_quote-all.jsonl").read_text() == (data / "shared" / "shared_quotes" / "shared_quote-all.jsonl").read_text()


def test_what_is_kept_is_coarse_and_old_quotes_are_dropped(tmp_path):
    from datetime import datetime, UTC
    from eventmate.pricing.shared_quotes import SharedQuoteStore
    now = datetime(2027, 3, 1, 12, tzinfo=UTC)
    store = SharedQuoteStore(tmp_path, now=lambda: now)
    kept = store.add(["email:" + "a" * 16], C.CATERER, 650, "per_plate", 43, None, datetime(2027, 2, 20, 14, 37, 12, tzinfo=UTC), now.date(), None)
    assert (kept.guest_count, kept.observed_at.hour, kept.observed_at.minute) == (40, 0, 0), "the day and a size band, not the minute and the exact count"
    store.add(["email:" + "b" * 16], C.CATERER, 500, "per_plate", 40, None, datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 10, tzinfo=UTC).date(), None)
    assert [q.amount_inr for q in store.earlier(["email:" + "b" * 16], C.CATERER)] == [], "out of date for more than 90 days: not shown"
    store.add(["email:" + "c" * 16], C.CATERER, 700, "per_plate", 40, None, datetime(2027, 2, 21, tzinfo=UTC), now.date(), None)  # a write prunes
    assert sorted(q.amount_inr for q in store._all()) == [650, 700]
