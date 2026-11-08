const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export const words = (s) => String(s ?? '').replace(/_/g, ' ');
export const capitalise = (s) => {
  const w = words(s);
  return w.charAt(0).toUpperCase() + w.slice(1);
};
export const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

/** Indian digit grouping: 1,50,000. */
export function inr(n) {
  if (typeof n !== 'number' || Number.isNaN(n)) return '–';
  let rest = String(Math.round(Math.abs(n)));
  let out = '';
  let first = true;
  while (rest.length) {
    const take = first ? 3 : 2;
    out = rest.slice(-take) + (out ? `,${out}` : '');
    rest = rest.slice(0, -take);
    first = false;
  }
  return `${n < 0 ? '-' : ''}₹${out}`;
}

/** "2026-12-12" -> "12 Dec 2026". Anything else is shown as it came. */
export function longDate(value) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(value || '');
  if (!m) return value || '';
  return `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}`;
}

export function shortDate(value) {
  const t = new Date(value);
  return Number.isNaN(t.getTime()) ? '' : t.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}
