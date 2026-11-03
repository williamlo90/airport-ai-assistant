import { createContext, useContext } from 'react';
import type { PublicSite } from '../config/publicSite';
import type { Locale } from '../../locales/messages';
export type RouteAccess =
  | { kind: 'unavailable' | 'checking' | 'anonymous' | 'customer' }
  | { kind: 'staff'; roles: readonly string[] };
export interface RoutingContextValue {
  site: PublicSite;
  locale: Locale;
  access: RouteAccess;
  switchLanguage: () => void;
}
export const RoutingContext = createContext<RoutingContextValue | null>(null);
export function useRouting() {
  const value = useContext(RoutingContext);
  if (!value) throw new Error('Routing provider required');
  return value;
}
