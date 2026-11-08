import { useState } from 'react';
import { Note, Notice } from '../components/Note';
import { Pulse } from '../components/Pulse';
import { useAction } from '../hooks/useAction';
import { useListing } from '../hooks/useListing';
import { api } from '../lib/api';
import { shortDate } from '../lib/format';
import { categoryLabel, FACT, nameOf, readCard, rupees, safeUrl, SOURCE } from '../lib/vendors';

const ORIGIN = { discovered: 'Found online', user: 'Added by you', test: 'Test vendor' };

function Listing({ card, listing }) {
  if (listing.status === 'idle') return null;
  if (listing.status === 'loading') return <p className="listing quiet">Fetching the listing…</p>;
  if (listing.status !== 'ready') return <p className="listing quiet">{listing.status === 'down' ? 'The listing could not be fetched just now.' : 'Google no longer lists this business under that id.'}</p>;
  const p = listing.data;
  return (
    <p className="listing">
      {p.address ? `${p.address} · ` : ''}
      {p.rating ? `${p.rating} from ${p.rating_count || 0} reviews · ` : ''}
      {safeUrl(p.maps_uri) && <a href={p.maps_uri} target="_blank" rel="noopener noreferrer">Open in Maps</a>}
      {safeUrl(p.website) && <> · <a href={p.website} target="_blank" rel="noopener noreferrer">Website</a></>}
      <img className="gmaps" src="/static/google-maps-logo.svg" alt="Google Maps" width="98" height="18" />
    </p>
  );
}

/** The one price a card puts forward. A current quote says so; an earlier one says whose, how old, and why it may no longer hold, with the wait for a fresh one beside it. */
function Price({ read }) {
  const { shown, isCurrent, fromSomeoneElse, waiting, replied } = read;
  if (!shown && !waiting && !replied) return <p className="price-empty">No quote yet.</p>;
  return (
    <div className={`price${isCurrent ? ' price--current' : ''}`}>
      {shown && (
        <>
          <span className="price-kind">{isCurrent ? 'Quote you confirmed' : fromSomeoneElse ? 'Earlier quote, to someone else' : 'Earlier quote'}</span>
          <span className="price-amount">{rupees(shown.value)}</span>
          <small className="price-note">
            {shown.captured_at ? `${shortDate(shown.captured_at)} · ` : ''}
            {shown.note}
          </small>
        </>
      )}
      {replied && !isCurrent && <p className="price-reply">Their reply is in. Read it and confirm the price under Replies.</p>}
      {waiting && <Pulse>{shown ? 'Asking for a fresh quote. This one stays until it arrives.' : 'Asked. Waiting for their quote.'}</Pulse>}
    </div>
  );
}

function UseEmail({ card, fact, sourcing, listing, onDone }) {
  const [name, setName] = useState(listing.data?.name || (card.name_confirmed ? card.name : ''));
  const [state, run] = useAction('That address could not be confirmed');
  const needsName = card.source === 'gplaces';

  async function submit(e) {
    e.preventDefault();
    const body = { by: sourcing.by.trim() };
    if (needsName) body.business_name = name.trim();
    const ok = await run(() => api(`${sourcing.base}/shortlist/${encodeURIComponent(card.vendor_id)}/contacts/${encodeURIComponent(fact.candidate_id || '')}/confirm`, { method: 'POST', body }));
    if (ok) { await sourcing.refresh(); onDone(); }
  }

  return (
    <form className="inline-form" onSubmit={submit}>
      {needsName && (
        <label className="field">
          <span>Business name for your records</span>
          <input type="text" maxLength={120} required value={name} onChange={(e) => setName(e.target.value)} />
        </label>
      )}
      {!sourcing.by.trim() && <p className="quiet">Add your name on the “Your name” note first. It is recorded with the confirmation.</p>}
      <div className="row">
        <button type="submit" className="btn btn--small" disabled={state.busy || !sourcing.by.trim() || (needsName && !name.trim())}>{state.busy ? 'Saving…' : `Confirm ${fact.value}`}</button>
        <button type="button" className="linkish" onClick={onDone}>Cancel</button>
      </div>
      {state.error && <Notice tone="bad">{state.error}</Notice>}
    </form>
  );
}

function Contacts({ card, read, sourcing, listing }) {
  const [using, setUsing] = useState(null);
  const [state, run] = useAction('That did not work');
  const [notes, setNotes] = useState([]);
  const url = (path) => `${sourcing.base}/shortlist/${encodeURIComponent(card.vendor_id)}${path}`;

  async function look() {
    const res = await api(url('/find-contacts'), { method: 'POST', body: {} });
    if (res.ok) setNotes(res.data?.notes || []);
    await sourcing.refresh();
    return res;
  }
  const reject = (fact) => run(async () => {
    const res = await api(url(`/contacts/${encodeURIComponent(fact.candidate_id || '')}/reject`), { method: 'POST', body: {} });
    await sourcing.refresh();
    return res;
  });

  return (
    <div className="contacts">
      {read.emails.map((f) => (
        <div key={`${f.field}${f.value}`} className="contact-line">
          <span className="contact-value">{f.value}</span>
          <small className="quiet">{f.status === 'found' ? `found${f.source_type === 'vendor_website' ? ' on their website' : ''}, not yet confirmed` : 'confirmed by you'}</small>
          {f.conflict && <Notice tone="warn">Another address is held for this business. Neither is chosen for you.</Notice>}
          {f.status === 'found' && using !== f.value && (
            <span className="row">
              <button type="button" className="chip" onClick={() => setUsing(f.value)}>Use this email</button>
              <button type="button" className="linkish" disabled={state.busy} onClick={() => reject(f)}>Not this one</button>
            </span>
          )}
          {using === f.value && <UseEmail card={card} fact={f} sourcing={sourcing} listing={listing} onDone={() => setUsing(null)} />}
        </div>
      ))}
      {read.whatsapp.map((f) => <div key={f.value} className="contact-line"><span className="contact-value">WhatsApp {f.value}</span><small className="quiet">confirmed by you</small></div>)}
      {!card.contact_ready && !read.found.length && (
        <button type="button" className="chip" disabled={state.busy} onClick={() => run(look)}>{state.busy ? 'Reading their site…' : 'Look for an email on their website'}</button>
      )}
      {notes.map((n) => <p key={n} className="quiet">{n}</p>)}
      {state.error && <Notice tone="bad">{state.error}</Notice>}
    </div>
  );
}

function Provenance({ card }) {
  const facts = card.facts || [];
  if (!facts.length) return null;
  return (
    <details className="provenance">
      <summary>Where this comes from</summary>
      <ul>
        {facts.map((f, i) => (
          <li key={`${f.field}${i}`}>
            <b>{FACT[f.field] || f.field}</b> {rupees(f.value)}
            <small>
              {SOURCE[f.source_type]}{f.captured_at ? `, ${shortDate(f.captured_at)}` : ''} · {f.user_confirmed ? 'confirmed by you' : 'not confirmed'}
              {safeUrl(f.source_ref) && f.source_type !== 'user_confirmed' && <> · <a href={f.source_ref} target="_blank" rel="noopener noreferrer">source</a></>}
            </small>
            {f.evidence && <blockquote>“{f.evidence}”</blockquote>}
          </li>
        ))}
      </ul>
    </details>
  );
}

/** A business as the person meets it: its name, the price on show (with the old one kept in view while a new one is awaited), how to reach it, and where each fact came from. */
export function VendorCard({ card, sourcing }) {
  const listing = useListing(card.place_id);
  const read = readCard(card);
  const tag = `${categoryLabel(card.category).toUpperCase()} · ${(card.label || ORIGIN[card.origin] || '').toUpperCase()}`;
  return (
    <Note tone="vendor" tag={tag} title={nameOf(card, listing.data)} data-vendor={card.vendor_id}>
      <Listing card={card} listing={listing} />
      {card.blocked && <Notice tone="bad">Cannot be contacted: {card.blocked}.</Notice>}
      <Price read={read} />
      <Contacts card={card} read={read} sourcing={sourcing} listing={listing} />
      <Provenance card={card} />
    </Note>
  );
}
