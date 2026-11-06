import { Rating } from '../feedback/Rating';
import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import { Link, useParams } from 'react-router';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { PageFrame } from '../../app/routing/pages';
import { useRouting } from '../../app/routing/context';
import { localizedTarget } from '../../app/routing/policy';
import { useUnsavedChanges } from '../../app/routing/draftState';
import { useAuthState } from '../../app/auth/context';
import { Button } from '../../components/Button';
import { errorMessage } from '../../api/errors';
import { useCaseDetail } from './detail-store';
import type { CaseDetailStore } from './detail-store';
import { continuationFormSchema } from './detail-contracts';
import type { CaseDetail } from './detail-contracts';
import styles from './cases.module.css';
import { AdditionalInformation } from './AdditionalInformation';
import { CaseAttachments } from './CaseAttachments';

export function CaseDetailPage() {
  const store = useCaseDetail(),
    { caseId } = useParams();
  return (
    <PageFrame>
      {store && caseId ? (
        <DetailScreen key={caseId} store={store} id={caseId} />
      ) : (
        <p role="alert">Case details are unavailable.</p>
      )}
    </PageFrame>
  );
}
function DetailScreen({ store, id }: { store: CaseDetailStore; id: string }) {
  const state = useSyncExternalStore(store.subscribe, store.getState),
    auth = useAuthState(store.auth),
    { locale } = useRouting();
  const heading = useRef<HTMLHeadingElement>(null),
    t = (en: string, id: string) => (locale === 'en' ? en : id);
  const [now, tick] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => tick(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    if (auth.phase === 'ready') void store.open(id).catch(() => {});
    return () => store.detach(id);
  }, [store, id, auth.phase, auth.epoch]);
  useLayoutEffect(() => {
    if (state.phase === 'ready' && !state.editing) heading.current?.focus();
  }, [state.phase, state.editing]);
  useUnsavedChanges(
    (state.editing && !!state.message) ||
      state.phase === 'sending' ||
      state.phase === 'unknown',
    () => store.discard(),
  );
  const c = state.detail,
    run = (fn: () => Promise<unknown>) => {
      void fn().catch(() => {});
    },
    busy = state.phase === 'sending' || state.phase === 'loading';
  const status = (value: CaseDetail['status']) =>
    ({
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
    })[value];
  const actor = (value: CaseDetail['next_actor']) =>
    ({
      staff: t('Support team', 'Tim bantuan'),
      customer: t('You', 'Anda'),
      external: t('External team', 'Pihak eksternal'),
      none: t('No action pending', 'Tidak ada tindakan tertunda'),
    })[value];
  const reason = (value: NonNullable<CaseDetail['close_reason']>) =>
    ({
      resolved: t('Resolved', 'Terselesaikan'),
      customer_cancelled: t('Cancelled by customer', 'Dibatalkan pelanggan'),
      duplicate: t('Duplicate case', 'Kasus duplikat'),
      no_response: t('No response', 'Tidak ada balasan'),
    })[value];
  const date = (value: string) =>
    new Intl.DateTimeFormat(locale === 'en' ? 'en-GB' : 'id-ID', {
      dateStyle: 'medium',
      timeStyle: 'short',
      timeZone: 'UTC',
    }).format(new Date(value)) + ' UTC';
  const events = {
    created: t('Request recorded', 'Permintaan tercatat'),
    identity_verified: t(
      'Customer identity verified',
      'Identitas pelanggan terverifikasi',
    ),
    assigned: t('Assignment updated', 'Penugasan diperbarui'),
    status_changed: t('Status updated', 'Status diperbarui'),
    reopened: t(
      'Same-issue follow-up recorded',
      'Tindak lanjut masalah yang sama tercatat',
    ),
    email_received: t('Email reply recorded', 'Balasan email tercatat'),
    customer_information: t(
      'Additional information recorded',
      'Informasi tambahan tercatat',
    ),
  };
  const prior =
    c?.history.filter(
      (h) =>
        h.event_type === 'status_changed' &&
        h.to_status === 'closed' &&
        h.close_reason &&
        h.resolution,
    ) ?? [];
  const waiting = now < state.retryAt;
  return (
    <div className={styles.layout}>
      <aside className={styles.journey}>
        <h2>{t('Your support case', 'Kasus bantuan Anda')}</h2>
        {c ? (
          <>
            <p>{t('Case reference', 'Referensi kasus')}</p>
            <strong className={styles.caseReference}>
              {c.public_reference}
            </strong>
          </>
        ) : null}
        <Link to={localizedTarget('/', locale)}>
          {t('Help centre', 'Pusat bantuan')}
        </Link>
        <Link to={localizedTarget('/help/services', locale)}>
          {t('Check service records', 'Periksa data layanan')}
        </Link>
        <Link to={localizedTarget('/help/cases', locale)}>
          {t('My cases', 'Kasus saya')}
        </Link>
      </aside>
      <section className={styles.card} aria-labelledby="case-title">
        <p className={styles.eyebrow}>
          {t(
            'Support, with the context that matters',
            'Bantuan dengan konteks yang jelas',
          )}
        </p>
        <h1 ref={heading} tabIndex={-1} id="case-title">
          {t('Your case, at a glance', 'Ringkasan kasus Anda')}
        </h1>
        {state.phase === 'idle' || state.phase === 'loading' ? (
          <p role="status">
            {t(
              'Reading the latest authorized case. No follow-up is sent by checking.',
              'Membaca kasus terbaru yang berhak diakses. Pemeriksaan tidak mengirim tindak lanjut.',
            )}
          </p>
        ) : null}
        {state.phase === 'unavailable' ? (
          <div role="alert">
            <p>
              {t(
                'Case information is not available. Check your access and try reading it again.',
                'Informasi kasus tidak tersedia. Periksa akses dan coba baca kembali.',
              )}
            </p>
            {state.error ? <p>{errorMessage(state.error, locale)}</p> : null}
            <Button
              variant="secondary"
              onClick={() => run(() => store.open(id))}
            >
              {t('Try reading case again', 'Coba baca kasus lagi')}
            </Button>
          </div>
        ) : null}
        {c ? (
          <>
            <div className={styles.status}>
              <strong>{status(c.status)}</strong>
              <span>
                {t('Latest update:', 'Pembaruan terakhir:')}{' '}
                {date(c.updated_at)}
              </span>
            </div>
            {c.status !== 'closed' && (
              <Link
                to={localizedTarget(
                  '/help/new?topic=contact&case=' + c.id,
                  locale,
                )}
              >
                {t(
                  'Ask staff about this case',
                  'Minta bantuan petugas untuk kasus ini',
                )}
              </Link>
            )}
            <section className={styles.section}>
              <h2>{t('What happens next', 'Langkah berikutnya')}</h2>
              <dl>
                <div>
                  <dt>{t('Next actor', 'Pihak berikutnya')}</dt>
                  <dd>{actor(c.next_actor)}</dd>
                </div>
                <div>
                  <dt>{t('Next step', 'Langkah berikutnya')}</dt>
                  <dd className={styles.report}>{c.next_action}</dd>
                </div>
              </dl>
            </section>
            {c.status === 'closed' && c.close_reason && c.resolution ? (
              <section className={styles.section}>
                <h2>{t('Handling result', 'Hasil penanganan')}</h2>
                <p>{reason(c.close_reason)}</p>
                <p className={styles.report}>{c.resolution}</p>
                {c.related_case_ref ? (
                  <p>
                    {t('Related case:', 'Kasus terkait:')} {c.related_case_ref}
                  </p>
                ) : null}
                {c.closed_at ? (
                  <p className={styles.help}>
                    {t('Recorded closure:', 'Penutupan tercatat:')}{' '}
                    {date(c.closed_at)}
                  </p>
                ) : null}
                {c.close_reason === 'no_response' ? (
                  <p>
                    {t(
                      'No response does not mean you were satisfied. You can still request help with the same issue.',
                      'Tidak ada balasan bukan berarti Anda puas. Anda tetap dapat meminta bantuan untuk masalah yang sama.',
                    )}
                  </p>
                ) : null}
              </section>
            ) : null}
            <Rating kind="case" id={c.id} revision={c.version} />
            {c.status !== 'closed' && prior.length > 0 ? (
              <section className={styles.section}>
                <h2>
                  {t(
                    'Previous handling results',
                    'Hasil penanganan sebelumnya',
                  )}
                </h2>
                {prior.map((h) => (
                  <div key={h.resulting_version}>
                    <p>{reason(h.close_reason!)}</p>
                    <p className={styles.report}>{h.resolution}</p>
                    <p className={styles.help}>
                      {t(
                        'History event recorded:',
                        'Peristiwa riwayat tercatat:',
                      )}{' '}
                      {date(h.created_at)}
                    </p>
                  </div>
                ))}
              </section>
            ) : null}
            {state.error && state.phase !== 'unavailable' ? (
              <p role="alert" className={styles.error}>
                {errorMessage(state.error, locale)}
              </p>
            ) : null}
            {state.notice === 'recorded' ? (
              <p role="status">
                {t(
                  'Your follow-up request was recorded. The current case status is shown above.',
                  'Permintaan tindak lanjut Anda tercatat. Status kasus saat ini ditampilkan di atas.',
                )}
              </p>
            ) : null}
            {state.notice === 'changed' ? (
              <p role="status">
                {t(
                  'The case changed. Review the latest status and previous result before deciding what to send.',
                  'Kasus berubah. Tinjau status terbaru dan hasil sebelumnya sebelum memutuskan apa yang akan dikirim.',
                )}
              </p>
            ) : null}
            {state.unknown ? (
              <div className={styles.notice} role="alert">
                <h2>
                  {t(
                    'Your follow-up result is not confirmed',
                    'Hasil tindak lanjut belum dipastikan',
                  )}
                </h2>
                <p>
                  {t(
                    'The latest case status is shown above. It does not prove that your earlier follow-up was accepted. No request has been resent.',
                    'Status kasus terbaru ditampilkan di atas. Status ini tidak membuktikan tindak lanjut sebelumnya diterima. Permintaan tidak dikirim ulang.',
                  )}
                </p>
                {state.message ? (
                  <p className={styles.report}>{state.message}</p>
                ) : null}
                <div className={styles.actions}>
                  <Button
                    variant="secondary"
                    disabled={busy}
                    onClick={() => run(() => store.recover())}
                  >
                    {t('Check latest case', 'Periksa kasus terbaru')}
                  </Button>
                  {state.canRetry ? (
                    <Button
                      disabled={busy || waiting}
                      onClick={() => run(() => store.retry())}
                    >
                      {t(
                        'Retry the same request',
                        'Coba ulang permintaan yang sama',
                      )}
                    </Button>
                  ) : null}
                  {state.phase === 'unknown' &&
                  !state.canRetry &&
                  c.status === 'closed' ? (
                    <Button
                      variant="secondary"
                      disabled={busy}
                      onClick={() => run(() => store.reviewAgain())}
                    >
                      {t('Review a new follow-up', 'Tinjau tindak lanjut baru')}
                    </Button>
                  ) : null}
                </div>
              </div>
            ) : null}
            {state.phase === 'sending' ? (
              <p role="status">
                {t(
                  'Sending your same-issue follow-up. The result is not confirmed yet.',
                  'Mengirim tindak lanjut masalah yang sama. Hasilnya belum dipastikan.',
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
            {state.editing &&
            state.phase === 'ready' &&
            c.status === 'closed' ? (
              <ContinuationForm
                key={id + ':' + c.version}
                store={store}
                waiting={waiting}
              />
            ) : null}
            {!state.editing &&
            state.phase === 'ready' &&
            c.status === 'closed' &&
            auth.phase === 'ready' &&
            auth.kind === 'customer' ? (
              <section className={styles.section}>
                <h2>{t('Still need help?', 'Masih memerlukan bantuan?')}</h2>
                <p>
                  {t(
                    'Request further handling only if this same issue remains unresolved.',
                    'Minta penanganan lanjutan hanya jika masalah yang sama belum terselesaikan.',
                  )}
                </p>
                <div className={styles.actions}>
                  <Button onClick={() => store.beginContinue()}>
                    {t(
                      'My issue is not resolved',
                      'Masalah saya belum selesai',
                    )}
                  </Button>
                  <Link to={localizedTarget('/help/new?topic=contact', locale)}>
                    {t('This is a different issue', 'Ini masalah yang berbeda')}
                  </Link>
                </div>
              </section>
            ) : null}
            {c.status === 'closed' &&
            (auth.phase !== 'ready' || auth.kind !== 'customer') ? (
              <p>
                {t(
                  'A verified customer account is required to request follow-up.',
                  'Akun pelanggan terverifikasi diperlukan untuk meminta tindak lanjut.',
                )}
              </p>
            ) : null}
            {auth.phase === 'ready' &&
            auth.kind === 'customer' &&
            !state.editing &&
            !state.unknown &&
            state.phase === 'ready' ? (
              <AdditionalInformation detail={c} details={store} />
            ) : null}
            <section className={styles.section}>
              <h2>{t('Your original report', 'Laporan awal Anda')}</h2>
              <h3>{c.summary}</h3>
              <p className={styles.report}>{c.customer_report}</p>
              <dl>
                <div>
                  <dt>{t('Booking reference', 'Referensi booking')}</dt>
                  <dd>
                    {c.booking_reference ?? t('Not provided', 'Belum diisi')}
                  </dd>
                </div>
                <div>
                  <dt>{t('Transaction reference', 'Referensi transaksi')}</dt>
                  <dd>
                    {c.transaction_reference ??
                      t('Not provided', 'Belum diisi')}
                  </dd>
                </div>
              </dl>
            </section>
            <section className={styles.section}>
              <h2>{t('Case history', 'Riwayat kasus')}</h2>
              {c.history.length ? (
                <ol className={styles.history}>
                  {c.history.map((h) => (
                    <li key={h.resulting_version}>
                      <h3>{events[h.event_type]}</h3>
                      <p className={styles.help}>{date(h.created_at)}</p>
                      {h.to_status ? <p>{status(h.to_status)}</p> : null}
                      {h.next_action ? (
                        <p className={styles.report}>{h.next_action}</p>
                      ) : null}
                      {h.close_reason ? <p>{reason(h.close_reason)}</p> : null}
                      {h.resolution ? (
                        <p className={styles.report}>{h.resolution}</p>
                      ) : null}
                      {h.customer_message ? (
                        <p className={styles.report}>{h.customer_message}</p>
                      ) : null}
                    </li>
                  ))}
                </ol>
              ) : (
                <p>
                  {t(
                    'No history entries are available.',
                    'Tidak ada riwayat yang tersedia.',
                  )}
                </p>
              )}
            </section>
            {auth.phase === 'ready' && auth.kind === 'customer' ? (
              <CaseAttachments auth={store.auth} caseId={id} />
            ) : null}
            <Button
              variant="secondary"
              disabled={busy || state.editing || state.unknown}
              onClick={() => run(() => store.open(id))}
            >
              {t('Refresh case', 'Perbarui kasus')}
            </Button>
          </>
        ) : null}
      </section>
    </div>
  );
}
function ContinuationForm({
  store,
  waiting,
}: {
  store: CaseDetailStore;
  waiting: boolean;
}) {
  const state = store.getState(),
    { locale } = useRouting(),
    t = (en: string, id: string) => (locale === 'en' ? en : id),
    summary = useRef<HTMLDivElement>(null);
  const {
    register,
    handleSubmit,
    getValues,
    setFocus,
    formState: { errors },
  } = useForm<{ message: string; sameIssue: boolean }>({
    resolver: zodResolver(continuationFormSchema),
    defaultValues: { message: state.message, sameIssue: false },
    shouldFocusError: false,
  });
  useEffect(() => setFocus('message'), [setFocus]);
  useEffect(() => {
    if (Object.keys(errors).length) summary.current?.focus();
  }, [errors]);
  return (
    <section className={styles.section}>
      <h2>
        {t('What still needs attention?', 'Apa yang masih perlu ditangani?')}
      </h2>
      <p>
        {t(
          'Explain what remains unresolved about this same issue. The previous handling result stays on the case.',
          'Jelaskan bagian dari masalah yang sama yang belum selesai. Hasil penanganan sebelumnya tetap tersimpan.',
        )}
      </p>
      <form
        className={styles.form}
        noValidate
        onChange={() => store.input(getValues('message'))}
        onSubmit={handleSubmit((v) => {
          const d = store.getState().detail;
          if (d)
            void store
              .continue({
                caseId: d.id,
                version: d.version,
                owner: store.auth.capture(),
                message: v.message,
                sameIssue: v.sameIssue,
              })
              .catch(() => {});
        })}
      >
        {Object.keys(errors).length ? (
          <div
            ref={summary}
            tabIndex={-1}
            role="alert"
            className={styles.error}
          >
            <h3>{t('Check your follow-up', 'Periksa tindak lanjut Anda')}</h3>
            <ul>
              {errors.message ? (
                <li>
                  <a
                    href="#continuation-message"
                    onClick={(e) => {
                      e.preventDefault();
                      setFocus('message');
                    }}
                  >
                    {t(
                      'Enter an explanation within 4,000 characters.',
                      'Isi penjelasan hingga 4.000 karakter.',
                    )}
                  </a>
                </li>
              ) : null}
              {errors.sameIssue ? (
                <li>
                  <a
                    href="#continuation-same"
                    onClick={(e) => {
                      e.preventDefault();
                      setFocus('sameIssue');
                    }}
                  >
                    {t(
                      'Confirm that this is the same unresolved issue.',
                      'Pastikan ini masalah yang sama yang belum terselesaikan.',
                    )}
                  </a>
                </li>
              ) : null}
            </ul>
          </div>
        ) : null}
        <label htmlFor="continuation-message">
          {t('Your explanation', 'Penjelasan Anda')}
        </label>
        <textarea
          id="continuation-message"
          autoFocus
          {...register('message')}
          rows={5}
          maxLength={4000}
          aria-invalid={!!errors.message}
          aria-describedby={
            errors.message ? 'continuation-message-error' : 'continuation-help'
          }
        />
        {errors.message ? (
          <p id="continuation-message-error" className={styles.fieldError}>
            {t(
              'Enter 1–4,000 characters, not only spaces.',
              'Isi 1–4.000 karakter, bukan hanya spasi.',
            )}
          </p>
        ) : null}
        <p id="continuation-help" className={styles.help}>
          {t(
            'Do not include passwords, card numbers or verification codes.',
            'Jangan sertakan kata sandi, nomor kartu atau kode verifikasi.',
          )}
        </p>
        <label className={styles.consent} htmlFor="continuation-same">
          <input
            id="continuation-same"
            type="checkbox"
            {...register('sameIssue')}
            aria-invalid={!!errors.sameIssue}
            aria-describedby={
              errors.sameIssue ? 'continuation-same-error' : undefined
            }
          />
          <span>
            {t(
              'This is the same issue and it is still unresolved.',
              'Ini masalah yang sama dan belum terselesaikan.',
            )}
          </span>
        </label>
        {errors.sameIssue ? (
          <p id="continuation-same-error" className={styles.fieldError}>
            {t(
              'Confirm the same-issue request before sending.',
              'Konfirmasi masalah yang sama sebelum mengirim.',
            )}
          </p>
        ) : null}
        <div className={styles.actions}>
          <Button type="submit" disabled={waiting}>
            {t('Request further handling', 'Minta penanganan lanjutan')}
          </Button>
          <Button
            type="button"
            variant="secondary"
            onClick={() => store.cancelEdit()}
          >
            {t('Keep case closed', 'Biarkan kasus ditutup')}
          </Button>
        </div>
      </form>
    </section>
  );
}
