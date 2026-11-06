import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { Link } from 'react-router';
import { useForm, useWatch } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { useStaff } from './context';
import type { StaffStore } from './store';
import { canHandleCases } from './store';
import { SignOut } from './StaffNavigation';
import { useAuthState } from '../../app/auth/context';
import { useRouting } from '../../app/routing/context';
import { localizedTarget } from '../../app/routing/policy';
import { useUnsavedChanges } from '../../app/routing/draftState';
import { PageFrame } from '../../app/routing/pages';
import { Button } from '../../components/Button';
import styles from './staff.module.css';
const loginSchema = z.object({
  contact: z.string().trim().email().max(320),
  password: z.string().min(1).max(128),
});
function useClock(deadline: number) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (deadline <= Date.now()) return;
    const timer = setInterval(() => {
      const current = Date.now();
      setNow(current);
      if (current >= deadline) clearInterval(timer);
    }, 1000);
    return () => clearInterval(timer);
  }, [deadline]);
  return now;
}
export function StaffLogin() {
  const store = useStaff();
  return store ? (
    <LoginScreen store={store} />
  ) : (
    <PageFrame>
      <p role="alert">Staff sign-in is unavailable.</p>
    </PageFrame>
  );
}
function LoginScreen({ store }: { store: StaffStore }) {
  const state = useSyncExternalStore(store.subscribe, store.getState),
    auth = useAuthState(store.auth),
    { locale } = useRouting(),
    t = (en: string, id: string) => (locale === 'en' ? en : id),
    now = useClock(state.retryAt);
  const waiting = state.retryAt > now,
    readyStaff = auth.phase === 'ready' && auth.kind === 'staff' && state.ready;
  useUnsavedChanges(state.busy, () => {});
  return (
    <PageFrame>
      <section className={styles.login} aria-labelledby="staff-login-title">
        <h1 id="staff-login-title">{t('Staff sign-in', 'Masuk petugas')}</h1>
        <p>
          {t(
            'Use your provisioned staff account to open the support workspace.',
            'Gunakan akun petugas yang telah disediakan untuk membuka ruang kerja bantuan.',
          )}
        </p>
        {state.busy ||
        auth.phase === 'checking' ||
        auth.phase === 'transition' ? (
          <p role="status">
            {t(
              'Checking the session. No sign-in request will be repeated automatically.',
              'Memeriksa sesi. Permintaan masuk tidak diulang otomatis.',
            )}
          </p>
        ) : null}
        {state.message === 'unknown' ? (
          <p role="alert" className={styles.error}>
            {t(
              'The session change result is not confirmed. Check the current session before trying again.',
              'Hasil perubahan sesi belum dipastikan. Periksa sesi saat ini sebelum mencoba lagi.',
            )}
          </p>
        ) : null}
        {state.message === 'rejected' && !readyStaff ? (
          <p role="alert" className={styles.error}>
            {t(
              'Sign-in could not be completed. Check your details and try again when allowed.',
              'Proses masuk belum dapat diselesaikan. Periksa data Anda dan coba kembali saat diizinkan.',
            )}
          </p>
        ) : null}
        {waiting ? (
          <p role="status">
            {t(
              'Please wait before trying again.',
              'Tunggu sebelum mencoba lagi.',
            )}{' '}
            {Math.ceil((state.retryAt - now) / 1000)}{' '}
            {t('seconds remaining.', 'detik tersisa.')}
          </p>
        ) : null}
        {readyStaff ? (
          <>
            <p role="status">
              {t(
                'A staff session and its memberships have been verified.',
                'Sesi petugas dan keanggotaannya telah diverifikasi.',
              )}
            </p>
            <Link to={localizedTarget('/staff', locale)}>
              {t('Open staff workspace', 'Buka ruang kerja petugas')}
            </Link>
            <SignOut store={store} />
          </>
        ) : null}
        {auth.phase === 'ready' && auth.kind === 'customer' ? (
          <>
            <p>
              {t(
                'You are signed in as a customer. Sign out explicitly before using a staff account.',
                'Anda masuk sebagai pelanggan. Keluar secara eksplisit sebelum menggunakan akun petugas.',
              )}
            </p>
            <SignOut store={store} />
          </>
        ) : null}
        {auth.phase === 'guest' ? (
          <Button
            disabled={state.busy || waiting}
            onClick={() => void store.prepare()}
          >
            {t('Prepare staff sign-in', 'Siapkan sesi masuk petugas')}
          </Button>
        ) : null}
        {auth.phase === 'ready' && auth.kind === 'anonymous' && !state.busy ? (
          <LoginForm
            key={auth.boundaryId + ':' + auth.epoch}
            store={store}
            waiting={waiting}
          />
        ) : null}
        {auth.phase === 'unavailable' || state.message === 'unknown' ? (
          <Button
            variant="secondary"
            disabled={state.busy}
            onClick={() => void store.auth.recover()}
          >
            {t('Check session', 'Periksa sesi')}
          </Button>
        ) : null}
        <p>
          <Link to={localizedTarget('/', locale)}>
            {t('Customer help centre', 'Pusat bantuan pelanggan')}
          </Link>
        </p>
      </section>
    </PageFrame>
  );
}
function LoginForm({
  store,
  waiting,
}: {
  store: StaffStore;
  waiting: boolean;
}) {
  const { locale } = useRouting(),
    t = (en: string, id: string) => (locale === 'en' ? en : id),
    summary = useRef<HTMLDivElement>(null),
    focusedSubmission = useRef(0);
  const {
    register,
    handleSubmit,
    resetField,
    setFocus,
    control,
    formState: { errors, submitCount },
  } = useForm<z.infer<typeof loginSchema>>({
    resolver: zodResolver(loginSchema),
    defaultValues: { contact: '', password: '' },
    shouldFocusError: false,
  });
  const dirty = useWatch({ control, name: 'password' }).length > 0;
  useUnsavedChanges(dirty, () => resetField('password', { defaultValue: '' }));
  useEffect(() => {
    if (submitCount > focusedSubmission.current && Object.keys(errors).length) {
      focusedSubmission.current = submitCount;
      summary.current?.focus();
    }
  }, [errors, submitCount]);
  return (
    <form
      className={styles.form}
      noValidate
      onSubmit={handleSubmit((v) => {
        const password = v.password;
        resetField('password', { defaultValue: '' });
        void store.login(v.contact, password);
      })}
    >
      {Object.keys(errors).length ? (
        <div ref={summary} tabIndex={-1} role="alert" className={styles.error}>
          <h2>{t('Check your sign-in details', 'Periksa data masuk Anda')}</h2>
          {(['contact', 'password'] as const)
            .filter((k) => errors[k])
            .map((k) => (
              <p key={k}>
                <a
                  href={'#staff-' + k}
                  onClick={(e) => {
                    e.preventDefault();
                    setFocus(k);
                  }}
                >
                  {k === 'contact'
                    ? t(
                        'Enter a valid staff email address.',
                        'Isi alamat email petugas yang valid.',
                      )
                    : t(
                        'Enter a password of 1–128 characters.',
                        'Isi kata sandi sepanjang 1–128 karakter.',
                      )}
                </a>
              </p>
            ))}
        </div>
      ) : null}
      <label htmlFor="staff-contact">{t('Staff email', 'Email petugas')}</label>
      <input
        id="staff-contact"
        {...register('contact')}
        type="email"
        autoComplete="username"
        maxLength={320}
        aria-invalid={!!errors.contact}
        aria-describedby={errors.contact ? 'staff-contact-error' : undefined}
      />
      {errors.contact ? (
        <p id="staff-contact-error" className={styles.fieldError}>
          {t(
            'Enter a valid staff email address.',
            'Isi alamat email petugas yang valid.',
          )}
        </p>
      ) : null}
      <label htmlFor="staff-password">{t('Password', 'Kata sandi')}</label>
      <input
        id="staff-password"
        {...register('password')}
        type="password"
        autoComplete="current-password"
        maxLength={128}
        aria-invalid={!!errors.password}
        aria-describedby={errors.password ? 'staff-password-error' : undefined}
      />
      {errors.password ? (
        <p id="staff-password-error" className={styles.fieldError}>
          {t(
            'Enter a password of 1–128 characters.',
            'Isi kata sandi sepanjang 1–128 karakter.',
          )}
        </p>
      ) : null}
      <Button type="submit" disabled={waiting}>
        {t('Sign in', 'Masuk')}
      </Button>
    </form>
  );
}
export function StaffWorkspace() {
  const store = useStaff();
  return store ? (
    <WorkspaceScreen store={store} />
  ) : (
    <PageFrame>
      <p role="alert">Staff access is unavailable.</p>
    </PageFrame>
  );
}
function WorkspaceScreen({ store }: { store: StaffStore }) {
  const state = useSyncExternalStore(store.subscribe, store.getState),
    auth = useAuthState(store.auth),
    { locale } = useRouting(),
    t = (en: string, id: string) => (locale === 'en' ? en : id);
  if (auth.phase !== 'ready' || auth.kind !== 'staff' || !state.ready)
    return (
      <PageFrame>
        <h1>{t('Staff access required', 'Akses petugas diperlukan')}</h1>
        <p role="status">
          {auth.phase === 'checking' || auth.phase === 'transition'
            ? t('Checking staff access…', 'Memeriksa akses petugas…')
            : t(
                'Sign in with a staff account to open this workspace.',
                'Masuk dengan akun petugas untuk membuka ruang kerja ini.',
              )}
        </p>
        <Link to={localizedTarget('/staff/login', locale)}>
          {t('Staff sign-in', 'Masuk petugas')}
        </Link>
      </PageFrame>
    );
  const member = state.memberships.find((m) => m.id === state.selectedId),
    role = (r: string) =>
      ({
        cs: t('Customer service', 'Layanan pelanggan'),
        supervisor: t('Supervisor', 'Supervisor'),
        admin: t('Administrator', 'Administrator'),
      })[r as 'cs'];
  return (
    <PageFrame staff>
      <section
        className={styles.workspace}
        aria-labelledby="staff-workspace-title"
      >
        <h1 id="staff-workspace-title">
          {t('Staff workspace', 'Ruang kerja petugas')}
        </h1>
        <p>
          {t(
            'Your workspace scope follows the memberships assigned to this staff account.',
            'Cakupan ruang kerja mengikuti keanggotaan yang ditetapkan untuk akun petugas ini.',
          )}
        </p>
        <label htmlFor="staff-membership">
          {t('Organization access', 'Akses organisasi')}
        </label>
        <select
          id="staff-membership"
          value={state.selectedId ?? ''}
          onChange={(e) => store.select(e.target.value)}
        >
          <option value="" disabled>
            {t(
              'Choose an assigned organization',
              'Pilih organisasi yang ditugaskan',
            )}
          </option>
          {state.memberships.map((m) => (
            <option key={m.id} value={m.id}>
              {m.organization_id} — {m.roles.map(role).join(', ')}
            </option>
          ))}
        </select>
        {member ? (
          <>
            <dl>
              <div>
                <dt>{t('Organization reference', 'Referensi organisasi')}</dt>
                <dd>{member.organization_id}</dd>
              </div>
              <div>
                <dt>
                  {t('Roles in this organization', 'Peran pada organisasi ini')}
                </dt>
                <dd>{member.roles.map(role).join(', ')}</dd>
              </div>
              <div>
                <dt>{t('Assigned queues', 'Antrean yang ditugaskan')}</dt>
                <dd>{member.queue_ids.length}</dd>
              </div>
            </dl>
            {canHandleCases(member) ? (
              <p>
                {t(
                  'Case access is enabled for the queues assigned to this membership.',
                  'Akses kasus tersedia untuk antrean yang ditetapkan pada keanggotaan ini.',
                )}
              </p>
            ) : (
              <p>
                {t(
                  'Case access is not enabled for this organization membership. Administrative access does not grant access to customer cases.',
                  'Akses kasus tidak tersedia pada keanggotaan organisasi ini. Akses administrasi tidak memberikan akses kasus pelanggan.',
                )}
              </p>
            )}
          </>
        ) : (
          <p role="status">
            {t(
              'Choose an organization to view your access.',
              'Pilih organisasi untuk melihat akses Anda.',
            )}
          </p>
        )}
        <div className={styles.actions}>
          <Button
            variant="secondary"
            disabled={state.busy}
            onClick={() => void store.auth.recover()}
          >
            {t('Refresh staff access', 'Perbarui akses petugas')}
          </Button>
        </div>
      </section>
    </PageFrame>
  );
}
