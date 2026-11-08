// One place that talks to the server: the session token, the JSON round trip, and the words shown when it goes wrong.

let token = null;

export async function startSession() {
  const res = await fetch('/api/session', { headers: { Accept: 'application/json' } });
  if (!res.ok) throw new Error('session');
  const data = await res.json();
  token = data.csrf;
  return data;
}

/** Resolves to { ok, status, data } and never rejects: a network failure is a result like any other. */
export async function api(path, { method = 'GET', body } = {}) {
  const headers = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (token) headers['X-EventMate-CSRF'] = token;
  try {
    const res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    const data = await res.json().catch(() => null);
    return { ok: res.ok, status: res.status, data };
  } catch {
    return { ok: false, status: 0, data: { error: 'The server could not be reached. Check that it is running and try again.' } };
  }
}

const words = (s) => String(s || '').replace(/_/g, ' ');

/** The server names the field it refused ("invalid input" alone would not say which), so show that. */
export function errorText(res, fallback) {
  const d = res?.data;
  if (!d || !(d.error || d.detail)) return `${fallback} (${res ? res.status : '?'}).`;
  const why = Array.isArray(d.details)
    ? d.details.map((x) => (x.field ? `${words(String(x.field).split('.').pop())}: ` : '') + x.message).join('; ')
    : '';
  return String(d.error || d.detail) + (why ? `: ${why}` : '');
}
