import { useCallback, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Findings } from './scenes/Findings';
import { Opening } from './scenes/Opening';
import { Review } from './scenes/Review';
import { useBrief } from './hooks/useBrief';
import { api, startSession } from './lib/api';
import { forget, hashFor, readHash, save, saved } from './lib/place';
import './styles/tokens.css';
import './styles/opening.css';
import './styles/rooms.css';
import './styles/findings.css';

/**
 * The page is one world with three places in it: the opening story, the review of what was read, and the findings.
 * The brief lives on the server from the first message; this decides which place is on screen, and keeps that in the
 * address so Back, Forward and a reload all land where the person was.
 */
function App() {
  const brief = useBrief();
  const [place, setPlace] = useState(() => readHash());
  const [reached, setReached] = useState(() => readHash().stage === 'findings'); // the findings are not started until the person gets there
  const [resuming, setResuming] = useState(() => Boolean(saved()) && readHash().stage !== 'opening');
  const [words, setWords] = useState(() => saved()?.words || '');
  const session = useRef(null);
  const { stage, node } = place;

  useEffect(() => { session.current = startSession().catch(() => null); }, []);

  // Pick up a brief this visit already started: the server still holds it, so a reload costs nothing.
  const { adopt } = brief;
  useEffect(() => {
    const mine = saved();
    const want = readHash();
    if (!mine || want.stage === 'opening') { setResuming(false); return undefined; }
    let alive = true;
    (async () => {
      await session.current;
      const res = await api(`/api/intake/${mine.stagedId}`);
      if (!alive) return;
      if (res.ok && res.data) {
        adopt(res.data, [mine.words]);
        if (res.data.state && res.data.state !== 'pending_validation') setPlace({ stage: 'findings', node: want.node || 'plan' });
      } else {
        forget(); // timed out or gone: start again from the opening, and say nothing alarming
        setPlace({ stage: 'opening', node: null });
        history.replaceState(null, '', location.pathname);
      }
      setResuming(false);
    })();
    return () => { alive = false; };
  }, [adopt]);

  // One entry in the browser's history for each place the person goes.
  const go = useCallback((next, nextNode = null, { replace = false } = {}) => {
    if (next === 'findings') setReached(true);
    setPlace({ stage: next, node: nextNode });
    const url = hashFor(next, nextNode).trim() || location.pathname;
    if (replace) history.replaceState(null, '', url);
    else history.pushState(null, '', url);
  }, []);

  useEffect(() => {
    const onPop = () => {
      const want = readHash();
      if (want.stage !== 'opening' && !brief.turn) { setPlace({ stage: 'opening', node: null }); return; } // nothing to show there yet
      if (want.stage === 'findings') setReached(true);
      setPlace(want);
    };
    addEventListener('popstate', onPop);
    return () => removeEventListener('popstate', onPop);
  }, [brief.turn]);

  const start = useCallback(async (text) => {
    await session.current;
    if (await brief.read(text)) {
      setWords(text);
      go('review');
    }
  }, [brief.read, go]);

  const stagedId = brief.turn?.staged_id;
  useEffect(() => { if (stagedId) save(stagedId, words || brief.messages[0] || ''); }, [stagedId, words, brief.messages]);

  useEffect(() => {
    document.body.style.overflow = stage === 'opening' ? '' : 'hidden';
    const frame = requestAnimationFrame(() => {
      if (stage === 'findings' && !node) document.querySelector('.finding-room .headline')?.focus({ preventScroll: true });
      else if (stage === 'opening' && brief.turn) document.querySelector('#idea')?.focus({ preventScroll: true });
    });
    return () => { cancelAnimationFrame(frame); document.body.style.overflow = ''; };
  }, [stage, node, brief.turn]);

  if (resuming) return <div className="resuming" role="status" aria-live="polite"><span>to;doh</span><p>Picking up where you left off…</p></div>;

  return (
    <>
      <div className="opening-root" inert={stage !== 'opening'} aria-hidden={stage !== 'opening'}>
        <Opening onIdea={start} busy={brief.busy} error={stage === 'opening' ? brief.error : ''} paused={stage !== 'opening'} initialText={words} />
      </div>
      {stage !== 'opening' && brief.turn && (
        <main className="continuum" aria-label="Planning">
          {reached && <Findings key={brief.turn.staged_id} brief={brief} inactive={stage !== 'findings'} active={node} onActive={(n) => go('findings', n)} onEdit={() => go('review')} />}
          <section className="room review-room" inert={stage !== 'review'}>
            <Review brief={brief} onDone={() => go('findings')} onBack={() => go('opening')} />
          </section>
        </main>
      )}
    </>
  );
}

createRoot(document.getElementById('root')).render(<App />);
