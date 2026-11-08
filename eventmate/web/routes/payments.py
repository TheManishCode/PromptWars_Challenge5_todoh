"""Money terms, payment approvals and the plain record of what was paid (WP4). Nothing here moves money except what a person approves, once, by an approved provider; the default only says what to pay."""

from __future__ import annotations

from flask import jsonify

from eventmate.web.base import (
    PayApproveBody,
    PayInstructBody,
    PayNoteBody,
    PayPaidBody,
    PayProposeBody,
    RefundBody,
    TermsConfirmBody,
    TermsProposeBody,
)


def register(app, k):
    body, em, lock, out, pid = k.body, k.em, k.lock, k.out, k.pid

    @app.get("/api/plans/<plan_id>/payments")
    def payments_get(plan_id):
        """The money terms, the payments, the events and the providers that are switched on, for this plan."""
        plan_id = pid(plan_id)
        with lock:
            return jsonify(em.payment_view(plan_id))

    @app.post("/api/plans/<plan_id>/payments/terms")
    def payments_terms_propose(plan_id):
        """Make money terms from a quote you confirmed in full: the deposit, the balance and the vendor's own sentence. A proposal only."""
        plan_id, b = pid(plan_id), body(TermsProposeBody)
        with lock:
            t = em.propose_payment_terms(plan_id, b.message_id, b.by)
        return out(t, 201)

    @app.post("/api/plans/<plan_id>/payments/terms/<terms_id>/confirm")
    def payments_terms_confirm(plan_id, terms_id):
        """Say who is paid, how and when. Until you do, no payment can be proposed."""
        plan_id, terms_id, b = pid(plan_id), pid(terms_id), body(TermsConfirmBody)
        with lock:
            t = em.payments.confirm_terms(plan_id, terms_id, b.by, b.payee_name, b.payee_method, b.payee_handle, b.deposit_due, b.balance_due, b.expect_version)
        return out(t)

    @app.post("/api/plans/<plan_id>/payments/terms/<terms_id>/refund")
    def payments_refund(plan_id, terms_id):
        """Write down a refund the vendor made. It goes in the budget ledger; nothing is sent back by the app."""
        plan_id, terms_id, b = pid(plan_id), pid(terms_id), body(RefundBody)
        with lock:
            t = em.payments.refund(plan_id, terms_id, b.by, b.amount_inr, b.note)
        return out(t)

    @app.post("/api/plans/<plan_id>/payments/terms/<terms_id>/note")
    def payments_note(plan_id, terms_id):
        """A dispute, a call, a promise, in your own words, with your name and the time. It changes no money."""
        plan_id, terms_id, b = pid(plan_id), pid(terms_id), body(PayNoteBody)
        with lock:
            em.payments.note(plan_id, b.by, b.note, terms_id)
        return jsonify({"ok": True}), 201

    @app.post("/api/plans/<plan_id>/payments")
    def payments_propose(plan_id):
        """Propose one payment (the deposit or the balance) for confirmed terms. It shows exactly what it is and carries a hash; nothing is paid."""
        plan_id, b = pid(plan_id), body(PayProposeBody)
        with lock:
            p = em.payments.propose_payment(plan_id, b.terms_id, b.kind, b.by)
        return out(p, 201)

    @app.post("/api/plans/<plan_id>/payments/<proposal_id>/approve")
    def payments_approve(plan_id, proposal_id):
        """Approve exactly this payment: the hash you send must be the hash of what is stored. It lasts 24 hours."""
        plan_id, proposal_id, b = pid(plan_id), pid(proposal_id), body(PayApproveBody)
        with lock:
            p = em.payments.approve(plan_id, proposal_id, b.content_hash, b.by)
        return out(p)

    @app.post("/api/plans/<plan_id>/payments/<proposal_id>/instruct")
    def payments_instruct(plan_id, proposal_id):
        """Carry out an approved payment once. The manual way writes what to pay; a provider that is switched on (off by default) submits it."""
        plan_id, proposal_id, b = pid(plan_id), pid(proposal_id), body(PayInstructBody)
        with lock:
            p = em.payments.instruct(plan_id, proposal_id, b.provider, b.by)
        return out(p)

    @app.get("/api/plans/<plan_id>/payments/<proposal_id>/status")
    def payments_status(plan_id, proposal_id):
        """What the provider says about a submitted payment, beside what you confirmed. It records nothing."""
        plan_id, proposal_id = pid(plan_id), pid(proposal_id)
        with lock:
            return jsonify(em.payments.provider_says(plan_id, proposal_id))

    @app.post("/api/plans/<plan_id>/payments/<proposal_id>/paid")
    def payments_paid(plan_id, proposal_id):
        """You say it was paid, with the reference you were given. This is what puts it in the budget ledger."""
        plan_id, proposal_id, b = pid(plan_id), pid(proposal_id), body(PayPaidBody)
        with lock:
            p = em.payments.mark_paid(plan_id, proposal_id, b.by, b.reference)
        return out(p)

    @app.post("/api/plans/<plan_id>/payments/<proposal_id>/failed")
    def payments_failed(plan_id, proposal_id):
        plan_id, proposal_id, b = pid(plan_id), pid(proposal_id), body(PayNoteBody)
        with lock:
            p = em.payments.mark_failed(plan_id, proposal_id, b.by, b.note)
        return out(p)

    @app.post("/api/plans/<plan_id>/payments/<proposal_id>/cancel")
    def payments_cancel(plan_id, proposal_id):
        plan_id, proposal_id, b = pid(plan_id), pid(proposal_id), body(PayNoteBody)
        with lock:
            p = em.payments.cancel(plan_id, proposal_id, b.by)
        return out(p)
