import { describe, expect, it } from 'vitest';
import { neededCategories, rows } from './brief';

describe('neededCategories', () => {
  it('always asks for a venue', () => {
    expect(neededCategories({})).toEqual(['venue']);
  });
  it('adds a caterer when there are food needs, as the server does', () => {
    expect(neededCategories({ dietary: ['vegetarian'] })).toEqual(['venue', 'caterer']);
  });
  it('does not ask for a caterer twice', () => {
    expect(neededCategories({ dietary: ['jain'], services: ['caterer', 'photographer'] })).toEqual(['venue', 'caterer', 'photographer']);
  });
});

describe('rows', () => {
  it('says an allergy was not stated instead of leaving it blank', () => {
    expect(rows({ city: 'Mysuru' }).find((r) => r.key === 'allergens').value).toMatch(/None stated/);
  });
});
