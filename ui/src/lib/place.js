// Where the person is, kept in the address and the session, so a reload, Back and Forward all land where they left.
//   #review            the review of what was read
//   #findings          the hub of the findings
//   #findings/vendors  one thought of the findings
// The brief itself lives on the server; only its id and the person's own words are kept here, for this visit.

const KEY = 'todoh.brief';
export const STEPS = ['brief', 'vendors', 'message', 'replies', 'compare', 'plan', 'overview'];

export function readHash(hash = location.hash) {
  const [stage, node] = hash.replace(/^#/, '').split('/');
  if (stage === 'review') return { stage: 'review', node: null };
  if (stage === 'findings') return { stage: 'findings', node: STEPS.includes(node) ? node : null };
  return { stage: 'opening', node: null };
}

export const hashFor = (stage, node) => (stage === 'opening' ? ' ' : `#${stage}${node ? `/${node}` : ''}`);

export function saved() {
  try { return JSON.parse(sessionStorage.getItem(KEY)) || null; } catch { return null; }
}
export function save(stagedId, words) {
  try { sessionStorage.setItem(KEY, JSON.stringify({ stagedId, words: String(words || '').slice(0, 2000) })); } catch { /* private mode: resuming is a convenience */ }
}
export function forget() {
  try { sessionStorage.removeItem(KEY); } catch { /* nothing to forget */ }
}
