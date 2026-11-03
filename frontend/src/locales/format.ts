import type { Locale } from './messages';
const tags: Record<Locale, string> = { en: 'en-US', id: 'id-ID' };

export function formatMoney(
  minorUnits: number,
  currency: string,
  exponent: number,
  locale: Locale,
): string {
  // Keep conversion within a range that cannot lose a minor unit in Number formatting.
  if (
    !Number.isSafeInteger(minorUnits) ||
    Math.abs(minorUnits) > 1_000_000_000_000
  )
    throw new RangeError('Unsupported amount');
  if (
    !Number.isInteger(exponent) ||
    exponent < 0 ||
    exponent > 6 ||
    !/^[A-Z]{3}$/.test(currency)
  )
    throw new RangeError('Invalid currency metadata');
  return new Intl.NumberFormat(tags[locale], {
    style: 'currency',
    currency,
    minimumFractionDigits: exponent,
    maximumFractionDigits: exponent,
  }).format(minorUnits / 10 ** exponent);
}
export function formatTimestamp(
  iso: string,
  locale: Locale,
  timeZone: string,
): string {
  const parts = /^(\d{4})-(\d{2})-(\d{2})T/.exec(iso);
  if (parts) {
    const year = Number(parts[1]),
      month = Number(parts[2]),
      day = Number(parts[3]);
    const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
    const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    if (day < 1 || day > (days[month - 1] ?? 0))
      throw new RangeError('Invalid calendar date');
  }
  if (
    !/^\d{4}-\d{2}-\d{2}T/.test(iso) ||
    !/(Z|[+-]\d{2}:\d{2})$/i.test(iso) ||
    !Number.isFinite(Date.parse(iso))
  )
    throw new RangeError('Expected timestamp with explicit offset');
  return new Intl.DateTimeFormat(tags[locale], {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    timeZone,
    timeZoneName: 'short',
  }).format(new Date(iso));
}
