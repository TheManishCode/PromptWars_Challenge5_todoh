import { useEffect, useState } from 'react';
import { api } from '../lib/api';

// A Google listing as fetched in this visit: shown, never kept past it (the page holds it in memory, nothing more).
const seen = new Map();

/** The live details of a Google-listed business. `idle` when the business has no place id. */
export function useListing(placeId) {
  const [state, setState] = useState(() => (placeId && seen.has(placeId) ? { status: 'ready', data: seen.get(placeId) } : { status: placeId ? 'loading' : 'idle', data: null }));

  useEffect(() => {
    if (!placeId || seen.has(placeId)) return undefined;
    let alive = true;
    api(`/api/places/${encodeURIComponent(placeId)}`).then((res) => {
      if (!alive) return;
      if (res.ok && res.data) {
        seen.set(placeId, res.data);
        setState({ status: 'ready', data: res.data });
      } else {
        setState({ status: res.status === 502 || res.status === 503 ? 'down' : 'gone', data: null });
      }
    });
    return () => { alive = false; };
  }, [placeId]);

  return state;
}
