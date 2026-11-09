import { useState } from 'react';
import { Collage, Note, Notice } from '../components/Note';
import { useAction } from '../hooks/useAction';
import { useResource } from '../hooks/useResource';
import { api } from '../lib/api';
import { inr, shareOf, shortDate } from '../lib/format';
import { categoryLabel, nameOf } from '../lib/vendors';
import { NameNote } from './NameNote';

const show = (f) => (f.value == null ? '—' : f.money ? inr(Number(f.value)) : f.value);

/** What each line of a price rests on: the vendor's own sentence and its date, and who confirmed it when. Nothing here is the app's own reading passed off as the vendor's. */
function Provenance({ reading }) {
  if (!reading) return null;
  const lines = reading.fields.filter((f) => f.state === 'confirmed');
  if (!lines.length) return null;
  return (
    <details className="provenance">
      <summary>What this rests on</summary>
      <p className="quiet">From the vendor’s reply of {shortDate(reading.received_at)}. {reading.match_method === 'sender' ? 'It was matched by the sender’s address, not by the thread. ' : ''}Each line is shown with the vendor’s own words and who confirmed it.</p>
      <ul className="prov-lines">
        {lines.map((f) => (
          <li key={f.key}>
            <b>{f.label}:</b> {show(f)}
            <small className="quiet">
              {f.edited ? ` · a value ${f.confirmed_by} typed, not the vendor’s line` : f.evidence ? ` · the vendor wrote: “${f.evidence}”` : ' · from the reply'}
              {` · confirmed by ${f.confirmed_by}${f.confirmed_at ? `, ${shortDate(f.confirmed_at)}` : ''}`}
            </small>
          </li>
        ))}
      </ul>
    </details>
  );
}

function Entry({ r, category, name, sourcing, chosen, onChosen, onUnchosen, guests, reading }) {
  const [setting, setSetting] = useState('');
  const [state, run] = useAction('That vendor could not be chosen');
  const [back, runBack] = useAction('That could not be taken back');
  const needsSetting = category === 'venue';

  async function choose() {
    const ok = await run(async () => {
      const body = { by: sourcing.by.trim(), ...(needsSetting ? { setting } : {}) };
      const res = await api(`${sourcing.base}/quote-readings/${encodeURIComponent(r.message_id)}/promote`, { method: 'POST', body });
      await sourcing.refresh();
      return res;
    });
    if (ok) onChosen(r.message_id);
  }

  async function takeBack() {
    const ok = await runBack(async () => {
      const res = await api(`${sourcing.base}/quote-readings/${encodeURIComponent(r.message_id)}/unpromote`, { method: 'POST', body: { by: sourcing.by.trim() } });
      await sourcing.refresh();
      return res;
    });
    if (ok) onUnchosen(r.message_id);
  }

  return (
    <li className="rank">
      <span className="rank-n" aria-hidden={r.rank ? undefined : 'true'}>{r.rank || '·'}</span>
      <div>
        <div className="rank-head"><b>{name}</b><span className="rank-total">{inr(r.total_inr)}</span></div>
        {r.deposit_inr != null && <small className="quiet">{r.deposit_inr > 0 ? `Deposit to book: ${inr(r.deposit_inr)} (${shareOf(r.deposit_inr, r.total_inr)}), part of the total, not on top of it` : 'No deposit to book'}</small>}
        <small className="quiet">{r.basis === 'per_plate' ? `${inr(r.price_inr)} a plate, for ${guests} guests` : 'as quoted'}</small>
        <div className="row">
          {needsSetting && !chosen && (
            <select aria-label="Indoor or outdoor" value={setting} onChange={(e) => setSetting(e.target.value)}>
              <option value="">Indoor or outdoor?</option>
              <option value="indoor">Indoor</option>
              <option value="outdoor">Outdoor</option>
            </select>
          )}
          {chosen ? (
            <>
              <small className="state state--confirmed">Open to the planner ✓</small>
              <button type="button" className="linkish" disabled={back.busy || !sourcing.by.trim()} onClick={takeBack}>{back.busy ? 'Taking back…' : 'Take this choice back'}</button>
            </>
          ) : (
            <button type="button" className="chip" disabled={state.busy || !sourcing.by.trim() || (needsSetting && !setting)} onClick={choose}>{state.busy ? 'Saving…' : 'Plan with this vendor'}</button>
          )}
        </div>
        {state.error && <Notice tone="bad">{state.error}</Notice>}
        {back.error && <Notice tone="bad">{back.error}</Notice>}
        <Provenance reading={reading} />
      </div>
    </li>
  );
}

function Category({ c, guests, names, sourcing, chosen, onChosen, onUnchosen, closed, onClose }) {
  const readingOf = (id) => sourcing.readings.find((a) => a.message_id === id);
  const label = categoryLabel(c.category);
  return (
    <Note tone="vendor" tag={label.toUpperCase()} title={c.status === 'ranked' ? `${label}: ranked` : c.status === 'waiting' ? `${label}: waiting` : `${label}: nothing to rank`} data-category={c.category}>
      {c.groups.map((g) => (
        <div key={String(g.taxes_included)}>
          <p className="quiet">{g.taxes_included ? 'Taxes included' : 'Taxes extra: not assumed, so ranked apart'}</p>
          <ol className="ranks">
            {g.ranked.map((r) => <Entry key={r.message_id} r={r} guests={guests} category={c.category} name={names[r.vendor_id] || 'A vendor'} sourcing={sourcing} chosen={chosen.has(r.message_id)} onChosen={onChosen} onUnchosen={onUnchosen} reading={readingOf(r.message_id)} />)}
          </ol>
        </div>
      ))}
      {c.status === 'waiting' && c.open_quotes.length > 0 && (
        <ul className="ranks">
          {c.open_quotes.map((r) => <Entry key={r.message_id} r={r} guests={guests} category={c.category} name={names[r.vendor_id] || 'A vendor'} sourcing={sourcing} chosen={chosen.has(r.message_id)} onChosen={onChosen} onUnchosen={onUnchosen} reading={readingOf(r.message_id)} />)}
        </ul>
      )}
      {c.status === 'waiting' && (
        <>
          {c.comparable === 1 && <p className="quiet">Only one quote so far, so there is nothing to compare it with. You can still plan with it.</p>}
          <p>{c.comparable} of {c.needed} needed quotes are confirmed. The first answer is not simply the winner, so ranking waits.</p>
          {c.comparable > 0 && !closed && <button type="button" className="chip" onClick={onClose}>I’m done waiting: rank what I have</button>}
        </>
      )}
      {c.status === 'none' && <p>No confirmed quote yet for this.</p>}
      {c.pending.map((p) => <p key={p.message_id} className="quiet"><b>{names[p.vendor_id] || 'A vendor'}</b> is not ranked: {p.why}.</p>)}
    </Note>
  );
}

export function ComparePanel({ sourcing }) {
  const [closed, setClosed] = useState(false);
  const [picked, setPicked] = useState(() => new Set());
  const [dropped, setDropped] = useState(() => new Set());
  const closedView = useResource(closed ? `${sourcing.base}/quote-comparison?closed=1` : null);
  const comparison = (closed && closedView.data) || sourcing.comparison;
  const chosen = new Set([...picked, ...(comparison?.categories || []).flatMap((c) => c.chosen || [])].filter((id) => !dropped.has(id)));  // what the server holds, so a reload still shows it
  const names = Object.fromEntries(sourcing.cards.map((c) => [c.vendor_id, nameOf(c)]));
  const out = [<NameNote key="name" by={sourcing.by} setBy={sourcing.setBy} />];

  if (!comparison) return <Collage>{[...out, <Note key="wait" title="Reading…"><p className="quiet">Loading your quotes.</p></Note>]}</Collage>;
  out.push(<Note key="rule" tag="THE RULE" title="How this is ranked"><p>{comparison.rule}</p></Note>);
  const live = comparison.categories.filter((c) => c.comparable || c.pending.length || c.status !== 'none');
  if (!live.length) out.push(<Note key="none" title="Nothing to compare yet"><p>Once two businesses in a category have answered and you have confirmed their lines, they are ranked here. One quote is never a ranking.</p></Note>);
  for (const c of live) out.push(<Category key={c.category} c={c} guests={comparison.guests} names={names} sourcing={sourcing} chosen={chosen} onChosen={(id) => { setDropped((s) => { const n = new Set(s); n.delete(id); return n; }); setPicked((s) => new Set(s).add(id)); }} onUnchosen={(id) => { setPicked((s) => { const n = new Set(s); n.delete(id); return n; }); setDropped((s) => new Set(s).add(id)); }} closed={closed} onClose={() => setClosed(true)} />);
  return <Collage>{out}</Collage>;
}
