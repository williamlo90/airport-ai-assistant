import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { useCaseDraft } from './context';
import type { BoundarySnapshot } from '../../api/contracts';
import type { CaseDraftStore } from './store';
import { canUseDraft, categories, services, reportSchema } from './contracts';
import type { ReportForm } from './contracts';
import { useRouting } from '../../app/routing/context';
import { localizedTarget } from '../../app/routing/policy';
import { useUnsavedChanges } from '../../app/routing/draftState';
import { useAuthState } from '../../app/auth/context';
import { PageFrame } from '../../app/routing/pages';
import { errorMessage } from '../../api/errors';
import { Button } from '../../components/Button';
import { Dialog } from '../../components/Dialog';
import styles from './cases.module.css';
import type { ReactNode } from 'react';
import { useSubmission } from './submission';
import { SubmissionPage } from './SubmissionPage';
import { DraftAttachments } from './DraftAttachments';

export function CaseDraftPage({ intake = false }: { intake?: boolean }) {
  const store = useCaseDraft();
  const submission = useSubmission();
  const { draftId } = useParams();
  return (
    <PageFrame>
      {store && submission && !intake ? (
        <SubmissionPage
          key={draftId}
          store={submission}
          id={draftId ?? ''}
          review={(locked, footer) => (
            <Screen
              key={draftId}
              store={store}
              intake={false}
              managed
              locked={locked}
              footer={footer}
            />
          )}
        />
      ) : store ? (
        <Screen
          key={intake ? 'intake' : draftId}
          store={store}
          intake={intake}
        />
      ) : (
        <p role="alert">Request review is unavailable.</p>
      )}
    </PageFrame>
  );
}
function Screen({
  store,
  intake,
  managed = false,
  locked = false,
  footer,
}: {
  store: CaseDraftStore;
  intake: boolean;
  managed?: boolean;
  locked?: boolean;
  footer?: ReactNode;
}) {
  const state = useSyncExternalStore(store.subscribe, store.getState),
    auth = useAuthState(store.auth);
  const { locale } = useRouting(),
    { draftId } = useParams(),
    navigate = useNavigate();
  const t = (en: string, id: string) => (locale === 'en' ? en : id);
  const [cancelIntent, setCancelIntent] = useState<{
      draftId: string;
      version: number;
      owner: BoundarySnapshot;
    } | null>(null),
    [field, setField] = useState<keyof ReportForm>('summary');
  const [, tick] = useState(0),
    heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (auth.phase !== 'ready' || managed) return;
    void (
      intake
        ? store.enterIntake()
        : draftId
          ? store.open(draftId)
          : Promise.resolve()
    ).catch(() => {});
  }, [store, intake, draftId, auth.phase, auth.epoch, managed]);
  useEffect(() => {
    const id = setInterval(() => tick((v) => v + 1), 1000);
    return () => clearInterval(id);
  }, []);
  useEffect(() => {
    if (state.phase === 'review' && !state.editing) heading.current?.focus();
  }, [state.phase, state.editing]);
  const draft = state.draft,
    busy = state.phase === 'saving',
    expired = store.expired();
  const linkedCaseId = draft?.case_id ?? state.handoffCaseId;
  const cancelOpen =
    !!cancelIntent &&
    draft?.draft_id === cancelIntent.draftId &&
    draft.version === cancelIntent.version &&
    store.auth.isCurrent(cancelIntent.owner);
  useUnsavedChanges(
    (intake &&
      state.phase === 'intake' &&
      !state.knownDraftId &&
      !!state.form.customer_report) ||
      state.editing,
    () => store.discard(),
  );
  const run = (fn: () => Promise<unknown>) => {
    void fn().catch(() => {});
  };
  const editingAllowed =
    auth.phase === 'ready' &&
    !!draft &&
    canUseDraft(auth.kind, draft.action_type, draft.payload, draft.case_id) &&
    draft?.state === 'pending' &&
    !expired &&
    !state.uncertain &&
    !locked &&
    !busy;
  const edit = (name: keyof ReportForm) => {
    setField(name);
    store.beginEdit();
  };
  const categoryLabels =
    locale === 'en'
      ? ['Airport information', 'Booking issue', 'Payment issue']
      : ['Informasi bandara', 'Kendala booking', 'Kendala pembayaran'];
  const serviceLabels =
    locale === 'en'
      ? ['General airport services', 'Parking', 'Lounge']
      : ['Layanan umum bandara', 'Parkir', 'Lounge'];
  return (
    <div className={styles.layout}>
      <aside className={styles.journey}>
        <h2>{t('Your support request', 'Permintaan bantuan Anda')}</h2>
        <ol>
          <li>{t('Your situation', 'Kendala Anda')}</li>
          <li>
            {state.actionType === 'request_handoff' &&
            auth.phase === 'ready' &&
            auth.kind === 'anonymous'
              ? t(
                  'General assistance in this browser',
                  'Bantuan umum di browser ini',
                )
              : t('Verify email', 'Verifikasi email')}
          </li>
          <li aria-current="step">
            {t('Review your request', 'Tinjau permintaan')}
          </li>
        </ol>
        <p>
          {t(
            'Check the details and where your request will go.',
            'Periksa rincian dan tujuan permintaan Anda.',
          )}
        </p>
        {state.actionType === 'request_handoff' && (
          <p role="status">
            {linkedCaseId
              ? t(
                  'Confirm to request staff assistance on your existing case. Its contact and follow-up settings stay unchanged.',
                  'Konfirmasi untuk meminta bantuan petugas pada kasus yang sama. Pengaturan kontak dan tindak lanjutnya tetap.',
                )
              : t(
                  'You are reviewing a request for staff assistance. Staff are requested only after you confirm. Without a follow-up email, continue in this browser.',
                  'Anda meninjau permintaan bantuan petugas. Permintaan baru dikirim setelah Anda mengonfirmasi. Tanpa email tindak lanjut, lanjutkan di browser ini.',
                )}
          </p>
        )}
        {linkedCaseId && (
          <p>
            {t('Linked support case: ', 'Kasus bantuan terkait: ')}
            <Link to={localizedTarget('/help/cases/' + linkedCaseId, locale)}>
              {'CASE-' + linkedCaseId.replaceAll('-', '')}
            </Link>
          </p>
        )}
        <Link to={localizedTarget('/', locale)}>
          {t('Back to help centre', 'Kembali ke pusat bantuan')}
        </Link>
      </aside>
      <section className={styles.card} aria-labelledby="request-title">
        <p className={styles.eyebrow}>
          {t(
            'Support, with the context that matters',
            'Bantuan dengan konteks yang jelas',
          )}
        </p>
        <h1 ref={heading} id="request-title" tabIndex={-1}>
          {t('Review your support request', 'Tinjau permintaan bantuan')}
        </h1>
        <p className={styles.intro}>
          {t(
            'Make sure the details are right. Preparing or editing a preview does not send your request to staff.',
            'Pastikan rinciannya benar. Menyiapkan atau mengedit preview belum mengirim permintaan kepada petugas.',
          )}
        </p>
        {state.error && !state.editing && (
          <p className={styles.error} role="alert">
            {errorMessage(state.error, locale)}
          </p>
        )}
        {state.notice === 'changed' && (
          <p className={styles.notice} role="status">
            {t(
              'The latest preview is shown below. Review the changes before saving again.',
              'Preview terbaru ditampilkan di bawah. Tinjau perubahan sebelum menyimpan lagi.',
            )}
          </p>
        )}
        {state.notice === 'saved' && draft?.state === 'pending' && (
          <p role="status">
            {t(
              'Changes saved. Review the updated details.',
              'Perubahan tersimpan. Tinjau rincian terbaru.',
            )}
          </p>
        )}
        {busy && (
          <p role="status">
            {t('Saving your preview…', 'Menyimpan preview Anda…')}
          </p>
        )}
        {state.uncertain && !busy && (
          <div className={styles.notice} role="alert">
            <p>
              {state.uncertain.kind === 'create'
                ? t(
                    'The preview may have been created, but its reference was not received. Do not create another preview for this attempt.',
                    'Preview mungkin sudah dibuat, tetapi referensinya belum diterima. Jangan membuat preview lain untuk upaya ini.',
                  )
                : t(
                    'The result of your last change is not confirmed. Checking the latest preview will not repeat the change.',
                    'Hasil perubahan terakhir belum dipastikan. Memeriksa preview terbaru tidak mengulang perubahan.',
                  )}
            </p>
            {draft && (
              <Button
                variant="secondary"
                onClick={() => run(() => store.open(draft.draft_id))}
              >
                {t('Check latest preview', 'Periksa preview terbaru')}
              </Button>
            )}
          </div>
        )}
        {state.phase === 'loading' && (
          <p role="status">
            {t('Loading your preview…', 'Memuat preview Anda…')}
          </p>
        )}
        {state.phase === 'unavailable' && (
          <Button
            variant="secondary"
            onClick={() => run(() => store.auth.recover())}
          >
            {t('Check session', 'Periksa sesi')}
          </Button>
        )}
        {intake && state.phase === 'idle' && (
          <p>
            {t(
              'Return to your conversation to prepare a request with its original context.',
              'Kembali ke percakapan untuk menyiapkan permintaan beserta konteksnya.',
            )}
          </p>
        )}
        {intake && state.knownDraftId && (
          <Link
            to={localizedTarget(
              '/help/submissions/' + state.knownDraftId,
              locale,
            )}
          >
            {t('Open your existing preview', 'Buka preview yang sudah ada')}
          </Link>
        )}
        {intake &&
          (state.phase === 'intake' || busy) &&
          !state.knownDraftId &&
          !state.uncertain && (
            <ReportEditor
              store={store}
              locale={locale}
              busy={busy}
              initial={state.form}
              submitLabel={t('Prepare review', 'Siapkan tinjauan')}
              onSubmit={() =>
                run(async () => {
                  const id = await store.create();
                  if (id)
                    await navigate(
                      localizedTarget('/help/submissions/' + id, locale),
                    );
                })
              }
            />
          )}
        {draft && (
          <>
            <div className={styles.status}>
              <strong>
                {draft.state === 'committed'
                  ? t('Result recorded', 'Hasil tercatat')
                  : locked
                    ? t('Result not confirmed', 'Hasil belum dipastikan')
                    : t('Request not sent', 'Permintaan belum dikirim')}
              </strong>
              {draft.state === 'pending' && !expired && (
                <span>
                  {t('Preview valid until', 'Preview berlaku sampai')}{' '}
                  {new Intl.DateTimeFormat(
                    locale === 'en' ? 'en-GB' : 'id-ID',
                    {
                      dateStyle: 'medium',
                      timeStyle: 'short',
                      timeZone: 'UTC',
                    },
                  ).format(new Date(draft.expires_at))}{' '}
                  UTC
                </span>
              )}
            </div>
            {(expired || draft.state === 'cancelled') && (
              <div className={styles.notice} role="status">
                <p>
                  {draft.state === 'cancelled'
                    ? t(
                        'This preview was cancelled.',
                        'Preview ini sudah dibatalkan.',
                      )
                    : t(
                        'This preview has expired. Its original deadline cannot be extended.',
                        'Preview ini sudah kedaluwarsa. Batas waktu aslinya tidak dapat diperpanjang.',
                      )}
                </p>
                {!state.uncertain && (
                  <Button
                    variant="secondary"
                    onClick={() =>
                      run(async () => {
                        if (await store.renew())
                          await navigate(
                            localizedTarget('/help/request', locale),
                          );
                      })
                    }
                  >
                    {t('Prepare a new review', 'Siapkan tinjauan baru')}
                  </Button>
                )}
              </div>
            )}
            {draft.state === 'committed' && (
              <p role="status">
                {t(
                  'This preview already has a recorded result.',
                  'Preview ini sudah memiliki hasil yang tercatat.',
                )}{' '}
                {draft.result_ref}
              </p>
            )}
            <section className={styles.section}>
              <div className={styles.sectionHeading}>
                <h2>{t('Your report', 'Laporan Anda')}</h2>
                <Button
                  variant="quiet"
                  disabled={!editingAllowed}
                  onClick={() => edit('summary')}
                >
                  {t('Edit report', 'Edit laporan')}
                </Button>
              </div>
              <h3>{draft.payload.summary}</h3>
              <p className={styles.report}>{draft.payload.customer_report}</p>
              <dl>
                <div>
                  <dt>{t('Topic', 'Topik')}</dt>
                  <dd>
                    {categoryLabels[categories.indexOf(draft.payload.category)]}
                  </dd>
                </div>
                <div>
                  <dt>{t('Service', 'Layanan')}</dt>
                  <dd>
                    {
                      serviceLabels[
                        services.indexOf(draft.payload.service_type)
                      ]
                    }
                  </dd>
                </div>
              </dl>
            </section>
            <section className={styles.section}>
              <div className={styles.sectionHeading}>
                <h2>{t('Related references', 'Referensi terkait')}</h2>
                <Button
                  variant="quiet"
                  disabled={!editingAllowed}
                  onClick={() => edit('booking_reference')}
                >
                  {t('Edit references', 'Edit referensi')}
                </Button>
              </div>
              <dl>
                <div>
                  <dt>{t('Booking reference', 'Referensi booking')}</dt>
                  <dd>
                    {draft.payload.booking_reference ??
                      t('Not provided', 'Belum diisi')}
                  </dd>
                </div>
                <div>
                  <dt>{t('Transaction reference', 'Referensi transaksi')}</dt>
                  <dd>
                    {draft.payload.transaction_reference ??
                      t('Not provided', 'Belum diisi')}
                  </dd>
                </div>
              </dl>
            </section>
            <section className={styles.section}>
              <div className={styles.sectionHeading}>
                <h2>{t('Follow-up', 'Tindak lanjut')}</h2>
                {!draft.case_id && (
                  <Button
                    variant="quiet"
                    disabled={!editingAllowed}
                    onClick={() => edit('contact_email')}
                  >
                    {t('Edit email', 'Edit email')}
                  </Button>
                )}
              </div>
              <p>
                {draft.case_id
                  ? t(
                      'Follow-up uses the contact settings already recorded on your existing case.',
                      'Tindak lanjut memakai pengaturan kontak yang sudah tercatat pada kasus Anda.',
                    )
                  : (draft.payload.contact_email ??
                    t(
                      'No follow-up address',
                      'Alamat tindak lanjut belum diisi',
                    ))}
              </p>
              <dl>
                <div>
                  <dt>{t('Handled by', 'Ditangani oleh')}</dt>
                  <dd>
                    {draft.destination.organization_name}
                    <br />
                    {draft.destination.queue_name}
                  </dd>
                </div>
                <div>
                  <dt>{t('Request language', 'Bahasa permintaan')}</dt>
                  <dd>
                    {draft.language === 'en' ? 'English' : 'Bahasa Indonesia'}
                  </dd>
                </div>
              </dl>
            </section>
            <details className={styles.section}>
              <summary>{t('Full preview', 'Preview lengkap')}</summary>
              <pre className={styles.report}>{draft.summary}</pre>
            </details>
            {auth.phase === 'ready' &&
            auth.kind === 'customer' &&
            draft.state === 'pending' &&
            !draft.case_id ? (
              <DraftAttachments
                key={draft.draft_id}
                store={store}
                draft={draft}
                disabled={!editingAllowed}
              />
            ) : null}
            {footer}
            {draft.state === 'pending' && (
              <div className={styles.actions}>
                <Button
                  variant="secondary"
                  disabled={!editingAllowed}
                  onClick={() => {
                    if (draft)
                      setCancelIntent({
                        draftId: draft.draft_id,
                        version: draft.version,
                        owner: store.auth.capture(),
                      });
                  }}
                >
                  {t('Cancel draft', 'Batalkan draf')}
                </Button>
              </div>
            )}
          </>
        )}
        {state.editing && draft && (
          <Dialog
            open
            title={t('Edit your request', 'Edit permintaan Anda')}
            closeLabel={t('Close editor', 'Tutup editor')}
            onClose={() => store.stopEdit()}
          >
            {expired && (
              <p role="alert" className={styles.error}>
                {t(
                  'This preview has expired. Close the editor and prepare a new review.',
                  'Pratinjau ini kedaluwarsa. Tutup editor dan siapkan tinjauan baru.',
                )}
              </p>
            )}
            {state.error && (
              <p role="alert" className={styles.error}>
                {errorMessage(state.error, locale)}
              </p>
            )}
            <ReportEditor
              key={draft.draft_id + ':' + draft.version}
              store={store}
              initial={state.form}
              locale={locale}
              busy={busy || expired || !!state.uncertain}
              focus={field}
              submitLabel={t('Save changes', 'Simpan perubahan')}
              onSubmit={() => run(() => store.update())}
            />
          </Dialog>
        )}
        <Dialog
          open={cancelOpen}
          title={t('Cancel this draft?', 'Batalkan draf ini?')}
          description={t(
            'Your request has not been sent. Cancelling only ends this preview.',
            'Permintaan Anda belum dikirim. Pembatalan hanya mengakhiri preview ini.',
          )}
          closeLabel={t(
            'Close cancellation dialog',
            'Tutup konfirmasi pembatalan',
          )}
          onClose={() => setCancelIntent(null)}
          actions={
            <>
              <Button variant="secondary" onClick={() => setCancelIntent(null)}>
                {t('Keep reviewing', 'Lanjutkan tinjauan')}
              </Button>
              <Button
                disabled={busy}
                onClick={() => {
                  const intent = cancelIntent;
                  setCancelIntent(null);
                  if (intent) run(() => store.cancel(intent));
                }}
              >
                {t('Yes, cancel draft', 'Ya, batalkan draf')}
              </Button>
            </>
          }
        />
      </section>
    </div>
  );
}
function ReportEditor({
  store,
  initial,
  locale,
  busy,
  submitLabel,
  onSubmit,
  focus,
}: {
  store: CaseDraftStore;
  initial: ReportForm;
  locale: 'en' | 'id';
  busy: boolean;
  submitLabel: string;
  onSubmit: () => void;
  focus?: keyof ReportForm;
}) {
  const session = store.auth.getState();
  const anonymous = session.phase === 'ready' && session.kind === 'anonymous';
  const handoff = store.getState().actionType === 'request_handoff';
  const linked = !!store.getState().handoffCaseId;
  const {
    register,
    handleSubmit,
    getValues,
    setFocus,
    formState: { errors, submitCount },
  } = useForm<ReportForm>({
    defaultValues: initial,
    resolver: zodResolver(reportSchema(store.getState().actionType, anonymous)),
    shouldFocusError: false,
  });
  const summary = useRef<HTMLDivElement>(null),
    t = (en: string, id: string) => (locale === 'en' ? en : id);
  useEffect(() => {
    if (focus) setFocus(focus);
  }, [focus, setFocus]);
  useEffect(() => {
    if (submitCount && Object.keys(errors).length) summary.current?.focus();
  }, [errors, submitCount]);
  const labels: Record<keyof ReportForm, string> = {
    category: t('What do you need help with?', 'Anda membutuhkan bantuan apa?'),
    service_type: t('Service', 'Layanan'),
    summary: t('Short summary', 'Ringkasan singkat'),
    customer_report: t('Your report', 'Laporan Anda'),
    booking_reference: t(
      'Booking reference (optional)',
      'Referensi booking (opsional)',
    ),
    transaction_reference: t(
      'Transaction reference (optional)',
      'Referensi transaksi (opsional)',
    ),
    contact_email: handoff
      ? t(
          'Email for follow-up (optional, verified address)',
          'Email tindak lanjut (opsional, alamat terverifikasi)',
        )
      : t('Email for follow-up', 'Email untuk tindak lanjut'),
  };
  const error = (name: keyof ReportForm) =>
    errors[name] ? (
      <p className={styles.fieldError} id={'request-' + name + '-error'}>
        {t(
          'Check this field and its allowed format.',
          'Periksa isian dan format yang diizinkan.',
        )}
      </p>
    ) : null;
  const props = (name: keyof ReportForm) => ({
    id: 'request-' + name,
    'aria-invalid': !!errors[name],
    'aria-describedby': errors[name] ? 'request-' + name + '-error' : undefined,
    ...register(name),
  });
  return (
    <form
      className={styles.form}
      noValidate
      onChange={() => store.input(getValues())}
      onSubmit={handleSubmit(() => {
        store.input(getValues());
        onSubmit();
      })}
    >
      {!!Object.keys(errors).length && (
        <div ref={summary} tabIndex={-1} role="alert" className={styles.error}>
          <h3>{t('Check your request', 'Periksa permintaan Anda')}</h3>
          <ul>
            {(Object.keys(errors) as (keyof ReportForm)[]).map((name) => (
              <li key={name}>
                <a
                  href={'#request-' + name}
                  onClick={(e) => {
                    e.preventDefault();
                    setFocus(name);
                  }}
                >
                  {labels[name]}
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
      <fieldset disabled={busy}>
        <div className={styles.twoColumns}>
          <div>
            <label htmlFor="request-category">{labels.category}</label>
            <select {...props('category')} disabled={anonymous}>
              {categories.map((v, i) => (
                <option key={v} value={v}>
                  {
                    (locale === 'en'
                      ? [
                          'Airport information',
                          'Booking issue',
                          'Payment issue',
                        ]
                      : [
                          'Informasi bandara',
                          'Kendala booking',
                          'Kendala pembayaran',
                        ])[i]
                  }
                </option>
              ))}
            </select>
            {error('category')}
          </div>
          <div>
            <label htmlFor="request-service_type">{labels.service_type}</label>
            <select {...props('service_type')} disabled={anonymous}>
              {services.map((v, i) => (
                <option key={v} value={v}>
                  {
                    (locale === 'en'
                      ? ['General airport services', 'Parking', 'Lounge']
                      : ['Layanan umum bandara', 'Parkir', 'Lounge'])[i]
                  }
                </option>
              ))}
            </select>
            {error('service_type')}
          </div>
        </div>
        <label htmlFor="request-summary">{labels.summary}</label>
        <input {...props('summary')} maxLength={200} />
        {error('summary')}
        <label htmlFor="request-customer_report">
          {labels.customer_report}
        </label>
        <textarea {...props('customer_report')} rows={6} maxLength={4000} />
        {error('customer_report')}
        <p className={styles.help}>
          {t(
            'Keep your report within 4,000 characters. Do not include passwords, card numbers or verification codes.',
            'Batasi laporan hingga 4.000 karakter. Jangan isi kata sandi, nomor kartu, atau kode verifikasi.',
          )}
        </p>
        <div className={styles.twoColumns}>
          {(['booking_reference', 'transaction_reference'] as const).map(
            (name) => (
              <div key={name}>
                <label htmlFor={'request-' + name}>{labels[name]}</label>
                <input {...props(name)} maxLength={80} readOnly={anonymous} />
                {error(name)}
              </div>
            ),
          )}
        </div>
        <p className={styles.help}>
          {t(
            'References use letters, numbers, underscores or hyphens.',
            'Referensi memakai huruf, angka, garis bawah, atau tanda hubung.',
          )}
        </p>
        {!linked && (
          <>
            <label htmlFor="request-contact_email">
              {labels.contact_email}
            </label>
            <input
              {...props('contact_email')}
              type="email"
              autoComplete="email"
              maxLength={320}
              readOnly={anonymous}
            />
            {error('contact_email')}
          </>
        )}
        {linked && (
          <p>
            {t(
              'Follow-up uses the contact already recorded on this case.',
              'Tindak lanjut memakai kontak yang sudah tercatat pada kasus ini.',
            )}
          </p>
        )}
        {handoff && !linked && (
          <p className={styles.help}>
            {anonymous
              ? t(
                  'General assistance here does not require email. Verify your identity before adding contact details or booking/payment references.',
                  'Bantuan umum di sini tidak memerlukan email. Verifikasi identitas sebelum menambah kontak atau referensi booking/pembayaran.',
                )
              : t(
                  'Leave blank to continue in this browser. If you provide an email, it must already be verified for your account.',
                  'Kosongkan untuk melanjutkan di browser ini. Jika mengisi email, alamat harus sudah terverifikasi untuk akun Anda.',
                )}
          </p>
        )}
        <Button type="submit" disabled={busy}>
          {busy ? t('Please wait…', 'Mohon tunggu…') : submitLabel}
        </Button>
      </fieldset>
    </form>
  );
}
