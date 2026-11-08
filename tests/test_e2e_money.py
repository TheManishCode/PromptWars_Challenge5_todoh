"""The whole chain on fakes, in one test: enquiry, reply, line-by-line confirmation, comparison, choosing the vendor, deposit terms, an approval bound to the exact payment, how-to-pay,
the user's own "I paid", the budget ledger, and a refund. Every step that moves money is a named person's step; the app moves nothing."""
from datetime import timedelta

from tests.conftest import TODAY
from eventmate.messaging.channels import Inbound
from tests.conftest import EXAMPLE
from tests.test_payments import UPI
from tests.test_quote_promote import confirm_all
from tests.test_quote_readings import OK_FOR_40
from tests.test_shared_quotes import ADDRESS, own_vendor, person


def test_enquiry_to_reply_to_confirmed_quote_to_chosen_vendor_to_deposit_to_ledger(tmp_path):
    em, chans, clock, brief = person(tmp_path, "e2e")
    own_vendor(em, brief)
    d = em.request_all_quotes(brief, "Asha")[0][0]
    em.approve_outreach(brief, d.draft_id, d.content_hash)  # enquiry: approved against its exact text
    chans["email"].inbox = [Inbound(channel="email", sender=ADDRESS, external_id="<e1>", received_at=clock() + timedelta(minutes=5), body=OK_FOR_40, in_reply_to=em.outreach(brief)[0].external_id)]
    clock.advance(minutes=10)
    em.poll_conversations()  # reply
    mid = em.outreach_store.inbound(brief)[-1].message_id
    confirm_all(em, brief, mid)  # line by line, the money lines one at a time
    assert next(a for a in em.quote_assessments(brief) if a.message_id == mid).comparable
    assert em.compare_quotes(brief, closed=True).categories[0].chosen == ()
    em.promote_quote(brief, mid, "Asha")  # choose the vendor from this reply's own quote, for the brief's event
    assert mid in em.compare_quotes(brief, closed=True).categories[0].chosen
    plan_id = em.plan(EXAMPLE, today=TODAY).plan.plan_id  # (the page builds the plan through the commit queue)
    em.brief_links.link(brief[len("brief_"):], plan_id)
    assert em.ledger_summary(plan_id).paid_total == 0
    # the money terms: the total at the plan's guest count, the deposit the vendor stated, the vendor's own sentence
    t = em.propose_payment_terms(plan_id, mid, "Asha")
    assert (t.total_inr, t.deposit_inr, t.balance_inr) == (78_000, 10_000, 68_000) and "Deposit to book" in t.source_sentence
    t = em.payments.confirm_terms(plan_id, t.terms_id, "Asha", "Sri Hari Caterers", "upi", UPI, TODAY + timedelta(days=5), TODAY + timedelta(days=30))
    # approve exactly this payment, be told how to pay, pay it yourself, say so
    p = em.payments.propose_payment(plan_id, t.terms_id, "deposit", "Asha")
    p = em.payments.approve(plan_id, p.proposal_id, p.content_hash, "Asha")
    p = em.payments.instruct(plan_id, p.proposal_id, "manual", "Asha")
    assert p.status == "instructed" and em.ledger_summary(plan_id).paid_total == 0
    em.payments.mark_paid(plan_id, p.proposal_id, "Asha", "UTR 1")
    assert em.ledger_summary(plan_id).paid_total == 10_000
    # the balance, then a refund the vendor made
    b = em.payments.propose_payment(plan_id, t.terms_id, "balance", "Asha")
    em.payments.approve(plan_id, b.proposal_id, b.content_hash, "Asha")
    em.payments.instruct(plan_id, b.proposal_id, "manual", "Asha")
    em.payments.mark_paid(plan_id, b.proposal_id, "Asha", "UTR 2")
    em.payments.refund(plan_id, t.terms_id, "Asha", 3_000, "a dish was not served")
    assert em.ledger_summary(plan_id).paid_total == 75_000
    kinds = [e.kind for e in em.payments.proposals.events(plan_id)]
    assert kinds.count("approved") == 2 and kinds.count("paid") == 2 and "refund" in kinds
    # taking the choice back does not touch the money record
    em.unpromote_quote(plan_id, mid, "Asha")
    assert em.ledger_summary(plan_id).paid_total == 75_000 and em.payments.terms.get(plan_id, t.terms_id).paid_inr == 78_000
    assert len(chans["email"].sent) == 1, "the only thing the app sent in all of this is the enquiry the user approved"
