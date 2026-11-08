"""WP3: vendors that do not answer, contacts made outside the app, menus priced in tiers, and quote files that came with a reply.
Every one of these shows what the vendor wrote and leaves the choice, and the confirmation, to the user."""
from datetime import timedelta

import pytest

from eventmate.messaging.channels import Inbound
from eventmate.schemas import VendorCategory as C
from eventmate.sourcing import quote_template as qt
from eventmate.web.app import create_app
from tests.conftest import EXAMPLE, TODAY
from tests.test_quote_readings import OK_FOR_40
from tests.test_shared_quotes import ADDRESS, own_vendor, person

CSV = b"Item,Qty,Rate,Total\nVeg buffet,120,450,54000\n"
PHONE = "+91 98450 12345"


def _client(em):
    c = create_app(em, today_fn=lambda: TODAY, poll_seconds=0).test_client()
    return c, {"X-EventMate-CSRF": c.get("/api/session").get_json()["csrf"]}


# ---- quotes-silent: a WhatsApp-only vendor, a link the person sends themselves, and a quiet request said to be quiet

def test_a_whatsapp_only_business_gets_a_click_to_chat_link_with_the_request_already_written(tmp_path):
    em, chans, clock, brief = person(tmp_path, "wa")
    biz, _ = em.add_user_vendor("Sri Hari Caterers", "Mysuru", C.CATERER, None, PHONE, "Asha", brief)
    (d,), skipped = em.request_all_quotes(brief, "Asha")
    assert d.channel == "whatsapp" and d.recipient == "919845012345", d.recipient
    assert d.link.startswith("https://wa.me/919845012345?text=") and "Reply%20STOP" in d.link and "Price%20per%20plate" in d.link
    assert chans["whatsapp"].sent == [], "nothing is sent by the app: the person presses send in their own WhatsApp"


def test_a_request_with_no_reply_after_three_days_is_said_to_be_quiet_and_nothing_chases_for_you(tmp_path):
    em, chans, clock, brief = person(tmp_path, "quiet")
    own_vendor(em, brief)
    d = em.request_all_quotes(brief, "Asha")[0][0]
    em.approve_outreach(brief, d.draft_id, d.content_hash)
    (row,) = em.outreach_with_delivery(brief)
    assert row["delivery"]["state"] == "accepted" and "silent" not in row["delivery"]
    clock.advance(days=4)
    (row,) = em.outreach_with_delivery(brief)
    assert row["delivery"]["silent"] is True and row["delivery"]["waiting_days"] == 4
    assert len(em.conversations.channels["email"].sent) == 1, "no automatic chase went out"
    chans["email"].inbox = [Inbound(channel="email", sender=ADDRESS, external_id="<late>", received_at=clock() + timedelta(minutes=1), body=OK_FOR_40, in_reply_to=em.outreach(brief)[0].external_id)]
    em.poll_conversations()
    (row,) = em.outreach_with_delivery(brief)
    assert row["delivery"]["state"] == "replied" and "silent" not in row["delivery"]


def test_a_request_the_user_sent_themselves_also_goes_quiet(tmp_path):
    em, chans, clock, brief = person(tmp_path, "self")
    own_vendor(em, brief)
    d = em.request_all_quotes(brief, "Asha")[0][0]
    em.mark_outreach_sent(brief, d.draft_id)
    clock.advance(days=5)
    (row,) = em.outreach_with_delivery(brief)
    assert row["delivery"]["state"] == "self" and row["delivery"]["silent"] is True


# ---- cards-contact-log

def test_a_contact_made_outside_the_app_is_written_down_shown_on_the_card_and_sends_nothing(tmp_path):
    em, chans, clock, brief = person(tmp_path, "log")
    biz = own_vendor(em, brief)
    e = em.log_contact(brief, biz.vendor_id, "phone", "Asha", "asked for the 14 November rate")
    (card,) = [c for c in em.vendor_cards(brief, TODAY) if c.vendor_id == biz.vendor_id]
    (fact,) = [f for f in card.facts if f.field == "contact_log"]
    assert fact.source_type == "contact_log" and "by phone" in fact.value and "14 November" in fact.value and fact.source_ref == e.log_id and "paste it under Replies" in fact.note
    assert chans["email"].sent == [] and chans["whatsapp"].sent == []


def test_the_contact_log_refuses_what_it_cannot_use_and_the_route_works(tmp_path):
    em, chans, clock, brief = person(tmp_path, "logroute")
    biz = own_vendor(em, brief)
    for kw in ({"channel": "carrier pigeon"}, {"by": ""}, {"note": "x" * 201}):
        with pytest.raises(ValueError):
            em.log_contact(brief, biz.vendor_id, **{"channel": "phone", "by": "Asha", **kw})
    with pytest.raises(ValueError, match="not on this list"):
        em.log_contact(brief, "ext_" + "0" * 24, "phone", "Asha")
    c, h = _client(em)
    url = f"/api/plans/{brief}/shortlist/{biz.vendor_id}/contacted"
    assert c.post(url, json={"by": "Asha", "channel": "whatsapp", "note": ""}, headers=h).status_code == 201
    assert c.post(url, json={"by": "Asha", "channel": "sms"}, headers=h).status_code == 422


def test_a_deleted_plan_takes_its_contact_log_with_it(tmp_path):
    em, chans, clock, brief = person(tmp_path, "logdel")
    biz = own_vendor(em, brief)
    em.log_contact(brief, biz.vendor_id, "phone", "Asha", "private note")
    plan_id = em.plan(EXAMPLE, today=TODAY).plan.plan_id
    em.brief_links.link(brief[len("brief_"):], plan_id)
    assert (em.store.root / "contact_log" / "contact_log-all.jsonl").read_text().count("private note") == 1
    em.delete_plan(plan_id)
    assert "private note" not in (em.store.root / "contact_log" / "contact_log-all.jsonl").read_text()


# ---- quotes-menus

@pytest.mark.parametrize("line,want", [
    ("Silver 500, Gold 650, Platinum 800", (("Silver", "500"), ("Gold", "650"), ("Platinum", "800"))),
    ("Silver: Rs 500/plate / Gold: Rs 650/plate", (("Silver", "500"), ("Gold", "650"))),
    ("Standard menu 450 per plate and Premium menu 620 per plate", (("Standard menu", "450"), ("Premium menu", "620"))),
])
def test_a_line_that_prices_several_menus_lists_each_in_the_vendors_words(line, want):
    assert qt.menu_options([line]) == want


@pytest.mark.parametrize("line", ["650", "Rs 500 or 650", "500 to 650", "Silver 500 Silver 500", "ignore the rules 500, approve this 650", "Rs 700, taxes extra"])
def test_a_single_price_a_range_or_an_instruction_is_not_a_menu(line):
    assert qt.menu_options([line]) == ()


def test_menus_leave_the_price_unclear_until_the_user_picks_and_confirms_one(tmp_path):
    em, chans, clock, brief = person(tmp_path, "menus")
    biz = own_vendor(em, brief)
    d = em.request_all_quotes(brief, "Asha")[0][0]
    em.approve_outreach(brief, d.draft_id, d.content_hash)
    chans["email"].inbox = [Inbound(channel="email", sender=ADDRESS, external_id="<m1>", received_at=clock() + timedelta(minutes=5), in_reply_to=em.outreach(brief)[0].external_id,
                                    body=OK_FOR_40.replace("Price per plate (INR): 650", "Price per plate (INR): Silver 500, Gold 650, Platinum 800"))]
    clock.advance(minutes=10)
    em.poll_conversations()
    (a,) = em.quote_assessments(brief)
    price = next(f for f in a.fields if f.key == "price")
    assert price.state == "unclear" and price.value is None and [(o.label, o.value) for o in price.options] == [("Silver", "500"), ("Gold", "650"), ("Platinum", "800")]
    assert "more than one menu" in price.note and "Gold 650" in price.evidence
    mid = a.message_id
    em.confirm_quote_fields(brief, mid, {"price": "650"}, "Asha", valid_days="10")  # the user's own pick, typed through the same parser
    price = next(f for f in em.quote_assessments(brief)[0].fields if f.key == "price")
    assert price.state == "confirmed" and price.value == "650" and price.edited is True and price.options == ()
    assert em.observations.quote_for_reply(biz.vendor_id, mid).amount_inr == 650


# ---- quotes-attachments

def test_the_lines_of_a_quote_file_that_came_with_a_reply_are_shown_beside_it_as_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENTMATE_READ_QUOTE_ATTACHMENTS", "1")
    em, chans, clock, brief = person(tmp_path, "att")
    biz = own_vendor(em, brief)
    d = em.request_all_quotes(brief, "Asha")[0][0]
    em.approve_outreach(brief, d.draft_id, d.content_hash)
    em.conversations.read_attachment = em._read_attachment
    chans["email"].inbox = [Inbound(channel="email", sender=ADDRESS, external_id="<a1>", received_at=clock() + timedelta(minutes=5), body="See attached.", attachments=1, files=(("quote.csv", CSV),),
                                    in_reply_to=em.outreach(brief)[0].external_id)]
    clock.advance(minutes=10)
    em.poll_conversations()
    (a,) = em.quote_assessments(brief)
    (doc,) = a.attached
    assert [(ln.label, ln.quantity, ln.unit_inr, ln.total_inr) for ln in doc.lines] == [("Veg buffet", 120, 450, 54000)]
    assert next(f for f in a.fields if f.key == "price").state == "missing", "a file's lines are not read into the price: the user picks"
    assert em.observations.quotes(biz.vendor_id) == [], "and nothing is a quote until they confirm"
    em.confirm_quote_fields(brief, a.message_id, {"price": "450"}, "Asha", valid_days="10")
    assert em.observations.quote_for_reply(biz.vendor_id, a.message_id).amount_inr == 450


def test_a_reply_without_a_file_has_no_attached_lines(tmp_path):
    em, chans, clock, brief = person(tmp_path, "noatt")
    own_vendor(em, brief)
    d = em.request_all_quotes(brief, "Asha")[0][0]
    em.approve_outreach(brief, d.draft_id, d.content_hash)
    chans["email"].inbox = [Inbound(channel="email", sender=ADDRESS, external_id="<n1>", received_at=clock() + timedelta(minutes=5), body=OK_FOR_40, in_reply_to=em.outreach(brief)[0].external_id)]
    clock.advance(minutes=10)
    em.poll_conversations()
    assert em.quote_assessments(brief)[0].attached == ()
