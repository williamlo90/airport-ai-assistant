import { useEffect, useMemo } from 'react';
import { AuthContext } from '../app/auth/context';
import { StaffContext } from '../features/staff/context';
import StaffCasesPage from '../features/staff/StaffCasesPage';
import { makeStaffFixture } from './staff-fixtures';
import { useLocation } from 'react-router';
export function Component() {
  const location = useLocation();
  const supervisor =
    new URLSearchParams(location.search).get('role') === 'supervisor';
  const fixture = useMemo(() => {
    const value = makeStaffFixture();
    if (supervisor) value.server.roles = ['supervisor'];
    return value;
  }, [supervisor]);
  const lease = useMemo(() => {
    let generation = 0;
    return {
      acquire: () => ++generation,
      isCurrent: (value: number) => value === generation,
    };
  }, []);
  useEffect(() => {
    const generation = lease.acquire();
    void fixture.ready();
    return () => {
      queueMicrotask(() => {
        if (lease.isCurrent(generation)) fixture.dispose();
      });
    };
  }, [fixture, lease]);
  return (
    <AuthContext value={fixture.auth}>
      <StaffContext value={fixture.staff}>
        <p
          role="note"
          style={{
            padding: '12px 24px',
            margin: 0,
            background: '#fff5df',
            color: '#78450a',
          }}
        >
          Design preview — Synthetic staff and cases. No network requests are
          made by this workspace.
        </p>
        <StaffCasesPage basePath="/__dev/staff/cases" />
      </StaffContext>
    </AuthContext>
  );
}
