import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { createMemoryRouter } from 'react-router';
import { afterEach, expect, it } from 'vitest';
import { App } from '../../src/app/App';
import { createBootstrap } from '../../src/app/config/publicSite';
import { createAppRoutes } from '../../src/app/routing/routes';
import { makeStaffFixture, caseId } from '../../src/dev/staff-fixtures';
const owned: ReturnType<typeof makeStaffFixture>[] = [];
async function mount(path = '/staff/cases?lang=en') {
  const f = makeStaffFixture();
  owned.push(f);
  await f.ready();
  const bootstrap = createBootstrap('localhost', '');
  if (bootstrap.status !== 'configured') throw Error('fixture');
  const router = createMemoryRouter(
    createAppRoutes(bootstrap.site, undefined, [], f.auth),
    { initialEntries: [path] },
  );
  render(<App bootstrap={bootstrap} router={router} />);
  await screen.findByText('Flight delay: parking booking ends tonight');
  return { ...f, router };
}
afterEach(() => owned.splice(0).forEach((f) => f.dispose()));
it('actual lazy route opens read-only detail, focuses heading and returns to the same list/page', async () => {
  const f = await mount();
  const link = screen.getByRole('link', {
    name: /Flight delay: parking booking ends tonight/,
  });
  fireEvent.click(link);
  const heading = await screen.findByRole('heading', {
    name: 'Flight delay: parking booking ends tonight',
  });
  await waitFor(() => expect(heading).toHaveFocus());
  expect(
    screen.getByRole('heading', { name: 'Customer report' }),
  ).toBeVisible();
  expect(
    screen.queryByRole('button', { name: /Claim|Close case|Assign case/ }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('link', { name: 'Back to cases' }));
  await waitFor(() =>
    expect(f.router.state.location.pathname).toBe('/staff/cases'),
  );
  await waitFor(() => expect(link).toHaveFocus());
  expect(
    f.server.requests.filter((r) => r.path.includes('/queues/')),
  ).toHaveLength(1);
});
it('tabs and status send server queries, keep locale and expose no fixture totals', async () => {
  const f = await mount();
  fireEvent.click(screen.getByRole('button', { name: /^Mine$/ }));
  await screen.findByText('Page 1');
  await waitFor(() =>
    expect(f.server.requests.at(-1)?.path).toContain('view=mine'),
  );
  fireEvent.change(screen.getByLabelText('Status', { exact: true }), {
    target: { value: 'in_progress' },
  });
  await waitFor(() =>
    expect(f.server.requests.at(-1)?.path).toContain('status=in_progress'),
  );
  expect(f.router.state.location.search).toBe('?lang=en');
  expect(f.server.requests.every((r) => r.method === 'GET')).toBe(true);
});
it('changing case route hides the previous report until the new pair is verified', async () => {
  const f = await mount('/staff/cases/' + caseId(1) + '?lang=en');
  await screen.findByRole('heading', { name: 'Customer report' });
  await act(() => f.router.navigate('/staff/cases/' + caseId(2) + '?lang=en'));
  await screen.findByRole('heading', { name: 'Lounge confirmation not found' });
  expect(
    screen.queryByText(/My flight has been delayed until tomorrow morning/),
  ).not.toBeInTheDocument();
});
