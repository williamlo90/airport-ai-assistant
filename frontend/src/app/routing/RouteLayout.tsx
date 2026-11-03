import { lazy, Suspense, useEffect } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router';
import type { PublicSite } from '../config/publicSite';
import { getInitialLocale, messages } from '../../locales/messages';
import { RoutingContext } from './context';
import type { RouteAccess } from './context';
import { DraftNavigation } from './drafts';
import { localizedTarget } from './policy';
import { HomePage } from './pages';

const CorePreview = import.meta.env.DEV
  ? lazy(() => import('../../dev/CorePreview'))
  : null;
export function Layout({
  site,
  access,
}: {
  site: PublicSite;
  access: RouteAccess;
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const locale = getInitialLocale(
    location.search,
    site.defaultLocale,
    site.supportedLocales,
  );
  useEffect(() => {
    document.documentElement.lang = locale;
    document.title = `${site.airportName} — ${site.productName}`;
  }, [locale, site]);
  function switchLanguage() {
    const next = locale === 'en' ? 'id' : 'en';
    if (site.supportedLocales.includes(next))
      void navigate(
        localizedTarget(
          `${location.pathname}${location.search}${location.hash}`,
          next,
        ),
        { replace: true },
      );
  }
  return (
    <RoutingContext value={{ site, locale, access, switchLanguage }}>
      <DraftNavigation locale={locale}>
        <Outlet />
      </DraftNavigation>
    </RoutingContext>
  );
}
export function HomeEntry({ site }: { site: PublicSite }) {
  const location = useLocation();
  const navigate = useNavigate();
  const locale = getInitialLocale(
    location.search,
    site.defaultLocale,
    site.supportedLocales,
  );
  const values = new URLSearchParams(location.search).getAll('preview');
  if (CorePreview && values.length === 1 && values[0] === 'components')
    return (
      <Suspense
        fallback={<p role="status">{messages[locale].loadingPreview}</p>}
      >
        <CorePreview
          site={site}
          locale={locale}
          onLanguageChange={() =>
            void navigate(
              localizedTarget(
                `${location.pathname}${location.search}${location.hash}`,
                locale === 'en' ? 'id' : 'en',
              ),
              { replace: true },
            )
          }
        />
      </Suspense>
    );
  return <HomePage />;
}
