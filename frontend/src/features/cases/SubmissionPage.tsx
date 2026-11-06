import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import type { ReactNode } from 'react';
import { Link } from 'react-router';
import { useAuthState } from '../../app/auth/context';
import { useRouting } from '../../app/routing/context';
import { localizedTarget } from '../../app/routing/policy';
import { Button } from '../../components/Button';
import { errorMessage } from '../../api/errors';
import type { SubmissionStore } from './submission';
import { canUseDraft } from './contracts';
import styles from './cases.module.css';

export function SubmissionPage({
  store,
  id,
  review,
}: {
  store: SubmissionStore;
  id: string;
  review: (locked: boolean, footer: ReactNode) => ReactNode;
}) {
  const state = useSyncExternalStore(store.subscribe, store.getState),
    auth = useAuthState(store.auth),
    { locale } = useRouting();
  const heading = useRef<HTMLHeadingElement>(null),
    t = (en: string, id: string) => (locale === 'en' ? en : id);
  useEffect(() => {
    if (auth.phase === 'ready') void store.open(id).catch(() => {});
    return () => store.detach(id);
  }, [store, id, auth.phase, auth.epoch]);
  useEffect(() => {
    if (state.phase === 'receipt') heading.current?.focus();
  }, [state.phase]);
  useEffect(() => {
    if (
      state.phase === 'receipt' &&
      state.receipt?.draft_id === id &&
      auth.phase === 'ready'
    )
      void store.loadReceiptContext(id);
  }, [store, id, state.phase, state.receipt, auth.phase, auth.epoch]);
  const conversationId = store.receiptConversation(id);
  const run = (fn: () => Promise<unknown>) => {
    void fn().catch(() => {});
  };
  if (['review', 'sending', 'unknown'].includes(state.phase))
    return review(
      state.phase !== 'review',
      <SubmissionControls store={store} />,
    );
  return (
    <div className={styles.layout}>
      <aside className={styles.journey}>
        <h2>{t('Your support request', 'Permintaan bantuan Anda')}</h2>
        <p>
          {t(
            'Keep this link and your reference for follow-up.',
            'Simpan tautan dan referensi ini untuk tindak lanjut.',
          )}
        </p>
        <Link to={localizedTarget('/', locale)}>
          {t('Back to help centre', 'Kembali ke pusat bantuan')}
        </Link>
      </aside>
      <section className={styles.card} aria-labelledby="submission-title">
        <p className={styles.eyebrow}>
          {t(
            'Support, with the context that matters',
            'Bantuan dengan konteks yang jelas',
          )}
        </p>
        <h1 id="submission-title" ref={heading} tabIndex={-1}>
          {state.phase === 'receipt'
            ? t(
                'Your support request is recorded',
                'Permintaan bantuan Anda tercatat',
              )
            : t('Check your request status', 'Periksa status permintaan Anda')}
        </h1>
        {state.phase === 'idle' || state.phase === 'loading' ? (
          <p role="status">
            {t(
              'Checking the recorded result. Your request will not be resent.',
              'Memeriksa hasil tercatat. Permintaan tidak dikirim ulang.',
            )}
          </p>
        ) : null}
        {auth.phase === 'ready' &&
        state.phase === 'receipt' &&
        state.receipt?.draft_id === id ? (
          <>
            <p className={styles.intro}>
              {t(
                'Keep the reference below when asking about this request.',
                'Simpan referensi di bawah saat menanyakan permintaan ini.',
              )}
            </p>
            <section className={styles.section}>
              <h2>
                {t('Your request reference', 'Referensi permintaan Anda')}
              </h2>
              <p className={styles.receiptReference}>
                {state.receipt.result_ref}
              </p>
            </section>
            <section className={styles.section}>
              <h2>{t('What happens next', 'Langkah berikutnya')}</h2>
              <p>
                {t(
                  'The support team can review your recorded request. Current case status and next steps have not been loaded in this view.',
                  'Tim bantuan dapat meninjau permintaan tercatat. Status kasus dan langkah berikutnya belum dimuat di tampilan ini.',
                )}
              </p>
              <p>
                {t(
                  'A recorded request does not confirm that an email was sent, delivered or read.',
                  'Permintaan tercatat tidak memastikan email telah dikirim, diterima, atau dibaca.',
                )}
              </p>
            </section>
            <p>
              {conversationId && (
                <Link
                  to={localizedTarget(
                    '/help/conversations/' + conversationId,
                    locale,
                  )}
                >
                  {t(
                    'Continue conversation and check staff updates',
                    'Lanjutkan percakapan dan periksa pembaruan petugas',
                  )}
                </Link>
              )}{' '}
              <Link
                to={localizedTarget(
                  '/help/cases/' + state.receipt.case_id,
                  locale,
                )}
              >
                {t('View case details', 'Lihat detail kasus')}
              </Link>
            </p>
            <Button
              variant="secondary"
              onClick={() => run(() => store.open(id))}
            >
              {t('Check recorded result', 'Periksa hasil tercatat')}
            </Button>
          </>
        ) : null}
        {state.phase === 'unavailable' ? (
          <div role="alert">
            <p>
              {!id
                ? t(
                    'The original request link is missing. Keep any reference you saved for follow-up. This page cannot check the result or safely create a replacement.',
                    'Tautan permintaan asli tidak tersedia. Simpan referensi yang sudah Anda catat. Halaman ini tidak dapat memeriksa hasil atau membuat pengganti dengan aman.',
                  )
                : t(
                    'We could not check the result. Do not assume the request failed or submit a replacement. Keep this link and check again.',
                    'Hasil belum dapat diperiksa. Jangan menganggap permintaan gagal atau membuat pengganti. Simpan tautan ini dan periksa lagi.',
                  )}
            </p>
            {state.error ? <p>{errorMessage(state.error, locale)}</p> : null}
            {id ? (
              <Button
                variant="secondary"
                onClick={() => run(() => store.open(id))}
              >
                {t('Check request status', 'Periksa status permintaan')}
              </Button>
            ) : null}
          </div>
        ) : null}
      </section>
    </div>
  );
}
function SubmissionControls({ store }: { store: SubmissionStore }) {
  const state = useSyncExternalStore(store.subscribe, store.getState),
    draftState = useSyncExternalStore(
      store.drafts.subscribe,
      store.drafts.getState,
    ),
    { locale } = useRouting(),
    auth = useAuthState(store.auth);
  const d = draftState.draft,
    t = (en: string, id: string) => (locale === 'en' ? en : id),
    run = (fn: () => Promise<unknown>) => {
      void fn().catch(() => {});
    };
  const [now, tick] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => tick(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const waiting = now < state.retryAt;
  const canSend =
    !waiting &&
    state.phase === 'review' &&
    auth.phase === 'ready' &&
    !!d &&
    canUseDraft(auth.kind, d.action_type, d.payload, d.case_id) &&
    d?.state === 'pending' &&
    !store.drafts.expired() &&
    !draftState.editing &&
    !draftState.uncertain &&
    !draftState.localFilePending &&
    draftState.phase === 'review';
  return (
    <section
      className={styles.section}
      aria-label={t('Send your request', 'Kirim permintaan Anda')}
    >
      {state.error ? (
        <p role="alert" className={styles.error}>
          {errorMessage(state.error, locale)}
        </p>
      ) : null}
      {state.error?.code === 'CASE_DESTINATION_CHANGED' ? (
        <p role="status">
          {t(
            'The handling destination changed. Edit and save your report to refresh it, then review the updated destination before sending.',
            'Tujuan penanganan berubah. Edit dan simpan laporan untuk memperbaruinya, lalu tinjau tujuan terbaru sebelum mengirim.',
          )}
        </p>
      ) : null}
      {state.error?.code === 'CASE_CONTACT_REQUIRED' ||
      state.error?.code === 'CASE_CONTACT_VERIFICATION_REQUIRED' ? (
        <p role="status">
          {t(
            'Edit the follow-up email and use an address already verified for your account. Entering an address does not verify it.',
            'Edit email tindak lanjut dan gunakan alamat yang sudah terverifikasi untuk akun Anda. Mengisi alamat tidak memverifikasinya.',
          )}
        </p>
      ) : null}
      {waiting ? (
        <p role="status">
          {t(
            'Please wait before sending again.',
            'Tunggu sebelum mengirim lagi.',
          )}{' '}
          {Math.ceil((state.retryAt - now) / 1000)}{' '}
          {t('seconds remaining.', 'detik tersisa.')}
        </p>
      ) : null}
      {state.reviewAgain ? (
        <p role="status">
          {t(
            'Review the complete preview again before choosing to send. No request has been resent automatically.',
            'Tinjau kembali seluruh preview sebelum memilih kirim. Permintaan tidak dikirim ulang otomatis.',
          )}
        </p>
      ) : null}
      {state.phase === 'sending' ? (
        <p role="status">
          {t(
            'Sending your support request. Please wait for its recorded result.',
            'Mengirim permintaan bantuan. Tunggu hasil tercatat.',
          )}
        </p>
      ) : null}
      {state.phase === 'unknown' ? (
        <div className={styles.notice} role="alert">
          <h2>
            {t(
              'We could not confirm the result',
              'Hasil belum dapat dipastikan',
            )}
          </h2>
          <p>
            {t(
              'Your request may already be recorded. Check its status before trying again. A pending result does not mean the earlier request failed.',
              'Permintaan mungkin sudah tercatat. Periksa status sebelum mencoba lagi. Status pending tidak berarti permintaan sebelumnya gagal.',
            )}
          </p>
          <div className={styles.actions}>
            <Button
              variant="secondary"
              onClick={() => run(() => store.recover())}
            >
              {t('Check request status', 'Periksa status permintaan')}
            </Button>
            {state.canRetry ? (
              <Button onClick={() => run(() => store.retry())}>
                {t('Retry the same request', 'Coba ulang permintaan yang sama')}
              </Button>
            ) : null}
          </div>
        </div>
      ) : null}
      {state.phase === 'review' && d?.state === 'pending' ? (
        <>
          <p>
            {d.action_type === 'request_handoff'
              ? d.case_id
                ? t(
                    'Confirming requests staff assistance for your existing case. Its contact and follow-up settings stay unchanged. Staff have not accepted this conversation yet.',
                    'Konfirmasi meminta bantuan petugas untuk kasus yang sama. Pengaturan kontak dan tindak lanjut tetap. Petugas belum menerima percakapan ini.',
                  )
                : t(
                    'Confirming requests staff assistance for this conversation. It does not mean a staff member has accepted yet. Without a follow-up email, continue in this browser.',
                    'Konfirmasi meminta bantuan petugas untuk percakapan ini. Ini belum berarti petugas sudah menerima. Tanpa email tindak lanjut, lanjutkan di browser ini.',
                  )
              : t(
                  'Sending creates a support request for the handling team shown above. It does not change a booking or payment.',
                  'Mengirim akan membuat permintaan bantuan untuk tim di atas. Booking atau pembayaran tidak diubah.',
                )}
          </p>
          <Button
            disabled={!canSend}
            onClick={() => {
              if (d)
                run(() =>
                  store.confirm({
                    draftId: d.draft_id,
                    version: d.version,
                    hash: d.payload_hash,
                    owner: store.auth.capture(),
                  }),
                );
            }}
          >
            {d.action_type === 'request_handoff'
              ? t('Request staff assistance', 'Minta bantuan petugas')
              : t('Send support request', 'Kirim permintaan bantuan')}
          </Button>
        </>
      ) : null}
    </section>
  );
}
