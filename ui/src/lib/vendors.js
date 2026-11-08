// How a vendor card's facts are worded. The server holds the facts and where each came from; this only decides how they read.
import { capitalise } from './format';

export const CATEGORIES = { venue: 'Venue', caterer: 'Caterer', photographer: 'Photographer' };
export const categoryLabel = (c) => CATEGORIES[c] || capitalise(c);

export const SOURCE = {
  user_confirmed: 'You confirmed this',
  vendor_website: 'Found on the vendor’s own website',
  osm_listing: 'OpenStreetMap listing',
  vendor_quote: 'A quote you confirmed',
  vendor_reply: 'The vendor’s reply',
  app_request: 'A request written here',
  earlier_quote: 'Quoted to someone else',
  contact_log: 'You wrote this down',
};

export const FACT = { name: 'Name', website: 'Website', 'contact.email': 'Email', 'contact.whatsapp': 'WhatsApp', quote: 'Quote', earlier_quote: 'Earlier quote', request: 'Request', reply: 'Reply', contact_log: 'Contacted' };

/** "INR 650 per plate for 40 guests" -> "₹650 per plate · 40 guests" */
export const rupees = (text) => String(text || '').replace(/^INR\s+/, '₹').replace(/ for (\d+) guests?$/, ' · $1 guests');

const ASKED = new Set(['approved', 'sending', 'sent', 'marked_sent']);

/** Reads a card's facts as the parts a person looks for: the price on show, whether a fresh one is awaited, and the ways to reach the business. */
export function readCard(card) {
  const facts = card.facts || [];
  const own = facts.filter((f) => f.field === 'quote');
  const earlier = facts.filter((f) => f.field === 'earlier_quote');
  const requests = facts.filter((f) => f.field === 'request');
  const replies = facts.filter((f) => f.field === 'reply');
  const current = own.find((f) => f.status === 'valid');
  // a request is out and nothing has come back: that is the time an earlier quote is worth showing
  const waiting = requests.some((f) => ASKED.has(f.status)) && !replies.length && !current;
  const shown = current || own[0] || earlier[0] || null;
  return {
    shown,
    isCurrent: Boolean(current),
    isEarlier: Boolean(shown) && !current,
    fromSomeoneElse: Boolean(shown) && shown.field === 'earlier_quote',
    waiting,
    replied: replies.length > 0,
    requests,
    replies,
    emails: facts.filter((f) => f.field === 'contact.email' && f.status !== 'rejected'),
    whatsapp: facts.filter((f) => f.field === 'contact.whatsapp' && f.status !== 'rejected'),
    found: facts.filter((f) => f.field === 'contact.email' && f.status === 'found'),
  };
}

export const safeUrl = (u) => (/^https?:\/\/[^\s<>"']+$/.test(u || '') ? u : null);

/** The label a business goes by while its own name is not one the user has confirmed (a Google listing's name is not stored). */
export const nameOf = (card, listing) => listing?.name || (card.name_confirmed ? card.name : 'Business from a listing');
