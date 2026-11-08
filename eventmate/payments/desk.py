"""The payment desk: terms, proposals, approvals, instructions and what the user confirms afterwards (WP4).

Rules, each of them tested with the guard broken:

* terms are proposed from a vendor reply whose price and deposit the user has confirmed, and confirmed by the user with who is paid and how; deposit and balance always add up to the total;
* a payment is for exactly one (terms, kind) and never more than what is left to pay; a second live one beside it is refused;
* approval is bound to a hash of the exact amount, currency, payee and quote, expires after 24 hours, and is refused if the terms changed after it was proposed;
* an approved payment is carried out once: it is claimed under a cross-process lock and a provider that fails or has an unknown outcome is never retried by itself;
* the app records a payment as paid only when the user says so; a provider's own report is shown beside it and never records anything;
* refunds, failures and disputes are explicit steps by a named person, each a line in the event log and, for money, an entry in the budget ledger;
* an external provider is never used from a hosted visitor's workspace.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

from eventmate.payments import approvals as ap
from eventmate.payments.providers import Instruction, PaymentProvider, PaymentRequest, ProviderError
from eventmate.payments.terms import PaymentTerms, TermsStore
from eventmate.schemas import VendorCategory


class PaymentDesk:
    def __init__(self, terms: TermsStore, proposals: ap.ProposalStore, providers: Callable[[], dict[str, PaymentProvider]], ledger_add: Callable[..., object], today: Callable[[], date],
                 external_allowed: bool = True):
        self.terms, self.proposals, self.providers, self._ledger_add, self.today = terms, proposals, providers, ledger_add, today
        self.external_allowed = external_allowed  # False in a hosted visitor's workspace: only the manual provider, which moves nothing

    @property
    def now(self) -> datetime:
        return self.proposals.now()

    # ------------------------------------------------------------ terms

    def propose_terms(self, plan_id: str, category: VendorCategory, vendor_id: str, vendor_name: str, message_id: str, total_inr: int, deposit_inr: int, source_sentence: str, by: str) -> PaymentTerms:
        if deposit_inr > total_inr:
            raise ValueError("the deposit is more than the whole price: confirm the lines again")
        t = self.terms.propose(plan_id, category=category, vendor_id=vendor_id, vendor_name=vendor_name, message_id=message_id, total_inr=total_inr, deposit_inr=deposit_inr,
                               balance_inr=total_inr - deposit_inr, source_sentence=source_sentence[:300])
        if t.version == 1 and t.status == "proposed":
            self.proposals.log(plan_id, "terms_proposed", by=by, terms_id=t.terms_id, amount_inr=t.total_inr, detail=f"deposit {t.deposit_inr}")
        return t

    def confirm_terms(self, plan_id: str, terms_id: str, by: str, payee_name: str, payee_method: str, payee_handle: str = "", deposit_due: date | None = None, balance_due: date | None = None,
                      expect_version: int | None = None) -> PaymentTerms:
        by = (by or "").strip()
        if not 1 <= len(by) <= 60:
            raise ValueError("say who is confirming these terms (a name)")
        cur = self.terms.get(plan_id, terms_id)
        if cur is None:
            raise ValueError("no such payment terms")
        if cur.status not in ("proposed", "confirmed"):
            raise ValueError(f"these terms are {cur.status}; they cannot be changed now")
        if any(p.terms_id == terms_id and p.status in ap.LIVE for p in self.proposals.all(plan_id)):
            raise ValueError("a payment for these terms is in progress: cancel it before changing who is paid")
        for label, due in (("deposit", deposit_due), ("balance", balance_due)):
            if due is not None and due < self.today() and (cur.deposit_inr if label == "deposit" else cur.balance_inr) > 0:
                raise ValueError(f"the {label} due date has already passed")
        t = self.terms.update(plan_id, terms_id, expect_version=expect_version, payee_name=payee_name.strip(), payee_method=payee_method, payee_handle=payee_handle.strip(), deposit_due=deposit_due,
                              balance_due=balance_due, status="confirmed" if cur.status == "proposed" else cur.status, confirmed_by=by, confirmed_at=self.now)
        self.proposals.log(plan_id, "terms_confirmed", by=by, terms_id=terms_id, detail=f"{t.payee_method} to {t.payee_name}")
        return t

    # ------------------------------------------------------------ proposals

    def _remaining(self, t: PaymentTerms, kind: str) -> int:
        if kind == "deposit":
            return max(0, t.deposit_inr - t.paid_inr)
        return max(0, t.total_inr - max(t.paid_inr, t.deposit_inr))

    def propose_payment(self, plan_id: str, terms_id: str, kind: str, by: str) -> ap.PaymentProposal:
        t = self.terms.get(plan_id, terms_id)
        if t is None:
            raise ValueError("no such payment terms")
        if t.status not in ("confirmed", "deposit_paid"):
            raise ValueError("confirm who is paid and how before proposing a payment" if t.status == "proposed" else f"these terms are {t.status}")
        if kind not in ("deposit", "balance"):
            raise ValueError("a payment is the deposit or the balance")
        amount = t.deposit_inr if kind == "deposit" else t.balance_inr
        if amount <= 0:
            raise ValueError(f"these terms have no {kind} to pay")
        if kind == "balance" and t.deposit_inr > 0 and t.status != "deposit_paid":
            raise ValueError("the deposit comes first: record it as paid before proposing the balance")
        if self._remaining(t, kind) < amount:
            raise ValueError(f"the {kind} is already paid")
        with self.proposals.lock():
            if any(p.terms_id == terms_id and p.kind == kind and p.status in ap.LIVE for p in self.proposals.all(plan_id)):
                raise ValueError(f"a {kind} payment for these terms is already in progress")
            h = ap.payment_hash(plan_id, terms_id, t.version, kind, amount, t.currency, t.payee_name, t.payee_method or "", t.payee_handle, t.message_id)
            p = self.proposals.create(plan_id, terms_id=terms_id, terms_version=t.version, kind=kind, amount_inr=amount, currency=t.currency, payee_name=t.payee_name, payee_method=t.payee_method or "other",
                                      payee_handle=t.payee_handle, quote_message_id=t.message_id, content_hash=h)
        self.proposals.log(plan_id, "proposed", by=by, proposal_id=p.proposal_id, terms_id=terms_id, amount_inr=amount, detail=kind)
        return p

    def _fresh(self, plan_id: str, p: ap.PaymentProposal) -> str | None:
        """Why this proposal can no longer be acted on, or None: it expired, or the terms moved on after it was written."""
        if self.now >= p.expires_at:
            return "the approval time has run out: propose the payment again"
        t = self.terms.get(plan_id, p.terms_id)
        if t is None or t.version != p.terms_version or t.status not in ("confirmed", "deposit_paid"):
            return "the terms changed after this payment was proposed: propose it again"
        return None

    def approve(self, plan_id: str, proposal_id: str, content_hash: str, by: str) -> ap.PaymentProposal:
        by = (by or "").strip()
        if not 1 <= len(by) <= 60:
            raise ValueError("say who is approving this payment (a name)")
        cur = self.proposals.get(plan_id, proposal_id)
        if cur is None:
            raise ValueError("no such payment")
        if cur.status != "proposed":
            raise ValueError(f"this payment is {cur.status}, not waiting for approval")
        why = self._fresh(plan_id, cur)
        if why:
            self.proposals.transition(plan_id, proposal_id, ("proposed",), status="expired" if "run out" in why else "cancelled", error=why)
            self.proposals.log(plan_id, "expired" if "run out" in why else "refused", by=by, proposal_id=proposal_id, detail=why)
            raise ValueError(why)
        if content_hash != cur.content_hash:
            self.proposals.log(plan_id, "refused", by=by, proposal_id=proposal_id, detail="the hash sent is not the hash of this payment")
            raise ValueError("what you approved is not what this payment is now: look at it again")
        p = self.proposals.transition(plan_id, proposal_id, ("proposed",), status="approved", approved_by=by, approved_at=self.now)
        self.proposals.log(plan_id, "approved", by=by, proposal_id=proposal_id, amount_inr=p.amount_inr, detail=p.kind)
        return p

    # ------------------------------------------------------------ carrying out

    def instruct(self, plan_id: str, proposal_id: str, provider_name: str = "manual", by: str = "") -> ap.PaymentProposal:
        """Carry out an approved payment, once. The manual provider says what to pay; an external one submits it. A failure is final for this proposal: nothing retries it by itself."""
        providers = self.providers()
        prov: PaymentProvider | None = providers.get(provider_name)
        if prov is None:
            raise ValueError("that way of paying is not switched on")
        if provider_name != "manual" and not self.external_allowed:
            raise ValueError("money cannot be moved from a visitor's workspace: use the manual instruction")
        cur = self.proposals.get(plan_id, proposal_id)
        if cur is None:
            raise ValueError("no such payment")
        why = self._fresh(plan_id, cur) if cur.status == "approved" else None
        if why:
            self.proposals.transition(plan_id, proposal_id, ("approved",), status="expired" if "run out" in why else "cancelled", error=why)
            raise ValueError(why)
        claimed = self.proposals.transition(plan_id, proposal_id, ("approved",), status="executing", provider=provider_name)  # the claim: only one caller gets past this
        self.proposals.log(plan_id, "attempted", by=by or (claimed.approved_by or ""), proposal_id=proposal_id, amount_inr=claimed.amount_inr, provider=provider_name)
        req = PaymentRequest(proposal_id=claimed.proposal_id, plan_id=plan_id, kind=claimed.kind, amount_inr=claimed.amount_inr, currency=claimed.currency, payee_name=claimed.payee_name,
                             payee_method=claimed.payee_method, payee_handle=claimed.payee_handle, quote_message_id=claimed.quote_message_id)
        try:
            ins: Instruction = prov.create_instruction(req)
        except ProviderError as exc:
            p = self.proposals.transition(plan_id, proposal_id, ("executing",), status="failed", error=str(exc)[:300])
            self.proposals.log(plan_id, "failed", by=by, proposal_id=proposal_id, amount_inr=p.amount_inr, provider=provider_name, detail=str(exc)[:300])
            return p
        status = "submitted" if ins.state == "submitted" else "instructed"
        p = self.proposals.transition(plan_id, proposal_id, ("executing",), status=status, provider_ref=ins.reference, instruction=ins.text)
        self.proposals.log(plan_id, status, by=by, proposal_id=proposal_id, amount_inr=p.amount_inr, provider=provider_name, detail=ins.reference)
        return p

    def provider_says(self, plan_id: str, proposal_id: str) -> dict:
        """What the provider reports for a submitted payment, beside what the user has confirmed. It records nothing."""
        p = self.proposals.get(plan_id, proposal_id)
        if p is None:
            raise ValueError("no such payment")
        prov = self.providers().get(p.provider)
        if prov is None or not p.provider_ref:
            return {"provider": p.provider, "provider_says": "unknown", "detail": "nothing to ask a provider about", "you_confirmed": p.status == "paid"}
        try:
            st = prov.status(p.provider_ref)
        except ProviderError as exc:
            return {"provider": p.provider, "provider_says": "unknown", "detail": str(exc), "you_confirmed": p.status == "paid"}
        self.proposals.log(plan_id, "reconciled", proposal_id=proposal_id, provider=p.provider, detail=f"{st.state} {st.detail}"[:300])
        return {"provider": p.provider, "provider_says": st.state, "detail": st.detail, "you_confirmed": p.status == "paid"}

    # ------------------------------------------------------------ what the user confirms

    def mark_paid(self, plan_id: str, proposal_id: str, by: str, reference: str = "") -> ap.PaymentProposal:
        by = (by or "").strip()
        if not 1 <= len(by) <= 60:
            raise ValueError("say who is recording this payment (a name)")
        cur = self.proposals.get(plan_id, proposal_id)
        if cur is None:
            raise ValueError("no such payment")
        t = self.terms.get(plan_id, cur.terms_id)
        if t is None:
            raise ValueError("no such payment terms")
        with self.proposals.lock():  # claim the recording once, so two tabs never write the money twice
            p = self.proposals.transition(plan_id, proposal_id, ("instructed", "submitted"), status="paid", paid_by=by, paid_at=self.now, paid_reference=(reference or "")[:100])
        self._ledger_add(plan_id, "payment", t.category, p.amount_inr, f"{p.kind} to {p.payee_name} (confirmed by {by})"[:200])
        paid = t.paid_inr + p.amount_inr
        self.terms.update(plan_id, t.terms_id, paid_inr=paid, status="paid" if paid >= t.total_inr else "deposit_paid")
        self.proposals.log(plan_id, "paid", by=by, proposal_id=proposal_id, terms_id=t.terms_id, amount_inr=p.amount_inr, provider=p.provider, detail=(reference or "")[:100])
        return p

    def mark_failed(self, plan_id: str, proposal_id: str, by: str, note: str = "") -> ap.PaymentProposal:
        by = (by or "").strip()
        if not 1 <= len(by) <= 60:
            raise ValueError("say who is recording this (a name)")
        p = self.proposals.transition(plan_id, proposal_id, ("instructed", "submitted", "approved"), status="failed", error=(note or "the user says the payment did not go through")[:300])
        self.proposals.log(plan_id, "failed", by=by, proposal_id=proposal_id, amount_inr=p.amount_inr, provider=p.provider, detail=(note or "did not go through")[:300])
        return p

    def cancel(self, plan_id: str, proposal_id: str, by: str) -> ap.PaymentProposal:
        by = (by or "").strip()
        if not 1 <= len(by) <= 60:
            raise ValueError("say who is cancelling this (a name)")
        p = self.proposals.transition(plan_id, proposal_id, ("proposed", "approved", "instructed"), status="cancelled", error="cancelled by the user")
        self.proposals.log(plan_id, "cancelled", by=by, proposal_id=proposal_id, amount_inr=p.amount_inr)
        return p

    def refund(self, plan_id: str, terms_id: str, by: str, amount_inr: int, note: str = "") -> PaymentTerms:
        by = (by or "").strip()
        if not 1 <= len(by) <= 60:
            raise ValueError("say who is recording this refund (a name)")
        t = self.terms.get(plan_id, terms_id)
        if t is None:
            raise ValueError("no such payment terms")
        if not isinstance(amount_inr, int) or isinstance(amount_inr, bool) or amount_inr <= 0:
            raise ValueError("a refund is a whole number of rupees above zero")
        if amount_inr > t.paid_inr - t.refunded_inr:
            raise ValueError(f"INR {amount_inr:,} is more than the INR {t.paid_inr - t.refunded_inr:,} paid and not yet refunded")
        self._ledger_add(plan_id, "refund", t.category, amount_inr, f"refund from {t.payee_name} (recorded by {by})"[:200])
        refunded = t.refunded_inr + amount_inr
        new = self.terms.update(plan_id, terms_id, refunded_inr=refunded, status="refunded" if refunded >= t.paid_inr else t.status)
        self.proposals.log(plan_id, "refund", by=by, terms_id=terms_id, amount_inr=amount_inr, detail=(note or "")[:300])
        return new

    def note(self, plan_id: str, by: str, text: str, terms_id: str = "") -> None:
        """A dispute, a call, a promise: the user's own words, in the log, with their name and the time. It changes no money."""
        by = (by or "").strip()
        if not 1 <= len(by) <= 60 or not (text or "").strip():
            raise ValueError("say who is writing this and what")
        self.proposals.log(plan_id, "note", by=by, terms_id=terms_id, detail=text.strip()[:300])
