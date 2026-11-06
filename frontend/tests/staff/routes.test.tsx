import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { createMemoryRouter } from 'react-router';
import { afterEach, expect, it } from 'vitest';
import { App } from '../../src/app/App';
import { createBootstrap } from '../../src/app/config/publicSite';
import { createAppRoutes } from '../../src/app/routing/routes';
import { staffFixture, CONTACT, PASSWORD, ORG } from './qa-fixture';
const owned: ReturnType<typeof staffFixture>[] = [];
async function mount() {
  const f = staffFixture();
  owned.push(f);
  await f.ready();
  const bootstrap = createBootstrap('localhost', '');
  if (bootstrap.status !== 'configured') throw Error('fixture');
  const router = createMemoryRouter(
    createAppRoutes(bootstrap.site, undefined, [], f.auth),
    { initialEntries: ['/staff/login?lang=en'] },
  );
  render(<App bootstrap={bootstrap} router={router} />);
  await screen.findByLabelText('Password');
  return { ...f, router };
}
afterEach(() => owned.splice(0).forEach((f) => f.dispose()));
it('the actual lazy login route uses the root provider and opens only freshly inspected staff scope', async () => {
  const f = await mount();
  fireEvent.change(screen.getByLabelText('Staff email'), {
    target: { value: CONTACT },
  });
  fireEvent.change(screen.getByLabelText('Password'), {
    target: { value: PASSWORD },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
  const workspace = await screen.findByRole('link', {
    name: 'Open staff workspace',
  });
  expect(f.router.state.location.pathname).toBe('/staff/login');
  expect(f.posts('/api/v1/staff/auth/login')).toHaveLength(1);
  expect(
    f.fetcher.mock.calls.some(([path]) => String(path) === '/api/v1/staff/me'),
  ).toBe(true);
  fireEvent.click(workspace);
  expect(
    await screen.findByRole('heading', { name: 'Staff workspace' }),
  ).toBeVisible();
  expect(screen.getByText(ORG)).toBeVisible();
  expect(screen.getByRole('link', { name: 'Cases' })).toBeVisible();
  expect(
    f.fetcher.mock.calls.some(([path]) => String(path).includes('/cases')),
  ).toBe(false);
});
it('invalid credentials focus a linked summary and never send a login command', async () => {
  const f = await mount();
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
  const alert = await screen.findByRole('alert');
  await waitFor(() => expect(alert).toHaveFocus());
  expect(screen.getByLabelText('Password')).toHaveAttribute(
    'aria-describedby',
    'staff-password-error',
  );
  fireEvent.click(
    screen.getByRole('link', { name: 'Enter a valid staff email address.' }),
  );
  await waitFor(() =>
    expect(screen.getByLabelText('Staff email')).toHaveFocus(),
  );
  expect(f.posts('/api/v1/staff/auth/login')).toHaveLength(0);
});
