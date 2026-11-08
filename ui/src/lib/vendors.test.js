import { describe, expect, it } from 'vitest';
import { inr, longDate } from './format';
import { readCard, rupees } from './vendors';

const fact = (field, status, extra = {}) => ({ field, status, value: 'INR 650 per plate for 40 guests', ...extra });

describe('readCard', () => {
  it('puts a current quote forward and does not call it earlier', () => {
    const r = readCard({ facts: [fact('quote', 'valid'), fact('earlier_quote', 'old')] });
    expect(r.isCurrent).toBe(true);
    expect(r.isEarlier).toBe(false);
    expect(r.shown.field).toBe('quote');
  });

  it('keeps an earlier quote from someone else on show while a request is out', () => {
    const r = readCard({ facts: [fact('earlier_quote', 'valid'), fact('request', 'sent')] });
    expect(r.isEarlier).toBe(true);
    expect(r.fromSomeoneElse).toBe(true);
    expect(r.waiting).toBe(true);
  });

  it('stops waiting once a reply is in, or once a quote is confirmed', () => {
    expect(readCard({ facts: [fact('request', 'sent'), fact('reply', 'received')] })).toMatchObject({ waiting: false, replied: true });
    expect(readCard({ facts: [fact('request', 'sent'), fact('quote', 'valid')] }).waiting).toBe(false);
  });

  it('is not waiting for a request that was only written, not approved', () => {
    expect(readCard({ facts: [fact('request', 'prepared')] }).waiting).toBe(false);
  });
});

describe('words', () => {
  it('reads money and dates the way people say them', () => {
    expect(rupees('INR 650 per plate for 40 guests')).toBe('₹650 per plate · 40 guests');
    expect(inr(150000)).toBe('₹1,50,000');
    expect(longDate('2026-12-12')).toBe('12 Dec 2026');
  });
});
