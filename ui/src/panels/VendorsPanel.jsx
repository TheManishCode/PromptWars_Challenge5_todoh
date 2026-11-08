import { useEffect, useState } from 'react';
import { useResource } from '../hooks/useResource';
import { Collage, Note, Notice } from '../components/Note';
import { useAction } from '../hooks/useAction';
import { api } from '../lib/api';
import { categoryLabel } from '../lib/vendors';
import { NameNote } from './NameNote';
import { VendorCard } from './VendorCard';

function AddVendor({ category, sourcing, onDone }) {
  const [form, setForm] = useState({ name: '', email: '', whatsapp: '' });
  const [state, run] = useAction('That business could not be added');
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const ready = sourcing.by.trim() && form.name.trim() && (form.email.trim() || form.whatsapp.trim());

  async function submit(e) {
    e.preventDefault();
    const body = { name: form.name.trim(), category, by: sourcing.by.trim() };
    if (form.email.trim()) body.email = form.email.trim();
    if (form.whatsapp.trim()) body.whatsapp = form.whatsapp.trim();
    const ok = await run(() => api(`${sourcing.base}/shortlist/user-vendor`, { method: 'POST', body }));
    if (ok) { await sourcing.refresh(); onDone(); }
  }

  return (
    <form className="inline-form" onSubmit={submit}>
      <label className="field"><span>Business name</span><input type="text" maxLength={120} required value={form.name} onChange={set('name')} autoFocus /></label>
      <label className="field"><span>Email</span><input type="email" maxLength={254} value={form.email} onChange={set('email')} /></label>
      <label className="field"><span>WhatsApp number, with country code</span><input type="tel" maxLength={30} value={form.whatsapp} onChange={set('whatsapp')} placeholder="+91…" /></label>
      {!sourcing.by.trim() && <p className="quiet">Add your name on the “Your name” note first.</p>}
      <div className="row">
        <button type="submit" className="btn btn--small" disabled={state.busy || !ready}>{state.busy ? 'Adding…' : 'Add'}</button>
        <button type="button" className="linkish" onClick={onDone}>Cancel</button>
      </div>
      <p className="quiet">If someone else has asked this business for a quote, you will see what it said, marked as theirs and for orientation only.</p>
      {state.error && <Notice tone="bad">{state.error}</Notice>}
    </form>
  );
}

/** Says so when an outside lookup is used up or failing, so a short list reads as "missing", not "none exist". Nothing is drawn (no empty collage cell) when all is well. */
function serviceNote(services) {
  const bad = (services || []).filter((s) => ['places', 'overpass', 'search'].includes(s.name) && ['quota_used', 'degraded'].includes(s.state));
  if (!bad.length) return null;
  return (
    <Note key="services" tone="warn" title="Online lookups are limited right now">
      {bad.map((s) => <p key={s.name}>{s.label}: {s.note}.</p>)}
      <p className="quiet">What is missing below may exist. Add the businesses you know, and look again later.</p>
    </Note>
  );
}

// Never the server's own wording: it can name a setting a visitor cannot change.
const SEARCH_DOWN = {
  503: 'Looking businesses up online is not switched on here. Add the ones you know.',
  429: 'This hour’s online lookups are used up. Try again later, or add the ones you know.',
};
const searchFailed = (res) => ({ ...res, data: { error: SEARCH_DOWN[res.status] || 'The search could not be done just now. Try again in a minute, or add the ones you know.' } });

/** True once `busy` has lasted `ms`: a lookup that is slow says so instead of looking stuck. */
function useSlow(busy, ms = 4000) {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    if (!busy) { setSlow(false); return undefined; }
    const t = setTimeout(() => setSlow(true), ms);
    return () => clearTimeout(t);
  }, [busy, ms]);
  return slow;
}

function Search({ category, count, sourcing }) {
  const [adding, setAdding] = useState(false);
  const [outcome, setOutcome] = useState(null);
  const [state, run] = useAction('The search did not work');
  const slow = useSlow(state.busy);

  async function find() {
    setOutcome(null);
    const ok = await run(async () => {
      const res = await api(`${sourcing.base}/shortlist/search`, { method: 'POST', body: { category, source: 'places', limit: 6 } });
      if (!res.ok) return searchFailed(res);
      setOutcome(res.data);
      return res;
    });
    if (ok) await sourcing.refresh();
  }

  return (
    <Note tone="action" title={categoryLabel(category)} busy={state.busy}>
      <p className="quiet">{count ? `${count} on your list.` : 'None on your list yet.'}</p>
      <div className="row">
        <button type="button" className="btn btn--small" disabled={state.busy} onClick={find}>{state.busy ? 'Searching…' : 'Find online'}</button>
        <button type="button" className="btn btn--small btn--ghost" aria-expanded={adding} onClick={() => setAdding((a) => !a)}>Add one you know</button>
      </div>
      {slow && <p className="quiet" role="status">Looking this up with Google can take up to half a minute. You can add a business you know while you wait.</p>}
      {outcome && (
        <p className="quiet" role="status">
          {outcome.total ? `${outcome.added} new, ${outcome.total} in all.` : 'Nothing found for this city. Add the ones you know.'}
          {(outcome.warnings || []).map((w) => <span key={w}> {w}</span>)}
        </p>
      )}
      {state.error && <Notice tone="bad">{state.error}</Notice>}
      {adding && <AddVendor category={category} sourcing={sourcing} onDone={() => setAdding(false)} />}
    </Note>
  );
}

const OPTIONAL = ['caterer', 'photographer'];

export function VendorsPanel({ categories, extra, setExtra, sourcing }) {
  const services = useResource('/api/services');
  const out = [<NameNote key="name" by={sourcing.by} setBy={sourcing.setBy} />, serviceNote(services.data)].filter(Boolean);
  for (const category of categories) {
    const mine = sourcing.cards.filter((c) => c.category === category);
    out.push(<Search key={`find-${category}`} category={category} count={mine.length} sourcing={sourcing} />);
    for (const card of mine) out.push(<VendorCard key={card.vendor_id} card={card} sourcing={sourcing} />);
  }
  const more = OPTIONAL.filter((c) => !categories.includes(c));
  if (more.length) {
    out.push(
      <Note key="more" tone="action">
        <p className="quiet">Also look for</p>
        <div className="row">{more.map((c) => <button key={c} type="button" className="chip" onClick={() => setExtra((e) => [...e, c])}>+ {categoryLabel(c)}</button>)}</div>
      </Note>,
    );
  }
  return <Collage>{out}</Collage>;
}
