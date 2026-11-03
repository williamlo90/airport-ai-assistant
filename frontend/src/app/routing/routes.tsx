import type { RouteObject } from 'react-router';
import type { PublicSite } from '../config/publicSite';
import type { RouteAccess } from './context';
import { Layout, HomeEntry } from './RouteLayout';
import {
  FatalRouteError,
  NewHelpPage,
  NotFoundPage,
  ResourcePage,
  RouteErrorPage,
  StaffLoginPage,
  StaffQueuePage,
  VerifyPage,
} from './pages';
export function createAppRoutes(
  site: PublicSite,
  access: RouteAccess = { kind: 'unavailable' },
  extraRoutes: RouteObject[] = [],
): RouteObject[] {
  const routes: RouteObject[] = [
    { index: true, element: <HomeEntry site={site} /> },
    { path: 'help/new', element: <NewHelpPage /> },
    {
      path: 'help/conversations/:conversationId',
      element: <ResourcePage resource="conversationId" />,
    },
    { path: 'help/verify', element: <VerifyPage /> },
    {
      path: 'help/submissions/:draftId',
      element: <ResourcePage resource="draftId" />,
    },
    { path: 'help/cases/:caseId', element: <ResourcePage resource="caseId" /> },
    { path: 'staff/login', element: <StaffLoginPage /> },
    { path: 'staff/cases', element: <StaffQueuePage /> },
    {
      path: 'staff/cases/:caseId',
      element: <ResourcePage staff resource="caseId" />,
    },
    ...extraRoutes,
  ];
  if (import.meta.env.DEV)
    routes.push({
      path: '__dev/navigation',
      lazy: () => import('../../dev/NavigationPreview'),
    });
  routes.push({ path: '*', element: <NotFoundPage /> });
  return [
    {
      path: '/',
      hydrateFallbackElement: <p role="status">Loading application…</p>,
      element: <Layout site={site} access={access} />,
      errorElement: <FatalRouteError />,
      children: [{ errorElement: <RouteErrorPage />, children: routes }],
    },
  ];
}
