import { useLayoutEffect } from 'react';
import { act, render, screen } from '@testing-library/react';
import { createMemoryRouter, useLocation } from 'react-router';
import { afterEach, expect, it } from 'vitest';
import { App } from '../../src/app/App';
import { createBootstrap } from '../../src/app/config/publicSite';
import { createAppRoutes } from '../../src/app/routing/routes';
import StaffCasesPage from '../../src/features/staff/StaffCasesPage';
import { makeStaffFixture, caseId } from '../../src/dev/staff-fixtures';

const owned: ReturnType<typeof makeStaffFixture>[] = [];
afterEach(() => owned.splice(0).forEach((f) => f.dispose()));

it('never commits the previous report under the next case URL before passive effects run', async () => {
  const f = makeStaffFixture();
  owned.push(f);
  await f.ready();
  const bootstrap = createBootstrap('localhost', '');
  if (bootstrap.status !== 'configured') throw Error('fixture');
  const commits: string[] = [];
  function Probe() {
    const location = useLocation();
    useLayoutEffect(() => {
      if (location.pathname.endsWith(caseId(2)))
        commits.push(document.body.textContent ?? '');
    }, [location.pathname]);
    return null;
  }
  const router = createMemoryRouter(
    createAppRoutes(
      bootstrap.site,
      undefined,
      [
        {
          path: 'qa-staff',
          element: (
            <>
              <StaffCasesPage basePath="/qa-staff" />
              <Probe />
            </>
          ),
          children: [{ index: true }, { path: ':caseId' }],
        },
      ],
      f.auth,
    ),
    { initialEntries: ['/qa-staff/' + caseId(1) + '?lang=en'] },
  );
  render(<App bootstrap={bootstrap} router={router} />);
  await screen.findByRole('heading', { name: 'Customer report' });
  await act(() => router.navigate('/qa-staff/' + caseId(2) + '?lang=en'));
  await screen.findByRole('heading', { name: 'Lounge confirmation not found' });
  expect(commits.length).toBeGreaterThan(0);
  for (const text of commits)
    expect(text).not.toContain(
      'My flight has been delayed until tomorrow morning',
    );
});
