import { useEffect, useMemo } from 'react';
import type { ReactNode } from 'react';
import type { AuthCoordinator } from '../../app/auth/coordinator';
import { StaffStore } from './store';
import { StaffContext } from './context';
export function StaffProvider({
  auth,
  children,
}: {
  auth: AuthCoordinator | null;
  children: ReactNode;
}) {
  const store = useMemo(() => (auth ? new StaffStore(auth) : null), [auth]);
  useEffect(() => {
    if (!store) return;
    const off = store.connect();
    return () => {
      off();
      store.dispose();
    };
  }, [store]);
  return <StaffContext value={store}>{children}</StaffContext>;
}
