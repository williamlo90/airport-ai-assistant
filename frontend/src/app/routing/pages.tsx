import {
  Link,
  isRouteErrorResponse,
  useLocation,
  useNavigate,
  useParams,
  useRouteError,
} from 'react-router';
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
      navigation={
        <Link to={localizedTarget('/staff/cases', locale)}>
          {locale === 'en' ? 'Cases' : 'Kasus'}
        </Link>
      }
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
export function HomePage() {
  const { site, locale } = useRouting();
  const c = messages[locale];
  return (
    <PageFrame>
      <section className="bootstrap">
        <p className="bootstrap-label">{c.preview}</p>
        <h1>
          {site.airportName} — {site.productName}
        </h1>
        <p role="status">{c.readiness}</p>
      </section>
    </PageFrame>
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
  if (accepted) return children;
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
            ? 'Session verification is not connected in this build. A URL or resource ID does not grant access.'
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
  const { locale } = useRouting();
  const location = useLocation();
  if (!readTopic(location.search)) return <NotFoundPage />;
  return (
    <PlaceholderPage
      title={locale === 'en' ? 'Customer support' : 'Bantuan pelanggan'}
    />
  );
}
export function VerifyPage() {
  const { locale } = useRouting();
  return (
    <PlaceholderPage
      title={locale === 'en' ? 'Verify your email' : 'Verifikasi email'}
    />
  );
}
export function StaffLoginPage() {
  const { locale } = useRouting();
  return (
    <PlaceholderPage
      title={locale === 'en' ? 'Staff sign-in' : 'Masuk petugas'}
    />
  );
}
export function StaffQueuePage() {
  const { locale } = useRouting();
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
  const title =
    resource === 'draftId'
      ? locale === 'en'
        ? 'Support request status'
        : 'Status pengajuan bantuan'
      : resource === 'conversationId'
        ? locale === 'en'
          ? 'Conversation'
          : 'Percakapan'
        : locale === 'en'
          ? 'Case details'
          : 'Detail kasus';
  return (
    <RequireAccess staff={staff}>
      <PlaceholderPage
        staff={staff}
        title={title}
        {...(resource === 'draftId'
          ? {
              note:
                locale === 'en'
                  ? 'The submission link is preserved. Its status is not connected yet; no successful submission is being claimed.'
                  : 'Tautan pengajuan dipertahankan. Statusnya belum terhubung; keberhasilan pengajuan belum dinyatakan.',
            }
          : {})}
      />
    </RequireAccess>
  );
}
