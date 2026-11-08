import { useCallback, useState } from 'react';
import { useResource } from './useResource';

const NAME_KEY = 'todoh.by';

/**
 * Everything the findings show about a brief that is out looking for vendors: the businesses and what is known of each,
 * the requests written and sent, the replies read, and the comparison. `scope` is the brief's key on the server.
 * Every action ends in `refresh`, so each panel is already showing the new state when the button comes back.
 */
export function useSourcing(scope) {
  const base = `/api/plans/${scope}`;
  const cards = useResource(`${base}/vendor-cards`);
  const drafts = useResource(`${base}/outreach`);
  const readings = useResource(`${base}/quote-readings`);
  const comparison = useResource(`${base}/quote-comparison`);
  const [by, setByState] = useState(() => sessionStorage.getItem(NAME_KEY) || '');

  const setBy = useCallback((name) => {
    setByState(name);
    sessionStorage.setItem(NAME_KEY, name);
  }, []);

  const { reload: reloadCards } = cards;
  const { reload: reloadDrafts } = drafts;
  const { reload: reloadReadings } = readings;
  const { reload: reloadComparison } = comparison;
  const refresh = useCallback(() => Promise.all([reloadCards(), reloadDrafts(), reloadReadings(), reloadComparison()]), [reloadCards, reloadDrafts, reloadReadings, reloadComparison]);

  const list = (r, key) => (key ? r.data?.[key] : r.data) || [];
  return {
    base,
    by,
    setBy,
    refresh,
    cards: list(cards, 'cards'),
    drafts: list(drafts),
    readings: list(readings, 'quotes'),
    comparison: comparison.data,
    loading: cards.loading && !cards.data,
    error: cards.error,
  };
}
