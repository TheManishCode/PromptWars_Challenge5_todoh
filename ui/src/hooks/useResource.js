import { useCallback, useEffect, useRef, useState } from 'react';
import { api, errorText } from '../lib/api';

/**
 * A GET that can be asked again. `path` null means "not yet" (nothing is fetched). The previous answer stays on
 * screen while the next one loads, so a panel updates in place instead of blinking empty.
 */
export function useResource(path) {
  const [state, setState] = useState({ data: null, loading: Boolean(path), error: '' });
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  const load = useCallback(async () => {
    if (!path) return;
    setState((s) => ({ ...s, loading: true }));
    const res = await api(path);
    if (!alive.current) return;
    setState(res.ok ? { data: res.data, loading: false, error: '' } : { data: null, loading: false, error: errorText(res, 'That could not be loaded') });
  }, [path]);

  useEffect(() => { load(); }, [load]);
  return { ...state, reload: load };
}
