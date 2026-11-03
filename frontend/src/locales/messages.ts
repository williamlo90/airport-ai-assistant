export type Locale = 'en' | 'id';

interface Messages {
  readonly preview: string;
  readonly readiness: string;
  readonly switchLanguage: string;
  readonly skip: string;
  readonly loadingPreview: string;
}

export const messages = {
  en: {
    preview: 'Frontend development preview',
    readiness:
      'The frontend is running. Customer support screens are being prepared.',
    switchLanguage: 'Bahasa Indonesia',
    skip: 'Skip to content',
    loadingPreview: 'Loading component preview…',
  },
  id: {
    preview: 'Pratinjau pengembangan frontend',
    readiness:
      'Frontend sudah berjalan. Layar layanan pelanggan sedang disiapkan.',
    switchLanguage: 'English',
    skip: 'Langsung ke konten',
    loadingPreview: 'Memuat pratinjau komponen…',
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
