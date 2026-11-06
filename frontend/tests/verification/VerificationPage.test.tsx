import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { StrictMode } from 'react';
import { createMemoryRouter } from 'react-router';
import { afterEach, expect, it } from 'vitest';
import { App } from '../../src/app/App';
import { createBootstrap } from '../../src/app/config/publicSite';
import { createAppRoutes } from '../../src/app/routing/routes';
import { verificationFixture } from './fixture';
const owned: ReturnType<typeof verificationFixture>[] = [];
afterEach(() => owned.splice(0).forEach((f) => f.dispose()));
async function mount(search = '?lang=en') {
  const f = verificationFixture();
  owned.push(f);
  await f.auth.start();
  const bootstrap = createBootstrap('localhost', '');
  if (bootstrap.status !== 'configured') throw Error('fixture');
  const router = createMemoryRouter(
    createAppRoutes(bootstrap.site, undefined, [], f.auth),
    { initialEntries: ['/help/verify' + search] },
  );
  render(
    <StrictMode>
      <App bootstrap={bootstrap} router={router} />
    </StrictMode>,
  );
  await screen.findByRole('textbox', { name: 'Email address' });
  return { ...f, router };
}
it('does not request OTP on mount; normal503 is honest and email survives language/focus', async () => {
  const f = await mount();
  expect(f.scenario.requests).toBe(0);
  f.scenario.challengeStatus = 503;
  fireEvent.change(screen.getByRole('textbox', { name: 'Email address' }), {
    target: { value: 'synthetic@example.com' },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Bahasa Indonesia' })),
  );
  expect(screen.getByRole('textbox', { name: 'Alamat email' })).toHaveValue(
    'synthetic@example.com',
  );
  await act(() => f.auth.recheck());
  expect(
    await screen.findByRole('textbox', { name: 'Alamat email' }),
  ).toHaveValue('synthetic@example.com');
  fireEvent.click(screen.getByRole('button', { name: 'Minta kode' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Pengiriman kode belum dikonfirmasi',
  );
  expect(
    screen.queryByRole('textbox', { name: 'Kode 8 digit' }),
  ).not.toBeInTheDocument();
  expect(f.router.state.location.search).toBe('?lang=id');
});
it('generic invalid/expired code empties OTP field and allows explicit same-browser retry', async () => {
  const f = await mount();
  fireEvent.change(screen.getByRole('textbox', { name: 'Email address' }), {
    target: { value: 'synthetic@example.com' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Request a code' }));
  const code = await screen.findByRole('textbox', { name: '8-digit code' });
  expect(code).toHaveFocus();
  fireEvent.change(code, { target: { value: 'bad' } });
  fireEvent.click(screen.getByRole('button', { name: 'Verify and continue' }));
  expect(await screen.findByText('Enter exactly 8 digits.')).toBeVisible();
  expect(f.scenario.verifies).toBe(0);
  f.scenario.verifyStatus = 422;
  fireEvent.change(code, { target: { value: '00000000' } });
  fireEvent.click(screen.getByRole('button', { name: 'Verify and continue' }));
  await waitFor(() =>
    expect(screen.getByRole('alert')).toHaveTextContent(
      'That code could not be verified',
    ),
  );
  expect(screen.getByRole('textbox', { name: '8-digit code' })).toHaveValue('');
  expect(f.scenario.verifies).toBe(1);
});
it('success is explicit review, retains no OTP/URL email, and creates no conversation automatically', async () => {
  const f = await mount();
  fireEvent.change(screen.getByRole('textbox', { name: 'Email address' }), {
    target: { value: 'synthetic@example.com' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Request a code' }));
  fireEvent.change(
    await screen.findByRole('textbox', { name: '8-digit code' }),
    { target: { value: '12345678' } },
  );
  fireEvent.click(screen.getByRole('button', { name: 'Verify and continue' }));
  expect(
    await screen.findByText('Email verified for this session'),
  ).toBeVisible();
  expect(
    screen.queryByRole('textbox', { name: '8-digit code' }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole('textbox', { name: 'Your report' })).toHaveValue('');
  expect(f.server.creates).toBe(0);
  expect(f.router.state.location.search).toBe('?lang=en');
});
it('keeps only the bounded existing-case selector through verification and explicit conversation creation', async () => {
  const selected = '55555555-5555-4555-8555-555555555555';
  const f = await mount('?case=' + selected + '&lang=en');
  fireEvent.change(screen.getByRole('textbox', { name: 'Email address' }), {
    target: { value: 'synthetic@example.com' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Request a code' }));
  fireEvent.change(
    await screen.findByRole('textbox', { name: '8-digit code' }),
    { target: { value: '12345678' } },
  );
  fireEvent.click(screen.getByRole('button', { name: 'Verify and continue' }));
  await screen.findByText('Email verified for this session');
  fireEvent.change(screen.getByRole('textbox', { name: 'Your report' }), {
    target: { value: 'I need help with my existing case.' },
  });
  expect(f.server.creates).toBe(0);
  fireEvent.click(
    screen.getByRole('button', { name: 'Continue with reviewed report' }),
  );
  await waitFor(() =>
    expect(f.router.state.location.pathname).toContain('/help/conversations/'),
  );
  expect(new URLSearchParams(f.router.state.location.search).get('case')).toBe(
    selected,
  );
  expect(f.router.state.location.search).not.toContain('12345678');
  expect(f.router.state.location.search).not.toContain('synthetic@');
  expect(f.server.creates).toBe(1);
});
