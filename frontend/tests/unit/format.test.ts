import { describe, expect, it } from 'vitest';
import { formatMoney, formatTimestamp } from '../../src/locales/format';

describe('source-aware formatters', () => {
  it('uses the supplied currency exponent, including zero and three decimal currencies', () => {
    expect(formatMoney(12345, 'USD', 2, 'en')).toBe('$123.45');
    expect(formatMoney(12345, 'JPY', 0, 'en')).toContain('12,345');
    expect(formatMoney(12345, 'KWD', 3, 'en')).toContain('12.345');
    expect(formatMoney(12345, 'USD', 2, 'id')).toContain('123,45');
  });
  it.each([NaN, Infinity, 1.25, Number.MAX_SAFE_INTEGER])(
    'rejects unsafe or unsupported minor units %s',
    (amount) => {
      expect(() => formatMoney(amount, 'USD', 2, 'en')).toThrow(RangeError);
    },
  );
  it('rejects invalid currency metadata instead of displaying a plausible zero', () => {
    expect(() => formatMoney(123, 'USD', -1, 'en')).toThrow();
    expect(() => formatMoney(123, 'US', 2, 'en')).toThrow();
  });
  it('uses explicit timezone rather than the browser default', () => {
    expect(formatTimestamp('2026-11-03T08:00:00Z', 'en', 'UTC')).toContain(
      'UTC',
    );
    expect(
      formatTimestamp('2026-11-03T08:00:00Z', 'en', 'America/New_York'),
    ).not.toBe(formatTimestamp('2026-11-03T08:00:00Z', 'en', 'UTC'));
  });
  it.each([
    'not a date',
    '2026-11-03T08:00:00',
    '2026-11-03',
    '2026-02-30T08:00:00Z',
    '2026-00-15T08:00:00Z',
  ])('rejects ambiguous or invalid timestamp %s', (iso) => {
    expect(() => formatTimestamp(iso, 'en', 'UTC')).toThrow(RangeError);
  });
});
