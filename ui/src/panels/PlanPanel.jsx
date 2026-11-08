import { Collage, Note, Notice } from '../components/Note';
import { usePlanBuild } from '../hooks/usePlanBuild';
import { capitalise, inr, longDate } from '../lib/format';
import { categoryLabel } from '../lib/vendors';
import { PaymentsPanel } from './PaymentsPanel';

function Built({ result, sourcing }) {
  const nodes = result.graph?.nodes || [];
  const event = nodes.find((n) => n.kind === 'event');
  const vendors = nodes.filter((n) => n.kind === 'vendor');
  const tasks = nodes.filter((n) => n.kind === 'task').sort((a, b) => a.start.localeCompare(b.start));
  return <Collage>{[
    event && (
      <Note key="event" tone="story" tag="THE PLAN" title={event.label}>
        <p className="node-copy">{event.city} · {longDate(event.date)}</p>
        <p className="price-amount">{inr(event.total_cost)}<small className="quiet"> of {inr(event.budget)}</small></p>
      </Note>
    ),
    ...vendors.map((v) => (
      <Note key={v.id} tone="vendor" tag={categoryLabel(v.category).toUpperCase()} title={v.label}>
        <p className="price-amount">{inr(v.cost)}</p>
        <p className="quiet">{v.why}</p>
        <small className="quiet">Nothing is booked or paid from here.</small>
      </Note>
    )),
    result.plan_id && <PaymentsPanel key="money" planId={result.plan_id} sourcing={sourcing} />,
    <Note key="next" tone="action" title="What now">
      <p className="quiet">Nothing has been booked or paid. The dashboard keeps this plan, its calendar and its changes. Back in “Compare”, you can change who you plan with.</p>
      <div className="row"><a className="btn btn--small" href="/app">Open it in the dashboard</a></div>
    </Note>,
    tasks.length > 0 && (
      <Note key="tasks" title="The timeline">
        <ol className="timeline">
          {tasks.map((t) => <li key={t.id}><b>{t.label}</b><small>{longDate(t.start.slice(0, 10))}{t.start.length > 10 ? ` · ${t.start.slice(11, 16)}` : ''}</small></li>)}
        </ol>
      </Note>
    ),
    ...(result.violations || []).map((v, i) => <Note key={`v${i}`} tone="warn" title="Something to look at"><p>{reason(v)}</p></Note>),
  ].filter(Boolean)}</Collage>;
}

/** What the server says it could not do, in the words it gave: the rule, what it found, and how to fix it. */
function reason(v) {
  return [capitalise(v.constraint || 'a rule'), v.detail ? `: ${v.detail}` : '', v.repair ? `. To fix: ${v.repair}` : ''].join('');
}

// This page plans only from vendors who answered, so a "no records in the database" message means "no quote yet for that kind", and the fix is to ask one.
const NO_QUOTE = /no (venue|caterer|photographer) records/i;
const friendly = (v) => (v.constraint === 'vendor_coverage' && NO_QUOTE.test(v.detail || '')
  ? `No ${NO_QUOTE.exec(v.detail)[1]} has a quote you confirmed and chose. Ask one under “Find vendors”, confirm their reply, and choose them under “Compare”.`
  : reason(v));

export function PlanPanel({ brief, sourcing, categories }) {
  const { status, error, start, reopen } = usePlanBuild(brief.turn.staged_id, brief.adopt);
  const byCat = Object.fromEntries((sourcing.comparison?.categories || []).map((c) => [c.category, c]));
  const missing = (categories || []).filter((c) => !(byCat[c]?.chosen?.length > 0));  // a vendor must be chosen under Compare, not just have answered
  const toChoose = missing.filter((c) => byCat[c]?.comparable > 0);
  const toAsk = missing.filter((c) => !(byCat[c]?.comparable > 0));
  if (!status) return <Collage><Note title="Reading…"><p className="quiet">Looking at where the plan stands.</p></Note></Collage>;

  if (status.state === 'done') return <Built result={status.result} sourcing={sourcing} />;

  if (status.state === 'processing') {
    return <Collage><Note tone="story" title="Building the plan"><div className="building" role="status"><i /></div><p className="quiet">The schedule and prices are being checked against your facts. A plan that breaks a hard rule is never shown.</p></Note></Collage>;
  }

  if (status.state === 'failed') {
    return <Collage><Note tone="warn" title="The plan could not be built"><p>{status.error}</p>{(status.result?.violations || []).map((v, i) => <p key={i} className="quiet">{friendly(v)}</p>)}<button type="button" className="btn btn--small" onClick={reopen}>Reopen the brief</button></Note></Collage>;
  }

  return (
    <Collage>
    <Note tone="story" title="Plan from who answered">
      <p>The plan is built only from vendors whose quote you confirmed and chose under “Compare”. Nothing is picked from a built-in list.</p>
      {toChoose.length > 0 && <Notice tone="warn">Choose {toChoose.map(categoryLabel).join(', ')} under “Compare”: press “Plan with this vendor” beside the quote you want.</Notice>}
      {toAsk.length > 0 && <Notice tone="warn">No confirmed quote yet for: {toAsk.map(categoryLabel).join(', ')}. Ask a few businesses, confirm a reply, and choose one under “Compare”. The plan needs one of each before it can be built.</Notice>}
      <button type="button" className="btn btn--small" disabled={missing.length > 0} onClick={() => start(true)}>Build the plan</button>
      {error && <Notice tone="bad">{error}</Notice>}
    </Note>
    </Collage>
  );
}
