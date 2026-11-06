import {
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import { Link } from 'react-router';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { AuthContext, useAuthState } from '../../app/auth/context';
import { useRouting } from '../../app/routing/context';
import { localizedTarget } from '../../app/routing/policy';
import { PageFrame } from '../../app/routing/pages';
import { Button } from '../../components/Button';
import { errorMessage } from '../../api/errors';
import { formatMoney, formatTimestamp } from '../../locales/format';
import { lookupSchema } from './contracts';
import type { LookupInput, Transaction } from './contracts';
import { SourceStore, useSourceStore } from './store';
import styles from '../cases/cases.module.css';

export function SourcePage() {
  const supplied = useSourceStore(),
    auth = useContext(AuthContext),
    { site } = useRouting();
  const owned = useMemo(
    () => (!supplied && auth ? new SourceStore(auth, site.deployment) : null),
    [supplied, auth, site.deployment],
  );
  useEffect(() => {
    if (!owned) return;
    const off = owned.connect();
    return () => {
      off();
      owned.dispose();
    };
  }, [owned]);
  const store = supplied ?? owned;
  return (
    <PageFrame>
      {store ? (
        <SourceScreen store={store} />
      ) : (
        <p role="alert">Service records are unavailable.</p>
      )}
    </PageFrame>
  );
}
function SourceScreen({ store }: { store: SourceStore }) {
  const state = useSyncExternalStore(store.subscribe, store.getState),
    auth = useAuthState(store.auth),
    { locale } = useRouting(),
    t = (en: string, id: string) => (locale === 'en' ? en : id);
  const [now, tick] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => tick(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => () => store.detach(), [store]);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (state.phase === 'ready') heading.current?.focus();
  }, [state.phase]);
  const run = (f: () => Promise<unknown>) => {
      void f().catch(() => {});
    },
    busy = state.phase === 'loading',
    waiting = now < state.retryAt;
  const time = (value: string, zone = 'UTC') =>
    formatTimestamp(value, locale, zone);
  const status = (value: string) =>
    ({
      confirmed: t('Confirmed', 'Terkonfirmasi'),
      cancelled: t('Cancelled', 'Dibatalkan'),
      pending: t('Pending', 'Tertunda'),
      unknown: t('Not confirmed by the source', 'Belum dipastikan oleh sumber'),
      paid: t('Paid', 'Dibayar'),
      failed: t('Failed', 'Gagal'),
      refunded: t('Refunded', 'Dikembalikan'),
    })[value as 'paid'];
  const observation = (v: {
    observed_at: string;
    source_updated_at: string;
  }) => (
    <dl>
      <div>
        <dt>{t('Observed at', 'Dibaca pada')}</dt>
        <dd>{time(v.observed_at)}</dd>
      </div>
      <div>
        <dt>{t('Source last updated', 'Terakhir diperbarui sumber')}</dt>
        <dd>{time(v.source_updated_at)}</dd>
      </div>
    </dl>
  );
  const transaction = (v: Transaction) => (
    <article
      className={styles.section}
      key={v.reference}
      aria-label={t('Transaction', 'Transaksi') + ' ' + v.reference}
    >
      <h3>{v.reference}</h3>
      <dl>
        <div>
          <dt>{t('Payment status', 'Status pembayaran')}</dt>
          <dd>{status(v.status)}</dd>
        </div>
        {v.source_status_label ? (
          <div>
            <dt>
              {t('Recorded source status label', 'Label status asli sumber')}
            </dt>
            <dd>{v.source_status_label}</dd>
          </div>
        ) : null}
        {v.booking_reference ? (
          <div>
            <dt>{t('Booking reference', 'Referensi booking')}</dt>
            <dd>{v.booking_reference}</dd>
          </div>
        ) : null}
        {v.allowed_scopes.includes('payment_details:read') ? (
          <>
            <div>
              <dt>{t('Amount', 'Nominal')}</dt>
              <dd>
                {formatMoney(
                  v.amount_minor!,
                  v.currency!,
                  v.currency_exponent!,
                  locale,
                )}{' '}
                ({v.currency})
              </dd>
            </div>
            <div>
              <dt>{t('Refund status', 'Status pengembalian')}</dt>
              <dd>
                {
                  {
                    none: t(
                      'No refund recorded',
                      'Tidak ada pengembalian tercatat',
                    ),
                    pending: t('Pending', 'Tertunda'),
                    completed: t('Completed', 'Selesai'),
                    unknown: t(
                      'Not confirmed by the source',
                      'Belum dipastikan oleh sumber',
                    ),
                  }[v.refund_status!]
                }
              </dd>
            </div>
          </>
        ) : null}
      </dl>
      {!v.allowed_scopes.includes('payment_details:read') ? (
        <p className={styles.help}>
          {t(
            'Only payment status is available with your current access. Amount and refund details are not provided.',
            'Akses saat ini hanya menyediakan status pembayaran. Nominal dan detail pengembalian tidak diberikan.',
          )}
        </p>
      ) : null}
      {observation(v)}
    </article>
  );
  return (
    <div className={styles.layout}>
      <aside className={styles.journey}>
        <h2>{t('Your service records', 'Data layanan Anda')}</h2>
        <p>
          {t(
            'Check an existing booking or payment using its reference.',
            'Periksa booking atau pembayaran yang sudah ada dengan referensinya.',
          )}
        </p>
        <Link to={localizedTarget('/', locale)}>
          {t('Help centre', 'Pusat bantuan')}
        </Link>
      </aside>
      <section className={styles.card} aria-labelledby="source-title">
        <p className={styles.eyebrow}>
          {t(
            'Information from the service system',
            'Informasi dari sistem layanan',
          )}
        </p>
        <h1 id="source-title" ref={heading} tabIndex={-1}>
          {t(
            'Check your booking or payment',
            'Periksa booking atau pembayaran Anda',
          )}
        </h1>
        <p className={styles.intro}>
          {t(
            'These records are read from the service system. They are separate from your report and case status.',
            'Data ini dibaca dari sistem layanan. Data ini terpisah dari laporan dan status kasus Anda.',
          )}
        </p>
        {auth.phase === 'ready' && auth.kind === 'customer' ? (
          <LookupForm
            key={state.formGeneration}
            store={store}
            busy={busy || waiting}
          />
        ) : (
          <p>
            {t(
              'A verified customer session is required to check service records. Return to your support conversation to verify your email.',
              'Sesi pelanggan terverifikasi diperlukan untuk memeriksa data layanan. Kembali ke percakapan bantuan untuk memverifikasi email Anda.',
            )}
          </p>
        )}
        {busy ? (
          <p role="status">
            {t(
              'Checking current access and source data…',
              'Memeriksa akses dan data sumber terbaru…',
            )}
          </p>
        ) : null}
        {waiting ? (
          <p role="status">
            {t(
              'Please wait before checking again.',
              'Tunggu sebelum memeriksa lagi.',
            )}{' '}
            {Math.ceil((state.retryAt - now) / 1000)}{' '}
            {t('seconds remaining.', 'detik tersisa.')}
          </p>
        ) : null}
        {state.phase === 'unavailable' ? (
          <div role="alert" className={styles.notice}>
            <h2>
              {t(
                'Service information is unavailable',
                'Informasi layanan tidak tersedia',
              )}
            </h2>
            <p>
              {state.error?.status === 404 || state.error?.status === 403
                ? t(
                    'This information is not available to your account. Check the reference or ask support for help.',
                    'Informasi ini tidak tersedia untuk akun Anda. Periksa referensi atau minta bantuan petugas.',
                  )
                : state.error?.status === 502 ||
                    state.error?.kind === 'invalid-response'
                  ? t(
                      'The source returned information that could not be validated. No earlier record is being shown. Try checking again.',
                      'Informasi dari sumber tidak dapat divalidasi. Data lama tidak ditampilkan. Coba periksa lagi.',
                    )
                  : state.error?.status === 503
                    ? t(
                        'The service system could not be reached. This does not mean your booking or payment failed. Try checking again.',
                        'Sistem layanan tidak dapat dihubungi. Ini tidak berarti booking atau pembayaran Anda gagal. Coba periksa lagi.',
                      )
                    : state.error
                      ? errorMessage(state.error, locale)
                      : t('Try checking again.', 'Coba periksa lagi.')}
            </p>
          </div>
        ) : null}
        {state.booking ? (
          <section
            className={styles.section}
            aria-label={t('Booking details', 'Detail booking')}
          >
            <h2>{state.booking.reference}</h2>
            <dl>
              <div>
                <dt>{t('Booking status', 'Status booking')}</dt>
                <dd>{status(state.booking.status)}</dd>
              </div>
              <div>
                <dt>{t('Service', 'Layanan')}</dt>
                <dd>
                  {state.booking.service_type === 'parking'
                    ? t('Parking', 'Parkir')
                    : t('Lounge', 'Lounge')}
                </dd>
              </div>
              <div>
                <dt>{t('Location', 'Lokasi')}</dt>
                <dd>{state.booking.location}</dd>
              </div>
              <div>
                <dt>{t('Starts', 'Mulai')}</dt>
                <dd>
                  {time(state.booking.start_at, state.booking.service_timezone)}
                </dd>
              </div>
              <div>
                <dt>{t('Ends', 'Selesai')}</dt>
                <dd>
                  {time(state.booking.end_at, state.booking.service_timezone)}
                </dd>
              </div>
              <div>
                <dt>{t('Service timezone', 'Zona waktu layanan')}</dt>
                <dd>{state.booking.service_timezone}</dd>
              </div>
            </dl>
            {state.booking.source_status_label ? (
              <p>
                {t(
                  'Recorded source status label:',
                  'Label status asli sumber:',
                )}{' '}
                {state.booking.source_status_label}
              </p>
            ) : null}
            {observation(state.booking)}
            <p className={styles.help}>
              {t(
                'Booking access does not automatically include payment access.',
                'Akses booking tidak otomatis memberikan akses pembayaran.',
              )}
            </p>
            <Button
              variant="secondary"
              disabled={busy || waiting}
              onClick={() => run(() => store.transactions())}
            >
              {t('Check related transactions', 'Periksa transaksi terkait')}
            </Button>
          </section>
        ) : null}
        {state.transaction ? transaction(state.transaction) : null}
        {state.page ? (
          <section
            className={styles.section}
            aria-label={t('Related transactions', 'Transaksi terkait')}
          >
            <h2>
              {t(
                'Transactions available to your account',
                'Transaksi yang tersedia untuk akun Anda',
              )}
            </h2>
            <p className={styles.help}>
              {t('Page', 'Halaman')} {state.pageNumber}.{' '}
              {t(
                'This is an authorized page, not a total of all payments.',
                'Ini halaman sesuai hak akses, bukan total semua pembayaran.',
              )}
            </p>
            {state.page.items.length ? (
              state.page.items.map(transaction)
            ) : (
              <p>
                {t(
                  'No transactions are available on this page for your account.',
                  'Tidak ada transaksi yang tersedia pada halaman ini untuk akun Anda.',
                )}
              </p>
            )}
            {state.page.next_cursor ? (
              <Button
                variant="secondary"
                disabled={busy || waiting}
                onClick={() => run(() => store.transactions(true))}
              >
                {t('Next transaction page', 'Halaman transaksi berikutnya')}
              </Button>
            ) : null}
          </section>
        ) : null}
        <section className={styles.section}>
          <h2>
            {t(
              'Need help understanding a record?',
              'Perlu bantuan memahami data?',
            )}
          </h2>
          <Link to={localizedTarget('/help/new?topic=contact', locale)}>
            {t('Start a support conversation', 'Mulai percakapan bantuan')}
          </Link>
          <p className={styles.help}>
            {t(
              'No service record is copied into a support request by this page.',
              'Halaman ini tidak menyalin data layanan ke permintaan bantuan.',
            )}
          </p>
        </section>
      </section>
    </div>
  );
}
function LookupForm({ store, busy }: { store: SourceStore; busy: boolean }) {
  const { locale } = useRouting(),
    t = (en: string, id: string) => (locale === 'en' ? en : id),
    summary = useRef<HTMLDivElement>(null);
  const {
    register,
    getValues,
    handleSubmit,
    setFocus,
    formState: { errors },
  } = useForm<LookupInput>({
    resolver: zodResolver(lookupSchema),
    defaultValues: store.getState().input,
    shouldFocusError: false,
  });
  useEffect(() => {
    if (Object.keys(errors).length) summary.current?.focus();
  }, [errors]);
  return (
    <form
      className={styles.form}
      noValidate
      onChange={() => store.changeInput(getValues())}
      onSubmit={handleSubmit((v) => {
        store.changeInput(v);
        void store.lookup().catch(() => {});
      })}
    >
      {Object.keys(errors).length ? (
        <div ref={summary} tabIndex={-1} role="alert" className={styles.error}>
          <h3>{t('Check the reference', 'Periksa referensi')}</h3>
          <a
            href="#source-reference"
            onClick={(e) => {
              e.preventDefault();
              setFocus('reference');
            }}
          >
            {t(
              'Use 1–80 letters, numbers, underscores or hyphens.',
              'Gunakan 1–80 huruf, angka, garis bawah atau tanda hubung.',
            )}
          </a>
        </div>
      ) : null}
      <label htmlFor="source-kind">{t('Record type', 'Jenis data')}</label>
      <select id="source-kind" {...register('kind')}>
        <option value="booking">{t('Booking', 'Booking')}</option>
        <option value="transaction">{t('Transaction', 'Transaksi')}</option>
      </select>
      <label htmlFor="source-reference">{t('Reference', 'Referensi')}</label>
      <input
        id="source-reference"
        {...register('reference')}
        maxLength={80}
        autoComplete="off"
        spellCheck={false}
        aria-invalid={!!errors.reference}
        aria-describedby={
          errors.reference
            ? 'source-reference-error source-reference-help'
            : 'source-reference-help'
        }
      />
      {errors.reference ? (
        <p id="source-reference-error" className={styles.fieldError}>
          {t(
            'Use 1–80 letters, numbers, underscores or hyphens.',
            'Gunakan 1–80 huruf, angka, garis bawah atau tanda hubung.',
          )}
        </p>
      ) : null}
      <p id="source-reference-help" className={styles.help}>
        {t(
          'A reference does not grant access. The service system checks which records your account may read.',
          'Referensi tidak memberikan akses. Sistem layanan memeriksa data yang boleh dibaca akun Anda.',
        )}
      </p>
      <Button type="submit" disabled={busy}>
        {t('Check service record', 'Periksa data layanan')}
      </Button>
    </form>
  );
}
