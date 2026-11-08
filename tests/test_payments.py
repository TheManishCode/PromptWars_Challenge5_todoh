"""Deposits and balances (WP4): terms from a confirmed quote, a payment approved against the hash of exactly what it is, carried out once, recorded paid only when the user says so, refunds and
failures as explicit steps. Nothing here moves money except by an approved provider, and the default provider only says what to pay."""
import multiprocessing
from datetime import timedelta

import pytest

from eventmate.messaging.channels import Inbound
from eventmate.payments import approvals as ap
from eventmate.payments.providers import ManualProvider, PayPalConfig, PayPalSandboxProvider, ProviderError, SANDBOX_HOST, provider_from_env
from eventmate.web.app import create_app
from tests.conftest import EXAMPLE, TODAY
from tests.test_quote_promote import confirm_all
from tests.test_quote_readings import OK_FOR_40
from tests.test_shared_quotes import ADDRESS, own_vendor, person

UPI = "sriharicaterers@okbank"


def world(tmp_path, name="pay"):
    """A caterer's reply confirmed in full under a brief that has become a plan (120 guests: 650 a plate is INR 78,000, deposit INR 10,000)."""
    em, chans, clock, brief = person(tmp_path, name)
    own_vendor(em, brief)
    d = em.request_all_quotes(brief, "Asha")[0][0]
    em.approve_outreach(brief, d.draft_id, d.content_hash)
    chans["email"].inbox = [Inbound(channel="email", sender=ADDRESS, external_id="<p1>", received_at=clock() + timedelta(minutes=5), body=OK_FOR_40, in_reply_to=em.outreach(brief)[0].external_id)]
    clock.advance(minutes=10)
    em.poll_conversations()
    mid = em.outreach_store.inbound(brief)[-1].message_id
    confirm_all(em, brief, mid)
    plan_id = em.plan(EXAMPLE, today=TODAY).plan.plan_id
    em.brief_links.link(brief[len("brief_"):], plan_id)
    return em, plan_id, mid, clock


def confirmed_terms(em, plan_id, mid, **kw):
    t = em.propose_payment_terms(plan_id, mid, "Asha")
    args = {"payee_name": "Sri Hari Caterers", "payee_method": "upi", "payee_handle": UPI, "deposit_due": TODAY + timedelta(days=5), "balance_due": TODAY + timedelta(days=30)}
    args.update(kw)
    return em.payments.confirm_terms(plan_id, t.terms_id, "Asha", **args)


def approved(em, plan_id, mid, kind="deposit"):
    t = confirmed_terms(em, plan_id, mid) if not em.payments.terms.all(plan_id) else em.payments.terms.all(plan_id)[0]
    p = em.payments.propose_payment(plan_id, t.terms_id, kind, "Asha")
    return t, em.payments.approve(plan_id, p.proposal_id, p.content_hash, "Asha")


# ---- terms

def test_terms_are_made_only_from_a_quote_confirmed_in_full_and_show_the_deposit_apart_from_the_total(tmp_path):
    em, plan_id, mid, clock = world(tmp_path)
    t = em.propose_payment_terms(plan_id, mid, "Asha")
    assert (t.total_inr, t.deposit_inr, t.balance_inr, t.status) == (78_000, 10_000, 68_000, "proposed") and "Deposit to book" in t.source_sentence
    assert em.propose_payment_terms(plan_id, mid, "Asha").terms_id == t.terms_id, "asking twice is one set of terms"
    assert em.payments.proposals.events(plan_id)[0].kind == "terms_proposed"
    c = em.compare_quotes(em.brief_links.brief_of(plan_id), closed=True).categories[0]
    ranked = c.groups[0].ranked[0] if c.groups else c.open_quotes[0]
    assert ranked.deposit_inr == 10_000 and ranked.total_inr == 650 * 120, "the deposit sits beside the total (the plan's 120 guests) and is not added to it"


def test_a_quote_not_confirmed_in_full_or_a_brief_with_no_plan_gets_no_terms(tmp_path):
    em, chans, clock, brief = person(tmp_path, "partial")
    own_vendor(em, brief)
    d = em.request_all_quotes(brief, "Asha")[0][0]
    em.approve_outreach(brief, d.draft_id, d.content_hash)
    chans["email"].inbox = [Inbound(channel="email", sender=ADDRESS, external_id="<p1>", received_at=clock() + timedelta(minutes=5), body=OK_FOR_40, in_reply_to=em.outreach(brief)[0].external_id)]
    clock.advance(minutes=10)
    em.poll_conversations()
    mid = em.outreach_store.inbound(brief)[-1].message_id
    plan_id = em.plan(EXAMPLE, today=TODAY).plan.plan_id
    em.brief_links.link(brief[len("brief_"):], plan_id)
    with pytest.raises(ValueError, match="confirm every line"):
        em.propose_payment_terms(plan_id, mid, "Asha")
    with pytest.raises(FileNotFoundError):  # a brief is not a plan: nothing is paid for it
        em.propose_payment_terms(brief, mid, "Asha")


@pytest.mark.parametrize("kw,words", [
    ({"payee_method": "upi", "payee_handle": ""}, "UPI id"),
    ({"payee_method": "paypal", "payee_handle": "not-an-email"}, "PayPal email"),
    ({"payee_method": "bank_transfer", "payee_handle": "A/C 123456789012 HDFC"}, "no account"),
    ({"deposit_due": TODAY - timedelta(days=1)}, "already passed"),
    ({"balance_due": TODAY + timedelta(days=1), "deposit_due": TODAY + timedelta(days=9)}, "balance cannot be due before"),
])
def test_terms_refuse_what_cannot_be_acted_on_and_keep_no_account_numbers(tmp_path, kw, words):
    em, plan_id, mid, clock = world(tmp_path)
    t = em.propose_payment_terms(plan_id, mid, "Asha")
    args = {"payee_name": "Sri Hari Caterers", "payee_method": "upi", "payee_handle": UPI, "deposit_due": TODAY + timedelta(days=5), "balance_due": TODAY + timedelta(days=30), **kw}
    with pytest.raises(ValueError, match=words):
        em.payments.confirm_terms(plan_id, t.terms_id, "Asha", **args)
    assert em.payments.terms.get(plan_id, t.terms_id).status == "proposed"


def test_terms_changed_in_another_tab_are_refused_and_a_payment_in_progress_locks_them(tmp_path):
    em, plan_id, mid, clock = world(tmp_path)
    t = confirmed_terms(em, plan_id, mid)
    with pytest.raises(ValueError, match="changed in the meantime"):
        em.payments.confirm_terms(plan_id, t.terms_id, "Asha", "Other", "cash", expect_version=t.version - 1)
    p = em.payments.propose_payment(plan_id, t.terms_id, "deposit", "Asha")
    with pytest.raises(ValueError, match="in progress"):
        em.payments.confirm_terms(plan_id, t.terms_id, "Asha", "Someone Else", "cash")
    em.payments.cancel(plan_id, p.proposal_id, "Asha")
    assert em.payments.confirm_terms(plan_id, t.terms_id, "Asha", "Someone Else", "cash").payee_name == "Someone Else"


# ---- proposals and approval

def test_a_payment_needs_confirmed_terms_and_the_deposit_comes_before_the_balance_and_there_is_one_live_payment_each(tmp_path):
    em, plan_id, mid, clock = world(tmp_path)
    t = em.propose_payment_terms(plan_id, mid, "Asha")
    with pytest.raises(ValueError, match="confirm who is paid"):
        em.payments.propose_payment(plan_id, t.terms_id, "deposit", "Asha")
    t = em.payments.confirm_terms(plan_id, t.terms_id, "Asha", "Sri Hari Caterers", "upi", UPI)
    with pytest.raises(ValueError, match="deposit comes first"):
        em.payments.propose_payment(plan_id, t.terms_id, "balance", "Asha")
    p = em.payments.propose_payment(plan_id, t.terms_id, "deposit", "Asha")
    assert (p.amount_inr, p.kind, p.currency, p.payee_name) == (10_000, "deposit", "INR", "Sri Hari Caterers") and len(p.content_hash) == 64
    with pytest.raises(ValueError, match="already in progress"):
        em.payments.propose_payment(plan_id, t.terms_id, "deposit", "Asha")


def test_approval_is_bound_to_the_hash_of_exactly_this_payment(tmp_path):
    em, plan_id, mid, clock = world(tmp_path)
    t = confirmed_terms(em, plan_id, mid)
    p = em.payments.propose_payment(plan_id, t.terms_id, "deposit", "Asha")
    with pytest.raises(ValueError, match="not what this payment is now"):
        em.payments.approve(plan_id, p.proposal_id, "0" * 64, "Asha")
    assert em.payments.proposals.get(plan_id, p.proposal_id).status == "proposed"
    wrong = ap.payment_hash(plan_id, t.terms_id, t.version, "deposit", 11_000, "INR", "Sri Hari Caterers", "upi", UPI, mid)  # the hash of a different amount
    with pytest.raises(ValueError, match="not what this payment is now"):
        em.payments.approve(plan_id, p.proposal_id, wrong, "Asha")
    ok = em.payments.approve(plan_id, p.proposal_id, p.content_hash, "Asha")
    assert ok.status == "approved" and ok.approved_by == "Asha"
    assert [e.kind for e in em.payments.proposals.events(plan_id) if e.proposal_id == p.proposal_id] == ["proposed", "refused", "refused", "approved"]


def test_a_record_that_was_edited_fails_its_own_hash(tmp_path):
    em, plan_id, mid, clock = world(tmp_path)
    t = confirmed_terms(em, plan_id, mid)
    p = em.payments.propose_payment(plan_id, t.terms_id, "deposit", "Asha")
    with pytest.raises(ValueError, match="content_hash"):
        ap.PaymentProposal.model_validate({**p.model_dump(), "amount_inr": 99_999})


def test_an_approval_lapses_after_24_hours_and_when_the_terms_move_on(tmp_path):
    em, plan_id, mid, clock = world(tmp_path)
    t = confirmed_terms(em, plan_id, mid)
    p = em.payments.propose_payment(plan_id, t.terms_id, "deposit", "Asha")
    clock.advance(hours=25)
    with pytest.raises(ValueError, match="run out"):
        em.payments.approve(plan_id, p.proposal_id, p.content_hash, "Asha")
    assert em.payments.proposals.get(plan_id, p.proposal_id).status == "expired"
    p2 = em.payments.propose_payment(plan_id, t.terms_id, "deposit", "Asha")  # a fresh proposal
    em.payments.terms.update(plan_id, t.terms_id, payee_name="A Different Payee")  # the terms move on (another tab, another person) after it was proposed
    with pytest.raises(ValueError, match="terms changed"):
        em.payments.approve(plan_id, p2.proposal_id, p2.content_hash, "Asha")


# ---- carrying out

def test_the_manual_way_says_exactly_what_to_pay_and_moves_nothing_and_happens_once(tmp_path):
    em, plan_id, mid, clock = world(tmp_path)
    t, p = approved(em, plan_id, mid)
    done = em.payments.instruct(plan_id, p.proposal_id, "manual", "Asha")
    assert done.status == "instructed" and "INR 10,000" in done.instruction and "Sri Hari Caterers" in done.instruction and UPI in done.instruction
    assert "does not move this money" in done.instruction and "does not guarantee" in done.instruction
    assert em.ledger_summary(plan_id).paid_total == 0, "an instruction is not a payment"
    with pytest.raises(ValueError, match="instructed"):
        em.payments.instruct(plan_id, p.proposal_id, "manual", "Asha")
    with pytest.raises(ValueError, match="not switched on"):
        em.payments.instruct(plan_id, p.proposal_id, "paypal_sandbox", "Asha")


def test_an_unapproved_payment_is_never_carried_out(tmp_path):
    em, plan_id, mid, clock = world(tmp_path)
    t = confirmed_terms(em, plan_id, mid)
    p = em.payments.propose_payment(plan_id, t.terms_id, "deposit", "Asha")
    with pytest.raises(ValueError, match="proposed"):
        em.payments.instruct(plan_id, p.proposal_id, "manual", "Asha")


def _claim(args):
    root, plan_id, proposal_id = args
    from eventmate.payments.approvals import ProposalStore

    try:
        ProposalStore(root).transition(plan_id, proposal_id, ("approved",), status="executing", provider="manual")
        return "won"
    except ValueError as exc:
        return str(exc)


def test_two_processes_carrying_out_one_approved_payment_one_wins(tmp_path):
    em, plan_id, mid, clock = world(tmp_path)
    t, p = approved(em, plan_id, mid)
    with multiprocessing.get_context("fork").Pool(4) as pool:
        results = pool.map(_claim, [(em.store.root, plan_id, p.proposal_id)] * 8)
    assert results.count("won") == 1 and all("executing" in r for r in results if r != "won"), results


# ---- recording what was paid

def test_paid_is_recorded_only_when_the_user_says_so_and_only_then_reaches_the_ledger(tmp_path):
    em, plan_id, mid, clock = world(tmp_path)
    t, p = approved(em, plan_id, mid)
    em.payments.instruct(plan_id, p.proposal_id, "manual", "Asha")
    done = em.payments.mark_paid(plan_id, p.proposal_id, "Asha", "UTR 4421")
    assert done.status == "paid" and done.paid_reference == "UTR 4421"
    assert em.ledger_summary(plan_id).paid_total == 10_000
    assert em.payments.terms.get(plan_id, t.terms_id).status == "deposit_paid" and em.payments.terms.get(plan_id, t.terms_id).paid_inr == 10_000
    with pytest.raises(ValueError, match="paid"):
        em.payments.mark_paid(plan_id, p.proposal_id, "Asha", "again")
    assert em.ledger_summary(plan_id).paid_total == 10_000, "twice is still once"


def test_the_balance_follows_the_deposit_and_a_full_payment_closes_the_terms(tmp_path):
    em, plan_id, mid, clock = world(tmp_path)
    t, p = approved(em, plan_id, mid)
    em.payments.instruct(plan_id, p.proposal_id, "manual", "Asha")
    em.payments.mark_paid(plan_id, p.proposal_id, "Asha", "r1")
    b = em.payments.propose_payment(plan_id, t.terms_id, "balance", "Asha")
    assert b.amount_inr == 68_000
    em.payments.approve(plan_id, b.proposal_id, b.content_hash, "Asha")
    em.payments.instruct(plan_id, b.proposal_id, "manual", "Asha")
    em.payments.mark_paid(plan_id, b.proposal_id, "Asha", "r2")
    assert em.payments.terms.get(plan_id, t.terms_id).status == "paid" and em.ledger_summary(plan_id).paid_total == 78_000
    with pytest.raises(ValueError, match="already paid|in progress|terms are paid"):
        em.payments.propose_payment(plan_id, t.terms_id, "balance", "Asha")


def test_a_refund_is_an_explicit_step_into_the_ledger_and_cannot_exceed_what_was_paid(tmp_path):
    em, plan_id, mid, clock = world(tmp_path)
    t, p = approved(em, plan_id, mid)
    em.payments.instruct(plan_id, p.proposal_id, "manual", "Asha")
    em.payments.mark_paid(plan_id, p.proposal_id, "Asha", "r1")
    with pytest.raises(ValueError, match="more than"):
        em.payments.refund(plan_id, t.terms_id, "Asha", 10_001)
    em.payments.refund(plan_id, t.terms_id, "Asha", 4_000, "vendor returned part")
    assert em.ledger_summary(plan_id).paid_total == 6_000
    assert em.payments.terms.get(plan_id, t.terms_id).refunded_inr == 4_000 and em.payments.terms.get(plan_id, t.terms_id).status == "deposit_paid"
    em.payments.refund(plan_id, t.terms_id, "Asha", 6_000)
    assert em.payments.terms.get(plan_id, t.terms_id).status == "refunded"


def test_a_failed_or_cancelled_payment_and_a_dispute_are_steps_with_a_name_and_a_time(tmp_path):
    em, plan_id, mid, clock = world(tmp_path)
    t, p = approved(em, plan_id, mid)
    em.payments.instruct(plan_id, p.proposal_id, "manual", "Asha")
    em.payments.mark_failed(plan_id, p.proposal_id, "Asha", "the bank declined it")
    em.payments.note(plan_id, "Asha", "vendor says the deposit was never received", t.terms_id)
    assert em.ledger_summary(plan_id).paid_total == 0 and em.payments.proposals.get(plan_id, p.proposal_id).status == "failed"
    again = em.payments.propose_payment(plan_id, t.terms_id, "deposit", "Asha")  # a failed one can be proposed afresh
    em.payments.cancel(plan_id, again.proposal_id, "Asha")
    log = [(e.kind, e.by) for e in em.payments.proposals.events(plan_id)]
    assert ("failed", "Asha") in log and ("note", "Asha") in log and ("cancelled", "Asha") in log
    with pytest.raises(ValueError):
        em.payments.note(plan_id, "", "anonymous")


# ---- providers: the sandbox adapter is off by default, sandbox only, and never a way round the approval

def test_only_the_manual_provider_exists_unless_the_sandbox_flag_and_credentials_are_set():
    assert list(provider_from_env({})) == ["manual"]
    assert list(provider_from_env({"EVENTMATE_PAYPAL_MODE": "sandbox"})) == ["manual"], "no credentials, no adapter"
    assert list(provider_from_env({"EVENTMATE_PAYPAL_MODE": "live", "EVENTMATE_PAYPAL_CLIENT_ID": "x", "EVENTMATE_PAYPAL_CLIENT_SECRET": "y"})) == ["manual"], "there is no live mode"
    assert sorted(provider_from_env({"EVENTMATE_PAYPAL_MODE": "sandbox", "EVENTMATE_PAYPAL_CLIENT_ID": "x", "EVENTMATE_PAYPAL_CLIENT_SECRET": "y"})) == ["manual", "paypal_sandbox"]
    assert SANDBOX_HOST == "api-m.sandbox.paypal.com"
    assert "x" not in repr(PayPalConfig("sandbox", "x" * 5, "y" * 5)).replace("sandbox", "")


class FakePayPal:
    def __init__(self, fail=False, status=201):
        self.calls, self.fail, self.status = [], fail, status

    def __call__(self, method, path, headers, body):
        self.calls.append((method, path, headers, body))
        if path == "/v1/oauth2/token":
            return 200, {"access_token": "tok-sandbox", "expires_in": 3000}
        if self.fail:
            raise OSError("connection reset")
        if method == "POST":
            return self.status, {"batch_header": {"payout_batch_id": "BATCH123", "batch_status": "PENDING"}}
        return 200, {"batch_header": {"batch_status": "SUCCESS"}}


def paypal_world(tmp_path, fake):
    em, plan_id, mid, clock = world(tmp_path)
    cfg = PayPalConfig("sandbox", "cid", "csecret")
    assert em.payments.external_allowed is False, "a workspace that shares a store (a hosted visitor's) starts with no external provider"
    em.payments.external_allowed = True  # this one stands for the operator's own workspace
    em.payments.providers = lambda: {"manual": ManualProvider(), "paypal_sandbox": PayPalSandboxProvider(cfg, fake)}
    t = confirmed_terms(em, plan_id, mid, payee_method="paypal", payee_handle="vendor@example.test")
    p = em.payments.propose_payment(plan_id, t.terms_id, "deposit", "Asha")
    return em, plan_id, t, em.payments.approve(plan_id, p.proposal_id, p.content_hash, "Asha")


def test_the_sandbox_adapter_submits_an_approved_payment_once_with_the_proposal_id_as_its_idempotency_key(tmp_path):
    fake = FakePayPal()
    em, plan_id, t, p = paypal_world(tmp_path, fake)
    done = em.payments.instruct(plan_id, p.proposal_id, "paypal_sandbox", "Asha")
    assert done.status == "submitted" and done.provider_ref == "BATCH123" and "No real money moved" in done.instruction
    post = next(c for c in fake.calls if c[1] == "/v1/payments/payouts")
    assert post[2]["PayPal-Request-Id"] == p.proposal_id and post[2]["Authorization"] == "Bearer tok-sandbox" and b"vendor@example.test" in post[3] and b'"10000.00"' in post[3]
    assert em.ledger_summary(plan_id).paid_total == 0, "a submission is not a recorded payment"
    says = em.payments.provider_says(plan_id, p.proposal_id)
    assert says["provider_says"] == "completed" and says["you_confirmed"] is False, "the provider's report sits beside what you confirmed and records nothing"
    assert em.ledger_summary(plan_id).paid_total == 0
    with pytest.raises(ValueError, match="submitted"):
        em.payments.instruct(plan_id, p.proposal_id, "paypal_sandbox", "Asha")


def payout_posts(fake):
    return [c for c in fake.calls if c[0] == "POST" and c[1] == "/v1/payments/payouts"]


def test_a_refusal_is_a_failure_that_frees_the_slot_and_names_no_secret(tmp_path):
    fake = FakePayPal(status=422)
    em, plan_id, t, p = paypal_world(tmp_path, fake)
    done = em.payments.instruct(plan_id, p.proposal_id, "paypal_sandbox", "Asha")
    assert done.status == "failed" and "refused" in done.error and "csecret" not in done.model_dump_json()
    with pytest.raises(ValueError, match="failed"):
        em.payments.instruct(plan_id, p.proposal_id, "paypal_sandbox", "Asha")
    assert em.payments.propose_payment(plan_id, t.terms_id, "deposit", "Asha").status == "proposed", "a refused payment did not happen: a new one may be proposed"


@pytest.mark.parametrize("lost", ["reset", 500, 503, 429])
def test_a_lost_answer_is_an_unknown_outcome_that_is_never_retried_and_never_paid_twice(tmp_path, lost):
    """R-020: PayPal accepted the payout and the answer was lost. The app must not call it failed, must not let a second payment be proposed, and must not post it again."""
    class LosesTheAnswer(FakePayPal):
        def __call__(self, method, path, headers, body):
            self.calls.append((method, path, headers, body))  # PayPal received it...
            if path == "/v1/oauth2/token":
                return 200, {"access_token": "tok-sandbox", "expires_in": 3000}
            if lost == "reset":
                raise OSError("connection reset")  # ...and the answer was lost
            return lost, {}

    fake = LosesTheAnswer()
    em, plan_id, t, p = paypal_world(tmp_path, fake)
    done = em.payments.instruct(plan_id, p.proposal_id, "paypal_sandbox", "Asha")
    assert done.status == "outcome_unknown" and "may or may not" in done.error and p.proposal_id in done.error and "csecret" not in done.model_dump_json()
    assert len(payout_posts(fake)) == 1
    with pytest.raises(ValueError, match="already in progress"):
        em.payments.propose_payment(plan_id, t.terms_id, "deposit", "Asha")
    for again in (lambda: em.payments.approve(plan_id, p.proposal_id, p.content_hash, "Asha"), lambda: em.payments.instruct(plan_id, p.proposal_id, "paypal_sandbox", "Asha"),
                  lambda: em.payments.cancel(plan_id, p.proposal_id, "Asha")):
        with pytest.raises(ValueError, match="outcome_unknown"):
            again()
    assert len(payout_posts(fake)) == 1, "nothing was posted a second time"
    says = em.payments.provider_says(plan_id, p.proposal_id)
    assert says["provider_says"] == "unknown" and p.proposal_id in says["detail"] and says["you_confirmed"] is False
    assert em.ledger_summary(plan_id).paid_total == 0, "unknown is not paid"
    assert [e.kind for e in em.payments.proposals.events(plan_id) if e.proposal_id == p.proposal_id][-1] == "unknown"


def test_the_user_settles_an_unknown_outcome_either_way_with_their_name(tmp_path):
    class LosesTheAnswer(FakePayPal):
        def __call__(self, method, path, headers, body):
            self.calls.append((method, path, headers, body))
            if path == "/v1/oauth2/token":
                return 200, {"access_token": "tok-sandbox", "expires_in": 3000}
            raise OSError("connection reset")

    fake = LosesTheAnswer()
    em, plan_id, t, p = paypal_world(tmp_path, fake)
    em.payments.instruct(plan_id, p.proposal_id, "paypal_sandbox", "Asha")
    em.payments.mark_failed(plan_id, p.proposal_id, "Asha", "checked the sandbox: no payout")  # it did not go through
    assert em.ledger_summary(plan_id).paid_total == 0
    again = em.payments.propose_payment(plan_id, t.terms_id, "deposit", "Asha")
    em.payments.approve(plan_id, again.proposal_id, again.content_hash, "Asha")
    em.payments.instruct(plan_id, again.proposal_id, "paypal_sandbox", "Asha")  # unknown again
    em.payments.mark_paid(plan_id, again.proposal_id, "Asha", "BATCH-FOUND")  # it did go through
    assert em.ledger_summary(plan_id).paid_total == 10_000 and em.payments.proposals.get(plan_id, again.proposal_id).paid_reference == "BATCH-FOUND"
    assert [(e.kind, e.by) for e in em.payments.proposals.events(plan_id) if e.kind in ("failed", "paid")] == [("failed", "Asha"), ("paid", "Asha")]


def test_a_payment_a_dead_process_was_carrying_out_becomes_unknown_never_failed_and_is_not_carried_out_again(tmp_path):
    fake = FakePayPal()
    em, plan_id, t, p = paypal_world(tmp_path, fake)
    em.payments.proposals.transition(plan_id, p.proposal_id, ("approved",), status="executing", provider="paypal_sandbox", executing_since=em.payments.now)  # claimed, and then the process died
    assert em.payments.settle_stuck(plan_id) == 0, "inside the lease it may still be running"
    clock = em.outreach_store.now
    clock.advance(minutes=6)
    view = em.payment_view(plan_id)
    assert [x["status"] for x in view["payments"]] == ["outcome_unknown"] and "stopped" in view["payments"][0]["error"]
    with pytest.raises(ValueError, match="outcome_unknown"):
        em.payments.instruct(plan_id, p.proposal_id, "paypal_sandbox", "Asha")
    with pytest.raises(ValueError, match="already in progress"):
        em.payments.propose_payment(plan_id, t.terms_id, "deposit", "Asha")
    assert payout_posts(fake) == []


# ---- R-021: freshness is checked again at the moment of carrying out

def test_an_approval_that_ran_out_before_it_was_carried_out_is_refused_expired_and_no_provider_is_called(tmp_path):
    fake = FakePayPal()
    em, plan_id, t, p = paypal_world(tmp_path, fake)
    em.outreach_store.now.advance(hours=25)
    with pytest.raises(ValueError, match="run out"):
        em.payments.instruct(plan_id, p.proposal_id, "paypal_sandbox", "Asha")
    assert em.payments.proposals.get(plan_id, p.proposal_id).status == "expired"
    assert fake.calls == [], "the provider was never asked: not even for a token"


def test_terms_that_moved_on_after_the_approval_stop_it_being_carried_out(tmp_path):
    fake = FakePayPal()
    em, plan_id, t, p = paypal_world(tmp_path, fake)
    em.payments.terms.update(plan_id, t.terms_id, payee_name="Someone Else Entirely")  # another path moves the terms after the approval
    with pytest.raises(ValueError, match="terms changed"):
        em.payments.instruct(plan_id, p.proposal_id, "paypal_sandbox", "Asha")
    assert em.payments.proposals.get(plan_id, p.proposal_id).status == "cancelled" and fake.calls == []


def test_paypal_pays_only_a_payee_with_a_paypal_address_and_only_when_switched_on():
    prov = PayPalSandboxProvider(PayPalConfig("off", "", ""), FakePayPal())
    with pytest.raises(ProviderError, match="switched off"):
        prov.create_instruction(__import__("eventmate.payments.providers", fromlist=["PaymentRequest"]).PaymentRequest("a" * 32, "p", "deposit", 100, "INR", "V", "upi", "x@y", "m"))
    on = PayPalSandboxProvider(PayPalConfig("sandbox", "c", "s"), FakePayPal())
    with pytest.raises(ProviderError, match="PayPal email"):
        on.create_instruction(__import__("eventmate.payments.providers", fromlist=["PaymentRequest"]).PaymentRequest("a" * 32, "p", "deposit", 100, "INR", "V", "upi", "x@y", "m"))


def test_a_visitors_workspace_can_never_move_money_through_a_provider(tmp_path):
    fake = FakePayPal()
    em, plan_id, t, p = paypal_world(tmp_path, fake)
    em.payments.external_allowed = False  # what a hosted visitor's workspace has (and what `person()` builds by default)
    with pytest.raises(ValueError, match="visitor"):
        em.payments.instruct(plan_id, p.proposal_id, "paypal_sandbox", "Asha")
    assert not [c for c in fake.calls if c[1].startswith("/v1/payments")], "nothing was sent to the provider"
    assert em.payments.instruct(plan_id, p.proposal_id, "manual", "Asha").status == "instructed", "the manual instruction is still there: it moves nothing"


# ---- the routes, the view and deletion

def test_the_routes_run_the_whole_path_and_say_what_the_app_does_not_do(tmp_path):
    em, plan_id, mid, clock = world(tmp_path)
    c = create_app(em, today_fn=lambda: TODAY, poll_seconds=0).test_client()
    h = {"X-EventMate-CSRF": c.get("/api/session").get_json()["csrf"]}
    base = f"/api/plans/{plan_id}/payments"
    t = c.post(base + "/terms", json={"message_id": mid, "by": "Asha"}, headers=h).get_json()
    assert t["deposit_inr"] == 10_000 and t["status"] == "proposed" and "not a booking" in t["notice"]
    assert c.post(base, json={"terms_id": t["terms_id"], "kind": "deposit", "by": "Asha"}, headers=h).status_code == 400
    assert c.post(f"{base}/terms/{t['terms_id']}/confirm", json={"by": "Asha", "payee_name": "Sri Hari Caterers", "payee_method": "upi", "payee_handle": UPI,
                                                                   "deposit_due": (TODAY + timedelta(days=3)).isoformat()}, headers=h).status_code == 200
    p = c.post(base, json={"terms_id": t["terms_id"], "kind": "deposit", "by": "Asha"}, headers=h).get_json()
    assert c.post(f"{base}/{p['proposal_id']}/approve", json={"content_hash": "1" * 64, "by": "Asha"}, headers=h).status_code == 400
    assert c.post(f"{base}/{p['proposal_id']}/approve", json={"content_hash": p["content_hash"], "by": "Asha"}, headers=h).status_code == 200
    ins = c.post(f"{base}/{p['proposal_id']}/instruct", json={}, headers=h).get_json()
    assert ins["status"] == "instructed" and "does not move this money" in ins["instruction"]
    assert c.post(f"{base}/{p['proposal_id']}/paid", json={"by": "Asha", "reference": "UTR 1"}, headers=h).status_code == 200
    view = c.get(base).get_json()
    assert view["terms"][0]["status"] == "deposit_paid" and [e["kind"] for e in view["events"]][-1] == "paid" and view["external"] == [] and "does not hold or move your money" in view["notice"]
    assert c.post(base + "/terms", json={"message_id": mid}, headers=h).status_code == 422


def test_deleting_a_plan_deletes_its_payment_terms_payments_and_events(tmp_path):
    em, plan_id, mid, clock = world(tmp_path)
    t, p = approved(em, plan_id, mid)
    root = em.store.root
    assert (root / "payment_terms" / f"payment_terms-{plan_id}.jsonl").exists() and (root / "payments" / "events" / f"payment_event-{plan_id}.jsonl").exists()
    em.delete_plan(plan_id)
    assert not any((root / "payment_terms").glob("*")) or not (root / "payment_terms" / f"payment_terms-{plan_id}.jsonl").exists()
    assert not (root / "payments" / f"payment_proposal-{plan_id}.jsonl").exists() and not (root / "payments" / "events" / f"payment_event-{plan_id}.jsonl").exists()
