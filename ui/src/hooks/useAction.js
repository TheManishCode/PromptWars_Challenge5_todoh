import { useCallback, useState } from 'react';
import { errorText } from '../lib/api';

/**
 * One thing a panel does to the server: whether it is under way and what went wrong. `run` takes a function that
 * resolves to the api result and gives back true when it worked, so a button can wait on it and a card can show the error.
 */
export function useAction(fallback = 'That did not work') {
  const [state, set] = useState({ busy: false, error: '' });
  const run = useCallback(async (call) => {
    set({ busy: true, error: '' });
    const res = await call();
    set({ busy: false, error: res.ok ? '' : errorText(res, fallback) });
    return res.ok;
  }, [fallback]);
  return [state, run];
}
