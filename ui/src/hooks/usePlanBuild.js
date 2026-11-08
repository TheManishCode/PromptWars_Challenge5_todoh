import { useCallback, useEffect, useState } from 'react';
import { api, errorText } from '../lib/api';

const POLL_MS = 1500;
const GIVE_UP_MS = 5 * 60 * 1000; // the old page gave up after five minutes; a build that never answers must not be waited on forever

/**
 * The plan's build, followed from the server. Starting it names the brief's version and hash, so the plan is built from exactly what was
 * reviewed, and the page then only asks how it is going. A panel that closes and opens again picks the build up where it is.
 * `quotesOnly`: plan only from the vendors who answered, never from built-in records.
 */
export function usePlanBuild(stagedId, onReopened) {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState('');

  const look = useCallback(async () => {
    const res = await api(`/api/intake/${stagedId}/status`);
    if (res.ok) setStatus(res.data);
    return res;
  }, [stagedId]);

  useEffect(() => { look(); }, [look]);
  useEffect(() => {
    if (status?.state !== 'processing') return undefined;
    const began = Date.now();
    const t = setInterval(() => {
      if (Date.now() - began > GIVE_UP_MS) {
        clearInterval(t);
        setError('The plan is taking longer than expected. Check again in a moment.');
      } else look();
    }, POLL_MS);
    return () => clearInterval(t);
  }, [status?.state, look]);

  const start = useCallback(async (quotesOnly = true) => {
    setError('');
    const current = status ?? (await look()).data; // a press before the first look has finished must not read a status that is not there yet
    if (!current?.version) {
      setError('The plan could not be started yet. Try again.');
      return false;
    }
    const res = await api(`/api/intake/${stagedId}/commit`, { method: 'POST', body: { version: current.version, draft_hash: current.draft_hash, from_quotes: quotesOnly } });
    if (res.ok) setStatus(res.data);
    else setError(errorText(res, 'The plan could not be started'));
    return res.ok;
  }, [stagedId, status]);

  const reopen = useCallback(async () => {
    const res = await api(`/api/intake/${stagedId}/reopen`, { method: 'POST', body: {} });
    if (res.ok) { setStatus(null); onReopened?.(res.data); look(); } else setError(errorText(res, 'That could not be reopened'));
  }, [stagedId, onReopened, look]);

  return { status, error, start, reopen };
}
