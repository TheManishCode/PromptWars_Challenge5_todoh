// What the review shows of a brief, and what each part is called. The server decides what is understood; this only words it.
import { capitalise, inr, longDate } from './format';

export const FIELDS = [
  ['event_type', 'Event'],
  ['community_subtype', 'Kind of gathering'],
  ['guest_count', 'Guests'],
  ['city', 'City'],
  ['event_date', 'Date'],
  ['budget_total', 'Budget'],
  ['dietary', 'Food'],
  ['diet_groups', 'Food groups'],
  ['allergens', 'Allergies'],
  ['allergy_unclear', 'Allergy to clarify'],
  ['diet_notes', 'Food to confirm'],
  ['diet_unclear', 'Food rule to clarify'],
  ['venue_setting', 'Setting'],
  ['services', 'Services'],
  ['reminders', 'Reminders'],
];

/** The facts that can be typed straight onto the review. The server's schema decides what is acceptable. */
export const EDITABLE = { guest_count: 'number', city: 'text', event_date: 'date', budget_total: 'number' };
/** Needed to plan at all. */
export const REQUIRED = ['city', 'guest_count', 'event_date', 'budget_total'];

export const NO_ALLERGY = 'None stated. If anyone has an allergy, write it before you plan.';

export const fieldLabel = (key) => FIELDS.find(([k]) => k === key)?.[1] ?? capitalise(key);

function group(g) {
  const who = g.count ? `${g.count} ${g.count === 1 ? 'guest' : 'guests'}` : g.share || 'some guests';
  return `${g.diet ? capitalise(g.diet) : 'Other food need'}: ${who}${g.exception ? ' (an exception)' : ''}`;
}

/** The value as a person would read it, or null when the draft holds nothing for it. */
export function showField(draft, key) {
  const v = draft?.[key];
  if (v === null || v === undefined || v === '' || (Array.isArray(v) && !v.length)) return null;
  if (key === 'budget_total') return inr(v);
  if (key === 'event_date') return longDate(v);
  if (key === 'diet_groups') return v.map(group).join('; ');
  if (Array.isArray(v)) return v.map(capitalise).join(', ');
  return key === 'event_type' || key === 'venue_setting' ? capitalise(v) : String(v);
}

/** Every row the review card shows. A missed allergy must not look the same as "none", so that row always says what it holds. */
export function rows(draft) {
  const out = [];
  for (const [key, label] of FIELDS) {
    let value = showField(draft, key);
    if (value === null && key === 'allergens' && !(draft?.allergy_unclear || []).length) value = NO_ALLERGY;
    if (value !== null) out.push({ key, label, value, raw: draft[key], editable: key in EDITABLE });
  }
  return out;
}

/** The kinds of vendor a plan needs: a venue always, a caterer whenever there are food needs (the server adds it the same way), and the rest only when asked for. */
export function neededCategories(draft) {
  const wanted = ['venue', ...(draft?.services || []).filter((c) => c !== 'venue')];
  if ((draft?.dietary || []).length && !wanted.includes('caterer')) wanted.push('caterer');
  return wanted;
}
