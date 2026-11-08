import { useState } from 'react';
import { Collage, Note, Notice } from '../components/Note';
import { useAction } from '../hooks/useAction';
import { useDebounced } from '../hooks/useDebounced';
import { useResource } from '../hooks/useResource';
import { api } from '../lib/api';
import { categoryLabel } from '../lib/vendors';
import { NameNote } from './NameNote';

const STATUS = {
  prepared: 'Waiting for your approval',
  approved: 'Approved, sending',
  sending: 'Sending',
  sent: 'Accepted by the mail server (that is not proof it was delivered)',
  failed: 'Could not be sent',
  outcome_unknown: 'Sent? The channel did not say',
  denied: 'You chose not to send',
  expired: 'Expired before you approved it',
  marked_sent: 'You sent it yourself',
  blocked: 'Blocked',
  stale: 'Out of date: the brief changed',
};

function Preview({ category, sourcing }) {
  const sender = useDebounced(sourcing.by.trim(), 500);
  const { data, error } = useResource(`${sourcing.base}/request-text?category=${category}&sender=${encodeURIComponent(sender)}`);
  return (
    <Note title={`What ${categoryLabel(category).toLowerCase()}s are asked`} tag="THE EXACT WORDS">
      {data ? <pre className="exact">{data.subject}{'\n\n'}{data.body}</pre> : <p className="quiet">{error || 'Writing it…'}</p>}
      <p className="quiet">It states your event, never your budget. The vendor answers in the same lines, so the reply can be read.</p>
    </Note>
  );
}

const NO_SENDING = 'Sending is switched off in this workspace, so nothing was sent. Use “Send it yourself” below: copy the message, send it from your own email or WhatsApp, then press “I sent it”.';

function SendYourself({ draft, act, busy, again = false }) {
  const [copied, setCopied] = useState(false);
  const text = `${draft.subject ? `${draft.subject}\n\n` : ''}${draft.body}`;
  const wa = draft.channel === 'whatsapp' && /^https:\/\/wa\.me\/\d{8,15}\?text=/.test(draft.link || '') ? draft.link : null; // WhatsApp's own click-to-chat link, the request already typed in: the person presses send in their own WhatsApp
  const mail = draft.channel === 'email' && draft.recipient ? `mailto:${draft.recipient}?subject=${encodeURIComponent(draft.subject || '')}&body=${encodeURIComponent(draft.body)}` : null;
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch {
      setCopied(false);
    }
  }
  return (
    <details className="self-send">
      <summary>{again ? 'Send it again yourself' : 'Send it yourself instead'}</summary>
      <p className="quiet">{again ? 'Nothing is sent from here. Send the same message from your own mail or WhatsApp, or call them, and paste what they say under Replies.' : 'Nothing is sent from here. Send it from your own mail or WhatsApp, press “I sent it”, and when they answer paste their reply under Replies.'}</p>
      <div className="row">
        <button type="button" className="chip" onClick={copy}>{copied ? 'Copied' : 'Copy the message'}</button>
        {mail && <a className="chip" href={mail}>Open in my email</a>}
        {wa && <a className="chip" href={wa} target="_blank" rel="noopener noreferrer">Open in WhatsApp</a>}
        {!again && <button type="button" className="chip" disabled={busy} onClick={() => act('sent', {})}>I sent it</button>}
      </div>
    </details>
  );
}

function Draft({ draft, sourcing }) {
  const [state, run] = useAction('That did not work');
  const act = (path, body) => run(async () => {
    let res = await api(`${sourcing.base}/outreach/${draft.draft_id}/${path}`, { method: 'POST', body });
    if (res.status === 403 && res.data?.code === 'sending_disabled') res = { ...res, data: { error: NO_SENDING } };
    await sourcing.refresh();
    return res;
  });
  return (
    <Note tone="vendor" tag={`${categoryLabel(draft.category).toUpperCase()} · ${draft.channel.toUpperCase()}`} title={draft.vendor_name} busy={state.busy}>
      <p className="quiet" data-delivery={draft.delivery?.state}>To {draft.recipient} · {draft.delivery?.label || STATUS[draft.status] || draft.status}</p>
      {draft.subject && <p className="exact-subject">{draft.subject}</p>}
      <pre className="exact">{draft.body}</pre>
      {draft.status === 'prepared' && (
        <div className="row">
          <button type="button" className="btn btn--small" disabled={state.busy} onClick={() => act('approve', { content_hash: draft.content_hash })}>{state.busy ? 'Working…' : 'Approve exactly this and send'}</button>
          <button type="button" className="linkish" disabled={state.busy} onClick={() => act('deny', {})}>Don’t send</button>
        </div>
      )}
      {draft.status === 'prepared' && <SendYourself draft={draft} act={act} busy={state.busy} />}
      {draft.delivery?.silent && (
        <>
          <Notice tone="warn">No reply yet after {draft.delivery.waiting_days} days. Nothing chases them for you: ask again yourself, or call.</Notice>
          <SendYourself draft={draft} act={act} busy={state.busy} again />
        </>
      )}
      {draft.status === 'prepared' && draft.link && <a className="linkish" href={draft.link} target="_blank" rel="noopener noreferrer">Or send it yourself from your own app</a>}
      {(draft.error || state.error) && <Notice tone="bad">{state.error || draft.error}</Notice>}
    </Note>
  );
}

export function MessagePanel({ categories, sourcing }) {
  const [state, run] = useAction('The requests could not be written');
  const [skipped, setSkipped] = useState([]);
  const ready = sourcing.cards.filter((c) => c.contact_ready && !c.blocked);
  const requests = sourcing.drafts.filter((d) => d.kind === 'request');
  const readyCategories = categories.filter((c) => ready.some((r) => r.category === c));

  async function write() {
    const ok = await run(async () => {
      const res = await api(`${sourcing.base}/outreach/request-all`, { method: 'POST', body: { sender_name: sourcing.by.trim() } });
      if (res.ok) setSkipped(res.data?.skipped || []);
      return res;
    });
    if (ok) await sourcing.refresh();
  }

  const out = [<NameNote key="name" by={sourcing.by} setBy={sourcing.setBy} />];
  if (!ready.length) {
    out.push(<Note key="none" tone="warn" title="Nobody to ask yet"><p>Confirm an email or WhatsApp for at least one business under “Find vendors”. Nothing is ever sent to an address you have not confirmed.</p></Note>);
    return <Collage>{out}</Collage>;
  }
  for (const category of readyCategories) out.push(<Preview key={`preview-${category}`} category={category} sourcing={sourcing} />);
  out.push(
    <Note key="write" tone="action" busy={state.busy}>
      <button type="button" className="btn btn--small" disabled={state.busy || !sourcing.by.trim()} onClick={write}>{state.busy ? 'Writing…' : 'Write one request per business'}</button>
      <p className="quiet">Writing is not sending. Each request below waits for you.</p>
      {skipped.map((s) => <p key={s} className="quiet">{s}</p>)}
      {state.error && <Notice tone="bad">{state.error}</Notice>}
    </Note>,
  );
  for (const d of requests) out.push(<Draft key={d.draft_id} draft={d} sourcing={sourcing} />);
  return <Collage>{out}</Collage>;
}
