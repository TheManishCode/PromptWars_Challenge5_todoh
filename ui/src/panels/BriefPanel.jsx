import { Collage, Note } from '../components/Note';
import { inr } from '../lib/format';
import { rows } from '../lib/brief';

const GROUPS = [
  ['The event', ['event_type', 'community_subtype', 'city', 'event_date']],
  ['Your people', ['guest_count', 'dietary', 'diet_groups', 'allergens', 'venue_setting']],
];

export function BriefPanel({ turn, messages, onEdit }) {
  const all = rows(turn.draft);
  const by = Object.fromEntries(all.map((r) => [r.key, r]));
  const line = (k) => by[k] && <div key={k} className="review-line"><span>{by[k].label}</span><strong>{by[k].value}</strong></div>;
  return <Collage>{[
    <Note key="original" tone="story"><p className="node-copy">{messages.join('\n\n')}</p></Note>,
    ...GROUPS.map(([title, keys]) => <Note key={title} title={title}>{keys.map(line)}</Note>),
    turn.draft.budget_total != null && <Note key="budget" title="The budget"><div className="review-line"><span>Total</span><strong>{inr(turn.draft.budget_total)}</strong></div><p className="quiet">Never written into a request to a vendor.</p></Note>,
    <Note key="edit" tone="action"><button type="button" className="btn btn--small btn--ghost" onClick={onEdit}>Change the details ↗</button></Note>,
    <Note key="unverified" tone="quiet"><p>Allergy and access words stay unverified until a vendor confirms them in writing.</p></Note>,
  ].filter(Boolean)}</Collage>;
}
