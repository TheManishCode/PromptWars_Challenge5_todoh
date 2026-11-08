import { Collage, Note } from '../components/Note';
import { plural } from '../lib/format';

/** Where each step stands, in words, and the way into it. */
export function OverviewPanel({ sourcing, go }) {
  const { cards, drafts, readings, comparison } = sourcing;
  const requests = drafts.filter((d) => d.kind === 'request');
  const sent = requests.filter((d) => ['sent', 'marked_sent'].includes(d.status));
  const waitingOk = requests.filter((d) => d.status === 'prepared');
  const comparable = readings.filter((r) => r.comparable);
  const ranked = (comparison?.categories || []).filter((c) => c.status === 'ranked');
  const holding = (comparison?.categories || []).filter((c) => c.status === 'waiting' && c.comparable > 0);
  const steps = [
    ['vendors', 'Who to ask', cards.length ? `${plural(cards.length, 'business', 'businesses')} on your list, ${cards.filter((c) => c.contact_ready).length} with a confirmed contact.` : 'Nobody on the list yet.', cards.length > 0],
    ['message', 'The ask', requests.length ? `${plural(requests.length, 'request', 'requests')} written, ${sent.length} sent${waitingOk.length ? `, ${waitingOk.length} waiting for your approval` : ''}.` : 'Nothing written yet.', sent.length > 0],
    ['replies', 'Replies', readings.length ? `${plural(readings.length, 'reply', 'replies')} read, ${comparable.length} fully confirmed.` : 'Nothing has come back.', comparable.length > 0],
    ['compare', 'Compare', ranked.length ? `${plural(ranked.length, 'category', 'categories')} ranked.` : holding.length ? 'Ranking waits for a second answer in each kind. You can rank now from what you have.' : 'Nothing ranked yet.', ranked.length > 0],
  ];
  return <Collage>{steps.map(([id, title, text, done]) => (
    <Note key={id} title={title} tone={done ? 'vendor' : 'plain'} tag={done ? 'DONE' : 'NEXT'}>
      <p>{text}</p>
      <button type="button" className="linkish" onClick={() => go(id)}>Open ↗</button>
    </Note>
  ))}</Collage>;
}
