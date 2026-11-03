import { useEffect, useState } from 'react';
import type { BootstrapState } from './config/publicSite';
import { messages } from '../locales/messages';
import type { Locale } from '../locales/messages';

export function App({ bootstrap }: { readonly bootstrap: BootstrapState }) {
  const [locale, setLocale] = useState<Locale>(
    bootstrap.status === 'configured' ? bootstrap.locale : 'en',
  );
  const title =
    bootstrap.status === 'configured'
      ? `${bootstrap.site.airportName} — ${bootstrap.site.productName}`
      : 'Site unavailable — AI Assistant';

  useEffect(() => {
    document.documentElement.lang = locale;
    document.title = title;
  }, [locale, title]);

  if (bootstrap.status === 'unconfigured') {
    return (
      <main className="bootstrap">
        <h1>Site unavailable</h1>
        <p role="alert">
          This address has not been configured for this application.
        </p>
      </main>
    );
  }

  const copy = messages[locale];
  const nextLocale = locale === 'en' ? 'id' : 'en';
  const canSwitch = bootstrap.site.supportedLocales.includes(nextLocale);

  function switchLanguage() {
    // Only the allowlisted language changes. No actor, host or tenant is read from query.
    const url = new URL(window.location.href);
    url.searchParams.set('lang', nextLocale);
    window.history.replaceState(null, '', url);
    setLocale(nextLocale);
  }

  return (
    <main className="bootstrap">
      <p className="bootstrap-label">{copy.preview}</p>
      <h1>{title}</h1>
      <p role="status">{copy.readiness}</p>
      {canSwitch && (
        <button type="button" onClick={switchLanguage}>
          {copy.switchLanguage}
        </button>
      )}
    </main>
  );
}
