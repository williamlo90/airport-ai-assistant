import { getInitialLocale } from '../../locales/messages';
import type { Locale } from '../../locales/messages';

export interface PublicSite {
  readonly airportName: string;
  readonly productName: string;
  readonly defaultLocale: Locale;
  readonly supportedLocales: readonly Locale[];
  readonly deployment: 'local-development';
}

export type BootstrapState =
  | {
      readonly status: 'configured';
      readonly site: PublicSite;
      readonly locale: Locale;
    }
  | { readonly status: 'unconfigured' };

const westbridge: PublicSite = Object.freeze({
  airportName: 'Westbridge Airport',
  productName: 'AI Assistant',
  defaultLocale: 'en',
  supportedLocales: Object.freeze(['en', 'id'] satisfies Locale[]),
  deployment: 'local-development',
});

// Reviewed deployment configuration, not a tenant selector or permission map.
// Real deployment hosts must be explicitly added and matched to backend registration.
const sitesByHost = new Map<string, PublicSite>([
  ['localhost', westbridge],
  ['127.0.0.1', westbridge],
]);

export function resolvePublicSite(hostname: string): PublicSite | undefined {
  // Match AuthService.tenant: lowercase, remove trailing dots, ASCII host only.
  // Input is location.hostname, never location.host (which can contain a port).
  const canonical = hostname.toLowerCase().replace(/\.+$/u, '');
  if (canonical.length > 253 || !/^[a-z0-9.-]+$/u.test(canonical))
    return undefined;
  return sitesByHost.get(canonical);
}

export function createBootstrap(
  hostname: string,
  search: string,
): BootstrapState {
  const site = resolvePublicSite(hostname);
  if (!site) return { status: 'unconfigured' };
  return {
    status: 'configured',
    site,
    locale: getInitialLocale(search, site.defaultLocale, site.supportedLocales),
  };
}
