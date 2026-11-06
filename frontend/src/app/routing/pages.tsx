import { lazy, Suspense } from 'react';
import { StaffNavigation } from '../../features/staff/StaffNavigation';
import { useStaff } from '../../features/staff/context';
const LazyStaffCases = lazy(
  () => import('../../features/staff/StaffCasesPage'),
);
const LazyStaffLogin = lazy(() =>
  import('../../features/staff/StaffPages').then((m) => ({
    default: m.StaffLogin,
  })),
);
const LazyStaffWorkspace = lazy(() =>
  import('../../features/staff/StaffPages').then((m) => ({
    default: m.StaffWorkspace,
  })),
);
export function StaffHomePage() {
  return (
    <Suspense fallback={<p role="status">Loading workspace…</p>}>
      <LazyStaffWorkspace />
    </Suspense>
  );
}
const LazySourcePage = lazy(() =>
  import('../../features/source/SourcePage').then((m) => ({
    default: m.SourcePage,
  })),
);
export function ServiceRecordsPage() {
  const { locale } = useRouting();
  return (
    <RequireAccess>
      <Suspense
        fallback={
          <p role="status">
            {locale === 'en'
              ? 'Loading service records…'
              : 'Memuat data layanan…'}
          </p>
        }
      >
        <LazySourcePage />
      </Suspense>
    </RequireAccess>
  );
}
const LazyCaseDetailPage = lazy(() =>
  import('../../features/cases/CaseDetailPage').then((m) => ({
    default: m.CaseDetailPage,
  })),
);
const LazyCustomerCases = lazy(() =>
  import('../../features/cases/CustomerCasesPage').then((m) => ({
    default: m.CustomerCasesPage,
  })),
);
export function CustomerCasesPage() {
  return (
    <RequireAccess>
      <Suspense fallback={<p role="status">Loading…</p>}>
        <LazyCustomerCases />
      </Suspense>
    </RequireAccess>
  );
}
const LazyChatPage = lazy(() =>
  import('../../features/chat/ChatPage').then((m) => ({ default: m.ChatPage })),
);
function ChatPage() {
  const { locale } = useRouting();
  return (
    <Suspense
      fallback={
        <p role="status">
          {locale === 'en' ? 'Loading conversation…' : 'Memuat percakapan…'}
        </p>
      }
    >
      <LazyChatPage />
    </Suspense>
  );
}
import {
  Link,
  isRouteErrorResponse,
  useLocation,
  useNavigate,
  useParams,
  useRouteError,
} from 'react-router';
import { Fragment, useContext } from 'react';
import { AuthContext, useAuthState } from '../auth/context';
import type { ReactNode } from 'react';
import { CustomerShell, StaffShell } from '../shells/Shells';
import { AsyncState } from '../../components/Feedback';
import { messages } from '../../locales/messages';
import { useRouting } from './context';
import { isResourceId, localizedTarget, readTopic } from './policy';

export function PageFrame({
  children,
  staff = false,
}: {
  children: ReactNode;
  staff?: boolean;
}) {
  const { site, locale, switchLanguage } = useRouting();
  const navigate = useNavigate();
  const c = messages[locale];
  const home = localizedTarget('/', locale);
  const props = {
    site,
    languageLabel: c.switchLanguage,
    onLanguageChange: switchLanguage,
    skipLabel: c.skip,
    homeHref: home,
    onHomeNavigate: (event: React.MouseEvent<HTMLAnchorElement>) => {
      if (
        event.button === 0 &&
        !event.ctrlKey &&
        !event.metaKey &&
        !event.shiftKey &&
        !event.altKey
      ) {
        event.preventDefault();
        void navigate(home);
      }
    },
  };
  return staff ? (
    <StaffShell
      {...props}
      navigationLabel={locale === 'en' ? 'Staff navigation' : 'Navigasi staf'}
      navigation={<StaffNavigation />}
    >
      {children}
    </StaffShell>
  ) : (
    <CustomerShell {...props}>{children}</CustomerShell>
  );
}
export function BackHome() {
  const { locale } = useRouting();
  return (
    <Link to={localizedTarget('/', locale)}>
      {locale === 'en' ? 'Back to help centre' : 'Kembali ke pusat bantuan'}
    </Link>
  );
}
export function NotFoundPage() {
  const { locale } = useRouting();
  return (
    <PageFrame>
      <h1>{locale === 'en' ? 'Page not found' : 'Halaman tidak ditemukan'}</h1>
      <p>
        {locale === 'en'
          ? 'This page is not available.'
          : 'Halaman ini tidak tersedia.'}
      </p>
      <BackHome />
    </PageFrame>
  );
}
export function RouteErrorPage() {
  const error = useRouteError();
  const { locale } = useRouting();
  if (isRouteErrorResponse(error) && error.status === 404)
    return <NotFoundPage />;
  return (
    <PageFrame>
      <h1>{locale === 'en' ? 'Page unavailable' : 'Halaman belum tersedia'}</h1>
      <p role="alert">
        {locale === 'en'
          ? 'This page could not be displayed. No result has been confirmed.'
          : 'Halaman belum dapat ditampilkan. Belum ada hasil yang dipastikan.'}
      </p>
      <BackHome />
    </PageFrame>
  );
}
export function FatalRouteError() {
  return (
    <main className="bootstrap">
      <h1>Application unavailable</h1>
      <p role="alert">The application could not be displayed.</p>
      <a href="/">Return to the application</a>
    </main>
  );
}
export function RequireAccess({
  staff = false,
  children,
}: {
  staff?: boolean;
  children: ReactNode;
}) {
  const { access, locale } = useRouting();
  const accepted = staff
    ? access.kind === 'staff' &&
      access.roles.some((role) => role === 'cs' || role === 'supervisor')
    : access.kind === 'anonymous' || access.kind === 'customer';
  const coordinator = useContext(AuthContext);
  const auth = useAuthState(coordinator);
  if (accepted)
    return (
      <Fragment
        key={
          auth.phase === 'ready'
            ? auth.boundaryId + ':' + auth.epoch
            : 'controlled-test'
        }
      >
        {children}
      </Fragment>
    );
  const loading = access.kind === 'checking';
  return (
    <PageFrame>
      <AsyncState
        state={loading ? 'loading' : 'unavailable'}
        title={
          locale === 'en'
            ? loading
              ? 'Checking access'
              : 'Session verification required'
            : loading
              ? 'Memeriksa akses'
              : 'Verifikasi sesi diperlukan'
        }
        message={
          locale === 'en'
            ? coordinator
              ? 'Check your session before opening private information. A link does not grant access.'
              : 'Session verification is not connected in this build. A URL or resource ID does not grant access.'
            : coordinator
              ? 'Periksa sesi sebelum membuka informasi privat. Tautan tidak memberikan akses.'
              : 'Verifikasi sesi belum terhubung pada build ini. URL atau ID resource tidak memberikan akses.'
        }
      />
      {staff && access.kind !== 'unavailable' && (
        <Link to={localizedTarget('/staff/login', locale)}>
          {locale === 'en' ? 'Staff sign-in' : 'Masuk petugas'}
        </Link>
      )}
      <BackHome />
    </PageFrame>
  );
}
export function PlaceholderPage({
  title,
  staff = false,
  note,
}: {
  title: string;
  staff?: boolean;
  note?: string;
}) {
  const { locale } = useRouting();
  return (
    <PageFrame staff={staff}>
      <h1>{title}</h1>
      <p role="status">
        {note ??
          (locale === 'en'
            ? 'This screen is not connected yet. No request has been sent.'
            : 'Layar ini belum terhubung. Tidak ada permintaan yang dikirim.')}
      </p>
      <BackHome />
    </PageFrame>
  );
}
export function NewHelpPage() {
  const location = useLocation();
  if (!readTopic(location.search)) return <NotFoundPage />;
  return <ChatPage />;
}
const LazyVerificationPage = lazy(() =>
  import('../../features/verification/VerificationPage').then((m) => ({
    default: m.VerificationPage,
  })),
);
export function VerifyPage() {
  const { locale } = useRouting();
  return (
    <Suspense
      fallback={
        <p role="status">
          {locale === 'en' ? 'Loading verification…' : 'Memuat verifikasi…'}
        </p>
      }
    >
      <LazyVerificationPage />
    </Suspense>
  );
}
const LazyCaseDraftPage = lazy(() =>
  import('../../features/cases/CaseDraftPage').then((m) => ({
    default: m.CaseDraftPage,
  })),
);
export function RequestReviewPage({ intake = false }: { intake?: boolean }) {
  const { locale } = useRouting();
  return (
    <RequireAccess>
      <Suspense
        fallback={
          <p role="status">
            {locale === 'en'
              ? 'Loading request review…'
              : 'Memuat tinjauan permintaan…'}
          </p>
        }
      >
        <LazyCaseDraftPage intake={intake} />
      </Suspense>
    </RequireAccess>
  );
}
export function StaffLoginPage() {
  return (
    <Suspense fallback={<p role="status">Loading staff sign-in…</p>}>
      <LazyStaffLogin />
    </Suspense>
  );
}
export function StaffQueuePage() {
  const { locale } = useRouting();
  const staff = useStaff();
  if (staff)
    return (
      <Suspense
        fallback={
          <p role="status">
            {locale === 'en' ? 'Loading staff cases…' : 'Memuat kasus petugas…'}
          </p>
        }
      >
        <LazyStaffCases />
      </Suspense>
    );
  return (
    <RequireAccess staff>
      <PlaceholderPage
        staff
        title={locale === 'en' ? 'Support cases' : 'Kasus layanan'}
      />
    </RequireAccess>
  );
}
export function ResourcePage({
  resource,
  staff = false,
}: {
  resource: 'conversationId' | 'draftId' | 'caseId';
  staff?: boolean;
}) {
  const params = useParams();
  const { locale } = useRouting();
  if (!isResourceId(params[resource])) return <NotFoundPage />;
  if (resource === 'draftId') return <RequestReviewPage />;
  if (resource === 'conversationId')
    return (
      <RequireAccess>
        <ChatPage />
      </RequireAccess>
    );
  if (resource === 'caseId' && !staff)
    return (
      <RequireAccess>
        <Suspense
          fallback={
            <p role="status">
              {locale === 'en' ? 'Loading case…' : 'Memuat kasus…'}
            </p>
          }
        >
          <LazyCaseDetailPage />
        </Suspense>
      </RequireAccess>
    );
  const title = locale === 'en' ? 'Case details' : 'Detail kasus';
  return (
    <RequireAccess staff={staff}>
      <PlaceholderPage staff={staff} title={title} />
    </RequireAccess>
  );
}
