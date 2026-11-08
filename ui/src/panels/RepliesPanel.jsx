import { useState } from 'react';
import { Collage, Note, Notice } from '../components/Note';
import { useAction } from '../hooks/useAction';
import { api } from '../lib/api';
import { inr, shortDate } from '../lib/format';
import { nameOf } from '../lib/vendors';
import { useResource } from '../hooks/useResource';
import { NameNote } from './NameNote';

const HOW = {
  sender: 'Matched by the sender’s address, not by the thread: check that this answers this event.',
  assigned: 'Filed here because you chose this event.',
  pasted: 'Pasted by you.',
};

const STATE = { confirmed: 'confirmed', read: 'read, waiting for you', unclear: 'unclear', missing: 'not in the reply' };
const PRICE_KEYS = ['price', 'price_total', 'package'];
const show = (f) => (f.value == null ? '' : f.money ? inr(Number(f.value)) : f.value);

function Field({ f, byKey, send, takeBack, busy }) {
  const [mode, setMode] = useState(null); // 'typing' | 'days'
  const [typed, setTyped] = useState(f.value || '');
  const [days, setDays] = useState(byKey.valid_days?.value || '');
  const isPrice = PRICE_KEYS.includes(f.key);
  const done = (ok) => ok && setMode(null);

  // the price is a quote only together with how long it holds
  const needsDays = isPrice && byKey.valid_days?.state !== 'confirmed';
  const confirm = () => (needsDays ? setMode('days') : send({ [f.key]: null }));

  return (
    <li className="reading-field" data-field={f.key} data-state={f.state}>
      <div className="reading-line">
        <b>{f.label}</b>
        <span className="reading-value">{show(f) || '—'}</span>
        <small className={`state state--${f.state}`}>
          {STATE[f.state]}{f.state === 'confirmed' ? ` by ${f.confirmed_by}${f.edited ? ', a value you typed' : ''}` : ''}
        </small>
      </div>
      {f.evidence && f.state !== 'confirmed' && <blockquote>“{f.evidence}”</blockquote>}
      {f.note && f.state !== 'confirmed' && <small className="quiet">{f.note}</small>}
      <div className="row">
        {f.state === 'read' && mode === null && (
          <>
            <button type="button" className={f.money ? 'chip chip--strong' : 'chip'} disabled={busy} onClick={confirm}>{f.money ? `Confirm ${show(f)}` : 'Confirm'}</button>
            <button type="button" className="linkish" onClick={() => setMode('typing')}>Change</button>
          </>
        )}
        {(f.state === 'unclear' || f.state === 'missing') && mode === null && <button type="button" className="linkish" onClick={() => setMode('typing')}>Type the value</button>}
        {f.state === 'confirmed' && <button type="button" className="linkish" disabled={busy} onClick={() => takeBack(f.key)}>Take back</button>}
        {mode === 'typing' && (
          <form className="inline-row" onSubmit={async (e) => { e.preventDefault(); if (typed.trim()) done(await send({ [f.key]: typed.trim() }, isPrice && byKey.valid_days?.value ? { valid_days: byKey.valid_days.value } : undefined)); }}>
            <input type="text" maxLength={120} aria-label={f.label} value={typed} onChange={(e) => setTyped(e.target.value)} autoFocus />
            <button type="submit" className="chip" disabled={busy}>Confirm what I typed</button>
            <button type="button" className="linkish" onClick={() => setMode(null)}>Cancel</button>
          </form>
        )}
        {mode === 'days' && (
          <form className="inline-row" onSubmit={async (e) => { e.preventDefault(); if (days) done(await send({ [f.key]: null }, { valid_days: String(days) })); }}>
            <label>Valid for how many days? <input type="number" min={1} max={365} required value={days} onChange={(e) => setDays(e.target.value)} autoFocus /></label>
            <button type="submit" className="chip chip--strong" disabled={busy}>Confirm price and validity</button>
            <button type="button" className="linkish" onClick={() => setMode(null)}>Cancel</button>
          </form>
        )}
      </div>
    </li>
  );
}

function Reading({ a, name, sourcing }) {
  const [state, run] = useAction('That could not be confirmed');
  const base = `${sourcing.base}/quote-readings/${encodeURIComponent(a.message_id)}`;
  const byKey = Object.fromEntries(a.fields.map((f) => [f.key, f]));
  const needName = () => !sourcing.by.trim();

  const send = (fields, extra) => needName() ? Promise.resolve(false) : run(async () => {
    const res = await api(`${base}/confirm`, { method: 'POST', body: { fields, by: sourcing.by.trim(), ...extra } });
    await sourcing.refresh();
    return res;
  });
  const takeBack = (field) => run(async () => {
    const res = await api(`${base}/retract`, { method: 'POST', body: { field, by: sourcing.by.trim() } });
    await sourcing.refresh();
    return res;
  });
  // how long a price holds goes with the price, and an amount is never confirmed in a batch
  const rest = a.fields.filter((f) => f.state === 'read' && !f.money && f.key !== 'inclusions' && f.key !== 'valid_days');

  return (
    <Note tone="vendor" tag={`REPLY OF ${shortDate(a.received_at).toUpperCase()}`} title={name} data-message={a.message_id} busy={state.busy}>
      {HOW[a.match_method] && <Notice tone={a.match_method === 'sender' ? 'warn' : 'quiet'}>{HOW[a.match_method]}</Notice>}
      <p className={`comparable${a.comparable ? ' is-yes' : ''}`}>{a.comparable ? 'Comparable: every required line is confirmed.' : 'Not comparable yet.'}</p>
      <progress max={a.required_total} value={a.confirmed_required} aria-label="Required lines confirmed" />
      <p className="quiet">
        {a.confirmed_required} of {a.required_total} required lines confirmed · {a.read_required - a.confirmed_required} read and waiting
        {a.template_used ? '' : ' · they did not use the template, so only what the reply clearly says was read'}
      </p>
      {needName() && <Notice tone="warn">Add your name on the “Your name” note to confirm anything.</Notice>}
      <ul className="reading-fields">
        {a.fields.map((f) => <Field key={f.key} f={f} byKey={byKey} send={send} takeBack={takeBack} busy={state.busy} />)}
      </ul>
      {rest.length > 1 && (
        <div className="row">
          <button type="button" className="chip" disabled={state.busy} onClick={() => send(Object.fromEntries(rest.map((f) => [f.key, null])))}>Confirm the {rest.length} other lines as read</button>
          <small className="quiet">Amounts are always confirmed one at a time.</small>
        </div>
      )}
      {a.issues.map((i) => <Notice key={`${i.field}${i.text}`} tone={i.blocking ? 'bad' : 'quiet'}>{i.blocking ? 'Does not fit: ' : 'Note: '}{i.text}{i.tentative ? ' (as read, not confirmed yet)' : ''}</Notice>)}
      {state.error && <Notice tone="bad">{state.error}</Notice>}
    </Note>
  );
}

const NO_MAIL = 'Mail is not connected in this workspace, so replies cannot arrive by themselves. Paste what the vendor wrote in the note below.';

/** A reply that came some other way (their own mail, a call, WhatsApp). It is read and confirmed line by line like any other, and is never shared with anyone. */
function PasteReply({ sourcing }) {
  const [state, run] = useAction('That reply could not be read');
  const [vendor, setVendor] = useState('');
  const [text, setText] = useState('');
  const [channel, setChannel] = useState('email');
  const asked = sourcing.cards.filter((c) => sourcing.drafts.some((d) => d.kind === 'request' && d.vendor_id === c.vendor_id));
  const chosen = vendor || asked[0]?.vendor_id || '';
  const ready = chosen && text.trim().length >= 3 && sourcing.by.trim();

  async function submit(e) {
    e.preventDefault();
    if (!ready) return;
    const ok = await run(async () => {
      const res = await api(`${sourcing.base}/replies/paste`, { method: 'POST', body: { vendor_id: chosen, text, by: sourcing.by.trim(), channel } });
      if (res.ok) await sourcing.refresh();
      return res;
    });
    if (ok) setText('');
  }

  if (!asked.length) return null;
  return (
    <Note tone="action" title="Got an answer another way?" busy={state.busy}>
      <form className="paste-reply" onSubmit={submit}>
        <label className="field">
          <span>Who wrote it</span>
          <select value={chosen} onChange={(e) => setVendor(e.target.value)}>{asked.map((c) => <option key={c.vendor_id} value={c.vendor_id}>{nameOf(c)}</option>)}</select>
        </label>
        <label className="field">
          <span>What they wrote, as it is</span>
          <textarea rows={6} maxLength={4000} value={text} onChange={(e) => setText(e.target.value)} placeholder={'Available on the date (yes/no): yes\nPrice per plate (INR): 650\n…'} />
        </label>
        <div className="row">
          <label className="field field--inline"><span>It came by</span><select value={channel} onChange={(e) => setChannel(e.target.value)}><option value="email">Email</option><option value="whatsapp">WhatsApp or a call</option></select></label>
          <button type="submit" className="btn btn--small" disabled={state.busy || !ready}>{state.busy ? 'Reading…' : 'Read this reply'}</button>
        </div>
        {!sourcing.by.trim() && <p className="quiet">Add your name on the “Your name” note first.</p>}
        <p className="quiet">It is read line by line and nothing counts until you confirm it. What you type here is kept in your plan only: it is never shown to anyone else.</p>
        {state.error && <Notice tone="bad">{state.error}</Notice>}
      </form>
    </Note>
  );
}

/** A reply from an address we wrote to for more than one event, with nothing in it that says which. It is filed nowhere until the person picks. */
function HeldReplies({ sourcing }) {
  const held = useResource('/api/reply-reviews');
  const [state, run] = useAction('That reply could not be filed');
  const items = held.data || [];
  if (!items.length) return null;
  const assign = (id, planId) => run(async () => {
    const res = await api(`/api/reply-reviews/${encodeURIComponent(id)}/assign`, { method: 'POST', body: { plan_id: planId } });
    await Promise.all([held.reload(), sourcing.refresh()]);
    return res;
  });
  const dismiss = (id) => run(async () => {
    const res = await api(`/api/reply-reviews/${encodeURIComponent(id)}/dismiss`, { method: 'POST', body: {} });
    await held.reload();
    return res;
  });
  return items.map((r) => (
    <Note key={r.review_id} tone="warn" tag="WHICH EVENT?" title="A reply is waiting for you" busy={state.busy}>
      <p>{r.sender} answered, but nothing in the message says which of your requests it answers. It is not filed anywhere until you say.</p>
      <pre className="exact">{r.body}</pre>
      <div className="row">
        {r.candidates.map((c) => <button key={c.draft_id} type="button" className="chip" disabled={state.busy} onClick={() => assign(r.review_id, c.plan_id)}>It answers: {c.event || c.vendor_name}{c.asked_at ? `, asked ${shortDate(c.asked_at)}` : ''}</button>)}
        <button type="button" className="linkish" disabled={state.busy} onClick={() => dismiss(r.review_id)}>It answers none of these</button>
      </div>
      {state.error && <Notice tone="bad">{state.error}</Notice>}
    </Note>
  ));
}

export function RepliesPanel({ sourcing }) {
  const [state, run] = useAction('Replies could not be checked');
  const names = Object.fromEntries(sourcing.cards.map((c) => [c.vendor_id, nameOf(c)]));
  const check = () => run(async () => {
    let res = await api('/api/inbox/poll', { method: 'POST', body: {} });
    if (res.status === 403 && res.data?.code === 'sending_disabled') res = { ...res, data: { error: NO_MAIL } };
    await sourcing.refresh();
    return res;
  });

  const out = [
    <NameNote key="name" by={sourcing.by} setBy={sourcing.setBy} />,
    <Note key="check" tone="action" busy={state.busy}>
      <button type="button" className="btn btn--small" disabled={state.busy} onClick={check}>{state.busy ? 'Checking…' : 'Check for replies now'}</button>
      <p className="quiet">Replies that come to your connected mailbox are read here.</p>
      {state.error && <Notice tone="bad">{state.error}</Notice>}
    </Note>,
  ];
  out.push(<HeldReplies key="held" sourcing={sourcing} />);
  out.push(<PasteReply key="paste" sourcing={sourcing} />);
  if (!sourcing.readings.length) out.push(<Note key="none" title="Nothing has come back yet"><p>When a business answers the request you approved, it appears here, read line by line.</p></Note>);
  for (const a of sourcing.readings) out.push(<Reading key={a.message_id} a={a} name={names[a.vendor_id] || 'A vendor'} sourcing={sourcing} />);
  return <Collage>{out}</Collage>;
}
