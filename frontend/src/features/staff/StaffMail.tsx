import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import type { StaffQueueStore } from './queue-store';
import type { Locale } from '../../locales/messages';
import { Button } from '../../components/Button';
import { errorMessage } from '../../api/errors';
import { StaffMailStore } from './mail-store';
import styles from './mail.module.css';

export function StaffMail({
  queue,
  caseId,
  locale,
}: {
  queue: StaffQueueStore;
  caseId: string;
  locale: Locale;
}) {
  const store = useMemo(
    () => new StaffMailStore(queue, caseId),
    [queue, caseId],
  );
  const state = useSyncExternalStore(store.subscribe, store.getState);
  const [now, setNow] = useState(Date.now);
  useEffect(() => store.connect(), [store]);
  useEffect(() => {
    if (!state.retryAt || now >= state.retryAt) return;
    const timer = setTimeout(
      () => setNow(Date.now()),
      Math.max(0, Math.min(state.retryAt - Date.now() + 1, 2147483647)),
    );
    return () => clearTimeout(timer);
  }, [state.retryAt, now]);
  const t = (en: string, id: string) => (locale === 'en' ? en : id);
  const date = (value: string) =>
    new Intl.DateTimeFormat(locale === 'en' ? 'en-GB' : 'id-ID', {
      dateStyle: 'medium',
      timeStyle: 'short',
      timeZone: 'UTC',
    }).format(new Date(value)) + ' UTC';
  const busy =
    state.deliveries.phase === 'loading' || state.replies.phase === 'loading';
  const waiting = state.retryAt > now;
  const deliveryLabels = {
    queued: t('Queued', 'Dalam antrean'),
    sending: t('Sending', 'Sedang dikirim'),
    accepted: t('Accepted by provider', 'Diterima penyedia'),
    delivered: t('Delivered', 'Terkirim'),
    bounced: t('Bounced', 'Ditolak tujuan'),
    failed: t('Failed', 'Gagal'),
    delivery_unknown: t(
      'Delivery not confirmed',
      'Pengiriman belum dipastikan',
    ),
  };
  const replyLabels = {
    pending: t('Processing pending', 'Menunggu pemrosesan'),
    publishing: t('Publication in progress', 'Sedang dipublikasikan'),
    published: t('Added to case', 'Ditambahkan ke kasus'),
    review: t('Needs staff review', 'Perlu ditinjau petugas'),
    rejected: t('Not added to case', 'Tidak ditambahkan ke kasus'),
    challenged: t('Verification requested', 'Verifikasi diminta'),
    verification_reply: t('Verification response', 'Balasan verifikasi'),
  };
  const verificationLabels = {
    pending: t('Pending', 'Menunggu'),
    expired: t('Expired', 'Kedaluwarsa'),
    consumed: t('Completed', 'Selesai'),
    failed: t('Failed', 'Gagal'),
  };
  const sendLabels = {
    queued: t('Queued', 'Dalam antrean'),
    reserved: t('Being prepared', 'Sedang disiapkan'),
    accepted: t('Accepted by provider', 'Diterima penyedia'),
    failed: t('Failed', 'Gagal'),
    delivery_unknown: t(
      'Delivery not confirmed',
      'Pengiriman belum dipastikan',
    ),
  };
  return (
    <section
      className={styles.section}
      aria-label={t('Email activity', 'Aktivitas email')}
    >
      <h3>{t('Email activity', 'Aktivitas email')}</h3>
      {!state.available ? (
        <p role="status">
          {t(
            'Email activity is hidden until current case access is verified.',
            'Aktivitas email disembunyikan sampai akses kasus terkini terverifikasi.',
          )}
        </p>
      ) : state.denied ? (
        <div role="alert">
          <p>
            {t(
              'Email access could not be verified. Refresh the case and check staff access.',
              'Akses email belum dapat diverifikasi. Muat ulang kasus dan periksa akses petugas.',
            )}
          </p>
          <Button
            variant="secondary"
            onClick={() => {
              void queue.openCase(caseId, true);
            }}
          >
            {t('Refresh case access', 'Muat ulang akses kasus')}
          </Button>
        </div>
      ) : (
        <>
          <p>
            {t(
              'Provider acceptance is not delivery. Delivery does not prove the email was read. Unconfirmed delivery must be reviewed before any resend.',
              'Penerimaan penyedia belum berarti terkirim. Status terkirim tidak membuktikan email dibaca. Pengiriman yang belum pasti harus diperiksa sebelum pengiriman ulang.',
            )}
          </p>
          <Button
            variant="secondary"
            disabled={busy || waiting}
            onClick={() => {
              void store.load();
            }}
          >
            {busy
              ? t('Loading email activity…', 'Memuat aktivitas email…')
              : state.deliveries.phase === 'idle' &&
                  state.replies.phase === 'idle'
                ? t('Load email activity', 'Muat aktivitas email')
                : t('Refresh email activity', 'Muat ulang aktivitas email')}
          </Button>
          {waiting && (
            <p role="status">
              {t(
                'Please wait before refreshing again.',
                'Tunggu sebelum memuat ulang lagi.',
              )}
            </p>
          )}
          <h4>{t('Outbound delivery', 'Pengiriman keluar')}</h4>
          {state.deliveries.error && (
            <p role="status">{errorMessage(state.deliveries.error, locale)}</p>
          )}
          {state.deliveries.phase === 'ready' && state.deliveries.data && (
            <>
              <p>
                {t(
                  'Up to 100 latest sends are shown. This view does not include message text or recipients.',
                  'Tampilan memuat maksimal 100 kiriman terbaru. Teks pesan dan penerima tidak disertakan.',
                )}
              </p>
              {state.deliveries.data.has_more && (
                <p role="status">
                  {t(
                    'More sends exist. Additional pages are not available in this view.',
                    'Masih ada kiriman lainnya. Halaman lanjutan belum tersedia pada tampilan ini.',
                  )}
                </p>
              )}
              {!state.deliveries.data.items.length && (
                <p>
                  {t(
                    'No outbound sends were returned for this case.',
                    'Tidak ada kiriman keluar yang dikembalikan untuk kasus ini.',
                  )}
                </p>
              )}
              <ol className={styles.list}>
                {state.deliveries.data.items.map((item) => (
                  <li key={item.job_id}>
                    <strong>{deliveryLabels[item.delivery_status]}</strong>
                    <p>
                      {t('Queued: ', 'Masuk antrean: ')}
                      <time dateTime={item.queued_at}>
                        {date(item.queued_at)}
                      </time>
                    </p>
                    {item.provider_status_at && (
                      <p>
                        {t('Provider update: ', 'Pembaruan penyedia: ')}
                        <time dateTime={item.provider_status_at}>
                          {date(item.provider_status_at)}
                        </time>
                      </p>
                    )}
                    {item.last_checked_at && (
                      <p>
                        {t('Last checked: ', 'Terakhir diperiksa: ')}
                        <time dateTime={item.last_checked_at}>
                          {date(item.last_checked_at)}
                        </time>
                      </p>
                    )}
                    {item.needs_review && (
                      <p>
                        {t(
                          'Requires staff review.',
                          'Memerlukan tinjauan petugas.',
                        )}
                      </p>
                    )}
                  </li>
                ))}
              </ol>
            </>
          )}
          <h4>{t('Inbound replies', 'Balasan masuk')}</h4>
          {state.replies.error && (
            <p role="status">{errorMessage(state.replies.error, locale)}</p>
          )}
          {state.replies.phase === 'ready' && state.replies.data && (
            <>
              <p>
                {t(
                  'Up to 100 replies are shown in server order, oldest first. This is not a complete email thread.',
                  'Maksimal 100 balasan ditampilkan sesuai urutan server, dari yang terlama. Ini bukan thread email lengkap.',
                )}
              </p>
              {state.replies.data.length === 100 && (
                <p role="status">
                  {t(
                    'The reply limit was reached. The service does not provide a continuation indicator.',
                    'Batas balasan tercapai. Layanan tidak menyediakan penanda kelanjutan.',
                  )}
                </p>
              )}
              {!state.replies.data.length && (
                <p>
                  {t(
                    'No inbound replies were returned for this case.',
                    'Tidak ada balasan masuk yang dikembalikan untuk kasus ini.',
                  )}
                </p>
              )}
              <ol className={styles.list}>
                {state.replies.data.map((item) => (
                  <li key={item.email_id}>
                    <strong>{replyLabels[item.state]}</strong>
                    <p>
                      <time dateTime={item.created_at}>
                        {date(item.created_at)}
                      </time>
                    </p>
                    {item.verification_state && (
                      <p>
                        {t('Verification: ', 'Verifikasi: ')}
                        {verificationLabels[item.verification_state]}
                      </p>
                    )}
                    {item.send_state && (
                      <p>
                        {t('Verification email: ', 'Email verifikasi: ')}
                        {sendLabels[item.send_state]}
                      </p>
                    )}
                    {item.body !== null ? (
                      <p className={styles.body}>{item.body}</p>
                    ) : (
                      <p>
                        {t(
                          'Message text is not available in this authorized view. Processing and verification metadata are shown above.',
                          'Teks pesan tidak tersedia pada tampilan berizin ini. Metadata pemrosesan dan verifikasi ditampilkan di atas.',
                        )}
                      </p>
                    )}
                  </li>
                ))}
              </ol>
            </>
          )}
        </>
      )}
    </section>
  );
}
