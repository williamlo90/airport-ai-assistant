import {
  useEffect,
  useLayoutEffect,
  useRef,
  useSyncExternalStore,
} from 'react';
import { Link, useNavigate, useLocation } from 'react-router';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { useVerification } from './context';
import { contactSchema, codeSchema } from './store';
import type { VerificationStore } from './store';
import { useRouting } from '../../app/routing/context';
import { localizedTarget, readHandoffCase } from '../../app/routing/policy';
import { useUnsavedChanges } from '../../app/routing/draftState';
import { PageFrame } from '../../app/routing/pages';
import { Button } from '../../components/Button';
import { errorMessage } from '../../api/errors';
import styles from './verification.module.css';
export function VerificationPage() {
  const store = useVerification();
  return (
    <PageFrame>
      {store ? (
        <Screen store={store} />
      ) : (
        <p role="alert">Verification unavailable.</p>
      )}
    </PageFrame>
  );
}
function Screen({ store }: { store: VerificationStore }) {
  const selectedCase = readHandoffCase(useLocation().search);
  const state = useSyncExternalStore(store.subscribe, store.getState),
    { locale } = useRouting(),
    navigate = useNavigate();
  const t = (en: string, id: string) => (locale === 'en' ? en : id);
  useLayoutEffect(() => {
    try {
      store.prepareFromChat();
    } catch {
      /* auth recovery closes the view */
    }
  }, [store]);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (state.phase === 'review') heading.current?.focus();
  }, [state.phase]);
  const session = store.auth.getState();
  const customer = session.phase === 'ready' && session.kind === 'customer';
  const busy =
    state.phase === 'requesting' ||
    state.phase === 'verifying' ||
    state.phase === 'continuing';
  useUnsavedChanges(!!state.contact || !!state.report?.body, () =>
    store.discard(),
  );
  const run = (fn: () => Promise<unknown>) => {
    void fn().catch(() => {});
  };
  return (
    <div className={styles.layout}>
      {selectedCase === undefined && (
        <p role="alert">
          {t(
            'The selected case link is invalid. Open the case again to continue.',
            'Tautan kasus yang dipilih tidak valid. Buka kasus kembali untuk melanjutkan.',
          )}
        </p>
      )}
      <aside className={styles.journey}>
        <h2>{t('Your support request', 'Permintaan bantuan Anda')}</h2>
        <ol>
          <li>{t('Your situation', 'Kendala Anda')}</li>
          <li aria-current={state.phase === 'review' ? 'false' : 'step'}>
            {t('Verify email', 'Verifikasi email')}
          </li>
          <li aria-current={state.phase === 'review' ? 'step' : 'false'}>
            {t('Review your report', 'Tinjau laporan Anda')}
          </li>
        </ol>
        <p>
          {t(
            'Your original story stays with this support flow.',
            'Cerita asli Anda tetap dibawa dalam alur bantuan ini.',
          )}
        </p>
        <Link to={localizedTarget('/', locale)}>
          {t('Back to help centre', 'Kembali ke pusat bantuan')}
        </Link>
      </aside>
      <section className={styles.card} aria-labelledby="verification-title">
        <p className={styles.eyebrow}>
          {t(
            'Support, with the context that matters',
            'Bantuan dengan konteks yang jelas',
          )}
        </p>
        <h1 id="verification-title" ref={heading} tabIndex={-1}>
          {state.phase === 'review'
            ? t('Review your report', 'Tinjau laporan Anda')
            : t('Verify your email', 'Verifikasi email Anda')}
        </h1>
        <p className={styles.intro}>
          {t(
            'Verify access to your information and prepare an address for support follow-up. Nothing is sent to staff at this step.',
            'Verifikasi akses ke informasi Anda dan siapkan alamat untuk tindak lanjut petugas. Langkah ini tidak mengirim permintaan kepada petugas.',
          )}
        </p>
        {state.error && (
          <div className={styles.notice} role="alert">
            <p>
              {state.error.code === 'VERIFICATION_FAILED'
                ? t(
                    'That code could not be verified. Check it or request a new one.',
                    'Kode belum dapat diverifikasi. Periksa kembali atau minta kode baru.',
                  )
                : state.error.code === 'VERIFICATION_UNAVAILABLE'
                  ? t(
                      'Email verification is currently unavailable. No code delivery has been confirmed.',
                      'Verifikasi email sedang tidak tersedia. Pengiriman kode belum dikonfirmasi.',
                    )
                  : errorMessage(state.error, locale)}
            </p>
            {state.error.status === 429 && state.blockedUntil !== null && (
              <p>
                {t(
                  'The server asked you to wait before another attempt. The buttons will become available when that wait ends.',
                  'Server meminta Anda menunggu sebelum mencoba lagi. Tombol akan tersedia setelah waktu tunggu berakhir.',
                )}
              </p>
            )}
          </div>
        )}
        {state.phase === 'guest' && (
          <div className={styles.section}>
            <p>
              {t(
                'Start a secure anonymous session before requesting a code.',
                'Mulai sesi anonim yang aman sebelum meminta kode.',
              )}
            </p>
            <Button
              disabled={store.blocked()}
              onClick={() => run(() => store.start())}
            >
              {t('Start secure session', 'Mulai sesi aman')}
            </Button>
          </div>
        )}
        {state.phase === 'staff' && (
          <p role="alert">
            {t(
              'This is a staff session. Customer email verification uses a separate customer flow.',
              'Ini sesi petugas. Verifikasi email pelanggan memakai alur pelanggan tersendiri.',
            )}
          </p>
        )}
        {(state.phase === 'checking' || state.phase === 'verifying') && (
          <p role="status">
            {t(
              'Checking your session and verification result…',
              'Memeriksa sesi dan hasil verifikasi…',
            )}
          </p>
        )}
        {state.phase === 'continue-unknown' && (
          <div className={styles.section}>
            <p className={styles.badge}>
              {state.contactVerified
                ? t(
                    'Email verified for this session',
                    'Email terverifikasi untuk sesi ini',
                  )
                : t(
                    'Existing customer session — provided contact',
                    'Sesi pelanggan aktif — kontak yang Anda isi',
                  )}
            </p>
            <p className={styles.contact}>{state.contact}</p>
            <p role="alert">
              {t(
                'Creating the new conversation has not been confirmed. A conversation may already exist. Do not create a replacement or send this report again.',
                'Pembuatan percakapan baru belum dipastikan. Percakapan mungkin sudah ada. Jangan membuat pengganti atau mengirim laporan ini lagi.',
              )}
            </p>
            <p>
              {t(
                'Your report remains in this browser. No message or staff request has been sent by this action.',
                'Laporan Anda tetap berada di browser ini. Tindakan ini tidak mengirim pesan atau permintaan kepada petugas.',
              )}
            </p>
            <label htmlFor="held-report">
              {t('Your report', 'Laporan Anda')}
            </label>
            <textarea
              id="held-report"
              rows={6}
              value={state.report?.body ?? ''}
              readOnly
            />
          </div>
        )}
        {(state.phase === 'unknown' || state.phase === 'unavailable') && (
          <div className={styles.section}>
            <p role="alert">
              {t(
                'Verification has not been confirmed. Your previous code and report were not carried into another account. Check your session before continuing.',
                'Verifikasi belum dikonfirmasi. Kode dan laporan sebelumnya tidak dibawa ke akun lain. Periksa sesi sebelum melanjutkan.',
              )}
            </p>
            <Button
              variant="secondary"
              onClick={() => run(() => store.auth.recover())}
            >
              {t('Check session', 'Periksa sesi')}
            </Button>
          </div>
        )}
        {(state.phase === 'email' || state.phase === 'requesting') && (
          <>
            <h2>
              {customer
                ? t(
                    'Use your existing verified contact',
                    'Gunakan kontak Anda yang sudah terverifikasi',
                  )
                : t('What is your email address?', 'Apa alamat email Anda?')}
            </h2>
            <p>
              {customer
                ? t(
                    'You already have a customer session. Enter an address already verified for this account. This form does not verify a new address; the service checks your contact when a request is confirmed.',
                    'Sesi pelanggan Anda masih aktif. Isi alamat yang sudah terverifikasi untuk akun ini. Form ini tidak memverifikasi alamat baru; layanan memeriksa kontak saat permintaan dikonfirmasi.',
                  )
                : t(
                    'Use an address you can access. Enter the code in the same browser where you requested it.',
                    'Gunakan alamat yang bisa Anda akses. Masukkan kode pada browser yang sama saat meminta kode.',
                  )}
            </p>
            <ContactForm
              key="contact"
              store={store}
              initial={state.contact}
              locale={locale}
              customer={customer}
              busy={busy || store.blocked()}
            />
          </>
        )}
        {state.phase === 'code' && (
          <>
            <h2>
              {t('Enter your verification code', 'Masukkan kode verifikasi')}
            </h2>
            <p>
              {t(
                'A verification code was requested for',
                'Kode verifikasi diminta untuk',
              )}{' '}
              <strong className={styles.contact}>{state.contact}</strong>.{' '}
              {t(
                'Use the 8-digit code in this browser.',
                'Gunakan kode 8 digit pada browser ini.',
              )}
            </p>
            <CodeForm
              store={store}
              locale={locale}
              busy={busy || store.blocked()}
            />
            <div className={styles.actions}>
              <Button
                variant="quiet"
                disabled={busy || store.blocked()}
                onClick={() => run(() => store.request(state.contact))}
              >
                {t('Request a new code', 'Minta kode baru')}
              </Button>
              <Button
                variant="quiet"
                disabled={busy}
                onClick={() => store.editContact()}
              >
                {t('Change email', 'Ubah email')}
              </Button>
            </div>
          </>
        )}
        {(state.phase === 'review' || state.phase === 'continuing') && (
          <div className={styles.section}>
            <p className={styles.badge}>
              {state.contactVerified
                ? t(
                    'Email verified for this session',
                    'Email terverifikasi untuk sesi ini',
                  )
                : t(
                    'Existing customer session — provided contact',
                    'Sesi pelanggan aktif — kontak yang Anda isi',
                  )}
            </p>
            <p className={styles.contact}>{state.contact}</p>
            <p>
              {t(
                'Review the text brought from your original support flow. Earlier commands and approvals were discarded. Continuing opens a new conversation with this text ready to review; it does not send a message or submit a staff request.',
                'Tinjau teks dari alur bantuan semula. Perintah dan persetujuan sebelumnya sudah dibuang. Melanjutkan membuka percakapan baru dengan teks siap ditinjau; tindakan ini tidak mengirim pesan atau permintaan petugas.',
              )}
            </p>
            <label htmlFor="retained-report">
              {t('Your report', 'Laporan Anda')}
            </label>
            <textarea
              id="retained-report"
              rows={7}
              value={state.report?.body ?? ''}
              readOnly={busy || !!state.continuedId}
              onChange={(e) => store.review(e.target.value)}
            />
            <p className={styles.hint}>
              {t(
                'Keep the reviewed report within 3,800 characters. Longer text is kept for you to edit, not shortened automatically.',
                'Batasi laporan yang ditinjau hingga 3.800 karakter. Teks yang lebih panjang tetap disimpan untuk Anda edit, bukan dipotong otomatis.',
              )}
            </p>
            <Button
              disabled={
                selectedCase === undefined ||
                busy ||
                !state.report?.body.trim() ||
                (state.report?.body.length ?? 0) > 3800 ||
                !!state.report?.body.includes('\0')
              }
              onClick={() =>
                run(async () => {
                  const id = await store.continue(locale);
                  if (id)
                    await navigate(
                      localizedTarget(
                        '/help/conversations/' +
                          id +
                          '?topic=contact' +
                          (selectedCase ? '&case=' + selectedCase : ''),
                        locale,
                      ),
                    );
                })
              }
            >
              {busy
                ? t('Preparing…', 'Menyiapkan…')
                : state.continuedId
                  ? t('Return to conversation', 'Kembali ke percakapan')
                  : t(
                      'Continue with reviewed report',
                      'Lanjutkan laporan yang sudah ditinjau',
                    )}
            </Button>
          </div>
        )}
      </section>
    </div>
  );
}
function ContactForm({
  store,
  initial,
  locale,
  customer,
  busy,
}: {
  store: VerificationStore;
  initial: string;
  locale: 'en' | 'id';
  customer: boolean;
  busy: boolean;
}) {
  const {
    register,
    handleSubmit,
    getValues,
    formState: { errors },
  } = useForm<{ contact: string }>({
    defaultValues: { contact: initial },
    resolver: zodResolver(contactSchema),
  });
  const t = (en: string, id: string) => (locale === 'en' ? en : id);
  return (
    <form
      className={styles.form}
      noValidate
      onSubmit={handleSubmit(async ({ contact }) => {
        try {
          if (customer) store.useExisting(contact);
          else await store.request(contact);
        } catch {
          /* boundary reset supplies recovery UI */
        }
      })}
    >
      <label htmlFor="verification-contact">
        {t('Email address', 'Alamat email')}
      </label>
      <input
        id="verification-contact"
        type="email"
        autoComplete="email"
        maxLength={320}
        disabled={busy}
        aria-invalid={!!errors.contact}
        aria-describedby="contact-error"
        {...register('contact', {
          onChange: () => store.contactDraft(getValues('contact')),
        })}
      />
      <p id="contact-error" role={errors.contact ? 'alert' : undefined}>
        {errors.contact
          ? t('Enter a valid email address.', 'Isi alamat email yang valid.')
          : ''}
      </p>
      <Button type="submit" disabled={busy}>
        {busy
          ? t('Please wait…', 'Mohon tunggu…')
          : customer
            ? t('Review my report', 'Tinjau laporan saya')
            : t('Request a code', 'Minta kode')}
      </Button>
    </form>
  );
}
function CodeForm({
  store,
  locale,
  busy,
}: {
  store: VerificationStore;
  locale: 'en' | 'id';
  busy: boolean;
}) {
  const {
    register,
    handleSubmit,
    reset,
    setFocus,
    formState: { errors },
  } = useForm<{ code: string }>({
    defaultValues: { code: '' },
    resolver: zodResolver(codeSchema),
  });
  useEffect(() => {
    setFocus('code');
  }, [setFocus]);
  const t = (en: string, id: string) => (locale === 'en' ? en : id);
  return (
    <form
      className={styles.form}
      noValidate
      onSubmit={handleSubmit(async ({ code }) => {
        reset({ code: '' });
        try {
          await store.verify(code);
        } catch {
          /* boundary reset supplies recovery UI */
        }
      })}
    >
      <label htmlFor="verification-code">
        {t('8-digit code', 'Kode 8 digit')}
      </label>
      <input
        id="verification-code"
        inputMode="numeric"
        autoComplete="one-time-code"
        maxLength={8}
        disabled={busy}
        aria-invalid={!!errors.code}
        aria-describedby="code-error"
        {...register('code')}
      />
      <p id="code-error" role={errors.code ? 'alert' : undefined}>
        {errors.code
          ? t('Enter exactly 8 digits.', 'Isi tepat 8 digit angka.')
          : ''}
      </p>
      <Button type="submit" disabled={busy}>
        {t('Verify and continue', 'Verifikasi dan lanjutkan')}
      </Button>
    </form>
  );
}
