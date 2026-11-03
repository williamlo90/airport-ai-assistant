export type Locale = 'en' | 'id';

interface Messages {
  readonly preview: string;
  readonly readiness: string;
  readonly switchLanguage: string;
}

export const messages = {
  en: {
    preview: 'Frontend development preview',
    readiness:
      'The frontend is running. Customer support screens are being prepared.',
    switchLanguage: 'Bahasa Indonesia',
  },
  id: {
    preview: 'Pratinjau pengembangan frontend',
    readiness:
      'Frontend sudah berjalan. Layar layanan pelanggan sedang disiapkan.',
    switchLanguage: 'English',
  },
} as const satisfies Record<Locale, Messages>;

export function getInitialLocale(
  search: string,
  fallback: Locale,
  supported: readonly Locale[],
): Locale {
  const values = new URLSearchParams(search).getAll('lang');
  const value = values[0];
  if (values.length !== 1 || (value !== 'en' && value !== 'id'))
    return fallback;
  return supported.includes(value) ? value : fallback;
}
