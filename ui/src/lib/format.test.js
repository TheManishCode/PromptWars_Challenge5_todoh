import { describe, expect, it } from 'vitest';
import { shareOf } from './format';

describe('shareOf', () => {
  it('shows the deposit as a share of the total, so Rs 25 on Rs 54,000 looks odd', () => {
    expect(shareOf(13500, 54000)).toBe('25% of the total');
    expect(shareOf(25, 54000)).toBe('0.05% of the total');
    expect(shareOf(18452, 61440)).toBe('30% of the total');
  });
  it('says nothing when it cannot be worked out', () => {
    expect(shareOf(100, 0)).toBe('');
    expect(shareOf(undefined, 100)).toBe('');
    expect(shareOf(-1, 100)).toBe('');
  });
});
