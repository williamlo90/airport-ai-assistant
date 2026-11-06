import { useState, useSyncExternalStore } from 'react';
import { Link } from 'react-router';
import { useStaff } from './context';
import type { StaffStore } from './store';
import { canHandleCases } from './store';
import { useAuthState } from '../../app/auth/context';
import type { BoundarySnapshot } from '../../api/contracts';
import { useRouting } from '../../app/routing/context';
import { localizedTarget } from '../../app/routing/policy';
import { Button } from '../../components/Button';
import { Dialog } from '../../components/Dialog';
export function StaffNavigation() {
  const store = useStaff();
  const { locale } = useRouting();
  return store ? (
    <Navigation store={store} />
  ) : (
    <Link to={localizedTarget('/staff/login', locale)}>
      {locale === 'en' ? 'Staff sign-in' : 'Masuk petugas'}
    </Link>
  );
}
function Navigation({ store }: { store: StaffStore }) {
  const state = useSyncExternalStore(store.subscribe, store.getState),
    { locale } = useRouting();
  const selected = state.memberships.find((m) => m.id === state.selectedId);
  return (
    <>
      <Link to={localizedTarget('/staff', locale)}>
        {locale === 'en' ? 'Workspace' : 'Ruang kerja'}
      </Link>
      {state.ready && canHandleCases(selected) ? (
        <Link to={localizedTarget('/staff/cases', locale)}>
          {locale === 'en' ? 'Cases' : 'Kasus'}
        </Link>
      ) : null}
      {state.ready &&
        state.memberships.some((m) => m.roles.includes('admin')) && (
          <Link to={localizedTarget('/admin', locale)}>
            {locale === 'en' ? 'Staff access' : 'Akses petugas'}
          </Link>
        )}
      {state.ready &&
        state.memberships.some((m) => m.roles.includes('admin')) && (
          <Link to={localizedTarget('/admin/information', locale)}>
            {locale === 'en' ? 'Service information' : 'Informasi layanan'}
          </Link>
        )}
      <SignOut store={store} />
    </>
  );
}
export function SignOut({ store }: { store: StaffStore }) {
  const state = useSyncExternalStore(store.subscribe, store.getState),
    auth = useAuthState(store.auth),
    { locale } = useRouting(),
    t = (en: string, id: string) => (locale === 'en' ? en : id);
  const [intent, setIntent] = useState<BoundarySnapshot | null>(null);
  const open =
    !!intent &&
    auth.phase === 'ready' &&
    auth.boundaryId === intent.boundaryId &&
    auth.epoch === intent.authEpoch;
  return (
    <>
      <Button
        variant="quiet"
        disabled={state.busy || auth.phase !== 'ready'}
        onClick={() => {
          try {
            setIntent(store.auth.capture());
          } catch {
            setIntent(null);
            void store.auth.recover();
          }
        }}
      >
        {t('Sign out', 'Keluar')}
      </Button>
      <Dialog
        open={open}
        title={t('Sign out of this account?', 'Keluar dari akun ini?')}
        description={t(
          'This ends the current session and clears information kept in this browser flow. Requests already sent may still complete.',
          'Ini mengakhiri sesi saat ini dan menghapus informasi yang disimpan pada alur browser ini. Permintaan yang sudah dikirim mungkin tetap diproses.',
        )}
        closeLabel={t('Close sign-out confirmation', 'Tutup konfirmasi keluar')}
        onClose={() => setIntent(null)}
        actions={
          <>
            <Button variant="secondary" onClick={() => setIntent(null)}>
              {t('Stay signed in', 'Tetap masuk')}
            </Button>
            <Button
              disabled={state.busy}
              onClick={() => {
                const chosen = intent;
                setIntent(null);
                if (chosen) void store.logout(chosen);
              }}
            >
              {t('Sign out of account', 'Keluar dari akun')}
            </Button>
          </>
        }
      />
    </>
  );
}
