import { NODES } from '../graph/nodes';

const ORDER = NODES.filter((n) => n.id !== 'overview').map((n) => n.id);
const label = (id) => NODES.find((n) => n.id === id).label;

/**
 * What to do next, at the foot of every thought: back to the one before, on to the one after. The way on says why it is worth taking
 * when something is waiting there (a request to approve, a reply to read, a quote to rank), so the person is never left to guess the order.
 */
function why(next, { drafts, readings, comparison, cards }) {
  if (next === 'message' && cards.some((c) => c.contact_ready)) return 'Your requests are ready to write.';
  if (next === 'replies' && drafts.some((d) => d.kind === 'request' && ['sent', 'marked_sent'].includes(d.status))) return 'Answers land here, or paste one you got.';
  if (next === 'compare' && readings.some((r) => r.comparable)) return 'You have a quote ready to rank.';
  if (next === 'plan' && (comparison?.categories || []).some((c) => c.status === 'ranked')) return 'You have chosen who to plan with.';
  return '';
}

export function StepNav({ active, go, sourcing }) {
  const i = ORDER.indexOf(active);
  if (active === 'overview') return <nav className="step-nav" aria-label="Steps"><button type="button" className="chip" onClick={() => go('brief')}>Start from your brief</button></nav>;
  const prev = ORDER[i - 1];
  const next = ORDER[i + 1];
  return (
    <nav className="step-nav" aria-label="Steps">
      {prev ? <button type="button" className="linkish" onClick={() => go(prev)}>← {label(prev)}</button> : <span />}
      {next ? (
        <span className="step-next">
          {why(next, sourcing) && <small className="quiet">{why(next, sourcing)}</small>}
          <button type="button" className="btn btn--small" onClick={() => go(next)}>Next: {label(next)} →</button>
        </span>
      ) : <a className="btn btn--small" href="/app">Open in the dashboard</a>}
    </nav>
  );
}
