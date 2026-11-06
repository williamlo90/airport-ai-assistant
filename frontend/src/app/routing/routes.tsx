import type { AuthCoordinator } from '../auth/coordinator';
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
  StaffHomePage,
  StaffQueuePage,
  VerifyPage,
  RequestReviewPage,
  ServiceRecordsPage,
  CustomerCasesPage,
} from './pages';
export function createAppRoutes(
  site: PublicSite,
  access: RouteAccess = { kind: 'unavailable' },
  extraRoutes: RouteObject[] = [],
  coordinator?: AuthCoordinator,
): RouteObject[] {
  const routes: RouteObject[] = [
    { index: true, element: <HomeEntry site={site} /> },
    { path: 'help/new', element: <NewHelpPage /> },
    {
      path: 'help/conversations/:conversationId',
      element: <ResourcePage resource="conversationId" />,
    },
    { path: 'help/verify', element: <VerifyPage /> },
    { path: 'help/request', element: <RequestReviewPage intake /> },
    { path: 'help/services', element: <ServiceRecordsPage /> },
    { path: 'help/submissions', element: <RequestReviewPage /> },
    {
      path: 'help/submissions/:draftId',
      element: <ResourcePage resource="draftId" />,
    },
    { path: 'help/cases', element: <CustomerCasesPage /> },
    { path: 'help/cases/:caseId', element: <ResourcePage resource="caseId" /> },
    { path: 'staff/login', element: <StaffLoginPage /> },
    { path: 'staff', element: <StaffHomePage /> },
    { path: 'admin', lazy: () => import('../../features/admin/route') },
    {
      path: 'admin/information',
      lazy: () => import('../../features/knowledge/route'),
    },
    {
      path: 'staff/cases',
      element: <StaffQueuePage />,
      children: [
        { index: true, element: null },
        { path: ':caseId', element: null },
      ],
    },
    ...extraRoutes,
  ];
  if (import.meta.env.DEV)
    routes.push({
      path: '__dev/chat/:conversationId',
      lazy: () => import('../../dev/ChatPreview'),
    });
  if (import.meta.env.DEV)
    routes.push({
      path: '__dev/staff/cases',
      lazy: () => import('../../dev/StaffPreview'),
      children: [
        { index: true, element: null },
        { path: ':caseId', element: null },
      ],
    });
  if (import.meta.env.DEV)
    routes.push({
      path: '__dev/navigation',
      lazy: () => import('../../dev/NavigationPreview'),
    });
  if (import.meta.env.DEV)
    routes.push({
      path: '__dev/environment',
      lazy: () => import('../../dev/EnvironmentPreview'),
    });
  if (import.meta.env.DEV)
    routes.push({
      path: '__dev/auth',
      lazy: () => import('../../dev/AuthPreview'),
    });
  if (import.meta.env.DEV)
    routes.push({
      path: '__dev/extra/:screen',
      lazy: () => import('../../dev/ExtraPreview'),
    });
  routes.push({ path: '*', element: <NotFoundPage /> });
  return [
    {
      path: '/',
      hydrateFallbackElement: <p role="status">Loading application…</p>,
      element: <Layout site={site} access={access} coordinator={coordinator} />,
      errorElement: <FatalRouteError />,
      children: [{ errorElement: <RouteErrorPage />, children: routes }],
    },
  ];
}
