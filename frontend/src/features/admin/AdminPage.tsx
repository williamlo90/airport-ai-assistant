import { useEffect, useMemo, useSyncExternalStore, useRef } from 'react';
import { Link } from 'react-router';
import { useStaff } from '../staff/context';
import type { StaffStore } from '../staff/store';
import { useRouting } from '../../app/routing/context';
import { localizedTarget } from '../../app/routing/policy';
import { useUnsavedChanges } from '../../app/routing/draftState';
import { PageFrame } from '../../app/routing/pages';
import { Button } from '../../components/Button';
import { errorMessage } from '../../api/errors';
import { AdminStore } from './store';
import { accessEdit, roles } from './contracts';
import styles from './admin.module.css';
export default function AdminPage() {
  const staff = useStaff();
  return (
    <PageFrame staff>
      {staff ? (
        <AdminEntry staff={staff} />
      ) : (
        <p role="alert">Staff access is unavailable.</p>
      )}
    </PageFrame>
  );
}
function AdminEntry({ staff }: { staff: StaffStore }) {
  const store = useMemo(() => new AdminStore(staff), [staff]);
  useEffect(() => store.connect(), [store]);
  return <AdminPanel store={store} />;
}
export function AdminPanel({ store }: { store: AdminStore }) {
  const staff = store.staff,
    scope = useSyncExternalStore(staff.subscribe, staff.getState),
    state = useSyncExternalStore(store.subscribe, store.getState),
    { locale } = useRouting(),
    t = (en: string, id: string) => (locale === 'en' ? en : id);
  const feedbackFocus = useRef<HTMLElement>(null);
  useEffect(() => {
    if (state.phase === 'review' || state.error) feedbackFocus.current?.focus();
  }, [state.phase, state.error]);
  const selected = scope.memberships.find((m) => m.id === scope.selectedId),
    allowed = scope.ready && selected?.roles.includes('admin');
  const dirty =
    !!state.edit &&
    !!state.current &&
    (state.edit.active !== state.current.member.active ||
      JSON.stringify([...state.edit.roles].sort()) !==
        JSON.stringify([...state.current.member.roles].sort()) ||
      JSON.stringify([...state.edit.queue_ids].sort()) !==
        JSON.stringify([...state.current.member.queue_ids].sort()));
  useUnsavedChanges(
    dirty || ['review', 'sending', 'unknown'].includes(state.phase),
    () => store.discard(),
  );
  const name = (r: (typeof roles)[number]) =>
    ({
      cs: t('Customer service', 'Layanan pelanggan'),
      supervisor: t('Supervisor', 'Supervisor'),
      admin: t('Administrator', 'Administrator'),
    })[r];
  const busy = ['loading', 'sending'].includes(state.phase),
    current = state.current,
    edit = state.edit;
  const run = (f: () => Promise<void>) => void f();
  return (
    <section className={styles.page}>
      <header>
        <h1>{t('Staff access', 'Akses petugas')}</h1>
        <p>
          {t(
            'Manage access for recorded staff accounts in one organization.',
            'Kelola akses akun petugas tercatat dalam satu organisasi.',
          )}
        </p>
        <Link to={localizedTarget('/staff', locale)}>
          {t('Back to workspace', 'Kembali ke ruang kerja')}
        </Link>
      </header>
      {!scope.ready ? (
        <>
          <p role="status">
            {t(
              'Sign in to check your staff access.',
              'Masuk untuk memeriksa akses petugas Anda.',
            )}
          </p>
          <Link to={localizedTarget('/staff/login', locale)}>
            {t('Staff sign-in', 'Masuk petugas')}
          </Link>
        </>
      ) : (
        <>
          <label className={styles.label}>
            {t('Organization membership', 'Keanggotaan organisasi')}
            <select
              value={scope.selectedId ?? ''}
              disabled={
                busy ||
                dirty ||
                state.phase === 'review' ||
                state.phase === 'unknown'
              }
              onChange={(e) => staff.select(e.target.value)}
            >
              <option value="" disabled>
                {t('Choose an organization', 'Pilih organisasi')}
              </option>
              {scope.memberships.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.organization_id}
                  {m.roles.includes('admin') ? ' (admin)' : ''}
                </option>
              ))}
            </select>
          </label>
          {!allowed ? (
            <p role="alert">
              {t(
                'Choose a membership with administrator access.',
                'Pilih keanggotaan dengan akses administrator.',
              )}
            </p>
          ) : (
            <>
              {state.phase === 'idle' && (
                <Button onClick={() => run(() => store.refresh())}>
                  {t('Load staff accounts', 'Muat akun petugas')}
                </Button>
              )}
              {busy && (
                <p role="status">
                  {state.phase === 'sending'
                    ? t('Saving access changes…', 'Menyimpan perubahan akses…')
                    : t(
                        'Checking current access…',
                        'Memeriksa akses saat ini…',
                      )}
                </p>
              )}
              {state.error && (
                <div
                  className={styles.notice}
                  role="alert"
                  tabIndex={-1}
                  ref={feedbackFocus as React.RefObject<HTMLDivElement>}
                >
                  <p>{errorMessage(state.error, locale)}</p>
                  <p>
                    {state.phase === 'unknown'
                      ? t(
                          'The save result is uncertain. Read the current access before making another change.',
                          'Hasil penyimpanan belum pasti. Baca akses saat ini sebelum mengubahnya lagi.',
                        )
                      : t(
                          'Read the current access and review any changes again.',
                          'Baca akses saat ini dan tinjau ulang perubahan.',
                        )}
                  </p>
                  <Button
                    variant="secondary"
                    onClick={() => run(() => store.recover())}
                  >
                    {t('Read current access', 'Baca akses saat ini')}
                  </Button>
                </div>
              )}
              {state.notice && (
                <p className={styles.notice} role="status">
                  {state.notice === 'saved'
                    ? t(
                        'Access changes were saved.',
                        'Perubahan akses tersimpan.',
                      )
                    : t(
                        'Current access loaded. This is not a receipt for the earlier request.',
                        'Akses saat ini dimuat. Ini bukan bukti penerimaan permintaan sebelumnya.',
                      )}
                </p>
              )}
              {state.page && (
                <>
                  <h2>{state.page.workspace.organization.name}</h2>
                  <p>
                    {t(
                      'Accounts are listed by account reference. Refresh to see newer changes.',
                      'Akun diurutkan berdasarkan referensi akun. Muat ulang untuk perubahan terbaru.',
                    )}
                  </p>
                  <ul className={styles.directory}>
                    {state.page.items.map((m) => (
                      <li key={m.principal_id}>
                        <div>
                          <strong>
                            {m.display_name ||
                              t(
                                'Unnamed staff account',
                                'Akun petugas tanpa nama',
                              )}
                          </strong>
                          <p>{m.principal_id}</p>
                          <span>
                            {m.active && m.principal_active
                              ? t('Active', 'Aktif')
                              : t('Inactive', 'Tidak aktif')}{' '}
                            / {m.roles.map(name).join(', ')}
                          </span>
                        </div>
                        <Button
                          variant="secondary"
                          onClick={() =>
                            run(() => store.choose(m.principal_id))
                          }
                        >
                          {t('View access', 'Lihat akses')}
                        </Button>
                      </li>
                    ))}
                  </ul>
                  {!state.page.items.length && (
                    <p>
                      {t(
                        'No staff accounts in this organization.',
                        'Belum ada akun petugas dalam organisasi ini.',
                      )}
                    </p>
                  )}
                  <div className={styles.actions}>
                    <Button
                      variant="secondary"
                      disabled={!state.canBack}
                      onClick={() => run(() => store.previous())}
                    >
                      {t('Previous', 'Sebelumnya')}
                    </Button>
                    <Button
                      variant="secondary"
                      disabled={!state.page.has_more}
                      onClick={() => run(() => store.next())}
                    >
                      {t('Next', 'Berikutnya')}
                    </Button>
                    <Button
                      variant="quiet"
                      onClick={() => run(() => store.refresh())}
                    >
                      {t('Refresh list', 'Muat ulang daftar')}
                    </Button>
                  </div>
                </>
              )}
              {current && (
                <article className={styles.member}>
                  <h2>
                    {current.member.display_name ||
                      t('Staff account', 'Akun petugas')}
                  </h2>
                  <p>{current.workspace.organization.name}</p>
                  <p className={styles.reference}>
                    {current.member.principal_id}
                  </p>
                  {!current.member.principal_active && (
                    <p>
                      {t(
                        'The account itself is inactive. Membership changes do not reactivate the account.',
                        'Akun ini tidak aktif. Perubahan keanggotaan tidak mengaktifkan akun.',
                      )}
                    </p>
                  )}
                  {store.isSelf && (
                    <p role="status">
                      {t(
                        'You can read your access here. Changes to your own account are not allowed.',
                        'Anda dapat membaca akses sendiri. Perubahan pada akun Anda sendiri tidak diizinkan.',
                      )}
                    </p>
                  )}
                  {state.phase === 'review' && state.review ? (
                    <section
                      className={styles.review}
                      tabIndex={-1}
                      ref={feedbackFocus}
                      aria-label={t(
                        'Review access changes',
                        'Tinjau perubahan akses',
                      )}
                    >
                      <h3>
                        {t('Review access changes', 'Tinjau perubahan akses')}
                      </h3>
                      <p>
                        {t(
                          'Check who will gain or lose access before saving.',
                          'Periksa siapa yang memperoleh atau kehilangan akses sebelum menyimpan.',
                        )}
                      </p>
                      <dl>
                        <dt>{t('Membership', 'Keanggotaan')}</dt>
                        <dd>
                          {state.review.before.active
                            ? t('Active', 'Aktif')
                            : t('Inactive', 'Tidak aktif')}{' '}
                          →{' '}
                          {state.review.after.active
                            ? t('Active', 'Aktif')
                            : t('Inactive', 'Tidak aktif')}
                        </dd>
                        <dt>{t('Roles', 'Peran')}</dt>
                        <dd>
                          {state.review.before.roles.map(name).join(', ')} →{' '}
                          {state.review.after.roles.map(name).join(', ')}
                        </dd>
                        <dt>{t('Queues', 'Antrean')}</dt>
                        <dd>
                          {state.review.before.queue_ids
                            .map(
                              (id) =>
                                current.workspace.queues.find(
                                  (q) => q.id === id,
                                )?.name ?? id,
                            )
                            .join(', ') || t('None', 'Tidak ada')}{' '}
                          →{' '}
                          {state.review.after.queue_ids
                            .map(
                              (id) =>
                                current.workspace.queues.find(
                                  (q) => q.id === id,
                                )?.name ?? id,
                            )
                            .join(', ') || t('None', 'Tidak ada')}
                        </dd>
                      </dl>
                      <div className={styles.actions}>
                        <Button onClick={() => run(() => store.save())}>
                          {t('Save access changes', 'Simpan perubahan akses')}
                        </Button>
                        <Button
                          variant="secondary"
                          onClick={() => store.cancel()}
                        >
                          {t('Back to editing', 'Kembali mengubah')}
                        </Button>
                      </div>
                    </section>
                  ) : (
                    edit && (
                      <form
                        onSubmit={(e) => {
                          e.preventDefault();
                          store.review();
                        }}
                      >
                        <fieldset disabled={busy || store.isSelf}>
                          <legend>
                            {t('Organization access', 'Akses organisasi')}
                          </legend>
                          <label>
                            <input
                              type="checkbox"
                              checked={edit.active}
                              onChange={(e) =>
                                store.edit({
                                  ...edit,
                                  active: e.target.checked,
                                })
                              }
                            />
                            {t('Membership active', 'Keanggotaan aktif')}
                          </label>
                          <h3>{t('Roles', 'Peran')}</h3>
                          {roles.map((r) => (
                            <label key={r}>
                              <input
                                type="checkbox"
                                checked={edit.roles.includes(r)}
                                onChange={(e) =>
                                  store.edit({
                                    ...edit,
                                    roles: e.target.checked
                                      ? [...edit.roles, r]
                                      : edit.roles.filter((x) => x !== r),
                                  })
                                }
                              />
                              {name(r)}
                            </label>
                          ))}
                          <h3>{t('Queues', 'Antrean')}</h3>
                          {current.workspace.queues.map((q) => (
                            <label key={q.id}>
                              <input
                                type="checkbox"
                                checked={edit.queue_ids.includes(q.id)}
                                disabled={
                                  !q.active && !edit.queue_ids.includes(q.id)
                                }
                                onChange={(e) =>
                                  store.edit({
                                    ...edit,
                                    queue_ids: e.target.checked
                                      ? [...edit.queue_ids, q.id]
                                      : edit.queue_ids.filter(
                                          (x) => x !== q.id,
                                        ),
                                  })
                                }
                              />
                              {q.name}
                              {!q.active
                                ? ' (' + t('inactive', 'tidak aktif') + ')'
                                : ''}
                            </label>
                          ))}
                        </fieldset>
                        {!accessEdit.safeParse(edit).success && (
                          <p role="alert">
                            {t(
                              'Select at least one role and no more than 100 queues.',
                              'Pilih minimal satu peran dan maksimal 100 antrean.',
                            )}
                          </p>
                        )}
                        <div className={styles.actions}>
                          <Button
                            type="submit"
                            disabled={
                              busy ||
                              store.isSelf ||
                              !dirty ||
                              !accessEdit.safeParse(edit).success ||
                              edit.queue_ids.some(
                                (id) =>
                                  !current.workspace.queues.some(
                                    (q) => q.id === id && q.active,
                                  ),
                              )
                            }
                          >
                            {t('Review changes', 'Tinjau perubahan')}
                          </Button>
                          <Button
                            variant="secondary"
                            disabled={busy}
                            onClick={() => store.discard()}
                          >
                            {t('Discard changes', 'Buang perubahan')}
                          </Button>
                        </div>
                      </form>
                    )
                  )}
                  {state.phase === 'ready' && !dirty && (
                    <Button
                      variant="quiet"
                      onClick={() => run(() => store.refresh())}
                    >
                      {t('Back to staff list', 'Kembali ke daftar petugas')}
                    </Button>
                  )}
                </article>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}
