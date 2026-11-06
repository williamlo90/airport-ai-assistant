import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import { Link, useMatch, useNavigate } from 'react-router';
import { useStaff } from './context';
import { StaffQueueStore } from './queue-store';
import { canHandleCases } from './store';
import { useRouting } from '../../app/routing/context';
import { useAuthState } from '../../app/auth/context';
import { PageFrame } from '../../app/routing/pages';
import { localizedTarget, isResourceId } from '../../app/routing/policy';
import { caseStatuses } from '../cases/detail-contracts';
import { views } from './queue-contracts';
import type { QueueFilter } from './queue-contracts';
import { Button } from '../../components/Button';
import styles from './queue.module.css';
import { StaffActions } from './StaffActions';
import { StaffMail } from './StaffMail';
import { StaffCommunication } from './StaffCommunication';
const names = {
  new: ['New', 'Baru'],
  in_progress: ['In progress', 'Ditangani'],
  awaiting_customer: ['Awaiting customer', 'Menunggu pelanggan'],
  awaiting_external: ['Awaiting external', 'Menunggu pihak luar'],
  closed: ['Closed', 'Ditutup'],
} as const;
export default function StaffCasesPage({
  basePath = '/staff/cases',
}: {
  basePath?: string;
}) {
  const staff = useStaff(),
    { site } = useRouting();
  const store = useMemo(
    () => (staff ? new StaffQueueStore(staff, site.airportName) : null),
    [staff, site.airportName],
  );
  useEffect(() => {
    if (!store) return;
    const off = store.connect();
    return () => {
      off();
      store.dispose();
    };
  }, [store]);
  return store ? (
    <Workspace store={store} basePath={basePath} />
  ) : (
    <PageFrame>
      <h1>Staff access required</h1>
    </PageFrame>
  );
}
function Workspace({
  store,
  basePath,
}: {
  store: StaffQueueStore;
  basePath: string;
}) {
  const state = useSyncExternalStore(store.subscribe, store.getState),
    staff = useSyncExternalStore(store.staff.subscribe, store.staff.getState),
    auth = useAuthState(store.staff.auth);
  const { locale } = useRouting(),
    navigate = useNavigate(),
    match = useMatch(basePath + '/:caseId'),
    caseId = match?.params.caseId?.toLowerCase() ?? null;
  const t = (en: string, id: string) => (locale === 'en' ? en : id),
    ix = locale === 'en' ? 0 : 1;
  const [now, setNow] = useState(() => Date.now()),
    list = useRef<HTMLDivElement>(null),
    heading = useRef<HTMLHeadingElement>(null),
    lastCase = useRef<string | null>(null),
    previousCase = useRef<string | null>(caseId),
    rowLinks = useRef(new Map<string, HTMLAnchorElement>());
  useEffect(() => {
    if (state.retryAt <= Date.now()) return;
    const timer = setInterval(() => {
      const current = Date.now();
      setNow(current);
      if (current >= state.retryAt) clearInterval(timer);
    }, 1000);
    return () => clearInterval(timer);
  }, [state.retryAt]);
  const [actionDraftLocked, setActionLocked] = useState(false);
  const [communicationLocked, setCommunicationLocked] = useState(false);
  const actionLocked = actionDraftLocked || communicationLocked;
  const waiting = state.retryAt > now;
  useEffect(() => {
    let active = true;
    queueMicrotask(() => {
      if (!active || !state.scopeKey) return;
      if (!caseId) store.clearDetail();
      if (state.phase === 'idle') void store.ensureQueue();
      else if (state.phase === 'ready' && caseId && isResourceId(caseId))
        void store.openCase(caseId);
    });
    return () => {
      active = false;
    };
  }, [store, caseId, state.scopeKey, state.phase]);
  useLayoutEffect(() => {
    if (list.current && state.phase === 'ready')
      list.current.scrollTop = store.scrollTop;
  }, [store, state.page, caseId, state.phase]);
  useLayoutEffect(() => {
    const previous = previousCase.current;
    previousCase.current = caseId;
    if (!caseId && previous) {
      if (window.scrollY !== store.windowScrollY)
        window.scrollTo({
          top: store.windowScrollY,
          left: 0,
          behavior: 'instant',
        });
      rowLinks.current.get(previous)?.focus({ preventScroll: true });
    }
  }, [store, caseId]);
  useEffect(() => {
    if (
      caseId &&
      state.detailPhase === 'ready' &&
      state.detail?.id === caseId &&
      lastCase.current !== caseId
    ) {
      heading.current?.focus();
      lastCase.current = caseId;
    } else if (!caseId) {
      lastCase.current = null;
    }
  }, [caseId, state.detailPhase, state.detail]);
  const index = localizedTarget(basePath, locale);
  function configure(value: Partial<QueueFilter>) {
    void navigate(index);
    store.configure(value);
  }
  if (auth.phase !== 'ready' || auth.kind !== 'staff' || !staff.ready)
    return (
      <PageFrame>
        <h1>{t('Staff access required', 'Akses petugas diperlukan')}</h1>
        <p role="status">
          {t(
            'Check your staff session before opening cases.',
            'Periksa sesi petugas sebelum membuka kasus.',
          )}
        </p>
        <Link to={localizedTarget('/staff/login', locale)}>
          {t('Staff sign-in', 'Masuk petugas')}
        </Link>
        <Button onClick={() => void store.staff.auth.recover()}>
          {t('Check session', 'Periksa sesi')}
        </Button>
      </PageFrame>
    );
  if (caseId && !isResourceId(caseId))
    return (
      <PageFrame staff>
        <h1>{t('Page not found', 'Halaman tidak ditemukan')}</h1>
        <Link to={index}>{t('Back to cases', 'Kembali ke kasus')}</Link>
      </PageFrame>
    );
  const member = staff.memberships.find((m) => m.id === staff.selectedId),
    page = state.page,
    detail =
      state.selectedId === caseId &&
      state.detail?.id === caseId &&
      state.capabilities?.case_id === caseId
        ? state.detail
        : null,
    cap = detail ? state.capabilities : null;
  const outside =
    detail &&
    cap &&
    ((state.filter.queueId && state.filter.queueId !== cap.queue.id) ||
      (state.filter.status && state.filter.status !== detail.status) ||
      (state.filter.view === 'unassigned' &&
        detail.assigned_membership_id !== null) ||
      (state.filter.view === 'mine' &&
        detail.assigned_membership_id !== member?.id));
  const status = (v: keyof typeof names) => names[v][ix];
  const date = (value: string) =>
    new Intl.DateTimeFormat(locale === 'en' ? 'en-US' : 'id-ID', {
      dateStyle: 'medium',
      timeStyle: 'short',
      timeZone: 'UTC',
    }).format(new Date(value)) + ' UTC';
  return (
    <PageFrame staff>
      <div className={styles.scope}>
        <label htmlFor="case-organization">
          {t('Organization access', 'Akses organisasi')}
        </label>
        <select
          id="case-organization"
          disabled={actionLocked}
          value={staff.selectedId ?? ''}
          onChange={(e) => {
            void navigate(index);
            store.staff.select(e.target.value);
          }}
        >
          <option value="" disabled>
            {t('Choose an organization', 'Pilih organisasi')}
          </option>
          {staff.memberships.map((m) => (
            <option key={m.id} value={m.id}>
              {page && page.membership_id === m.id
                ? page.organization.name
                : m.organization_id}
            </option>
          ))}
        </select>
        <Button
          variant="quiet"
          disabled={actionLocked}
          onClick={() => void store.staff.auth.recover()}
        >
          {t('Refresh staff access', 'Perbarui akses petugas')}
        </Button>
      </div>
      {!member || !canHandleCases(member) ? (
        <section className={styles.empty}>
          <h1>{t('Support cases', 'Kasus layanan')}</h1>
          <p role="status">
            {member
              ? t(
                  'Case access is not enabled for this organization.',
                  'Akses kasus tidak tersedia untuk organisasi ini.',
                )
              : t(
                  'Choose an assigned organization to open its cases.',
                  'Pilih organisasi yang ditugaskan untuk membuka kasusnya.',
                )}
          </p>
        </section>
      ) : (
        <div className={styles.layout} data-detail-open={!!caseId}>
          <section className={styles.queue} aria-labelledby="queue-title">
            <header className={styles.queueHeading}>
              <h1 id="queue-title">{t('Support cases', 'Kasus layanan')}</h1>
              {page ? <p>{page.organization.name}</p> : null}
            </header>
            <div
              className={styles.tabs}
              role="group"
              aria-label={t('Case view', 'Tampilan kasus')}
            >
              {views.map((v) => (
                <button
                  key={v}
                  aria-pressed={state.filter.view === v}
                  disabled={waiting || actionLocked}
                  onClick={() => configure({ view: v })}
                >
                  {
                    {
                      unassigned: t('Unassigned', 'Belum ditugaskan'),
                      mine: t('Mine', 'Milik saya'),
                      all: t('All', 'Semua'),
                    }[v]
                  }
                </button>
              ))}
            </div>
            <div className={styles.filters}>
              <label htmlFor="case-status">{t('Status', 'Status')}</label>
              <select
                id="case-status"
                value={state.filter.status}
                disabled={waiting || actionLocked}
                onChange={(e) =>
                  configure({ status: e.target.value as QueueFilter['status'] })
                }
              >
                <option value="">{t('All statuses', 'Semua status')}</option>
                {caseStatuses.map((s) => (
                  <option key={s} value={s}>
                    {status(s)}
                  </option>
                ))}
              </select>
              <label htmlFor="case-queue">
                {t('Service queue', 'Antrean layanan')}
              </label>
              <select
                id="case-queue"
                value={state.filter.queueId}
                disabled={waiting || actionLocked || !page}
                onChange={(e) => configure({ queueId: e.target.value })}
              >
                <option value="">
                  {t('All assigned queues', 'Semua antrean yang ditugaskan')}
                </option>
                {page?.queues.map((q) => (
                  <option key={q.id} value={q.id}>
                    {q.name}
                  </option>
                ))}
              </select>
            </div>
            {waiting ? (
              <p role="status" className={styles.notice}>
                {t(
                  'Please wait before retrying.',
                  'Tunggu sebelum mencoba lagi.',
                )}{' '}
                {Math.ceil((state.retryAt - now) / 1000)}s
              </p>
            ) : null}
            {state.phase === 'loading' ? (
              <p role="status" className={styles.empty}>
                {t('Loading cases…', 'Memuat kasus…')}
              </p>
            ) : null}
            {state.phase === 'unavailable' ? (
              <div className={styles.empty} role="alert">
                <h2>{t('Cases unavailable', 'Kasus belum tersedia')}</h2>
                <p>
                  {state.error?.status === 400
                    ? t(
                        'This page cursor is no longer available. Refresh to start again.',
                        'Cursor halaman tidak tersedia lagi. Muat ulang dari awal.',
                      )
                    : t(
                        'The current case list could not be verified. Refresh or check your staff access.',
                        'Daftar kasus belum dapat diverifikasi. Muat ulang atau periksa akses petugas.',
                      )}
                </p>
              </div>
            ) : null}
            {state.phase === 'ready' && page ? (
              <>
                <div
                  className={styles.list}
                  ref={list}
                  onScroll={(e) => {
                    store.rememberListScroll(e.currentTarget.scrollTop);
                  }}
                >
                  {page.items.length ? (
                    <ul>
                      {page.items.map((item) => (
                        <li key={item.id}>
                          <Link
                            onClick={(event) => {
                              if (
                                event.button === 0 &&
                                !event.ctrlKey &&
                                !event.metaKey &&
                                !event.shiftKey &&
                                !event.altKey
                              )
                                store.rememberWindowScroll(window.scrollY);
                            }}
                            to={localizedTarget(
                              basePath + '/' + item.id,
                              locale,
                            )}
                            ref={(node) => {
                              if (node) rowLinks.current.set(item.id, node);
                              else rowLinks.current.delete(item.id);
                            }}
                            aria-current={
                              caseId === item.id ? 'page' : undefined
                            }
                          >
                            <span className={styles.itemTop}>
                              <span>{item.public_reference}</span>
                              <span className={styles.status}>
                                {status(item.status)}
                              </span>
                            </span>
                            <strong>{item.summary}</strong>
                            <span className={styles.itemMeta}>
                              {item.assigned_membership_id === member.id
                                ? t('Assigned to you', 'Ditugaskan kepada Anda')
                                : item.assigned_membership_id
                                  ? t('Assigned', 'Sudah ditugaskan')
                                  : t('Unassigned', 'Belum ditugaskan')}
                            </span>
                            <span className={styles.itemMeta}>
                              {t('Updated', 'Diperbarui')}{' '}
                              {date(item.updated_at)}
                            </span>
                          </Link>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p role="status" className={styles.empty}>
                      {t(
                        'No cases match this view right now.',
                        'Belum ada kasus yang sesuai dengan tampilan ini.',
                      )}
                    </p>
                  )}
                </div>
                <div className={styles.paging}>
                  <Button
                    variant="quiet"
                    disabled={waiting || actionLocked || state.pageNumber === 1}
                    onClick={() => void store.previous()}
                  >
                    {t('Previous page', 'Halaman sebelumnya')}
                  </Button>
                  <span>
                    {t('Page', 'Halaman')} {state.pageNumber}
                  </span>
                  <Button
                    variant="quiet"
                    disabled={waiting || actionLocked || !page.has_more}
                    onClick={() => void store.next()}
                  >
                    {t('Next page', 'Halaman berikutnya')}
                  </Button>
                </div>
                <p className={styles.help}>
                  {t(
                    'Oldest created first. This is a live list; refresh to include cases that enter this view after you pass their position.',
                    'Urutan dari pembuatan terlama. Daftar ini berubah; muat ulang untuk kasus yang masuk setelah posisinya terlewati.',
                  )}
                </p>
              </>
            ) : null}
            <footer className={styles.queueFooter}>
              <Button
                variant="secondary"
                disabled={waiting || actionLocked || state.phase === 'loading'}
                onClick={() => void store.loadQueue(true)}
              >
                {t('Refresh cases', 'Muat ulang kasus')}
              </Button>
            </footer>
          </section>
          <section
            className={styles.detail}
            aria-label={t('Case details', 'Detail kasus')}
          >
            {caseId ? (
              <Link className={styles.back} to={index}>
                {t('Back to cases', 'Kembali ke kasus')}
              </Link>
            ) : null}
            {!caseId ? (
              <div className={styles.empty}>
                <h2>{t('Choose a case', 'Pilih kasus')}</h2>
                <p>
                  {t(
                    'Open a case to read the customer’s report and handling history.',
                    'Buka kasus untuk membaca laporan pelanggan dan riwayat penanganan.',
                  )}
                </p>
              </div>
            ) : state.detailPhase === 'loading' ? (
              <p role="status" className={styles.empty}>
                {t('Loading case details…', 'Memuat detail kasus…')}
              </p>
            ) : detail && cap && state.detailPhase === 'ready' ? (
              <>
                <header className={styles.detailHeader}>
                  <p className={styles.reference}>{detail.public_reference}</p>
                  <h2 ref={heading} tabIndex={-1}>
                    {detail.summary}
                  </h2>
                  <span className={styles.status}>{status(detail.status)}</span>
                </header>
                {outside ? (
                  <p className={styles.notice}>
                    {t(
                      'This case is outside the current queue filters.',
                      'Kasus ini berada di luar filter antrean saat ini.',
                    )}
                  </p>
                ) : null}
                <dl className={styles.facts}>
                  <div>
                    <dt>{t('Service queue', 'Antrean layanan')}</dt>
                    <dd>{cap.queue.name}</dd>
                  </div>
                  <div>
                    <dt>{t('Assigned staff', 'Petugas yang ditugaskan')}</dt>
                    <dd>
                      {cap.assignee?.display_name ??
                        (detail.assigned_membership_id
                          ? t(
                              'Assigned staff unavailable',
                              'Data petugas belum tersedia',
                            )
                          : t('Unassigned', 'Belum ditugaskan'))}
                    </dd>
                  </div>
                  <div>
                    <dt>{t('Next actor', 'Pihak berikutnya')}</dt>
                    <dd>
                      {
                        {
                          staff: t('Staff', 'Petugas'),
                          customer: t('Customer', 'Pelanggan'),
                          external: t('External service', 'Layanan eksternal'),
                          none: t('None', 'Tidak ada'),
                        }[detail.next_actor]
                      }
                    </dd>
                  </div>
                  <div>
                    <dt>{t('Next action', 'Langkah berikutnya')}</dt>
                    <dd>{detail.next_action}</dd>
                  </div>
                  <div>
                    <dt>{t('Last updated', 'Terakhir diperbarui')}</dt>
                    <dd>{date(detail.updated_at)}</dd>
                  </div>
                </dl>
                <section className={styles.section}>
                  <h3>{t('Customer report', 'Laporan pelanggan')}</h3>
                  <p className={styles.help}>
                    {t(
                      'Original message. Reported references have not been verified against service records.',
                      'Pesan asli. Referensi laporan belum diverifikasi dengan data layanan.',
                    )}
                  </p>
                  <p className={styles.report} lang={detail.language}>
                    {detail.customer_report}
                  </p>
                  {detail.booking_reference ? (
                    <p>
                      {t(
                        'Reported booking reference',
                        'Referensi booking yang dilaporkan',
                      )}
                      : {detail.booking_reference}
                    </p>
                  ) : null}
                  {detail.transaction_reference ? (
                    <p>
                      {t(
                        'Reported transaction reference',
                        'Referensi transaksi yang dilaporkan',
                      )}
                      : {detail.transaction_reference}
                    </p>
                  ) : null}
                </section>
                {detail.status === 'closed' ? (
                  <section className={styles.section}>
                    <h3>{t('Handling outcome', 'Hasil penanganan')}</h3>
                    <p>{detail.resolution}</p>
                    <p>
                      {t('Closure reason', 'Alasan penutupan')}:{' '}
                      {detail.close_reason?.replaceAll('_', ' ')}
                    </p>
                  </section>
                ) : null}
                <StaffMail
                  key={state.scopeKey + ':' + detail.id + ':' + detail.version}
                  queue={store}
                  caseId={detail.id}
                  locale={locale}
                />
                <details className={styles.section}>
                  <summary>{t('Case history', 'Riwayat kasus')}</summary>
                  <ol className={styles.history}>
                    {detail.history.map((event) => (
                      <li key={event.resulting_version}>
                        <strong>{event.event_type.replaceAll('_', ' ')}</strong>
                        <p>{date(event.created_at)}</p>
                        {event.to_status ? (
                          <p>{status(event.to_status)}</p>
                        ) : null}
                        {event.next_action ? <p>{event.next_action}</p> : null}
                        {event.resolution ? <p>{event.resolution}</p> : null}
                        {event.customer_message ? (
                          <p className={styles.report}>
                            {event.customer_message}
                          </p>
                        ) : null}
                      </li>
                    ))}
                  </ol>
                </details>
              </>
            ) : (
              <div className={styles.empty} role="alert">
                <h2>{t('Case unavailable', 'Kasus belum tersedia')}</h2>
                <p>
                  {t(
                    'The case and its current access could not be verified together. Refresh the case or check staff access.',
                    'Kasus dan aksesnya belum dapat diverifikasi bersamaan. Muat ulang kasus atau periksa akses petugas.',
                  )}
                </p>
              </div>
            )}
            {caseId ? (
              <StaffCommunication
                key={'communication:' + caseId}
                queue={store}
                caseId={caseId}
                disabled={actionDraftLocked}
                onLockChange={setCommunicationLocked}
              />
            ) : null}
            {caseId ? (
              <fieldset
                className={styles.operationGroup}
                disabled={communicationLocked}
              >
                <StaffActions
                  key={caseId}
                  queue={store}
                  caseId={caseId}
                  onLockChange={setActionLocked}
                />
              </fieldset>
            ) : null}
            {caseId ? (
              <footer className={styles.detailFooter}>
                <Button
                  variant="secondary"
                  disabled={
                    waiting ||
                    actionLocked ||
                    state.detailPhase === 'loading' ||
                    state.phase !== 'ready'
                  }
                  onClick={() => void store.openCase(caseId, true)}
                >
                  {t('Refresh case', 'Muat ulang kasus')}
                </Button>
              </footer>
            ) : null}
          </section>
        </div>
      )}
    </PageFrame>
  );
}
