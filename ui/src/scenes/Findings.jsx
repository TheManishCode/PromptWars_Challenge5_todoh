import { useEffect, useRef, useState } from 'react';
import { Graph } from '../graph/Graph';
import { NODES, TITLES } from '../graph/nodes';
import { useConnector } from '../hooks/useConnector';
import { useSourcing } from '../hooks/useSourcing';
import { neededCategories } from '../lib/brief';
import { longDate } from '../lib/format';
import { readCard } from '../lib/vendors';
import { BriefPanel } from '../panels/BriefPanel';
import { ComparePanel } from '../panels/ComparePanel';
import { MessagePanel } from '../panels/MessagePanel';
import { OverviewPanel } from '../panels/OverviewPanel';
import { PlanPanel } from '../panels/PlanPanel';
import { StepNav } from '../components/StepNav';
import { RepliesPanel } from '../panels/RepliesPanel';
import { VendorsPanel } from '../panels/VendorsPanel';

const WATCH_MS = 30000; // while a request is out, look again this often

/** The small numbers on the bubbles: what is waiting at each step. */
function badges({ cards, drafts, readings, comparison }) {
  const approve = drafts.filter((d) => d.kind === 'request' && d.status === 'prepared').length;
  const ranked = (comparison?.categories || []).filter((c) => c.status === 'ranked').length;
  return {
    vendors: cards.length || '',
    message: approve || '',
    replies: readings.length || '',
    compare: ranked || '',
  };
}

export function Findings({ brief, inactive, onEdit, active, onActive }) {
  const { turn, messages } = brief;
  const draft = turn.draft;
  const sourcing = useSourcing(`brief_${turn.staged_id}`);
  const [extra, setExtra] = useState([]); // kinds of business the person added beyond what the brief names
  const categories = [...new Set([...neededCategories(draft), ...extra])];
  const ref = useRef(null);
  const line = useConnector(ref, active);
  const { refresh } = sourcing;
  const waiting = sourcing.cards.some((c) => readCard(c).waiting);

  function select(id) {
    onActive(id);
    if (ref.current?.scrollTop > 160) ref.current.scrollTo({ top: 0, behavior: 'smooth' });
  }
  function close() {
    onActive(null);
    ref.current?.scrollTo({ top: 0, behavior: 'smooth' });
    requestAnimationFrame(() => ref.current?.querySelector('[data-node]')?.focus({ preventScroll: true }));
  }

  const seen = useRef(false);
  useEffect(() => { // what changed while the review was open is read again (the first time round the resources have just loaded)
    if (!inactive && seen.current) refresh();
    seen.current = true;
  }, [inactive, refresh]);

  useEffect(() => { // a quote on its way: the page looks again without being asked
    if (inactive || !waiting) return undefined;
    const t = setInterval(() => { if (!document.hidden) refresh(); }, WATCH_MS);
    return () => clearInterval(t);
  }, [inactive, waiting, refresh]);

  useEffect(() => {
    if (inactive) return undefined;
    const onKey = (e) => { if (e.key === 'Escape' && !e.target.matches?.('input, textarea, select')) close(); };
    addEventListener('keydown', onKey);
    return () => removeEventListener('keydown', onKey);
  }, [inactive]);

  const title = TITLES[active] || null;
  const panel = {
    brief: <BriefPanel turn={turn} messages={messages} onEdit={onEdit} />,
    vendors: <VendorsPanel categories={categories} extra={extra} setExtra={setExtra} sourcing={sourcing} />,
    message: <MessagePanel categories={categories} sourcing={sourcing} />,
    replies: <RepliesPanel sourcing={sourcing} />,
    compare: <ComparePanel sourcing={sourcing} />,
    plan: <PlanPanel brief={brief} sourcing={sourcing} categories={categories} />,
    overview: <OverviewPanel sourcing={sourcing} go={select} />,
  }[active];

  return (
    <section className={`room finding-room${active ? ' reading' : ''}`} ref={ref} inert={inactive} aria-label="Connected findings">
      <div className="scene-top">
        <a className="wordmark" href="/" aria-label="to;doh home">to;doh</a>
        <span>YOUR GATHERING</span>
        {active ? <button id="reading-close" type="button" onClick={close}>Close notes ×</button> : <button id="back-review" type="button" onClick={onEdit}>Revisit the brief ↗</button>}
      </div>

      <div className="finding-board">
        <div className="finding-hub">
          <div className="hub-title">
            <h2 className="headline" tabIndex={-1}>Let’s open<br /><em>this up.</em></h2>
            <p className="hub-context">{draft.city} · {draft.guest_count} guests · {longDate(draft.event_date)}</p>
          </div>
          <Graph active={active} onSelect={select} badges={badges(sourcing)} />
          {!active && (
            <div className="hub-invite">
              <span className="eyebrow-line">The pieces are coming together</span>
              <h2 className="headline headline--small">Your gathering.<br /><em>Taking shape.</em></h2>
              <p>Follow a thought to explore the possibilities.<br />Every choice stays yours.</p>
              <small>Tap a bubble. Arrow keys explore.</small>
            </div>
          )}
          <nav className="thought-key" aria-label="Thoughts">
            {NODES.map(({ id, label }, i) => (
              <button key={id} type="button" aria-current={active === id ? 'true' : undefined} onClick={() => select(id)}>
                <span>{String(i + 1).padStart(2, '0')}</span>{label}
              </button>
            ))}
          </nav>
        </div>

        {active && (
          <article className="node-page" key={active} aria-label={`Notes for ${title[0].toLowerCase()}`}>
            <p className="eyebrow-line">{title[0]} / CONNECTED FINDING</p>
            <h2 className="node-heading">{title[1][0]} <em>{title[1][1]}</em></h2>
            <p className="node-intro">{title[2]}</p>
            {sourcing.error && <p className="error-line" role="alert">{sourcing.error}</p>}
            {panel}
            <StepNav active={active} go={select} sourcing={sourcing} />
          </article>
        )}
      </div>
      {active && line && <svg className="note-connection" aria-hidden="true"><path d={line} /></svg>}

      <footer className="reading-footer">
        <span>Nothing is sent, booked or paid without your approval of the exact words.</span>
        <button type="button" className="link-button" onClick={() => select('overview')}>Where things stand ↗</button>
      </footer>
    </section>
  );
}
