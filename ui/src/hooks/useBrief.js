import { useCallback, useReducer, useRef } from 'react';
import { api, errorText } from '../lib/api';

const initial = { turn: null, messages: [], busy: false, error: '' };

function reducer(state, action) {
  switch (action.type) {
    case 'busy':
      return { ...state, busy: true, error: '' };
    case 'turn':
      return { ...state, busy: false, error: '', turn: action.turn, messages: action.messages ?? state.messages };
    case 'failed':
      return { ...state, busy: false, error: action.error };
    case 'reset':
      return initial;
    default:
      return state;
  }
}

/**
 * The conversation that turns words into a brief. The server reads each message and keeps the draft;
 * this hook only carries the draft back, as the page always has: only the newest message is sent, with the
 * fact the last question was about, and the brief's place on the server once it has one.
 */
export function useBrief() {
  const [state, dispatch] = useReducer(reducer, initial);
  const latest = useRef(state);
  latest.current = state;

  /** Resolves to true when the server read it. `answer` is an option of an open question that was pressed. */
  const read = useCallback(async (text, answer) => {
    const message = String(text || '').trim();
    if (latest.current.busy || (!message && !answer)) return false;
    const { turn, messages } = latest.current;
    dispatch({ type: 'busy' });
    const res = await api('/api/intake', {
      method: 'POST',
      body: { messages: [message], draft: turn?.draft, expecting: turn?.asking || undefined, answer, staged_id: turn?.staged_id || undefined },
    });
    if (!res.ok || !res.data) {
      dispatch({ type: 'failed', error: errorText(res, 'The brief could not be read') });
      return false;
    }
    dispatch({ type: 'turn', turn: res.data, messages: answer ? messages : [...messages, message] });
    return true;
  }, []);

  const adopt = useCallback((turn, messages) => dispatch({ type: "turn", turn, messages }), []);

  const reload = useCallback(async () => {
    const id = latest.current.turn?.staged_id;
    if (!id) return;
    const res = await api(`/api/intake/${id}`);
    if (res.ok && res.data) dispatch({ type: 'turn', turn: res.data });
  }, []);

  /** Type a fact onto the review. A brief that changed elsewhere is read again and the person is told. */
  const edit = useCallback(async (patch) => {
    const { turn } = latest.current;
    if (!turn?.staged_id) return false;
    dispatch({ type: 'busy' });
    const res = await api(`/api/intake/${turn.staged_id}/fields`, { method: 'PATCH', body: { fields: patch, version: turn.version } });
    if (res.ok && res.data) {
      dispatch({ type: 'turn', turn: res.data });
      return true;
    }
    dispatch({ type: 'failed', error: res.status === 409 ? 'This brief changed somewhere else, so it was loaded again. Check it and try again.' : errorText(res, 'That could not be used') });
    if (res.status === 409 || res.status === 410) reload();
    return false;
  }, [reload]);

  const reset = useCallback(() => dispatch({ type: 'reset' }), []);

  return { ...state, read, edit, reload, adopt, reset };
}
