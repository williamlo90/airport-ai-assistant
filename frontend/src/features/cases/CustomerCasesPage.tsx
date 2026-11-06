import { useContext, useEffect, useMemo, useSyncExternalStore } from 'react';
import { Link } from 'react-router';
import { AuthContext, useAuthState } from '../../app/auth/context';
import { PageFrame } from '../../app/routing/pages';
import { useRouting } from '../../app/routing/context';
import { localizedTarget } from '../../app/routing/policy';
import { Button } from '../../components/Button';
import { errorMessage } from '../../api/errors';
import { caseStatuses } from './detail-contracts';
import { CustomerCasesStore } from './customer-store';
import type { CustomerCaseStatus } from './customer-contracts';
import styles from './cases.module.css';

export function CustomerCasesPage() {
  const auth = useContext(AuthContext);
  const state = useAuthState(auth),
    { locale } = useRouting();
  const store = useMemo(
    () => (auth ? new CustomerCasesStore(auth) : null),
    [auth],
  );
  const verified = state.phase === 'ready' && state.kind === 'customer';
  return (
    <PageFrame>
      {store && verified ? (
        <CaseList key={state.epoch} store={store} />
      ) : (
        <section className={styles.card}>
          <h1>{locale === 'en' ? 'My cases' : 'Kasus saya'}</h1>
          <p className={styles.intro}>
            {locale === 'en'
              ? 'Verify your email to view your support cases.'
              : 'Verifikasi email Anda untuk melihat kasus bantuan Anda.'}
          </p>
          <Link to={localizedTarget('/help/verify', locale)}>
            {locale === 'en' ? 'Verify email' : 'Verifikasi email'}
          </Link>
        </section>
      )}
    </PageFrame>
  );
}
function CaseList({ store }: { store: CustomerCasesStore }) {
  const state = useSyncExternalStore(store.subscribe, store.getState),
    { locale } = useRouting();
  const t = (en: string, id: string) => (locale === 'en' ? en : id);
  useEffect(() => {
    const off = store.connect();
    void store.refresh();
    return off;
  }, [store]);
  const labels: Record<CustomerCaseStatus, string> = {
    new: t('Awaiting assignment', 'Menunggu penugasan'),
    in_progress: t('In progress', 'Sedang ditangani'),
    awaiting_customer: t(
      'Waiting for your information',
      'Menunggu informasi Anda',
    ),
    awaiting_external: t(
      'Waiting for an external team',
      'Menunggu pihak eksternal',
    ),
    closed: t('Closed', 'Ditutup'),
  };
  const busy = state.phase === 'loading';
  return (
    <div className={styles.layout}>
      <aside className={styles.journey}>
        <h2>{t('Your support', 'Bantuan Anda')}</h2>
        <p>
          {t(
            'Follow each request and see what happens next.',
            'Ikuti setiap permintaan dan langkah berikutnya.',
          )}
        </p>
        <Link to={localizedTarget('/', locale)}>
          {t('Help centre', 'Pusat bantuan')}
        </Link>
      </aside>
      <section className={styles.card} aria-labelledby="my-cases-title">
        <p className={styles.eyebrow}>
          {t('Your requests, in one place', 'Permintaan Anda, di satu tempat')}
        </p>
        <h1 id="my-cases-title">{t('My cases', 'Kasus saya')}</h1>
        <p className={styles.intro}>
          {t(
            'Open a case for its latest information and next step. Cases appear oldest first.',
            'Buka kasus untuk informasi terbaru dan langkah berikutnya. Kasus terlama tampil terlebih dahulu.',
          )}
        </p>
        <div className={styles.form}>
          <label htmlFor="my-cases-status">
            {t('Case status', 'Status kasus')}
          </label>
          <select
            id="my-cases-status"
            value={state.status ?? ''}
            disabled={busy}
            onChange={(e) => {
              void store.refresh(
                (e.target.value || null) as CustomerCaseStatus | null,
              );
            }}
          >
            <option value="">{t('All statuses', 'Semua status')}</option>
            {caseStatuses.map((status) => (
              <option key={status} value={status}>
                {labels[status]}
              </option>
            ))}
          </select>
        </div>
        {busy && (
          <p role="status" className={styles.intro}>
            {t('Loading your cases…', 'Memuat kasus Anda…')}
          </p>
        )}
        {state.error && (
          <p role="alert" className={styles.error}>
            {errorMessage(state.error, locale)}
          </p>
        )}
        {state.page && (
          <>
            {state.page.items.length === 0 ? (
              <p role="status" className={styles.intro}>
                {t(
                  'No cases found for this selection.',
                  'Tidak ada kasus untuk pilihan ini.',
                )}
              </p>
            ) : (
              <ul
                className={styles.caseList}
                aria-label={t('Your cases', 'Kasus Anda')}
              >
                {state.page.items.map((item) => (
                  <li key={item.id}>
                    <p className={styles.eyebrow}>{labels[item.status]}</p>
                    <h2>
                      <Link
                        to={localizedTarget('/help/cases/' + item.id, locale)}
                      >
                        {item.summary}
                      </Link>
                    </h2>
                    <p className={styles.caseReference}>
                      {item.public_reference}
                    </p>
                    <p>{item.next_action}</p>
                  </li>
                ))}
              </ul>
            )}
            <div
              className={styles.actions}
              aria-label={t('Case pages', 'Halaman kasus')}
            >
              <Button
                variant="secondary"
                disabled={!state.canGoBack}
                onClick={() => {
                  void store.previous();
                }}
              >
                {t('Previous', 'Sebelumnya')}
              </Button>
              <Button
                variant="secondary"
                disabled={!state.page.has_more}
                onClick={() => {
                  void store.next();
                }}
              >
                {t('Next', 'Berikutnya')}
              </Button>
            </div>
          </>
        )}
        <div className={styles.actions}>
          <Button
            variant="quiet"
            disabled={busy}
            onClick={() => {
              void store.refresh();
            }}
          >
            {t('Refresh cases', 'Muat ulang kasus')}
          </Button>
        </div>
      </section>
    </div>
  );
}
