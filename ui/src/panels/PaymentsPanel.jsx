import { useState } from 'react';
import { Note, Notice } from '../components/Note';
import { useAction } from '../hooks/useAction';
import { useResource } from '../hooks/useResource';
import { api } from '../lib/api';
import { inr, longDate, shareOf, shortDate } from '../lib/format';
import { categoryLabel } from '../lib/vendors';

const METHODS = { upi: 'UPI', bank_transfer: 'Bank transfer', paypal: 'PayPal', cash: 'Cash', cheque: 'Cheque', card_link: 'A card payment link', other: 'Another way' };
const STATE = {
  proposed: 'Waiting for your approval', approved: 'Approved: now say how it will be paid', instructed: 'Waiting for you to pay and confirm', submitted: 'Submitted to the provider: confirm when it is paid',
  outcome_unknown: 'We could not tell whether it went through: look, then say',
  paid: 'Paid, as you confirmed', failed: 'Did not go through', cancelled: 'Cancelled', expired: 'Expired: propose it again',
};

function TermsForm({ terms, post, busy }) {
  const [f, setF] = useState({ payee_name: terms.vendor_name, payee_method: 'upi', payee_handle: '', deposit_due: '', balance_due: '' });
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }));
  const needsHandle = f.payee_method === 'upi' || f.payee_method === 'paypal';
  const submit = (e) => {
    e.preventDefault();
    const body = { payee_name: f.payee_name.trim(), payee_method: f.payee_method, payee_handle: needsHandle ? f.payee_handle.trim() : '', expect_version: terms.version };
    if (f.deposit_due) body.deposit_due = f.deposit_due;
    if (f.balance_due) body.balance_due = f.balance_due;
    post(`/terms/${terms.terms_id}/confirm`, body);
  };
  return (
    <form className="inline-form" onSubmit={submit}>
      <p className="quiet">Who is paid, and how? Use what the vendor wrote. No account or card numbers are kept here: they stay in the vendor’s own message.</p>
      <label className="field"><span>Who is paid</span><input type="text" maxLength={120} required value={f.payee_name} onChange={set('payee_name')} /></label>
      <label className="field"><span>How</span>
        <select value={f.payee_method} onChange={set('payee_method')}>{Object.entries(METHODS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
      </label>
      {needsHandle && <label className="field"><span>{f.payee_method === 'upi' ? 'Their UPI id (like name@bank)' : 'Their PayPal email'}</span><input type={f.payee_method === 'paypal' ? 'email' : 'text'} maxLength={100} required value={f.payee_handle} onChange={set('payee_handle')} /></label>}
      {terms.deposit_inr > 0 && <label className="field"><span>Deposit due (optional)</span><input type="date" value={f.deposit_due} onChange={set('deposit_due')} /></label>}
      {terms.balance_inr > 0 && <label className="field"><span>Balance due (optional)</span><input type="date" value={f.balance_due} onChange={set('balance_due')} /></label>}
      <div className="row"><button type="submit" className="btn btn--small" disabled={busy}>Confirm these terms</button></div>
    </form>
  );
}

function Payment({ p, post, busy }) {
  const [ref, setRef] = useState('');
  return (
    <div className="payment" data-payment={p.proposal_id} data-status={p.status}>
      <p><b>{p.kind === 'deposit' ? 'Deposit' : 'Balance'}: {inr(p.amount_inr)}</b> to {p.payee_name} · {METHODS[p.payee_method] || p.payee_method}{p.payee_handle ? ` (${p.payee_handle})` : ''}</p>
      <small className="quiet">{STATE[p.status] || p.status} · what you approve is exactly this, fingerprint {p.content_hash.slice(0, 12)}{p.status === 'proposed' ? ` · valid until ${shortDate(p.expires_at)}` : ''}</small>
      {p.status === 'proposed' && (
        <div className="row">
          <button type="button" className="btn btn--small" disabled={busy} onClick={() => post(`/${p.proposal_id}/approve`, { content_hash: p.content_hash })}>Approve exactly this payment</button>
          <button type="button" className="linkish" disabled={busy} onClick={() => post(`/${p.proposal_id}/cancel`, {})}>Don’t pay this</button>
        </div>
      )}
      {p.status === 'approved' && <div className="row"><button type="button" className="btn btn--small" disabled={busy} onClick={() => post(`/${p.proposal_id}/instruct`, { provider: 'manual' })}>Show me how to pay</button></div>}
      {p.status === 'outcome_unknown' && (
        <>
          <Notice tone="warn">{p.error || 'The answer was lost, so nobody knows whether this was paid.'} Nothing will be sent again by itself. Check with the provider or your bank, then tell the app what you found.</Notice>
          <label className="field"><span>The reference, if it went through (optional)</span><input type="text" maxLength={100} value={ref} onChange={(e) => setRef(e.target.value)} /></label>
          <div className="row">
            <button type="button" className="chip chip--strong" disabled={busy} onClick={() => post(`/${p.proposal_id}/paid`, { reference: ref.trim() })}>It went through</button>
            <button type="button" className="chip" disabled={busy} onClick={() => post(`/${p.proposal_id}/failed`, { note: 'checked: it did not go through' })}>It did not go through</button>
          </div>
        </>
      )}
      {(p.status === 'instructed' || p.status === 'submitted') && (
        <>
          {p.instruction && <p className="exact-note">{p.instruction}</p>}
          <label className="field"><span>The reference you were given (optional)</span><input type="text" maxLength={100} value={ref} onChange={(e) => setRef(e.target.value)} /></label>
          <div className="row">
            <button type="button" className="chip chip--strong" disabled={busy} onClick={() => post(`/${p.proposal_id}/paid`, { reference: ref.trim() })}>I paid</button>
            <button type="button" className="linkish" disabled={busy} onClick={() => post(`/${p.proposal_id}/failed`, { note: '' })}>It didn’t go through</button>
            <button type="button" className="linkish" disabled={busy} onClick={() => post(`/${p.proposal_id}/cancel`, {})}>Cancel</button>
          </div>
        </>
      )}
      {p.error && p.status !== 'paid' && <small className="quiet">{p.error}</small>}
    </div>
  );
}

function Terms({ t, payments, post, busy }) {
  const mine = payments.filter((p) => p.terms_id === t.terms_id);
  const live = mine.some((p) => ['proposed', 'approved', 'executing', 'instructed', 'submitted', 'outcome_unknown'].includes(p.status));
  const [refund, setRefund] = useState('');
  return (
    <div className="terms" data-terms={t.terms_id}>
      <h4>{categoryLabel(t.category)}: {t.vendor_name}</h4>
      <p>Total {inr(t.total_inr)} · deposit {inr(t.deposit_inr)}{t.deposit_inr > 0 ? ` (${shareOf(t.deposit_inr, t.total_inr)})` : ''} · balance {inr(t.balance_inr)}{t.deposit_due ? ` · deposit due ${longDate(t.deposit_due)}` : ''}{t.balance_due ? ` · balance due ${longDate(t.balance_due)}` : ''}</p>
      {t.source_sentence && <blockquote>“{t.source_sentence}”</blockquote>}
      <small className="quiet">Paid so far {inr(t.paid_inr)}{t.refunded_inr ? ` · refunded ${inr(t.refunded_inr)}` : ''} · {t.status.replace('_', ' ')}</small>
      {t.status === 'proposed' && <TermsForm terms={t} post={post} busy={busy} />}
      {(t.status === 'confirmed' || t.status === 'deposit_paid') && !live && (
        <div className="row">
          {t.status === 'confirmed' && t.deposit_inr > 0 && <button type="button" className="chip" disabled={busy} onClick={() => post('', { terms_id: t.terms_id, kind: 'deposit' })}>Propose the deposit</button>}
          {t.balance_inr > 0 && (t.deposit_inr === 0 || t.status === 'deposit_paid') && <button type="button" className="chip" disabled={busy} onClick={() => post('', { terms_id: t.terms_id, kind: 'balance' })}>Propose the balance</button>}
        </div>
      )}
      {mine.map((p) => <Payment key={p.proposal_id} p={p} post={post} busy={busy} />)}
      {t.paid_inr - t.refunded_inr > 0 && (
        <form className="inline-form" onSubmit={(e) => { e.preventDefault(); if (refund) post(`/terms/${t.terms_id}/refund`, { amount_inr: Number(refund) }); setRefund(''); }}>
          <label className="field"><span>The vendor refunded (₹)</span><input type="number" min={1} max={t.paid_inr - t.refunded_inr} value={refund} onChange={(e) => setRefund(e.target.value)} /></label>
          <button type="submit" className="linkish" disabled={busy || !refund}>Write the refund down</button>
        </form>
      )}
    </div>
  );
}

/** Deposits and balances for the vendors you chose. The app writes down what was agreed and what you approved and paid; it moves no money by itself, and says so. */
export function PaymentsPanel({ planId, sourcing }) {
  const view = useResource(`/api/plans/${planId}/payments`);
  const [state, run] = useAction('That did not work');
  const base = `/api/plans/${planId}/payments`;
  const by = sourcing.by.trim();
  const post = (path, body) => run(async () => {
    const res = await api(`${base}${path}`, { method: 'POST', body: { ...body, by } });
    await view.reload();
    return res;
  });
  const chosen = (sourcing.comparison?.categories || []).flatMap((c) => c.chosen || []);
  const data = view.data;
  const have = new Set((data?.terms || []).map((t) => t.message_id));
  const open = chosen.filter((id) => !have.has(id));
  return (
    <Note tone="vendor" tag="MONEY" title="Deposits and balances" busy={state.busy}>
      <p className="quiet">{data?.notice || 'to;doh-the event planner records and instructs. It does not hold or move your money by itself, and it does not guarantee that a vendor delivers.'}</p>
      {!by && <Notice tone="warn">Add your name on the “Your name” note in Compare to record anything here.</Notice>}
      {(data?.terms || []).map((t) => <Terms key={t.terms_id} t={t} payments={data.payments} post={post} busy={state.busy || !by} />)}
      {open.length > 0 && by && (
        <div className="row">
          {open.map((id) => <button key={id} type="button" className="chip" disabled={state.busy} onClick={() => post('/terms', { message_id: id })}>Set up the deposit and balance of a chosen quote</button>)}
        </div>
      )}
      {!data?.terms?.length && !open.length && <p className="quiet">Choose a vendor under “Compare” first. Terms are made from a quote you confirmed in full, the deposit included.</p>}
      {data?.events?.length > 0 && (
        <details className="provenance">
          <summary>What has been done</summary>
          <ul className="prov-lines">{data.events.map((e) => <li key={e.event_id}>{shortDate(e.at)} · {e.by || 'the app'} · {e.kind.replace('_', ' ')}{e.amount_inr != null ? ` ${inr(e.amount_inr)}` : ''}{e.detail ? ` · ${e.detail}` : ''}</li>)}</ul>
        </details>
      )}
      {(state.error || view.error) && <Notice tone="bad">{state.error || view.error}</Notice>}
    </Note>
  );
}
